"""Error pause of the checked search.

A title whose check ends in an error (outcome error or grab_failed: a read
that failed, /parse, an indexer failure, the read before the grab, a grab
*arr refused) is not remembered: no cache entry, no place in the dry-run
round. Picked again on every run, it would take a place of "per run" each
time, and with a fixed order (oldest first, per run 1) the next title would
never come up while the error lasts. So such a title sits out a while: the
skills leave it out before counting "per run" (dry run and active, force
runs too); the command path ignores the pause.

The pause is no search: no cache entry, no grab block, no history item. It
lasts ERROR_PAUSE_HOURS[failures - 1] hours (6, 12, then 24 for every
further error). Any other outcome of the title ends it. An error more than
ERROR_PAUSE_FORGET_HOURS after the last pause ended counts as the first
again; housekeeping drops such rows. The rows live in the database, so a
restart keeps them; "Clear cache" drops the instance's rows too.
"""

import sqlite3
from typing import Optional

from backend.database import get_db

ERROR_PAUSE_HOURS = (6, 12, 24)
ERROR_PAUSE_FORGET_HOURS = 24
PAUSING_OUTCOMES = ("error", "grab_failed")


def note_with(conn: sqlite3.Connection, instance_id: int, cache_key: str, outcome: str) -> None:
    """Set, extend or end the pause of one title after a check, on an open
    connection (in the same transaction as the title's log row)."""
    if not cache_key:
        return
    if outcome not in PAUSING_OUTCOMES:
        conn.execute("DELETE FROM checked_search_pauses WHERE instance_id=? AND cache_key=?",
                     (instance_id, cache_key))
        return
    row = conn.execute(
        "SELECT failures, paused_until > datetime('now', 'localtime', ? || ' hours') AS recent "
        "FROM checked_search_pauses WHERE instance_id=? AND cache_key=?",
        (f"-{ERROR_PAUSE_FORGET_HOURS}", instance_id, cache_key),
    ).fetchone()
    failures = int(row["failures"]) + 1 if row is not None and row["recent"] else 1
    hours = ERROR_PAUSE_HOURS[min(failures, len(ERROR_PAUSE_HOURS)) - 1]
    conn.execute(
        "INSERT INTO checked_search_pauses (instance_id, cache_key, failures, paused_until) "
        "VALUES (?, ?, ?, datetime('now', 'localtime', ? || ' hours')) "
        "ON CONFLICT(instance_id, cache_key) DO UPDATE SET "
        "failures=excluded.failures, paused_until=excluded.paused_until",
        (instance_id, cache_key, failures, f"+{hours}"),
    )


def paused_keys(instance_id: int) -> frozenset:
    """The cache keys of the instance's titles that sit out right now."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT cache_key FROM checked_search_pauses "
            "WHERE instance_id=? AND paused_until > datetime('now', 'localtime')",
            (instance_id,),
        )
        return frozenset(row["cache_key"] for row in rows)


def purge_expired(instance_id: int) -> int:
    """Housekeeping: drop rows that no longer count — the pause ended more
    than ERROR_PAUSE_FORGET_HOURS ago, so a new error starts at the first
    step anyway."""
    with get_db() as conn:
        return conn.execute(
            "DELETE FROM checked_search_pauses WHERE instance_id=? "
            "AND paused_until <= datetime('now', 'localtime', ? || ' hours')",
            (instance_id, f"-{ERROR_PAUSE_FORGET_HOURS}"),
        ).rowcount


def clear_with(conn: sqlite3.Connection, instance_id: Optional[int] = None) -> int:
    if instance_id is None:
        return conn.execute("DELETE FROM checked_search_pauses").rowcount
    return conn.execute("DELETE FROM checked_search_pauses WHERE instance_id=?", (instance_id,)).rowcount
