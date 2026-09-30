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


def add(instance_id: int, cache_key: str, title: str, item_type: str) -> None:
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO searched_items (instance_id, cache_key, title, item_type)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(instance_id, cache_key) DO UPDATE SET searched_at=datetime('now','localtime')
            """,
            (instance_id, cache_key, title, item_type),
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


def lookup_many(instance_id: int, keys: Iterable[str], retry_hours: int = 0) -> dict[str, datetime]:
    """cache_key -> searched_at (UTC) for every key in the cache.

    One connection for a whole page of candidates instead of one per record
    (A-L3). With retry_hours > 0 only entries inside the window count.
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
                "SELECT cache_key, searched_at FROM searched_items "
                f"WHERE instance_id=? AND cache_key IN ({placeholders})"
            )
            params: list = [instance_id, *chunk]
            if retry_hours > 0:
                statement += " AND searched_at > datetime('now', 'localtime', ? || ' hours')"
                params.append(f"-{retry_hours}")
            for row in conn.execute(statement, params):
                found[row["cache_key"]] = local_to_utc(row["searched_at"])
    return found


def purge_expired(instance_id: int, retry_hours: int) -> int:
    """Delete entries outside the retry window. They no longer block anything,
    they only pile up (B6). No window (0) means permanent — nothing expires."""
    if retry_hours <= 0:
        return 0
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM searched_items WHERE instance_id=? "
            "AND searched_at <= datetime('now', 'localtime', ? || ' hours')",
            (instance_id, f"-{retry_hours}"),
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
