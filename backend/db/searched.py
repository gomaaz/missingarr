from typing import Optional
from backend.database import get_db


def exists(instance_id: int, cache_key: str, retry_hours: int = 0) -> bool:
    with get_db() as conn:
        if retry_hours > 0:
            row = conn.execute(
                """
                SELECT 1 FROM searched_items
                WHERE instance_id=? AND cache_key=?
                AND searched_at > datetime('now', ? || ' hours')
                """,
                (instance_id, cache_key, f"-{retry_hours}"),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT 1 FROM searched_items WHERE instance_id=? AND cache_key=?",
                (instance_id, cache_key),
            ).fetchone()
        return row is not None


def add(instance_id: int, cache_key: str, title: str, item_type: str) -> None:
    with get_db() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO searched_items (instance_id, cache_key, title, item_type)
            VALUES (?, ?, ?, ?)
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
            ORDER BY s.searched_at DESC LIMIT ? OFFSET ?
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
