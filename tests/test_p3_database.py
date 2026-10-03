import os
import sqlite3
import stat

import pytest

from backend import database, db
from backend.config import settings


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "missingarr.db"
    monkeypatch.setattr(settings, "database_url", str(path))
    monkeypatch.setattr(database, "_cached_secret_key", None)
    database.init_db()
    return path


def make_instance(**fields):
    data = {"name": "Sonarr", "type": "sonarr", "url": "http://127.0.0.1:9", "api_key": "k" * 32}
    data.update(fields)
    return db.instances.create(data)


def sql(statement, params=()):
    with database.get_db() as conn:
        return conn.execute(statement, params).fetchall()


INSTANCES_DDL = """
CREATE TABLE instances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('sonarr','radarr')),
    url TEXT NOT NULL,
    api_key TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    search_missing_enabled INTEGER NOT NULL DEFAULT 1,
    search_upgrades_enabled INTEGER NOT NULL DEFAULT 0,
    interval_minutes INTEGER NOT NULL DEFAULT 15,
    retry_hours INTEGER NOT NULL DEFAULT 0,
    rate_window_minutes INTEGER NOT NULL DEFAULT 60,
    rate_cap INTEGER NOT NULL DEFAULT 25,
    search_order TEXT NOT NULL DEFAULT 'random'
        CHECK(search_order IN ('random','smart','newest_first','oldest_first')),
    missing_mode TEXT NOT NULL DEFAULT 'episode'
        CHECK(missing_mode IN ('smart','season_packs','show_batch','episode')),
    missing_per_run INTEGER NOT NULL DEFAULT 5,
    upgrades_per_run INTEGER NOT NULL DEFAULT 1,
    seconds_between_actions INTEGER NOT NULL DEFAULT 2,
    hours_after_release INTEGER NOT NULL DEFAULT 9,
    upgrade_source TEXT NOT NULL DEFAULT 'monitored_items_only'
        CHECK(upgrade_source IN ('wanted_list_only','monitored_items_only','both')),
    quiet_start TEXT,
    quiet_end TEXT,
    connection_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK(connection_status IN ('unknown','online','offline','error')),
    last_seen_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE activity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id INTEGER REFERENCES instances(id) ON DELETE CASCADE,
    instance_name TEXT NOT NULL,
    level TEXT NOT NULL CHECK(level IN ('info','warn','error','debug')),
    skill TEXT,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX idx_activity_created ON activity_log(created_at DESC);
CREATE TABLE searched_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id INTEGER NOT NULL REFERENCES instances(id) ON DELETE CASCADE,
    cache_key TEXT NOT NULL,
    title TEXT NOT NULL,
    item_type TEXT NOT NULL,
    searched_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(instance_id, cache_key)
);
CREATE INDEX idx_searched_instance ON searched_items(instance_id);
CREATE INDEX idx_searched_at ON searched_items(searched_at DESC);
CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

# Layout of a 0.7.0 database after all 0.7.0 migrations and the status rebuild.
SCHEMA_0_7_0 = INSTANCES_DDL + """
CREATE TABLE search_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id INTEGER REFERENCES instances(id) ON DELETE CASCADE,
    instance_name TEXT NOT NULL,
    skill TEXT NOT NULL CHECK(skill IN ('search_missing','search_upgrades')),
    wanted_count INTEGER NOT NULL DEFAULT 0,
    triggered_count INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running'
        CHECK(status IN ('running','success','error','pending','partial','failed','unverified')),
    error_message TEXT,
    verified_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_history_instance ON search_history(instance_id);
CREATE INDEX idx_history_started ON search_history(started_at DESC);
CREATE TABLE search_history_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES search_history(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    arr_id INTEGER,
    item_type TEXT NOT NULL CHECK(item_type IN ('movie','episode','season','series')),
    command_id INTEGER,
    command_status TEXT NOT NULL DEFAULT 'legacy',
    cache_key TEXT NOT NULL DEFAULT '',
    verified_at TEXT,
    created_at TEXT
);
CREATE INDEX idx_history_items_run ON search_history_items(run_id);
CREATE INDEX idx_history_items_pending ON search_history_items(command_status);
"""

SEED_0_7_0 = """
INSERT INTO instances (id, name, type, url, api_key, retry_hours)
VALUES (1, 'Sonarr', 'sonarr', 'http://sonarr:8989', 'enc:abc', 168),
       (2, 'Radarr', 'radarr', 'http://radarr:7878', 'enc:def', 1);
INSERT INTO search_history (id, instance_id, instance_name, skill, started_at, status)
VALUES (1, 1, 'Sonarr', 'search_missing', '2026-09-01 10:00:00', 'running'),
       (2, 1, 'Sonarr', 'search_missing', '2026-09-01 11:00:00', 'pending'),
       (3, 2, 'Radarr', 'search_missing', '2026-09-01 12:00:00', 'success');
INSERT INTO search_history_items (run_id, title, item_type, command_id, command_status, cache_key, created_at)
VALUES (2, 'Show S01E01', 'episode', 101, 'submitted', 'ep:1', '2026-09-01 11:00:01'),
       (3, 'Movie (2020)', 'movie', NULL, 'legacy', '', NULL);
INSERT INTO searched_items (instance_id, cache_key, title, item_type, searched_at)
VALUES (1, 'ep:1', 'Show S01E01', 'episode', '2026-09-01 11:00:01');
INSERT INTO activity_log (instance_id, instance_name, level, message) VALUES (1, 'Sonarr', 'info', 'hello');
INSERT INTO app_settings (key, value) VALUES ('encryption_key', 'x'), ('secret_key', 'y');
"""

# Before command verification (0.6.13): narrow status CHECK, items without command columns.
SCHEMA_0_6_13 = INSTANCES_DDL + """
CREATE TABLE search_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id INTEGER REFERENCES instances(id) ON DELETE CASCADE,
    instance_name TEXT NOT NULL,
    skill TEXT NOT NULL CHECK(skill IN ('search_missing','search_upgrades')),
    wanted_count INTEGER NOT NULL DEFAULT 0,
    triggered_count INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running','success','error')),
    error_message TEXT
);
CREATE INDEX idx_history_instance ON search_history(instance_id);
CREATE INDEX idx_history_started ON search_history(started_at DESC);
CREATE TABLE search_history_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES search_history(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    arr_id INTEGER,
    item_type TEXT NOT NULL CHECK(item_type IN ('movie','episode','season','series'))
);
CREATE INDEX idx_history_items_run ON search_history_items(run_id);
"""

SEED_0_6_13 = """
INSERT INTO instances (id, name, type, url, api_key) VALUES (1, 'Sonarr', 'sonarr', 'http://sonarr:8989', 'enc:abc');
INSERT INTO search_history (id, instance_id, instance_name, skill, started_at, status)
VALUES (1, 1, 'Sonarr', 'search_missing', '2026-03-01 10:00:00', 'success'),
       (2, 1, 'Sonarr', 'search_upgrades', '2026-03-01 11:00:00', 'success');
INSERT INTO search_history_items (run_id, title, arr_id, item_type)
VALUES (1, 'A', 1, 'episode'), (1, 'B', 2, 'episode'), (2, 'C', 3, 'season');
"""

TABLES = ["instances", "search_history", "search_history_items", "searched_items",
          "activity_log", "app_settings"]


def build(path, script):
    conn = sqlite3.connect(path)
    conn.executescript(script)
    conn.commit()
    conn.close()


def counts(path):
    conn = sqlite3.connect(path)
    try:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
    finally:
        conn.close()


def columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def ancestor_rule_since(path):
    conn = sqlite3.connect(path)
    try:
        row = conn.execute("SELECT value FROM app_settings WHERE key='ancestor_rule_since'").fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def test_retry_hours_survive_restarts(db_path):
    for hours in (1, 168):
        make_instance(name=f"i{hours}", retry_hours=hours)
    database.init_db()
    database.init_db()
    assert sorted(r[0] for r in sql("SELECT retry_hours FROM instances")) == [1, 168]


def test_0_7_0_database_upgrades_in_place_and_idempotently(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    build(path, SCHEMA_0_7_0 + SEED_0_7_0)
    before = counts(path)
    monkeypatch.setattr(settings, "database_url", str(path))
    database.init_db()
    marker = ancestor_rule_since(path)
    database.init_db()
    # Only new rows: the ancestor-rule marker (A9) and the local-timestamps
    # marker (0.10.1), each written once.
    assert counts(path) == {**before, "app_settings": before["app_settings"] + 2}
    assert marker is not None and ancestor_rule_since(path) == marker
    conn = sqlite3.connect(path)
    try:
        assert dict(conn.execute("SELECT id, retry_hours FROM instances")) == {1: 168, 2: 1}
        assert "last_checked_at" in columns(conn, "search_history_items")
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_history_instance_started'"
        ).fetchone()
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("SELECT status FROM search_history WHERE id=1").fetchone()[0] == "running"
    finally:
        conn.close()


def test_0_6_13_database_keeps_every_history_item(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    build(path, SCHEMA_0_6_13 + SEED_0_6_13)
    monkeypatch.setattr(settings, "database_url", str(path))
    database.init_db()
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM search_history_items").fetchone()[0] == 3
        assert {r[0] for r in conn.execute("SELECT command_status FROM search_history_items")} == {"legacy"}
        table_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='search_history'"
        ).fetchone()[0]
        assert "'pending'" in table_sql
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


REBUILD_STEPS = [
    "CREATE TABLE search_history_rebuilt",
    "INSERT INTO search_history_rebuilt",
    "DROP TABLE search_history",
    "ALTER TABLE search_history_rebuilt RENAME",
    "CREATE INDEX IF NOT EXISTS idx_history_instance",
    "CREATE INDEX IF NOT EXISTS idx_history_started",
]


@pytest.mark.parametrize("step", REBUILD_STEPS)
def test_rebuild_failure_at_any_step_leaves_history_and_items_intact(tmp_path, monkeypatch, step):
    path = tmp_path / "legacy.db"
    build(path, SCHEMA_0_6_13 + SEED_0_6_13 +
          "ALTER TABLE search_history ADD COLUMN verified_count INTEGER NOT NULL DEFAULT 0;")
    real_connect = sqlite3.connect

    class FailingStep:
        def __init__(self, connection):
            object.__setattr__(self, "connection", connection)

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def __setattr__(self, name, value):
            setattr(self.connection, name, value)

        def execute(self, statement, parameters=()):
            if statement.strip().startswith(step):
                raise sqlite3.OperationalError("injected failure")
            return self.connection.execute(statement, parameters)

    monkeypatch.setattr(settings, "database_url", str(path))
    monkeypatch.setattr(database.sqlite3, "connect",
                        lambda *a, **k: FailingStep(real_connect(*a, **k)))
    with pytest.raises(sqlite3.OperationalError, match="injected failure"):
        database._widen_history_status_check()

    conn = real_connect(path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM search_history").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM search_history_items").fetchone()[0] == 3
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='search_history_rebuilt'"
        ).fetchone() is None
    finally:
        conn.close()


def test_unexpected_migration_error_stops_the_start(db_path, monkeypatch):
    # SQLite refuses this on every table, empty or not (a non-constant default
    # only fails on tables that already hold rows).
    broken = ("search_history_items", "broken", "INTEGER PRIMARY KEY")
    monkeypatch.setattr(database, "_COLUMN_MIGRATIONS", database._COLUMN_MIGRATIONS + [broken])
    with pytest.raises(sqlite3.OperationalError, match="PRIMARY KEY"):
        database.init_db()


def test_a_column_added_in_the_meantime_is_tolerated(db_path, monkeypatch):
    monkeypatch.setattr(database, "_columns", lambda conn, table: set())
    with database.get_db() as conn:
        database._add_missing_columns(conn)


def test_incomplete_schema_is_reported(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    build(path, SCHEMA_0_7_0)
    monkeypatch.setattr(settings, "database_url", str(path))
    monkeypatch.setattr(database, "_add_missing_columns", lambda conn: None)
    with pytest.raises(RuntimeError, match="search_history_items.last_checked_at"):
        database.init_db()


def test_database_file_is_private(db_path):
    assert stat.S_IMODE(os.stat(db_path).st_mode) == 0o600


def test_connections_wait_for_locks(db_path):
    with database.get_db() as conn:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000


def test_new_settings_have_safe_defaults():
    from backend.config import Settings
    fresh = Settings(_env_file=None)
    assert fresh.secret_key == ""
    assert fresh.cookie_secure is False
    assert fresh.history_retention_days == 365
    with pytest.raises(ValueError):
        Settings(_env_file=None, history_retention_days=-1)


def test_env_file_copied_from_the_example_is_accepted(tmp_path):
    # .env.example carries container-only keys (PUID/PGID) that Settings does
    # not know; a local `cp .env.example .env` must not crash the import.
    from backend.config import Settings
    env_file = tmp_path / ".env"
    env_file.write_text("PUID=1000\nPGID=1000\nHISTORY_RETENTION_DAYS=30\n")
    loaded = Settings(_env_file=str(env_file))
    assert loaded.history_retention_days == 30


def test_ancestor_rule_marker_is_written_once(db_path):
    first = ancestor_rule_since(db_path)
    assert first is not None and len(first) == 19
    sql("UPDATE app_settings SET value='2026-01-01 00:00:00' WHERE key='ancestor_rule_since'")
    database.init_db()
    assert ancestor_rule_since(db_path) == "2026-01-01 00:00:00"


# Real releases, not hand-written DDL: the live database dates from March 2026
# and grew through many versions by ALTER TABLE and the 0.6.13 rebuild.
HISTORIC_TAGS = ["v0.5.2", "v0.6.0", "v0.6.12", "v0.6.13", "v0.7.0"]


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


def test_database_grown_through_real_releases_upgrades_cleanly(tmp_path, monkeypatch):
    path = tmp_path / "grown.db"
    monkeypatch.setattr(settings, "database_url", str(path))
    first, *later = [historic_database_module(tag) for tag in HISTORIC_TAGS]
    first.init_db()
    build(path, """
        INSERT INTO instances (id, name, type, url, api_key) VALUES (1, 'Sonarr', 'sonarr', 'http://sonarr:8989', 'enc:abc');
        INSERT INTO search_history (id, instance_id, instance_name, skill, started_at, status)
        VALUES (1, 1, 'Sonarr', 'search_missing', '2026-03-22 10:00:00', 'success');
        INSERT INTO search_history_items (run_id, title, arr_id, item_type) VALUES (1, 'A', 1, 'episode'), (1, 'B', 2, 'season');
        INSERT INTO searched_items (instance_id, cache_key, title, item_type) VALUES (1, 'ser:1', 'Show', 'series');
    """)
    for module in later:
        module.init_db()
    # A run from 0.7.0 with an open command; retry_hours set after 0.7.0's reset.
    build(path, """
        UPDATE instances SET retry_hours=168 WHERE id=1;
        INSERT INTO search_history (id, instance_id, instance_name, skill, started_at, status)
        VALUES (2, 1, 'Sonarr', 'search_missing', '2026-09-01 11:00:00', 'pending');
        INSERT INTO search_history_items (run_id, title, item_type, command_id, command_status, cache_key, created_at)
        VALUES (2, 'C', 'episode', 101, 'submitted', 'ep:3', '2026-09-01 11:00:01');
    """)
    before = counts(path)

    database.init_db()
    database.init_db()

    # The ancestor-rule marker (A9) and the local-timestamps marker (0.10.1).
    assert counts(path) == {**before, "app_settings": before["app_settings"] + 2}
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("SELECT retry_hours FROM instances WHERE id=1").fetchone()[0] == 168
        assert "last_checked_at" in columns(conn, "search_history_items")
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_history_instance_started'"
        ).fetchone()
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        statuses = dict(conn.execute(
            "SELECT title, command_status FROM search_history_items"
        ).fetchall())
        assert statuses == {"A": "legacy", "B": "legacy", "C": "submitted"}
        assert "'pending'" in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='search_history'"
        ).fetchone()[0]
    finally:
        conn.close()
