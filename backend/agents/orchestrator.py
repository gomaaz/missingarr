import threading
from typing import Optional

from backend import db
from backend.agents.base import BaseAgent
from backend.agents.sonarr import SonarrAgent
from backend.agents.radarr import RadarrAgent


class Orchestrator:
    def __init__(self, broadcaster=None):
        self.broadcaster = broadcaster
        self._agents: dict[int, BaseAgent] = {}
        self._lock = threading.Lock()

    def _make_agent(self, config: dict) -> BaseAgent:
        arr_type = config.get("type", "sonarr")
        if arr_type == "sonarr":
            return SonarrAgent(config, self.broadcaster)
        elif arr_type == "radarr":
            return RadarrAgent(config, self.broadcaster)
        else:
            raise ValueError(f"Unknown instance type: {arr_type}")

    def start_all(self):
        instances = db.instances.get_all(include_disabled=False)
        for inst in instances:
            self.start_agent(inst["id"])

    def stop_all(self):
        with self._lock:
            agent_ids = list(self._agents.keys())
        for agent_id in agent_ids:
            self.stop_agent(agent_id)

    def start_agent(self, instance_id: int):
        config = db.instances.get_by_id(instance_id)
        if not config or not config.get("enabled"):
            return

        with self._lock:
            if instance_id in self._agents:
                self._agents[instance_id].stop()

            agent = self._make_agent(config)
            self._agents[instance_id] = agent

        agent.start()

    def stop_agent(self, instance_id: int):
        with self._lock:
            agent = self._agents.pop(instance_id, None)
        if agent:
            agent.stop()

    def reload_agent(self, instance_id: int):
        self.stop_agent(instance_id)
        config = db.instances.get_by_id(instance_id)
        if config and config.get("enabled"):
            self.start_agent(instance_id)

    def trigger(self, instance_id: int, skill_name: str, force: bool = True):
        with self._lock:
            agent = self._agents.get(instance_id)

        if not agent:
            # Try to spin up a temporary run if instance exists but wasn't running
            config = db.instances.get_by_id(instance_id)
            if config:
                agent = self._make_agent(config)
                agent._skills = agent.build_skills()
                agent.trigger_now(skill_name, force=force)
            return

        agent.trigger_now(skill_name, force=force)

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
