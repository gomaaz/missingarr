from typing import Optional
from backend.database import get_db
from backend.verification import ITEM_SUBMITTED, ITEM_EXPIRED, ITEM_FAILED

_INSERT_ITEM = """
    INSERT INTO search_history_items
        (run_id, title, arr_id, item_type, cache_key, command_id,
         command_status, created_at, verified_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now','localtime'),
            CASE WHEN ? = 'now' THEN datetime('now','localtime') ELSE NULL END)
"""

# A finished run can still hold commands awaiting their verdict: a run that
# stopped on a store failure is closed as 'error' with its submitted items.
# Deleting it would cut the only link from those command ids to their cache
# keys (B-L2). Takes ITEM_SUBMITTED as its one parameter.
_AWAITS_VERDICT = """
    EXISTS (SELECT 1 FROM search_history_items si
            WHERE si.run_id = search_history.id AND si.command_status = ?)
"""


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
            f"SELECT * FROM search_history {where} "
            f"ORDER BY started_at DESC, id DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [dict(r) for r in rows]


def get_last_for_instance(instance_id: int) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM search_history
            WHERE instance_id=?
            ORDER BY started_at DESC, id DESC LIMIT 3
            """,
            (instance_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def record_submission(
    run_id: int,
    instance_id: int,
    title: str,
    arr_id: Optional[int],
    item_type: str,
    cache_key: str,
    command_id: Optional[int],
) -> int:
    """Store a command *arr accepted — history item and retry-cache entry in
    one transaction, so a crash or lock between the two cannot leave a sent
    command without its cache entry or the other way round (B2).

    Without a command id nothing is cached: the entry could never be
    verified, and with retry_hours=0 it would block the title for good (A12).
    """
    if command_id is not None:
        status, verified = ITEM_SUBMITTED, None
    else:
        status, verified = ITEM_EXPIRED, "now"
    with get_db() as conn:
        cursor = conn.execute(
            _INSERT_ITEM,
            (run_id, title, arr_id, item_type, cache_key, command_id, status, verified),
        )
        if command_id is not None and cache_key:
            conn.execute(
                """
                INSERT INTO searched_items (instance_id, cache_key, title, item_type)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(instance_id, cache_key) DO UPDATE SET searched_at=datetime('now','localtime')
                """,
                (instance_id, cache_key, title, item_type),
            )
        return cursor.lastrowid


def record_failed_submission(run_id: int, title: str, arr_id: Optional[int], item_type: str) -> int:
    """A command *arr did not accept. Filed as failed with no command id and
    no cache entry: the next run picks the title up again, and the run's
    verdict can no longer come out as a clean success (A6)."""
    with get_db() as conn:
        cursor = conn.execute(
            _INSERT_ITEM,
            (run_id, title, arr_id, item_type, "", None, ITEM_FAILED, "now"),
        )
        return cursor.lastrowid


def close_interrupted_runs() -> int:
    """Close runs a restart cut off (B-L1). Only finish_run() moves a run out of
    'running'; a killed process never gets there. Runs that sent something go
    to 'pending' so verify_commands settles them; the rest become 'error'."""
    with get_db() as conn:
        rows = conn.execute("SELECT id FROM search_history WHERE status='running'").fetchall()
        for row in rows:
            sent = conn.execute(
                "SELECT COUNT(*) FROM search_history_items WHERE run_id=? AND command_status != ?",
                (row["id"], ITEM_FAILED),
            ).fetchone()[0]
            has_items = conn.execute(
                "SELECT 1 FROM search_history_items WHERE run_id=? LIMIT 1", (row["id"],)
            ).fetchone()
            conn.execute(
                """
                UPDATE search_history
                SET status=?, triggered_count=?, error_message=?,
                    finished_at=datetime('now','localtime')
                WHERE id=?
                """,
                ("pending" if has_items else "error", sent, "Interrupted by restart", row["id"]),
            )
        return len(rows)


def purge_old_runs(instance_id: int, days: int) -> int:
    """Delete finished runs older than `days` (B7). Items go with them (ON
    DELETE CASCADE). Open runs and runs with commands still awaiting a
    verdict stay: their items still link command ids to cache keys."""
    if days <= 0:
        return 0
    with get_db() as conn:
        cursor = conn.execute(
            f"""
            DELETE FROM search_history
            WHERE instance_id=? AND status NOT IN ('running','pending')
              AND started_at < datetime('now','localtime', ? || ' days')
              AND NOT {_AWAITS_VERDICT}
            """,
            (instance_id, f"-{days}", ITEM_SUBMITTED),
        )
        return cursor.rowcount


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


def _items_filter(instance_id, item_type, skill, search) -> tuple[str, list]:
    conditions: list[str] = []
    params: list = []
    if instance_id is not None:
        conditions.append("h.instance_id=?")
        params.append(instance_id)
    if item_type:
        conditions.append("si.item_type=?")
        params.append(item_type)
    if skill:
        conditions.append("h.skill=?")
        params.append(skill)
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        conditions.append("(si.title LIKE ? ESCAPE '\\' OR h.instance_name LIKE ? ESCAPE '\\')")
        params += [pattern, pattern]
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    return where, params


def query_items_flat(
    instance_id: Optional[int] = None,
    item_type: Optional[str] = None,
    skill: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 250,
    offset: int = 0,
) -> list[dict]:
    """Flat list of triggered items joined with their run + instance data."""
    where, params = _items_filter(instance_id, item_type, skill, search)
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT
                si.id AS item_id,
                si.run_id,
                h.instance_id,
                h.started_at,
                h.instance_name,
                h.skill,
                h.status,
                h.verified_count,
                h.error_message,
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
            ORDER BY h.started_at DESC, h.id DESC, si.id
            LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        ).fetchall()
        return [dict(r) for r in rows]


def count_items_flat(
    instance_id: Optional[int] = None,
    item_type: Optional[str] = None,
    skill: Optional[str] = None,
    search: Optional[str] = None,
) -> int:
    where, params = _items_filter(instance_id, item_type, skill, search)
    with get_db() as conn:
        return conn.execute(
            f"""
            SELECT COUNT(*)
            FROM search_history_items si
            JOIN search_history h ON h.id = si.run_id
            {where}
            """,
            params,
        ).fetchone()[0]


def get_pending_items(instance_id: int, limit: int = 50) -> list[dict]:
    """Items still awaiting a verdict. Never-checked items first, then the one
    checked longest ago, so fifty commands *arr never settles cannot starve
    every newer one (B4)."""
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT si.id, si.run_id, si.command_id, si.cache_key, si.last_checked_at
            FROM search_history_items si
            JOIN search_history h ON h.id = si.run_id
            WHERE h.instance_id = ?
              AND si.command_status = ?
              AND si.command_id IS NOT NULL
            ORDER BY (si.last_checked_at IS NOT NULL), si.last_checked_at, si.id
            LIMIT ?
            """,
            (instance_id, ITEM_SUBMITTED, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def mark_checked(item_ids: list[int]) -> None:
    """Note that *arr answered for these items without settling them yet.
    Drives the rotation above and the grace period in expire_stale_items."""
    if not item_ids:
        return
    with get_db() as conn:
        conn.executemany(
            "UPDATE search_history_items SET last_checked_at=datetime('now','localtime') WHERE id=?",
            [(item_id,) for item_id in item_ids],
        )


def resolve_item(item_id: int, status: str, instance_id: int, cache_key: str) -> bool:
    """Write a verdict and, for a failed command, release its cache key — in
    one transaction (B3). Returns True when a cache entry was released."""
    with get_db() as conn:
        conn.execute(
            """
            UPDATE search_history_items
            SET command_status=?, verified_at=datetime('now','localtime'),
                last_checked_at=datetime('now','localtime')
            WHERE id=?
            """,
            (status, item_id),
        )
        if status == ITEM_FAILED and cache_key:
            cursor = conn.execute(
                "DELETE FROM searched_items WHERE instance_id=? AND cache_key=?",
                (instance_id, cache_key),
            )
            return cursor.rowcount > 0
    return False


def expire_stale_items(instance_id: int, hours: int = 24) -> int:
    """Give up on items *arr never resolved. Returns how many were expired.

    Only items *arr has answered at least once after their `hours` were up:
    an instance switched off for a day must first ask, then give up (B5).

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
                    AND si.last_checked_at IS NOT NULL
                    AND si.last_checked_at
                        >= datetime(COALESCE(si.created_at, h.started_at), ? || ' hours')
              )
            """,
            (ITEM_EXPIRED, ITEM_SUBMITTED, instance_id, ITEM_SUBMITTED, f"+{hours}"),
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
    """The instance's most recently finished run. Both numbers on the card come
    from this one row, never from two different runs (B-L4)."""
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT id, status, triggered_count, verified_count
            FROM search_history
            WHERE instance_id=? AND finished_at IS NOT NULL AND status != 'running'
            ORDER BY finished_at DESC, id DESC LIMIT 1
            """,
            (instance_id,),
        ).fetchone()
        return dict(row) if row else None


def clear() -> dict:
    """Delete finished runs only. Open runs, and finished runs with commands
    still awaiting a verdict, keep their submitted items: an item is the only
    link from a command id to its cache key, and without it a command that
    later fails would keep its title blocked (B-L2)."""
    with get_db() as conn:
        kept = conn.execute(
            f"SELECT COUNT(*) FROM search_history "
            f"WHERE status IN ('running','pending') OR {_AWAITS_VERDICT}",
            (ITEM_SUBMITTED,),
        ).fetchone()[0]
        cursor = conn.execute(
            f"DELETE FROM search_history "
            f"WHERE status NOT IN ('running','pending') AND NOT {_AWAITS_VERDICT}",
            (ITEM_SUBMITTED,),
        )
        return {"deleted": cursor.rowcount, "kept_open": kept}
