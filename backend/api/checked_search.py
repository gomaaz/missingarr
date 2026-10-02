from typing import Literal, Optional

from fastapi import APIRouter, Query, Response
from fastapi.responses import StreamingResponse

from backend import db
from backend.checked_search.settings import CheckedSearchSettings

router = APIRouter()

Mode = Literal["dry_run", "active"]
Outcome = Literal["grabbed", "would_grab", "no_clean_hit", "no_results", "error", "grab_failed", "grab_uncertain",
                  "changed_meanwhile"]


def _filters(instance_id, mode, outcome, q, only_differences, current_round) -> dict:
    return {
        "instance_id": instance_id,
        "mode": mode,
        "outcome": outcome,
        "search": (q or "").strip() or None,
        "only_differences": only_differences,
        "current_round": current_round,
    }


@router.get("/checked-search")
def list_checked_search(
    response: Response,
    instance_id: Optional[int] = None,
    mode: Optional[Mode] = None,
    outcome: Optional[Outcome] = None,
    q: Optional[str] = Query(None, max_length=200),
    only_differences: bool = False,
    current_round: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Pre-filter log, one row per title; X-Total-Count = matches without limit/offset.
    current_round: dry-run rows of the running round only (active rows always)."""
    filters = _filters(instance_id, mode, outcome, q, only_differences, current_round)
    response.headers["X-Total-Count"] = str(db.checked_search_log.count(**filters))
    return _mark_settings_changes(db.checked_search_log.query(**filters, limit=limit, offset=offset))


def _mark_settings_changes(rows: list[dict]) -> list[dict]:
    """settings_changed: the row was checked under rule settings its instance
    no longer has — a dry run checks the title again (badge on the page)."""
    current: dict = {}
    for row in rows:
        instance_id = row.get("instance_id")
        if instance_id not in current:
            inst = db.instances.get_by_id(instance_id) if instance_id is not None else None
            current[instance_id] = (
                CheckedSearchSettings.from_stored(inst.get("checked_search_settings"))
                .rules_fingerprint(inst.get("type") or "") if inst else None
            )
        stored = row.get("settings_fingerprint")
        row["settings_changed"] = bool(stored and current[instance_id] and stored != current[instance_id])
    return rows


@router.get("/checked-search.csv")
def checked_search_csv(
    instance_id: Optional[int] = None,
    mode: Optional[Mode] = None,
    outcome: Optional[Outcome] = None,
    q: Optional[str] = Query(None, max_length=200),
    only_differences: bool = False,
    current_round: bool = False,
):
    """The same filter as the list, one line per checked release."""
    filters = _filters(instance_id, mode, outcome, q, only_differences, current_round)
    return StreamingResponse(
        db.checked_search_log.iter_csv(**filters),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="checked-search.csv"'},
    )
