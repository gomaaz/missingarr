import json
import sqlite3
from typing import Optional
from backend.database import get_db
from backend.crypto import encrypt, decrypt

CHECKED_SEARCH_MODES = ("off", "dry_run", "active")
# Local time with milliseconds — the format of checked_search_log.created_at.
# The round itself is the counter dry_run_round; its start is for display.
_NOW_MS = "strftime('%Y-%m-%d %H:%M:%f','now','localtime')"


def _mask_api_key(key: str) -> str:
    if len(key) <= 6:
        return "****"
    return key[:4] + "****" + key[-2:]


def _settings_dict(raw) -> dict:
    """The stored settings JSON as a dict. Anything unreadable counts as
    empty: the defaults apply then (CheckedSearchSettings.from_stored)."""
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _settings_json(value) -> str:
    return json.dumps(_settings_dict(value), sort_keys=True, ensure_ascii=False)


def _checked_mode(value) -> str:
    return value if value in CHECKED_SEARCH_MODES else "off"


def _flag(value, default: bool = True) -> int:
    return int(default if value is None else bool(value))


def row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    if "api_key" in d and d["api_key"]:
        d["api_key"] = decrypt(d["api_key"])
    if "checked_search_settings" in d:
        d["checked_search_settings"] = _settings_dict(d["checked_search_settings"])
    if "profile_fingerprints" in d:
        d["profile_fingerprints"] = _settings_dict(d["profile_fingerprints"])
    if "profile_fingerprints_baseline" in d:
        raw = d["profile_fingerprints_baseline"]
        d["profile_fingerprints_baseline"] = None if raw is None else _settings_dict(raw)
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
    checked = _checked_mode(data.get("checked_search") or "off")
    with get_db() as conn:
        conn.execute(
            f"""
            INSERT INTO instances (
                name, type, url, api_key,
                enabled, search_missing_enabled, search_upgrades_enabled,
                interval_minutes, retry_hours,
                rate_window_minutes, rate_cap,
                search_order, missing_mode,
                missing_per_run, upgrades_per_run,
                seconds_between_actions, hours_after_release,
                upgrade_source, quiet_start, quiet_end,
                checked_search, checked_search_settings, search_again_after_profile_change,
                dry_run_round_started_at, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                      CASE WHEN ? = 'dry_run' THEN {_NOW_MS} END,
                      datetime('now','localtime'), datetime('now','localtime'))
            """,
            (
                data["name"], data["type"], data["url"], encrypt(data["api_key"]),
                int(data.get("enabled", True)),
                int(data.get("search_missing_enabled", True)),
                int(data.get("search_upgrades_enabled", False)),
                data.get("interval_minutes", 15),
                data.get("retry_hours", 0),
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
                checked,
                _settings_json(data.get("checked_search_settings")),
                _flag(data.get("search_again_after_profile_change")),
                checked,
            ),
        )
        row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        row = conn.execute("SELECT * FROM instances WHERE id=?", (row_id,)).fetchone()
        return row_to_dict(row)


def update(instance_id: int, data: dict) -> Optional[dict]:
    """checked_search / checked_search_settings / search_again_after_profile_change
    missing or None: keep what is stored (a client that does not know them
    does not reset them). Switching to 'dry_run' from another mode starts a
    new dry-run round (counter + 1). The profile fingerprints are never
    touched here."""
    with get_db() as conn:
        existing = conn.execute(
            "SELECT api_key, checked_search, checked_search_settings, search_again_after_profile_change "
            "FROM instances WHERE id=?",
            (instance_id,),
        ).fetchone()
        if not existing:
            return None

        # Keep existing encrypted key if no new key provided
        raw_key = data.get("api_key")
        if raw_key:
            api_key = encrypt(raw_key)
        else:
            api_key = existing["api_key"]  # already encrypted in DB

        old_mode = existing["checked_search"]
        new_mode = _checked_mode(data.get("checked_search")) if data.get("checked_search") else old_mode
        settings_json = (
            _settings_json(data["checked_search_settings"])
            if data.get("checked_search_settings") is not None
            else existing["checked_search_settings"]
        )
        new_round = new_mode == "dry_run" and old_mode != "dry_run"
        search_again = _flag(data.get("search_again_after_profile_change"),
                             bool(existing["search_again_after_profile_change"]))

        conn.execute(
            f"""
            UPDATE instances SET
                name=?, type=?, url=?, api_key=?,
                enabled=?, search_missing_enabled=?, search_upgrades_enabled=?,
                interval_minutes=?, retry_hours=?,
                rate_window_minutes=?, rate_cap=?,
                search_order=?, missing_mode=?,
                missing_per_run=?, upgrades_per_run=?,
                seconds_between_actions=?, hours_after_release=?,
                upgrade_source=?, quiet_start=?, quiet_end=?,
                checked_search=?, checked_search_settings=?, search_again_after_profile_change=?,
                dry_run_round=dry_run_round + ?,
                dry_run_round_started_at=CASE WHEN ? THEN {_NOW_MS} ELSE dry_run_round_started_at END,
                updated_at=datetime('now','localtime')
            WHERE id=?
            """,
            (
                data["name"], data["type"], data["url"], api_key,
                int(data.get("enabled", True)),
                int(data.get("search_missing_enabled", True)),
                int(data.get("search_upgrades_enabled", False)),
                data.get("interval_minutes", 15),
                data.get("retry_hours", 0),
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
                new_mode, settings_json, search_again, int(new_round), int(new_round),
                instance_id,
            ),
        )
        row = conn.execute(
            "SELECT * FROM instances WHERE id=?", (instance_id,)
        ).fetchone()
        return row_to_dict(row)


def reset_dry_run(instance_id: int) -> Optional[str]:
    """Start a new dry-run round: every title is checked once more. A run
    that is still going keeps writing into the round it began in. Returns
    the new round start, or None when the instance does not exist."""
    with get_db() as conn:
        cursor = conn.execute(
            f"UPDATE instances SET dry_run_round=dry_run_round + 1, dry_run_round_started_at={_NOW_MS} "
            "WHERE id=?",
            (instance_id,),
        )
        if cursor.rowcount == 0:
            return None
        return conn.execute(
            "SELECT dry_run_round_started_at FROM instances WHERE id=?", (instance_id,)
        ).fetchone()[0]


def store_profile_fingerprints(instance_id: int, fingerprints: dict) -> Optional[dict]:
    """Keep the profile fingerprints a run just read (profile id -> value).
    The first call also sets the baseline: cache entries from before 0.9.0
    count as searched under it, so the update alone releases nothing.
    Returns the baseline, or None when the instance does not exist."""
    text = json.dumps(fingerprints, sort_keys=True)
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE instances SET profile_fingerprints=?, "
            "profile_fingerprints_baseline=COALESCE(profile_fingerprints_baseline, ?) WHERE id=?",
            (text, text, instance_id),
        )
        if cursor.rowcount == 0:
            return None
        row = conn.execute(
            "SELECT profile_fingerprints_baseline FROM instances WHERE id=?", (instance_id,)
        ).fetchone()
        return _settings_dict(row[0])


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


def toggle_skill(instance_id: int, skill: str, enabled: bool):
    col = "search_missing_enabled" if skill == "missing" else "search_upgrades_enabled"
    with get_db() as conn:
        conn.execute(
            f"UPDATE instances SET {col}=?, updated_at=datetime('now','localtime') WHERE id=?",
            (int(enabled), instance_id),
        )


def toggle_enabled(instance_id: int, enabled: bool) -> Optional[dict]:
    with get_db() as conn:
        conn.execute(
            "UPDATE instances SET enabled=?, updated_at=datetime('now','localtime') WHERE id=?",
            (int(enabled), instance_id),
        )
        row = conn.execute(
            "SELECT * FROM instances WHERE id=?", (instance_id,)
        ).fetchone()
        return row_to_dict(row) if row else None
