import sqlite3
from typing import Optional
from backend.database import get_db
from backend.crypto import encrypt, decrypt


def _mask_api_key(key: str) -> str:
    if len(key) <= 6:
        return "****"
    return key[:4] + "****" + key[-2:]


def row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    if "api_key" in d and d["api_key"]:
        d["api_key"] = decrypt(d["api_key"])
    return d


def get_all(include_disabled: bool = True) -> list[dict]:
    with get_db() as conn:
        if include_disabled:
            rows = conn.execute(
                "SELECT * FROM instances ORDER BY type, name"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM instances WHERE enabled=1 ORDER BY type, name"
            ).fetchall()
        return [row_to_dict(r) for r in rows]


def get_by_id(instance_id: int) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM instances WHERE id=?", (instance_id,)
        ).fetchone()
        return row_to_dict(row) if row else None


def create(data: dict) -> dict:
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO instances (
                name, type, url, api_key,
                enabled, search_missing_enabled, search_upgrades_enabled,
                interval_minutes, retry_hours,
                rate_window_minutes, rate_cap,
                search_order, missing_mode,
                missing_per_run, upgrades_per_run,
                seconds_between_actions, hours_after_release,
                upgrade_source, quiet_start, quiet_end
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                data["name"], data["type"], data["url"], encrypt(data["api_key"]),
                int(data.get("enabled", True)),
                int(data.get("search_missing_enabled", True)),
                int(data.get("search_upgrades_enabled", False)),
                data.get("interval_minutes", 15),
                data.get("retry_hours", 1),
                data.get("rate_window_minutes", 60),
                data.get("rate_cap", 25),
                data.get("search_order", "random"),
                data.get("missing_mode", "episode"),
                data.get("missing_per_run", 5),
                data.get("upgrades_per_run", 1),
                data.get("seconds_between_actions", 2),
                data.get("hours_after_release", 9),
                data.get("upgrade_source", "monitored_items_only"),
                data.get("quiet_start") or None,
                data.get("quiet_end") or None,
            ),
        )
        row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        row = conn.execute("SELECT * FROM instances WHERE id=?", (row_id,)).fetchone()
        return row_to_dict(row)


def update(instance_id: int, data: dict) -> Optional[dict]:
    with get_db() as conn:
        existing = conn.execute(
            "SELECT api_key FROM instances WHERE id=?", (instance_id,)
        ).fetchone()
        if not existing:
            return None

        # Keep existing encrypted key if no new key provided
        raw_key = data.get("api_key")
        if raw_key:
            api_key = encrypt(raw_key)
        else:
            api_key = existing["api_key"]  # already encrypted in DB

        conn.execute(
            """
            UPDATE instances SET
                name=?, type=?, url=?, api_key=?,
                enabled=?, search_missing_enabled=?, search_upgrades_enabled=?,
                interval_minutes=?, retry_hours=?,
                rate_window_minutes=?, rate_cap=?,
                search_order=?, missing_mode=?,
                missing_per_run=?, upgrades_per_run=?,
                seconds_between_actions=?, hours_after_release=?,
                upgrade_source=?, quiet_start=?, quiet_end=?,
                updated_at=datetime('now')
            WHERE id=?
            """,
            (
                data["name"], data["type"], data["url"], api_key,
                int(data.get("enabled", True)),
                int(data.get("search_missing_enabled", True)),
                int(data.get("search_upgrades_enabled", False)),
                data.get("interval_minutes", 15),
                data.get("retry_hours", 1),
                data.get("rate_window_minutes", 60),
                data.get("rate_cap", 25),
                data.get("search_order", "random"),
                data.get("missing_mode", "episode"),
                data.get("missing_per_run", 5),
                data.get("upgrades_per_run", 1),
                data.get("seconds_between_actions", 2),
                data.get("hours_after_release", 9),
                data.get("upgrade_source", "monitored_items_only"),
                data.get("quiet_start") or None,
                data.get("quiet_end") or None,
                instance_id,
            ),
        )
        row = conn.execute(
            "SELECT * FROM instances WHERE id=?", (instance_id,)
        ).fetchone()
        return row_to_dict(row)


def delete(instance_id: int) -> bool:
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM instances WHERE id=?", (instance_id,)
        )
        return cursor.rowcount > 0


def update_status(instance_id: int, status: str, last_seen_at: Optional[str] = None):
    with get_db() as conn:
        if last_seen_at:
            conn.execute(
                "UPDATE instances SET connection_status=?, last_seen_at=? WHERE id=?",
                (status, last_seen_at, instance_id),
            )
        else:
            conn.execute(
                "UPDATE instances SET connection_status=? WHERE id=?",
                (status, instance_id),
            )


def toggle_enabled(instance_id: int, enabled: bool) -> Optional[dict]:
    with get_db() as conn:
        conn.execute(
            "UPDATE instances SET enabled=?, updated_at=datetime('now') WHERE id=?",
            (int(enabled), instance_id),
        )
        row = conn.execute(
            "SELECT * FROM instances WHERE id=?", (instance_id,)
        ).fetchone()
        return row_to_dict(row) if row else None
