import sqlite3
from typing import Optional
from backend.database import get_db
from backend.config import settings


def insert(
    instance_id: Optional[int],
    instance_name: str,
    level: str,
    message: str,
    skill: Optional[str] = None,
):
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO activity_log (instance_id, instance_name, level, skill, message)
            VALUES (?, ?, ?, ?, ?)
            """,
            (instance_id, instance_name, level, skill, message),
        )
        _trim(conn)


def _trim(conn: sqlite3.Connection):
    count = conn.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0]
    if count > settings.max_log_entries:
        excess = count - settings.max_log_entries
        conn.execute(
            """
            DELETE FROM activity_log WHERE id IN (
                SELECT id FROM activity_log ORDER BY created_at ASC LIMIT ?
            )
            """,
            (excess,),
        )


def query(
    instance_id: Optional[int] = None,
    level: Optional[str] = None,
    include_debug: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    conditions = []
    params: list = []

    if instance_id is not None:
        conditions.append("instance_id=?")
        params.append(instance_id)
    if level:
        conditions.append("level=?")
        params.append(level)
    if not include_debug:
        conditions.append("level != 'debug'")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params += [limit, offset]

    with get_db() as conn:
        rows = conn.execute(
            f"SELECT * FROM activity_log {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [dict(r) for r in rows]


def clear():
    with get_db() as conn:
        conn.execute("DELETE FROM activity_log")
