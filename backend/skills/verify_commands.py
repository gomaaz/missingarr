from backend.skills.base import BaseSkill
from backend import db
from backend.verification import (
    map_command_status,
    aggregate_run_status,
    ITEM_SUBMITTED,
    ITEM_COMPLETED,
    ITEM_FAILED,
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

    def execute(self, agent, force: bool = False) -> None:
        instance_id = agent.config["id"]

        expired = db.history.expire_stale_items(instance_id, self.STALE_HOURS)
        if expired:
            agent.log(
                "warn",
                self.name,
                f"Gave up on {expired} command(s) still unresolved after {self.STALE_HOURS}h",
            )

        pending = db.history.get_pending_items(instance_id, self.MAX_PER_RUN)
        resolved = 0

        for item in pending:
            http_status, payload = agent.http_get_raw(f"/api/v3/command/{item['command_id']}")
            status = map_command_status(http_status, payload)
            if status == ITEM_SUBMITTED:
                continue  # still running, or *arr unreachable — try next pass

            db.history.set_item_status(item["id"], status)
            resolved += 1

            if status == ITEM_FAILED and item["cache_key"]:
                removed = db.searched.delete(instance_id, item["cache_key"])
                if removed:
                    agent.log(
                        "warn",
                        self.name,
                        f"Command {item['command_id']} failed in *arr — "
                        f"released '{item['cache_key']}' for another attempt",
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

        # The card shows the youngest run, so read it back rather than counting
        # this pass: one pass may resolve items from several runs, or none.
        latest = db.history.get_latest_run_verification(instance_id)
        if latest:
            agent.state["last_verified"] = latest["verified_count"]

        if pending:
            # Both numbers, always: 50 queried with 0 resolved is a backlog, and
            # logging only the resolved count would hide it.
            agent.log(
                "info",
                self.name,
                f"Queried {len(pending)} command(s), {resolved} resolved",
            )
