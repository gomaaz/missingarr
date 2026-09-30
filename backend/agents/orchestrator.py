import logging
import threading
from typing import Optional

from backend import db
from backend.agents.base import BaseAgent, InstanceRuntime, TRIGGER_STARTED, wait_runtime_idle
from backend.agents.sonarr import SonarrAgent
from backend.agents.radarr import RadarrAgent
from backend.skills.verify_commands import VerifyCommandsSkill

logger = logging.getLogger("missingarr.orchestrator")

TRIGGER_NOT_FOUND = "not_found"


class Orchestrator:
    def __init__(self, broadcaster=None):
        self.broadcaster = broadcaster
        self._agents: dict[int, BaseAgent] = {}
        self._lock = threading.Lock()
        # Throwaway agents of force runs on disabled instances, kept while they
        # run so that disabling or deleting can still abort them. Several per
        # instance: search_missing and search_upgrades may run at once.
        self._adhoc: dict[int, set[BaseAgent]] = {}
        # Rate window and skill locks per instance; they outlive the agent
        # objects that come and go on save, disable/enable and force runs (A-L4).
        self._runtimes: dict[int, InstanceRuntime] = {}
        self._runtimes_lock = threading.Lock()
        # App-wide retention pass for every instance, started by start_all.
        self._housekeeping_stop = threading.Event()
        self._housekeeping_thread: Optional[threading.Thread] = None

    def _runtime(self, instance_id: int) -> InstanceRuntime:
        with self._runtimes_lock:
            return self._runtimes.setdefault(instance_id, InstanceRuntime())

    def _agent_class(self, arr_type: str):
        if arr_type == "sonarr":
            return SonarrAgent
        if arr_type == "radarr":
            return RadarrAgent
        raise ValueError(f"Unknown instance type: {arr_type}")

    def _make_agent(self, config: dict) -> BaseAgent:
        agent_class = self._agent_class(config.get("type", "sonarr"))
        return agent_class(config, self.broadcaster, runtime=self._runtime(config["id"]))

    def start_all(self):
        instances = db.instances.get_all(include_disabled=False)
        for inst in instances:
            self.start_agent(inst["id"])
        if self._housekeeping_thread is None or not self._housekeeping_thread.is_alive():
            self._housekeeping_stop.clear()
            self._housekeeping_thread = threading.Thread(
                target=self._housekeeping_loop, name="housekeeping", daemon=True,
            )
            self._housekeeping_thread.start()

    def stop_all(self):
        self._housekeeping_stop.set()
        # Includes instances that only have a throwaway force run going.
        with self._lock:
            agent_ids = set(self._agents) | set(self._adhoc)
        for agent_id in agent_ids:
            self.stop_agent(agent_id)
        if self._housekeeping_thread is not None:
            self._housekeeping_thread.join(timeout=5)

    def _housekeeping_loop(self) -> None:
        # Right at start, then at the skill's interval until stop_all.
        while not self._housekeeping_stop.is_set():
            self.housekeeping()
            self._housekeeping_stop.wait(VerifyCommandsSkill.HOUSEKEEPING_INTERVAL_SECONDS)

    def housekeeping(self) -> None:
        """Retention (B6/B7) for every instance, disabled ones included.

        verify_commands does it only where an agent runs, and an instance that
        stays disabled has none: its old runs and expired cache rows were never
        removed. The agents made here are never started and only lend their
        log(); housekeeping reads and deletes rows and sends nothing to *arr.
        The throttle is the skill's, so an instance whose agent did it within
        the hour is skipped.
        """
        try:
            instances = db.instances.get_all()
        except Exception as exc:
            logger.warning("Housekeeping could not read the instances: %s", exc)
            return
        skill = VerifyCommandsSkill()
        for config in instances:
            if self._housekeeping_stop.is_set():
                return
            try:
                agent = self._agent_class(config.get("type", "sonarr"))(config, self.broadcaster)
            except ValueError as exc:
                logger.warning("Housekeeping skipped instance %s: %s", config.get("id"), exc)
                continue
            skill.housekeeping(agent)

    def start_agent(self, instance_id: int):
        config = db.instances.get_by_id(instance_id)
        if not config or not config.get("enabled"):
            return
        with self._lock:
            old = self._agents.pop(instance_id, None)
        if old is not None:
            old.stop(abort_running=False)
        agent = self._make_agent(config)
        with self._lock:
            # A concurrent start may have registered an agent meanwhile; stop
            # it too, or its scheduler would keep running unreferenced.
            displaced = self._agents.get(instance_id)
            self._agents[instance_id] = agent
        if displaced is not None:
            displaced.stop(abort_running=False)
        agent.start()

    def stop_agent(self, instance_id: int, abort_running: bool = True, wait_seconds: float = 0.0) -> bool:
        """Returns False only when waiting was asked for and a skill still runs."""
        with self._lock:
            agent = self._agents.pop(instance_id, None)
            adhocs = self._adhoc.pop(instance_id, set()) if abort_running else set()
        if agent is not None:
            agent.stop(abort_running=abort_running)
        for adhoc in adhocs:
            adhoc.request_abort()
        if wait_seconds > 0 and not wait_runtime_idle(self._runtime(instance_id), wait_seconds):
            logger.warning("Instance %s still busy after %.0fs", instance_id, wait_seconds)
            return False
        return True

    def reload_agent(self, instance_id: int):
        """Restart with fresh config; a search in progress keeps running."""
        self.stop_agent(instance_id, abort_running=False)
        config = db.instances.get_by_id(instance_id)
        if config and config.get("enabled"):
            self.start_agent(instance_id)

    def refresh_config(self, instance_id: int) -> None:
        """Pick up changed skill flags without a restart (A2)."""
        with self._lock:
            agent = self._agents.get(instance_id)
        if agent is not None:
            agent.refresh_config()

    def forget_instance(self, instance_id: int, wait_seconds: float = 15.0) -> bool:
        """Before deleting: abort, wait until no skill holds a lock, drop state.

        Returns False if a skill is still running after `wait_seconds`. The
        caller must not delete the row then: the skill could still send a
        command, and the runtime (with its unsaved submissions) is kept.
        """
        if not self.stop_agent(instance_id, abort_running=True, wait_seconds=wait_seconds):
            return False
        with self._runtimes_lock:
            self._runtimes.pop(instance_id, None)
        return True

    def trigger(self, instance_id: int, skill_name: str, force: bool = True) -> str:
        """Returns TRIGGER_STARTED, TRIGGER_BUSY, TRIGGER_UNKNOWN_SKILL or TRIGGER_NOT_FOUND."""
        with self._lock:
            agent = self._agents.get(instance_id)
        if agent is None:
            config = db.instances.get_by_id(instance_id)
            if not config:
                return TRIGGER_NOT_FOUND
            # Disabled instance: a throwaway agent on the shared runtime, kept
            # while it runs so that disabling or deleting can still abort it.
            agent = self._make_agent(config)
            agent._skills = agent.build_skills()
            with self._lock:
                self._adhoc.setdefault(instance_id, set()).add(agent)
            result = agent.trigger_now(
                skill_name, force=force,
                on_done=lambda done: self._forget_adhoc(instance_id, done),
            )
            if result != TRIGGER_STARTED:
                self._forget_adhoc(instance_id, agent)
            return result
        return agent.trigger_now(skill_name, force=force)

    def _forget_adhoc(self, instance_id: int, agent: BaseAgent) -> None:
        with self._lock:
            agents = self._adhoc.get(instance_id)
            if agents is None:
                return
            agents.discard(agent)
            if not agents:
                del self._adhoc[instance_id]

    def get_agent_state(self, instance_id: int) -> Optional[dict]:
        with self._lock:
            agent = self._agents.get(instance_id)
        if not agent:
            return None
        state = dict(agent.state)
        state["rate_used"] = agent.get_rate_used()
        state["rate_cap"] = agent.config.get("rate_cap", 25)
        state["rate_window"] = agent.config.get("rate_window_minutes", 60)
        return state

    def get_all_states(self) -> dict[int, dict]:
        with self._lock:
            agent_ids = list(self._agents.keys())
        return {aid: self.get_agent_state(aid) for aid in agent_ids}

    def is_running(self, instance_id: int) -> bool:
        with self._lock:
            return instance_id in self._agents
