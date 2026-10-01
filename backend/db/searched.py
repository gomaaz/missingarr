from datetime import datetime, timezone
from typing import Iterable, Optional

from backend.database import get_db

LOOKUP_CHUNK = 500  # well below SQLite's variable limit


def local_to_utc(value: str) -> datetime:
    """searched_at is written with datetime('now','localtime'). Read it back in
    the same local zone (the process TZ) and return aware UTC, so it compares
    with *arr's airDateUtc."""
    naive = datetime.strptime(value.replace("T", " ")[:19], "%Y-%m-%d %H:%M:%S")
    return naive.astimezone(timezone.utc)


def add(instance_id: int, cache_key: str, title: str, item_type: str,
        profile_fingerprint: Optional[str] = None) -> None:
    """profile_fingerprint: the title's quality profile when it was searched
    (0.9.0). A new search of a cached title takes the new one over, never
    NULL over a known one; it is no grab of the checked search (grabbed_at
    cleared) and no history item wrote it (history_item_id cleared)."""
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO searched_items (instance_id, cache_key, title, item_type, profile_fingerprint)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(instance_id, cache_key) DO UPDATE SET
                searched_at=datetime('now','localtime'),
                profile_fingerprint=COALESCE(excluded.profile_fingerprint, searched_items.profile_fingerprint),
                grabbed_at=NULL,
                history_item_id=NULL
            """,
            (instance_id, cache_key, title, item_type, profile_fingerprint),
        )


def query(
    instance_id: Optional[int] = None,
    item_type: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    conditions = []
    params: list = []

    if instance_id is not None:
        conditions.append("s.instance_id=?")
        params.append(instance_id)
    if item_type:
        conditions.append("s.item_type=?")
        params.append(item_type)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params += [limit, offset]

    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT s.*, i.name AS instance_name
            FROM searched_items s
            LEFT JOIN instances i ON i.id = s.instance_id
            {where}
            ORDER BY s.searched_at DESC, s.id DESC LIMIT ? OFFSET ?
            """,
            params,
        ).fetchall()
        return [dict(r) for r in rows]


def count(instance_id: Optional[int] = None) -> dict:
    with get_db() as conn:
        if instance_id is not None:
            row = conn.execute(
                "SELECT COUNT(*) as total FROM searched_items WHERE instance_id=?",
                (instance_id,),
            ).fetchone()
            return {"total": row["total"]}
        rows = conn.execute(
            """
            SELECT instance_id, i.name AS instance_name, COUNT(*) AS total
            FROM searched_items s
            LEFT JOIN instances i ON i.id = s.instance_id
            GROUP BY instance_id
            """,
        ).fetchall()
        return [dict(r) for r in rows]


def delete(instance_id: int, cache_key: str) -> int:
    """Release a cached key so the item becomes searchable again.

    Called when *arr reported the command as failed. With retry_hours=0 the
    cache has no time window, so without this the title would stay blocked
    forever despite never having been searched.
    """
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM searched_items WHERE instance_id=? AND cache_key=?",
            (instance_id, cache_key),
        )
        return cursor.rowcount


def clear(instance_id: Optional[int] = None) -> int:
    with get_db() as conn:
        if instance_id is not None:
            cursor = conn.execute(
                "DELETE FROM searched_items WHERE instance_id=?",
                (instance_id,),
            )
        else:
            cursor = conn.execute("DELETE FROM searched_items")
        return cursor.rowcount


def lookup_many(
    instance_id: int,
    keys: Iterable[str],
    retry_hours: int = 0,
    fingerprints: Optional[dict] = None,
    grab_release_days: int = 0,
) -> dict[str, datetime]:
    """cache_key -> searched_at (UTC) for every key in the cache.

    One connection for a whole page of candidates instead of one per record
    (A-L3). With retry_hours > 0 only entries inside the window count.

    fingerprints (0.9.0, "Search again after profile changes"): cache_key ->
    (current, baseline) fingerprint of the title's quality profile. An entry
    then blocks only while it was searched under the current fingerprint; an
    entry from before 0.9.0 (no fingerprint) counts as searched under the
    baseline. Without a current fingerprint (profile unknown) the entry keeps
    blocking. None: every entry blocks, as before.

    grab_release_days (0.9.0, "Search again if still missing after"): an
    entry of a checked-search grab (grabbed_at) blocks exactly that many
    days — also when retry_hours is shorter — and is released afterwards,
    also under the same fingerprint (a changed profile releases it earlier,
    like any entry). Pass it only for candidates from a wanted list — there
    a grab that is still listed is still missing. 0: off, a grab keeps the
    plain retry window.
    """
    wanted = list(dict.fromkeys(k for k in keys if k))
    found: dict[str, datetime] = {}
    if not wanted:
        return found
    with get_db() as conn:
        for start in range(0, len(wanted), LOOKUP_CHUNK):
            chunk = wanted[start:start + LOOKUP_CHUNK]
            placeholders = ",".join("?" * len(chunk))
            statement = (
                "SELECT cache_key, searched_at, profile_fingerprint, "
                "(grabbed_at IS NOT NULL AND grabbed_at <= datetime('now', 'localtime', ? || ' days')) "
                "AS grab_due FROM searched_items "
                f"WHERE instance_id=? AND cache_key IN ({placeholders})"
            )
            params: list = [f"-{max(0, grab_release_days)}", instance_id, *chunk]
            if retry_hours > 0:
                # A grab's own days decide when it is released, not the window.
                statement += (" AND (searched_at > datetime('now', 'localtime', ? || ' hours')"
                              " OR (? > 0 AND grabbed_at IS NOT NULL))")
                params += [f"-{retry_hours}", grab_release_days]
            for row in conn.execute(statement, params):
                if grab_release_days > 0 and row["grab_due"]:
                    continue
                if fingerprints is not None and not _same_profile(row, fingerprints):
                    continue
                found[row["cache_key"]] = local_to_utc(row["searched_at"])
    return found


def _same_profile(row, fingerprints: dict) -> bool:
    """Was this entry searched under the current fingerprint of its title's
    profile? Unknown current fingerprint: yes (nothing is released)."""
    current, baseline = fingerprints.get(row["cache_key"], (None, None))
    if current is None:
        return True
    stored = row["profile_fingerprint"] if row["profile_fingerprint"] is not None else baseline
    return stored == current


def purge_expired(instance_id: int, retry_hours: int, keep_grab_days: int = 0) -> int:
    """Delete entries outside the retry window. They no longer block anything,
    they only pile up (B6). No window (0) means permanent — nothing expires.

    keep_grab_days (0.9.0): a grab of the checked search (grabbed_at) blocks
    that many days whatever retry_hours says ("Search again if still missing
    after"), so its row stays until then."""
    if retry_hours <= 0:
        return 0
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM searched_items WHERE instance_id=? "
            "AND searched_at <= datetime('now', 'localtime', ? || ' hours') "
            "AND NOT (grabbed_at IS NOT NULL AND grabbed_at > datetime('now', 'localtime', ? || ' days'))",
            (instance_id, f"-{retry_hours}", f"-{max(0, keep_grab_days)}"),
        )
        return cursor.rowcount


def count_filtered(instance_id: Optional[int] = None, item_type: Optional[str] = None) -> int:
    conditions, params = [], []
    if instance_id is not None:
        conditions.append("instance_id=?")
        params.append(instance_id)
    if item_type:
        conditions.append("item_type=?")
        params.append(item_type)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    with get_db() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM searched_items {where}", params).fetchone()[0]
