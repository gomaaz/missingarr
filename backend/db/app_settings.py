"""Small key/value store in the app_settings table."""
from typing import Optional

from backend.database import get_db


def get_value(key: str) -> Optional[str]:
    with get_db() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None


def set_value(key: str, value: str) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def delete_value(key: str) -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM app_settings WHERE key=?", (key,))
