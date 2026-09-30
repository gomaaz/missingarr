import logging
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests
from apscheduler.schedulers.background import BackgroundScheduler

from backend import db
from backend.skills.base import BaseSkill

logger = logging.getLogger("missingarr.agent")

MIN_INTERVAL_MINUTES = 1
MAX_INTERVAL_MINUTES = 10080
UPGRADE_INTERVAL_FACTOR = 4


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
    """

    rate_lock: threading.Lock = field(default_factory=threading.Lock)
    action_timestamps: deque = field(default_factory=deque)
    skill_locks: dict = field(default_factory=dict)
    skill_locks_guard: threading.Lock = field(default_factory=threading.Lock)

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

    def stop(self):
        self._stop_event.set()
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self.state["status"] = "off"
        self.state["next_run_at"] = None
        self.log("info", "system", f"Agent stopped — '{self.config['name']}'")

    def reload(self, new_config: dict):
        self.stop()
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

    def _run_skill(self, skill_name: str, force: bool = False):
        skill = self._get_skill(skill_name)
        if not skill:
            return

        # Refresh config from DB so search preferences changed in the UI
        # are picked up immediately without requiring an agent restart.
        fresh = self._load_fresh_config()
        if fresh:
            self.config = fresh

        # Check skill-level enable flags — config already refreshed above
        if not force and skill_name == "search_missing" and not self.config.get("search_missing_enabled"):
            self.log("debug", skill_name, "Skipping — missing search disabled")
            return
        if not force and skill_name == "search_upgrades" and not self.config.get("search_upgrades_enabled"):
            self.log("debug", skill_name, "Skipping — upgrades search disabled")
            return

        # Check quiet hours — skipped for health_check and force runs
        if skill_name != "health_check" and not force and self._in_quiet_hours():
            self.log("debug", skill_name, "Skipping — quiet hours active")
            self.state["status"] = "quiet"
            return

        # Guard against concurrent runs of the same skill. Force triggers wait
        # up to 90 s for an active run of that skill to finish; scheduled
        # triggers are dropped immediately.
        lock = self._skill_lock(skill_name)
        deadline = time.monotonic() + (90 if force else 0)
        acquired = lock.acquire(blocking=False)
        while not acquired and time.monotonic() < deadline:
            time.sleep(1)
            acquired = lock.acquire(blocking=False)
        if not acquired:
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
            lock.release()

    def trigger_now(self, skill_name: str, force: bool = True):
        """Manual trigger — runs in a separate thread to not block the caller."""
        skill = self._get_skill(skill_name)
        if not skill:
            self.log("warn", "system", f"Force trigger ignored — skill '{skill_name}' not registered on this agent")
            return

        self.log("info", "system", f"Force trigger received for '{skill_name}'")
        t = threading.Thread(
            target=self._run_skill,
            args=[skill_name, force],
            daemon=True,
        )
        t.start()

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

    # Deprecated: only until the skills use reserve_action() (P2). Task Z removes them.
    def check_rate_cap(self) -> bool:
        with self.runtime.rate_lock:
            self._prune_actions(time.monotonic())
            return len(self.runtime.action_timestamps) < self._rate_cap()

    def record_action(self) -> None:
        with self.runtime.rate_lock:
            self.runtime.action_timestamps.append(time.monotonic())

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

    def http_get(self, path: str, params: Optional[dict] = None) -> dict:
        url = self.config["url"].rstrip("/") + path
        api_key = self.config["api_key"]
        resp = requests.get(
            url,
            headers={"X-Api-Key": api_key},
            params=params or {},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def http_post(self, path: str, body: dict) -> dict:
        url = self.config["url"].rstrip("/") + path
        api_key = self.config["api_key"]
        resp = requests.post(
            url,
            headers={"X-Api-Key": api_key, "Content-Type": "application/json"},
            json=body,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def http_get_raw(self, path: str) -> tuple[int, dict | None]:
        """GET that reports the status code instead of raising on 4xx/5xx.

        Verification needs to tell "*arr does not know this command" (404, a
        real answer) apart from "*arr is unreachable" (retry later), which
        raise_for_status collapses into one exception. Returns status 0 for
        network-level failures.
        """
        url = self.config["url"].rstrip("/") + path
        try:
            resp = requests.get(
                url,
                headers={"X-Api-Key": self.config["api_key"]},
                timeout=10,
            )
        except requests.exceptions.RequestException:
            return 0, None

        if resp.status_code != 200:
            return resp.status_code, None
        try:
            return 200, resp.json()
        except ValueError:
            return 200, None
