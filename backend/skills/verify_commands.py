import threading
import time

from backend.skills.base import BaseSkill
from backend import db
from backend.config import settings
from backend.verification import (
    map_command_status,
    aggregate_run_status,
    ITEM_SUBMITTED,
    ITEM_COMPLETED,
)


class VerifyCommandsSkill(BaseSkill):
    """Resolve what *arr actually did with the commands we sent.

    Runs on its own schedule rather than at the end of a search run: commands
    are still queued when a run finishes, and blocking the run to wait would
    stall the agent for as long as *arr takes.
    """

    name = "verify_commands"

    MAX_PER_RUN = 50
    STALE_HOURS = 24

    HOUSEKEEPING_INTERVAL_SECONDS = 3600
    # Per instance id, shared by every skill object: a reload creates new
    # skills, and the throttle must survive that.
    _last_housekeeping: dict[int, float] = {}
    # The agent and the orchestrator's app-wide pass may reach one instance at
    # the same moment; only one of them may pass the throttle.
    _housekeeping_guard = threading.Lock()

    def execute(self, agent, force: bool = False) -> None:
        instance_id = agent.config["id"]

        # Ask first, expire afterwards: an item gets its answer from *arr even
        # when the instance was off for longer than STALE_HOURS (B5).
        pending = db.history.get_pending_items(instance_id, self.MAX_PER_RUN)
        queried = resolved = 0
        answered: list[int] = []

        for item in pending:
            if agent.stop_requested():
                break
            http_status, payload = agent.http_get_raw(f"/api/v3/command/{item['command_id']}")
            queried += 1
            status = map_command_status(http_status, payload)
            if status == ITEM_SUBMITTED:
                # Only *arr's own word on this command counts as a check. A
                # 502/503/504 from a reverse proxy in front of a dead Sonarr,
                # a 401/403 for a wrong key or a 3xx are no answer, and must
                # not use up the grace period (B5). A 404 never gets here:
                # map_command_status settles it as expired.
                if http_status == 200 and isinstance(payload, dict):
                    answered.append(item["id"])
                continue

            released = db.history.resolve_item(item["id"], status, instance_id, item["cache_key"])
            resolved += 1
            if released:
                agent.log(
                    "warn",
                    self.name,
                    f"Command {item['command_id']} failed in *arr — "
                    f"released '{item['cache_key']}' for another attempt",
                )

        db.history.mark_checked(answered)

        expired = db.history.expire_stale_items(instance_id, self.STALE_HOURS)
        if expired:
            agent.log(
                "warn",
                self.name,
                f"Gave up on {expired} command(s) still unresolved after {self.STALE_HOURS}h",
            )

        # Settle every run that has nothing open left — not just the ones touched
        # above. A run whose items were all filed as expired on insert (no command
        # id came back) never passes through the loop and would stay pending.
        for run_id in db.history.get_unresolved_run_ids(instance_id):
            statuses = db.history.get_item_statuses(run_id)
            db.history.update_run_verification(
                run_id,
                aggregate_run_status(statuses),
                statuses.count(ITEM_COMPLETED),
            )

        # Read the card's numbers back rather than counting this pass: one pass
        # may resolve items from several runs, or none. Both come from the same
        # finished run, never from two different ones (B-L4).
        latest = db.history.get_latest_run_verification(instance_id)
        if latest:
            agent.state["last_verified"] = latest["verified_count"]
            agent.state["last_triggered"] = latest["triggered_count"]

        self.housekeeping(agent)

        if queried:
            # Both numbers, always: 50 queried with 0 resolved is a backlog, and
            # logging only the resolved count would hide it. Counted as asked,
            # not as fetched: an abort can end the loop early.
            agent.log(
                "info",
                self.name,
                f"Queried {queried} command(s), {resolved} resolved",
            )

    def housekeeping(self, agent) -> None:
        """Hourly per instance: drop finished runs past HISTORY_RETENTION_DAYS
        (B7) and cache rows outside the retry window (B6).

        Database only, never *arr. The orchestrator also calls it for every
        instance, disabled ones included: those have no agent running this
        skill. Whichever caller comes first in the hour does the work."""
        instance_id = agent.config["id"]
        with self._housekeeping_guard:
            now = time.monotonic()
            last = self._last_housekeeping.get(instance_id)
            if last is not None and now - last < self.HOUSEKEEPING_INTERVAL_SECONDS:
                return
            self._last_housekeeping[instance_id] = now

        try:
            retry_hours = int(agent.config.get("retry_hours") or 0)
            cache = db.searched.purge_expired(instance_id, retry_hours)
            runs = db.history.purge_old_runs(instance_id, settings.history_retention_days)
        except Exception as exc:
            agent.log("warn", self.name, f"Housekeeping failed: {exc}")
            return

        if cache or runs:
            agent.log(
                "info",
                self.name,
                f"Housekeeping: removed {runs} run(s) older than "
                f"{settings.history_retention_days} days and {cache} expired cache entr"
                f"{'y' if cache == 1 else 'ies'}",
            )
