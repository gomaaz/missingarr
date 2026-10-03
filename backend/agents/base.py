import logging
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Callable, Optional

import requests
from apscheduler.schedulers.background import BackgroundScheduler

from backend import db
from backend.skills.base import BaseSkill

logger = logging.getLogger("missingarr.agent")

MIN_INTERVAL_MINUTES = 1
MAX_INTERVAL_MINUTES = 10080
UPGRADE_INTERVAL_FACTOR = 4

TRIGGER_STARTED = "started"
TRIGGER_BUSY = "busy"
TRIGGER_UNKNOWN_SKILL = "unknown_skill"

# Verification only reads command status from *arr; holding it back during
# quiet hours just delayed verdicts and, near 24 h, lost them (B5).
QUIET_HOURS_EXEMPT = ("health_check", "verify_commands")


@dataclass
class InstanceRuntime:
    """Per-instance state that must outlive one agent object.

    Saving the form, switching the instance off and on, and a force run on a
    disabled instance each create a new agent. The rate window and the skill
    locks belong to the instance: otherwise every save refilled the rate cap
    (A-L4) and a run of the old agent could overlap one of the new agent.

    One lock per skill. state["status"] is a display value shared by the
    whole agent and must not double as a mutex — doing so let a running
    search block the health check every single hour.

    unsaved_submissions holds commands *arr accepted whose history item and
    cache entry could not be written (skills.base.store_unsaved_submissions).
    It lives in memory only: a restart of the process loses it, and those
    titles can then be searched once more. That is accepted on purpose.
    """

    rate_lock: threading.Lock = field(default_factory=threading.Lock)
    action_timestamps: deque = field(default_factory=deque)
    skill_locks: dict = field(default_factory=dict)
    skill_locks_guard: threading.Lock = field(default_factory=threading.Lock)
    unsaved_lock: threading.Lock = field(default_factory=threading.Lock)
    unsaved_submissions: list = field(default_factory=list)

    def skill_lock(self, skill_name: str) -> threading.Lock:
        with self.skill_locks_guard:
            return self.skill_locks.setdefault(skill_name, threading.Lock())

    def busy_skills(self) -> list[str]:
        with self.skill_locks_guard:
            return sorted(name for name, lock in self.skill_locks.items() if lock.locked())


def wait_runtime_idle(runtime: InstanceRuntime, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while runtime.busy_skills():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True


class BaseAgent(ABC):
    HEALTH_CHECK_INTERVAL_MINUTES = 5
    SEARCH_JOBS = (("missing", "search_missing_enabled"), ("upgrades", "search_upgrades_enabled"))

    def __init__(self, config: dict, broadcaster=None, runtime: InstanceRuntime | None = None):
        self.config = config
        self.broadcaster = broadcaster
        self._scheduler: Optional[BackgroundScheduler] = None
        self._stop_event = threading.Event()
        self._abort_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._scheduler_failed = False
        self.runtime = runtime if runtime is not None else InstanceRuntime()

        # Live state exposed to dashboard
        # "starting" until _run has really started the scheduler: the card must
        # not claim "scheduled" for an agent whose scheduler never ran (A1).
        self.state = {
            "status": "starting",   # starting | scheduled | running | off | quiet | error
            "next_run_at": None,
            "last_wanted": 0,
            "last_triggered": 0,
            "last_verified": 0,
            "last_sync": None,
            "connection_status": config.get("connection_status", "unknown"),
            "last_seen_at": config.get("last_seen_at"),
        }

        self._skills: list[BaseSkill] = []

    @abstractmethod
    def build_skills(self) -> list[BaseSkill]:
        ...

    def start(self):
        self._stop_event.clear()
        self._abort_event.clear()
        self._skills = self.build_skills()
        self._thread = threading.Thread(
            target=self._run,
            name=f"agent-{self.config['id']}-{self.config['name']}",
            daemon=True,
        )
        self._thread.start()
        # "Agent started" is logged by _run once the scheduler really runs (A1).

    def stop(self, abort_running: bool = True):
        """Stop scheduling. With abort_running a search in progress ends at its
        next check (A-L5); a reload passes False so saving the form does not
        cut a long run short."""
        if abort_running:
            self._abort_event.set()
        self._stop_event.set()
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self.state["status"] = "off"
        self.state["next_run_at"] = None
        self.log("info", "system", f"Agent stopped — '{self.config['name']}'")

    def request_abort(self) -> None:
        """Only set the abort signal (throwaway force agents, tests)."""
        self._abort_event.set()

    def stop_requested(self) -> bool:
        return self._abort_event.is_set()

    def wait_or_stop(self, seconds: float) -> bool:
        """Sleep between actions; returns True as soon as an abort is requested."""
        if seconds <= 0:
            return self._abort_event.is_set()
        return self._abort_event.wait(seconds)

    def wait_idle(self, timeout: float) -> bool:
        """True once no skill of this instance holds its lock any more."""
        return wait_runtime_idle(self.runtime, timeout)

    def reload(self, new_config: dict):
        self.stop(abort_running=False)
        self.config = new_config
        self.state["connection_status"] = new_config.get("connection_status", "unknown")
        self.start()

    def _interval_minutes(self) -> int:
        raw = self.config.get("interval_minutes", 15)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = 15
        clamped = min(max(value, MIN_INTERVAL_MINUTES), MAX_INTERVAL_MINUTES)
        if clamped != value:
            self.log(
                "warn", "system",
                f"interval_minutes={raw} is outside {MIN_INTERVAL_MINUTES}..{MAX_INTERVAL_MINUTES} "
                f"— using {clamped}",
            )
        return clamped

    def _build_scheduler(self) -> BackgroundScheduler:
        scheduler = BackgroundScheduler(
            timezone="UTC",
            job_defaults={"misfire_grace_time": 60, "coalesce": True},
        )
        instance_id = self.config["id"]
        interval = self._interval_minutes()
        upgrade_interval = interval * UPGRADE_INTERVAL_FACTOR
        now = datetime.now(timezone.utc)

        # Both search jobs are always registered and _run_skill gates them on
        # the enable flag it re-reads from the database. Registering only the
        # enabled ones meant a skill switched on from the card never ran (A2).
        scheduler.add_job(
            self._run_skill, "interval", minutes=interval,
            start_date=now + timedelta(minutes=interval),
            args=["search_missing"], id=f"missing_{instance_id}",
        )
        scheduler.add_job(
            self._run_skill, "interval", minutes=upgrade_interval,
            start_date=now + timedelta(minutes=upgrade_interval),
            args=["search_upgrades"], id=f"upgrades_{instance_id}",
        )
        scheduler.add_job(
            self._run_skill, "interval", minutes=self.HEALTH_CHECK_INTERVAL_MINUTES,
            args=["health_check"], id=f"health_{instance_id}",
            next_run_time=now + timedelta(seconds=10),
        )
        # Verification runs regardless of the search flags: entries submitted
        # before a skill was switched off would stay unresolved otherwise.
        scheduler.add_job(
            self._run_skill, "interval", minutes=2,
            args=["verify_commands"], id=f"verify_{instance_id}",
            next_run_time=now + timedelta(seconds=30),
        )
        return scheduler

    def _run(self):
        scheduler = None
        try:
            scheduler = self._build_scheduler()
            scheduler.start()
        except Exception as exc:
            logger.exception("Scheduler for instance %s failed to start", self.config.get("id"))
            self._scheduler_failed = True
            # Log first, then publish the status: whoever sees "error" must find the reason.
            self.log("error", "system", f"Scheduler failed to start — no searches will run: {exc}")
            self.state["next_run_at"] = None
            self.state["status"] = "error"
            if scheduler is not None and scheduler.running:
                scheduler.shutdown(wait=False)
            return

        self._scheduler = scheduler
        self._update_next_run()
        self.state["status"] = "scheduled"
        self.log("info", "system",
                 f"Agent started — {self.config['type'].upper()} '{self.config['name']}'")

        self._stop_event.wait()
        if scheduler.running:
            scheduler.shutdown(wait=False)

    def _load_fresh_config(self) -> dict | None:
        try:
            return db.instances.get_by_id(self.config["id"])
        except Exception as exc:
            logger.warning("Could not reload config of instance %s: %s", self.config.get("id"), exc)
            return None

    def refresh_config(self) -> None:
        """Pick up changed flags without restarting — a running search keeps going."""
        fresh = self._load_fresh_config()
        if fresh:
            self.config = fresh
        self._update_next_run()

    def _run_skill(self, skill_name: str, force: bool = False, reserved: bool = False):
        """Scheduler jobs and trigger_now land here.

        reserved: trigger_now already holds the skill lock for this run. It
        takes the lock before it answers "started"; a locked() check alone let
        two quick triggers both pass, and one of the two runs was then dropped
        here. The reserved lock is released on every path, early exits too.
        """
        lock = self.runtime.skill_lock(skill_name)
        if not reserved:
            self._gated_run(skill_name, force, lock)
            return
        try:
            self._gated_run(skill_name, force, None)
        finally:
            lock.release()

    def _gated_run(self, skill_name: str, force: bool, lock: Optional[threading.Lock]):
        """lock: the skill lock to take, or None when the caller holds it."""
        skill = self._get_skill(skill_name)
        if not skill:
            return

        # Refresh config from DB so search preferences changed in the UI
        # are picked up immediately without requiring an agent restart.
        fresh = self._load_fresh_config()
        if fresh:
            self.config = fresh

        # Skill-level enable flags — config already refreshed above.
        # Every early exit of a search job recomputes next_run_at; otherwise the
        # card counted down to 00m 00s after a skipped run and stayed there
        # until the next real run (A13).
        if not force and skill_name == "search_missing" and not self.config.get("search_missing_enabled"):
            self.log("debug", skill_name, "Skipping — missing search disabled")
            self._update_next_run()
            return
        if not force and skill_name == "search_upgrades" and not self.config.get("search_upgrades_enabled"):
            self.log("debug", skill_name, "Skipping — upgrades search disabled")
            self._update_next_run()
            return

        if skill_name not in QUIET_HOURS_EXEMPT and not force and self._in_quiet_hours():
            self.log("debug", skill_name, "Skipping — quiet hours active")
            self.state["status"] = "quiet"
            self._update_next_run()
            return

        # One run per skill at a time. A second request is dropped at once —
        # the API reports "busy" before it gets here (A11), so waiting would
        # only turn a double click into a second, repeated run.
        if lock is not None and not lock.acquire(blocking=False):
            self.log("warn", skill_name, "Already running — skipping duplicate trigger")
            return

        # Only the search skills drive the dashboard status; health_check and
        # verify_commands run alongside them and must not make the card flicker.
        drives_display = skill_name in ("search_missing", "search_upgrades")

        try:
            if drives_display:
                self.state["status"] = "running"
            skill.execute(self, force=force)
        except Exception as exc:
            self.log("error", skill_name, f"Unhandled exception: {exc}")
        finally:
            if drives_display:
                self.state["status"] = "error" if self._scheduler_failed else "scheduled"
                self._update_next_run()
            if lock is not None:
                lock.release()

    def trigger_now(self, skill_name: str, force: bool = True,
                    on_done: Callable[["BaseAgent"], None] | None = None) -> str:
        """Manual trigger — runs in its own thread. Returns TRIGGER_*.

        on_done(agent) is called in that thread once the run is over; the
        orchestrator uses it to forget a throwaway agent (and its decrypted
        config) as soon as it is finished."""
        if not self._get_skill(skill_name):
            self.log("warn", "system", f"Trigger ignored — skill '{skill_name}' not registered on this agent")
            return TRIGGER_UNKNOWN_SKILL
        # Reserve the lock here, in the caller: "started" must mean the run
        # will happen. The thread hands it back when the run is over.
        lock = self.runtime.skill_lock(skill_name)
        if not lock.acquire(blocking=False):
            self.log("info", "system", f"Trigger for '{skill_name}' rejected — it is already running")
            return TRIGGER_BUSY

        def run():
            try:
                self._run_skill(skill_name, force, reserved=True)
            finally:
                if on_done is not None:
                    on_done(self)

        try:
            self.log("info", "system", f"{'Force' if force else 'Manual'} trigger received for '{skill_name}'")
            threading.Thread(
                target=run,
                name=f"trigger-{self.config['id']}-{skill_name}",
                daemon=True,
            ).start()
        except BaseException:
            lock.release()
            raise
        return TRIGGER_STARTED

    def _skill_lock(self, skill_name: str) -> threading.Lock:
        return self.runtime.skill_lock(skill_name)

    def _get_skill(self, name: str) -> Optional[BaseSkill]:
        for s in self._skills:
            if s.name == name:
                return s
        return None

    def _in_quiet_hours(self) -> bool:
        qs = self.config.get("quiet_start")
        qe = self.config.get("quiet_end")
        if not qs or not qe:
            return False

        now = datetime.now()
        now_t = now.hour * 60 + now.minute

        try:
            sh, sm = map(int, qs.split(":"))
            eh, em = map(int, qe.split(":"))
        except (ValueError, AttributeError):
            return False

        start_t = sh * 60 + sm
        end_t = eh * 60 + em

        if start_t <= end_t:
            return start_t <= now_t < end_t
        else:
            # Overnight: e.g. 23:00 – 06:00
            return now_t >= start_t or now_t < end_t

    def _rate_window_seconds(self) -> float:
        try:
            return max(0, int(self.config.get("rate_window_minutes", 60))) * 60
        except (TypeError, ValueError):
            return 3600

    def _rate_cap(self) -> int:
        try:
            return int(self.config.get("rate_cap", 25))
        except (TypeError, ValueError):
            return 25

    def _prune_actions(self, now: float) -> None:
        """Caller holds runtime.rate_lock."""
        cutoff = now - self._rate_window_seconds()
        stamps = self.runtime.action_timestamps
        while stamps and stamps[0] < cutoff:
            stamps.popleft()

    def reserve_action(self) -> float | None:
        """Check the cap and claim a slot in one step (A10).

        Missing and upgrade runs hold different skill locks and run in
        parallel; checking and recording separately let both pass the last
        free slot. Returns a token for release_action(), or None when the cap
        is reached.
        """
        with self.runtime.rate_lock:
            # Taken under the lock so the deque stays in ascending order.
            now = time.monotonic()
            self._prune_actions(now)
            if len(self.runtime.action_timestamps) >= self._rate_cap():
                return None
            self.runtime.action_timestamps.append(now)
            return now

    def release_action(self, token: float) -> None:
        """Give a slot back when the command was not accepted by *arr."""
        with self.runtime.rate_lock:
            try:
                self.runtime.action_timestamps.remove(token)
            except ValueError:
                pass

    def get_rate_used(self) -> int:
        with self.runtime.rate_lock:
            self._prune_actions(time.monotonic())
            return len(self.runtime.action_timestamps)

    def _update_next_run(self):
        """next_run_at = the earliest run of the search jobs that are switched on (A13)."""
        scheduler = self._scheduler
        if scheduler is None:
            return
        times = []
        for prefix, flag in self.SEARCH_JOBS:
            if not self.config.get(flag):
                continue
            job = scheduler.get_job(f"{prefix}_{self.config['id']}")
            if job is not None and job.next_run_time is not None:
                times.append(job.next_run_time)
        self.state["next_run_at"] = min(times).isoformat() if times else None

    def log(self, level: str, skill: str, message: str):
        cfg = self.config
        instance_id = cfg.get("id")
        instance_name = cfg.get("name", "unknown")

        # Mask API key if accidentally in message
        api_key = cfg.get("api_key") or ""
        if api_key and api_key in message:
            message = message.replace(api_key, "****")

        try:
            db.activity.insert(instance_id, instance_name, level, message, skill)
        except Exception as exc:
            # A log line must never turn a successful action into a failure:
            # the POST to *arr has already happened when the debug line after
            # it is written (B2).
            logger.warning("Could not store log line for instance %s (%s): %s",
                           instance_id, exc, message)

        if self.broadcaster:
            try:
                self.broadcaster.broadcast({
                    "instance_id": instance_id,
                    "instance_name": instance_name,
                    "level": level,
                    "skill": skill,
                    "message": message,
                    "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })
            except Exception as exc:
                logger.warning("Could not broadcast log line: %s", exc)

    @staticmethod
    def _check_response(resp: requests.Response) -> None:
        # requests would follow a redirect and send X-Api-Key along to the new
        # host (only Authorization is stripped). *arr never redirects its API,
        # so a 3xx means a wrong URL — report it instead of following (C4).
        if 300 <= resp.status_code < 400:
            raise requests.exceptions.HTTPError(
                f"{resp.status_code} redirect not followed — check the instance URL",
                response=resp,
            )
        resp.raise_for_status()

    def http_get(self, path: str, params: Optional[dict] = None, timeout: float = 10) -> dict:
        """timeout: seconds; the checked search waits longer for /release,
        which runs the indexer search before it answers."""
        url = self.config["url"].rstrip("/") + path
        resp = requests.get(
            url,
            headers={"X-Api-Key": self.config["api_key"]},
            params=params or {},
            timeout=timeout,
            allow_redirects=False,
        )
        self._check_response(resp)
        return resp.json()

    def http_post(self, path: str, body: dict, timeout: float = 10) -> dict:
        url = self.config["url"].rstrip("/") + path
        resp = requests.post(
            url,
            headers={"X-Api-Key": self.config["api_key"], "Content-Type": "application/json"},
            json=body,
            timeout=timeout,
            allow_redirects=False,
        )
        self._check_response(resp)
        return resp.json()

    def http_delete(self, path: str, params: Optional[dict] = None, timeout: float = 10) -> None:
        """DELETE on *arr (the Imports page removes a download from the queue).

        Same rules as http_get/http_post: the key goes in the header only, a
        redirect is not followed (C4), 4xx/5xx raise. The answer body is
        never read: *arr answers DELETE with an empty 200, a 204 is fine too.
        """
        url = self.config["url"].rstrip("/") + path
        resp = requests.delete(
            url,
            headers={"X-Api-Key": self.config["api_key"]},
            params=params or {},
            timeout=timeout,
            allow_redirects=False,
        )
        self._check_response(resp)

    def http_get_raw(self, path: str) -> tuple[int, dict | None]:
        """GET that reports the status code instead of raising on 4xx/5xx.

        Verification needs to tell "*arr does not know this command" (404, a
        real answer) apart from "*arr is unreachable" (retry later), which
        raise_for_status collapses into one exception. Returns status 0 for
        network-level failures. A redirect is not followed (C4) and comes back
        as its 3xx status without payload.
        """
        url = self.config["url"].rstrip("/") + path
        try:
            resp = requests.get(
                url,
                headers={"X-Api-Key": self.config["api_key"]},
                timeout=10,
                allow_redirects=False,
            )
        except requests.exceptions.RequestException:
            return 0, None

        if resp.status_code != 200:
            return resp.status_code, None
        try:
            return 200, resp.json()
        except ValueError:
            return 200, None
