import sqlite3

import pytest

from backend import database
from backend.config import settings


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "missingarr.db"
    monkeypatch.setattr(settings, "database_url", str(path))
    monkeypatch.setattr(database, "_cached_secret_key", None)
    database.init_db()
    return path


def sql(statement, params=()):
    with database.get_db() as conn:
        return [tuple(r) for r in conn.execute(statement, params).fetchall()]


def columns(conn, table):
    return {row[1]: row for row in conn.execute(f"PRAGMA table_info({table})")}


def historic_database_module(tag):
    import subprocess
    import types
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    try:
        source = subprocess.run(
            ["git", "-C", str(root), "show", f"{tag}:backend/database.py"],
            capture_output=True, text=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip(f"git tag {tag} is not available in this checkout")
    module = types.ModuleType(f"historic_database_{tag.replace('.', '_')}")
    exec(compile(source, f"{tag}:backend/database.py", "exec"), module.__dict__)
    return module


def test_fresh_database_has_the_checked_search_schema(db_path):
    conn = sqlite3.connect(db_path)
    try:
        cols = columns(conn, "instances")
        assert {"checked_search", "checked_search_settings", "dry_run_round", "dry_run_round_started_at",
                "search_again_after_profile_change", "profile_fingerprints",
                "profile_fingerprints_baseline"} <= set(cols)
        assert {"profile_fingerprint", "grabbed_at", "history_item_id", "no_results_at"} <= set(
            columns(conn, "searched_items"))
        assert set(columns(conn, "checked_search_log")) == {
            "id", "instance_id", "run_id", "mode", "skill", "arr_id", "cache_key", "title", "created_at",
            "outcome", "arr_pick", "pick", "pick_indexer", "pick_score", "pick_size", "pick_quality",
            "candidates", "error_message", "profile_fingerprint", "profile_id", "dry_run_round",
            "settings_fingerprint",
        }
        indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert {"idx_cs_log_instance_created", "idx_cs_log_instance_mode_key"} <= indexes
    finally:
        conn.close()


def test_0_8_0_database_gets_the_new_columns_with_defaults(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    monkeypatch.setattr(settings, "database_url", str(path))
    historic_database_module("v0.8.0").init_db()
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO instances (id, name, type, url, api_key) "
                 "VALUES (1, 'Radarr', 'radarr', 'http://radarr:7878', 'enc:abc')")
    conn.execute("INSERT INTO searched_items (instance_id, cache_key, title, item_type) "
                 "VALUES (1, 'mov:5', 'Old', 'movie')")
    conn.commit()
    conn.close()

    database.init_db()
    database.init_db()  # idempotent

    conn = sqlite3.connect(path)
    try:
        row = conn.execute("SELECT checked_search, checked_search_settings, dry_run_round, "
                           "dry_run_round_started_at, search_again_after_profile_change, profile_fingerprints, "
                           "profile_fingerprints_baseline FROM instances WHERE id=1").fetchone()
        assert row == ("off", "{}", 0, None, 1, "{}", None)
        # cached before 0.9.0: no fingerprint, counts as searched under the baseline; no checked grab;
        # no item named as its writer; no empty search of the checked search
        assert conn.execute("SELECT profile_fingerprint, grabbed_at, history_item_id, no_results_at "
                            "FROM searched_items").fetchall() == [(None, None, None, None)]
        assert conn.execute("SELECT COUNT(*) FROM checked_search_log").fetchone()[0] == 0
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def test_unknown_checked_search_mode_is_refused(db_path):
    sql("INSERT INTO instances (name, type, url, api_key) VALUES ('R', 'radarr', 'http://r', 'k')")
    with pytest.raises(sqlite3.IntegrityError):
        sql("UPDATE instances SET checked_search='maybe'")
