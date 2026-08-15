from typing import Optional
from backend.database import get_db
from backend.verification import ITEM_SUBMITTED, ITEM_EXPIRED


def start_run(instance_id: int, instance_name: str, skill: str) -> int:
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO search_history (instance_id, instance_name, skill, started_at)
            VALUES (?, ?, ?, datetime('now','localtime'))
            """,
            (instance_id, instance_name, skill),
        )
        row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        return row_id


def finish_run(
    run_id: int,
    wanted_count: int,
    triggered_count: int,
    status: str = "success",
    error_message: Optional[str] = None,
):
    """Close a run.

    A run that sent commands is not finished when the HTTP calls returned — it
    is finished when *arr reported what became of them. Such a run is therefore
    closed as 'pending'; verify_commands derives the final verdict. Only a run
    that sent nothing (or that threw) gets its verdict here.
    """
    with get_db() as conn:
        if status == "success":
            has_items = conn.execute(
                "SELECT 1 FROM search_history_items WHERE run_id=? LIMIT 1",
                (run_id,),
            ).fetchone()
            if has_items:
                status = "pending"

        conn.execute(
            """
            UPDATE search_history SET
                wanted_count=?, triggered_count=?,
                status=?, error_message=?,
                finished_at=datetime('now','localtime')
            WHERE id=?
            """,
            (wanted_count, triggered_count, status, error_message, run_id),
        )


def query(
    instance_id: Optional[int] = None,
    skill: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    conditions = []
    params: list = []

    if instance_id is not None:
        conditions.append("instance_id=?")
        params.append(instance_id)
    if skill:
        conditions.append("skill=?")
        params.append(skill)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params += [limit, offset]

    with get_db() as conn:
        rows = conn.execute(
            f"SELECT * FROM search_history {where} ORDER BY started_at DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [dict(r) for r in rows]


def get_last_for_instance(instance_id: int) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM search_history
            WHERE instance_id=?
            ORDER BY started_at DESC LIMIT 3
            """,
            (instance_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def insert_item(
    run_id: int,
    title: str,
    arr_id: Optional[int],
    item_type: str,
    cache_key: str = "",
    command_id: Optional[int] = None,
):
    """Record a triggered search.

    Without a command id from *arr there is nothing to verify later, so the
    row is filed as expired rather than claiming a pending verification — and
    it gets its verified_at right away, because it is never open.
    """
    if command_id is not None:
        status, verified = ITEM_SUBMITTED, None
    else:
        status, verified = ITEM_EXPIRED, "now"

    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO search_history_items
                (run_id, title, arr_id, item_type, cache_key, command_id,
                 command_status, created_at, verified_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now','localtime'),
                    CASE WHEN ? = 'now' THEN datetime('now','localtime') ELSE NULL END)
            """,
            (run_id, title, arr_id, item_type, cache_key, command_id, status, verified),
        )


def get_items_for_run(run_id: int) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM search_history_items WHERE run_id=? ORDER BY id",
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def query_with_items(
    instance_id: Optional[int] = None,
    skill: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    rows = query(instance_id=instance_id, skill=skill, limit=limit, offset=offset)
    for row in rows:
        row["search_items"] = get_items_for_run(row["id"])
    return rows


def query_items_flat(
    instance_id: Optional[int] = None,
    item_type: Optional[str] = None,
    limit: int = 250,
    offset: int = 0,
) -> list[dict]:
    """Flat list of triggered items joined with their run + instance data."""
    conditions = []
    params: list = []

    if instance_id is not None:
        conditions.append("h.instance_id=?")
        params.append(instance_id)
    if item_type:
        conditions.append("si.item_type=?")
        params.append(item_type)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params += [limit, offset]

    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT
                h.started_at,
                h.instance_name,
                h.skill,
                h.status,
                h.verified_count,
                COALESCE(inst.type, '') AS arr_type,
                si.title,
                si.item_type,
                si.arr_id,
                si.command_id,
                si.command_status
            FROM search_history_items si
            JOIN search_history h ON h.id = si.run_id
            LEFT JOIN instances inst ON inst.id = h.instance_id
            {where}
            ORDER BY h.started_at DESC, si.id
            LIMIT ? OFFSET ?
            """,
            params,
        ).fetchall()
        return [dict(r) for r in rows]


def get_pending_items(instance_id: int, limit: int = 50) -> list[dict]:
    """Items of this instance that still await a verdict, oldest first."""
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT si.id, si.run_id, si.command_id, si.cache_key
            FROM search_history_items si
            JOIN search_history h ON h.id = si.run_id
            WHERE h.instance_id = ?
              AND si.command_status = ?
              AND si.command_id IS NOT NULL
            ORDER BY si.id
            LIMIT ?
            """,
            (instance_id, ITEM_SUBMITTED, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def set_item_status(item_id: int, status: str) -> None:
    with get_db() as conn:
        conn.execute(
            """
            UPDATE search_history_items
            SET command_status=?, verified_at=datetime('now','localtime')
            WHERE id=?
            """,
            (status, item_id),
        )


def expire_stale_items(instance_id: int, hours: int = 24) -> int:
    """Give up on items *arr never resolved. Returns how many were expired.

    Ages by the item's own created_at, not by the run's start: a run with
    missing_per_run=600 and a two-second delay spans over twenty minutes, so
    its last items would otherwise get a shorter grace period than its first.
    Legacy rows have no created_at and fall back to the run's start.
    """
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE search_history_items
            SET command_status=?, verified_at=datetime('now','localtime')
            WHERE command_status=?
              AND id IN (
                  SELECT si.id
                  FROM search_history_items si
                  JOIN search_history h ON h.id = si.run_id
                  WHERE h.instance_id = ?
                    AND si.command_status = ?
                    AND COALESCE(si.created_at, h.started_at)
                        < datetime('now','localtime', ? || ' hours')
              )
            """,
            (ITEM_EXPIRED, ITEM_SUBMITTED, instance_id, ITEM_SUBMITTED, f"-{hours}"),
        )
        return cursor.rowcount


def get_unresolved_run_ids(instance_id: int) -> list[int]:
    """Runs of this instance still marked pending that have nothing open left.

    Deliberately not "runs touched in this pass": a run whose items were all
    filed as expired on insert (no command id came back) never passes through
    verification at all and would otherwise stay pending forever.
    """
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT h.id
            FROM search_history h
            WHERE h.instance_id = ?
              AND h.status = 'pending'
              AND NOT EXISTS (
                  SELECT 1 FROM search_history_items si
                  WHERE si.run_id = h.id AND si.command_status = ?
              )
            """,
            (instance_id, ITEM_SUBMITTED),
        ).fetchall()
        return [r["id"] for r in rows]


def get_item_statuses(run_id: int) -> list[str]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT command_status FROM search_history_items WHERE run_id=?",
            (run_id,),
        ).fetchall()
        return [r["command_status"] for r in rows]


def update_run_verification(run_id: int, status: str, verified_count: int) -> None:
    """Write the derived verdict — only for a run that is still pending.

    Guarding on status='pending' does two things: a run that threw keeps its
    'error' (the outcome of the commands it managed to send before dying does
    not change that), and a run already settled as success/partial/failed
    cannot be silently rewritten later.
    """
    with get_db() as conn:
        conn.execute(
            "UPDATE search_history SET status=?, verified_count=? WHERE id=? AND status='pending'",
            (status, verified_count, run_id),
        )


def get_latest_run_verification(instance_id: int) -> Optional[dict]:
    """The instance's youngest run with its sent and confirmed counts."""
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT id, status, triggered_count, verified_count
            FROM search_history
            WHERE instance_id=? AND skill != 'health_check'
            ORDER BY id DESC LIMIT 1
            """,
            (instance_id,),
        ).fetchone()
        return dict(row) if row else None


def clear():
    with get_db() as conn:
        conn.execute("DELETE FROM search_history")
