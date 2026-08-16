import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests
from apscheduler.schedulers.background import BackgroundScheduler

from backend import db
from backend.skills.base import BaseSkill


class BaseAgent(ABC):
    HEALTH_CHECK_INTERVAL_MINUTES = 5

    def __init__(self, config: dict, broadcaster=None):
        self.config = config
        self.broadcaster = broadcaster
        self._scheduler: Optional[BackgroundScheduler] = None
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # One lock per skill. state["status"] is a display value shared by the
        # whole agent and must not double as a mutex — doing so let a running
        # search block the health check every single hour.
        self._skill_locks: dict[str, threading.Lock] = {}
        self._skill_locks_guard = threading.Lock()

        # Rate-limit tracking: timestamps of recent actions
        self._action_timestamps: deque = deque()

        # Live state exposed to dashboard
        self.state = {
            "status": "scheduled",   # scheduled | running | off | quiet
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
        self._skills = self.build_skills()
        self._thread = threading.Thread(
            target=self._run,
            name=f"agent-{self.config['id']}-{self.config['name']}",
            daemon=True,
        )
        self._thread.start()
        self.log("info", "system", f"Agent started — {self.config['type'].upper()} '{self.config['name']}'")

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

    def _run(self):
        self._scheduler = BackgroundScheduler(
            timezone="UTC",
            job_defaults={"misfire_grace_time": 60, "coalesce": True},
        )

        cfg = self.config
        interval = cfg.get("interval_minutes", 15)

        # Register missing search job
        if cfg.get("search_missing_enabled") and self._get_skill("search_missing"):
            self._scheduler.add_job(
                self._run_skill,
                "interval",
                minutes=interval,
                args=["search_missing"],
                id=f"missing_{cfg['id']}",
                next_run_time=None,  # don't run immediately
            )
            # Schedule first run after interval
            first_run = datetime.now(timezone.utc) + timedelta(minutes=interval)
            self.state["next_run_at"] = first_run.isoformat()
            self._scheduler.reschedule_job(
                f"missing_{cfg['id']}",
                trigger="interval",
                minutes=interval,
                start_date=first_run,
            )

        # Register upgrades job (separate interval)
        if cfg.get("search_upgrades_enabled") and self._get_skill("search_upgrades"):
            upgrade_interval = cfg.get("interval_minutes", 15) * 4  # upgrades less frequent
            self._scheduler.add_job(
                self._run_skill,
                "interval",
                minutes=upgrade_interval,
                args=["search_upgrades"],
                id=f"upgrades_{cfg['id']}",
                next_run_time=datetime.now(timezone.utc) + timedelta(minutes=upgrade_interval),
            )

        # Health check every 5 minutes
        self._scheduler.add_job(
            self._run_skill,
            "interval",
            minutes=self.HEALTH_CHECK_INTERVAL_MINUTES,
            args=["health_check"],
            id=f"health_{cfg['id']}",
            next_run_time=datetime.now(timezone.utc) + timedelta(seconds=10),
        )

        # Verification every 2 minutes. Runs regardless of the search-enabled
        # flags: entries submitted before a skill was switched off would stay
        # unresolved forever otherwise.
        self._scheduler.add_job(
            self._run_skill,
            "interval",
            minutes=2,
            args=["verify_commands"],
            id=f"verify_{cfg['id']}",
            next_run_time=datetime.now(timezone.utc) + timedelta(seconds=30),
        )

        self._scheduler.start()
        self.state["status"] = "scheduled"

        # Block until stop_event is set
        self._stop_event.wait()
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    def _run_skill(self, skill_name: str, force: bool = False):
        skill = self._get_skill(skill_name)
        if not skill:
            return

        # Refresh config from DB so search preferences changed in the UI
        # are picked up immediately without requiring an agent restart.
        fresh = db.instances.get_by_id(self.config["id"])
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
                self.state["status"] = "scheduled"
                # Update next_run_at from scheduler
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
        with self._skill_locks_guard:
            return self._skill_locks.setdefault(skill_name, threading.Lock())

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

    def check_rate_cap(self) -> bool:
        """Returns True if we're allowed to perform another action."""
        window_minutes = self.config.get("rate_window_minutes", 60)
        cap = self.config.get("rate_cap", 25)
        cutoff = time.monotonic() - window_minutes * 60

        with self._lock:
            # Remove old timestamps outside the window
            while self._action_timestamps and self._action_timestamps[0] < cutoff:
                self._action_timestamps.popleft()
            return len(self._action_timestamps) < cap

    def record_action(self):
        """Record that an action was taken (for rate-cap tracking)."""
        with self._lock:
            self._action_timestamps.append(time.monotonic())

    def get_rate_used(self) -> int:
        window_minutes = self.config.get("rate_window_minutes", 60)
        cutoff = time.monotonic() - window_minutes * 60
        with self._lock:
            while self._action_timestamps and self._action_timestamps[0] < cutoff:
                self._action_timestamps.popleft()
            return len(self._action_timestamps)

    def _update_next_run(self):
        if not self._scheduler:
            return
        job_id = f"missing_{self.config['id']}"
        job = self._scheduler.get_job(job_id)
        if job and job.next_run_time:
            self.state["next_run_at"] = job.next_run_time.isoformat()

    def log(self, level: str, skill: str, message: str):
        cfg = self.config
        instance_id = cfg.get("id")
        instance_name = cfg.get("name", "unknown")

        # Mask API key if accidentally in message
        api_key = cfg.get("api_key", "")
        if api_key and api_key in message:
            message = message.replace(api_key, "****")

        db.activity.insert(instance_id, instance_name, level, message, skill)

        if self.broadcaster:
            self.broadcaster.broadcast({
                "instance_id": instance_id,
                "instance_name": instance_name,
                "level": level,
                "skill": skill,
                "message": message,
            })

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
