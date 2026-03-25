from typing import Optional
from backend.database import get_db


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
    with get_db() as conn:
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


def insert_item(run_id: int, title: str, arr_id: Optional[int], item_type: str):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO search_history_items (run_id, title, arr_id, item_type) VALUES (?, ?, ?, ?)",
            (run_id, title, arr_id, item_type),
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
                COALESCE(inst.type, '') AS arr_type,
                si.title,
                si.item_type
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


def clear():
    with get_db() as conn:
        conn.execute("DELETE FROM search_history")
