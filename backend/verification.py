"""Pure decision logic for command verification.

No HTTP and no database access, so every rule here is testable without a
running *arr instance. The skill in backend/skills/verify_commands.py is the
thin adapter that feeds this module.
"""

ITEM_SUBMITTED = "submitted"
ITEM_COMPLETED = "completed"
ITEM_FAILED = "failed"
ITEM_EXPIRED = "expired"
ITEM_LEGACY = "legacy"

RUN_SUCCESS = "success"
RUN_PENDING = "pending"
RUN_PARTIAL = "partial"
RUN_FAILED = "failed"
RUN_UNVERIFIED = "unverified"

# *arr command states that mean the command will not run (or did not finish).
_ARR_FAILURE_STATES = {"failed", "aborted", "cancelled"}


def map_command_status(http_status: int, payload: dict | None) -> str:
    """Translate an *arr command lookup into our item status.

    `status` is authoritative, not `result`: *arr keeps `status` indefinitely
    but resets `result` to "unknown" once the command ages out of the live
    queue. Anything inconclusive stays ITEM_SUBMITTED so the next pass retries
    instead of recording a verdict we do not have.

    http_status 0 is used by the caller for network-level failures.
    """
    if http_status == 404:
        return ITEM_EXPIRED
    if http_status != 200 or not isinstance(payload, dict):
        return ITEM_SUBMITTED

    arr_state = str(payload.get("status") or "").lower()
    if arr_state == "completed":
        return ITEM_COMPLETED
    if arr_state in _ARR_FAILURE_STATES:
        return ITEM_FAILED
    return ITEM_SUBMITTED


def aggregate_run_status(item_statuses: list[str]) -> str:
    """Derive a run's status from the statuses of its items.

    Legacy rows carry no command id and can never be verified, so they do not
    influence the verdict.
    """
    relevant = [s for s in item_statuses if s != ITEM_LEGACY]

    if not relevant:
        return RUN_SUCCESS
    if ITEM_SUBMITTED in relevant:
        return RUN_PENDING
    if all(s == ITEM_COMPLETED for s in relevant):
        return RUN_SUCCESS
    if ITEM_COMPLETED in relevant:
        return RUN_PARTIAL
    if ITEM_FAILED in relevant:
        return RUN_FAILED
    return RUN_UNVERIFIED
