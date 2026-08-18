import sqlite3

import pytest

from backend import database
from backend.config import settings
from backend.db import activity, history, searched


@pytest.fixture
def database_path(tmp_path, monkeypatch):
    path = tmp_path / "missingarr.db"
    monkeypatch.setattr(settings, "database_url", str(path))
    database.init_db()
    return path


def test_history_query_orders_same_second_by_newest_id(database_path):
    with database.get_db() as conn:
        timestamp = "2026-08-16 23:23:28"
        for name in ("older", "newer"):
            conn.execute(
                """
                INSERT INTO search_history
                    (instance_name, skill, started_at)
                VALUES (?, 'search_missing', ?)
                """,
                (name, timestamp),
            )

    rows = history.query()

    assert [row["instance_name"] for row in rows] == ["newer", "older"]
    assert rows[0]["id"] > rows[1]["id"]


def test_activity_query_orders_same_second_by_newest_id(database_path):
    with database.get_db() as conn:
        timestamp = "2026-08-16 04:43:56"
        for message in ("older", "newer"):
            conn.execute(
                """
                INSERT INTO activity_log
                    (instance_name, level, message, created_at)
                VALUES ('Sonarr', 'info', ?, ?)
                """,
                (message, timestamp),
            )

    rows = activity.query()

    assert [row["message"] for row in rows] == ["newer", "older"]
    assert rows[0]["id"] > rows[1]["id"]


def test_activity_trim_removes_lowest_id_first_for_same_second(
    database_path, monkeypatch
):
    monkeypatch.setattr(settings, "max_log_entries", 2)
    with database.get_db() as conn:
        timestamp = "2026-08-16 04:43:56"
        for message in ("oldest", "middle", "newest"):
            conn.execute(
                """
                INSERT INTO activity_log
                    (instance_name, level, message, created_at)
                VALUES ('Sonarr', 'info', ?, ?)
                """,
                (message, timestamp),
            )
        activity._trim(conn)
        remaining = conn.execute(
            "SELECT id, message FROM activity_log ORDER BY id"
        ).fetchall()

    assert [row["message"] for row in remaining] == ["middle", "newest"]
    assert [row["id"] for row in remaining] == [2, 3]


def test_searched_query_orders_same_second_by_newest_id(database_path):
    with database.get_db() as conn:
        conn.execute(
            """
            INSERT INTO instances (name, type, url, api_key)
            VALUES ('Sonarr', 'sonarr', 'http://sonarr', 'key')
            """
        )
        instance_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        timestamp = "2026-08-16 23:23:28"
        for cache_key, title in (("older", "Older"), ("newer", "Newer")):
            conn.execute(
                """
                INSERT INTO searched_items
                    (instance_id, cache_key, title, item_type, searched_at)
                VALUES (?, ?, ?, 'episode', ?)
                """,
                (instance_id, cache_key, title, timestamp),
            )

    rows = searched.query()

    assert [row["cache_key"] for row in rows] == ["newer", "older"]
    assert rows[0]["id"] > rows[1]["id"]


def test_history_rebuild_failure_rolls_back_original_table(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE search_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id INTEGER,
                instance_name TEXT NOT NULL,
                skill TEXT NOT NULL,
                wanted_count INTEGER NOT NULL DEFAULT 0,
                triggered_count INTEGER NOT NULL DEFAULT 0,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL DEFAULT 'running'
                    CHECK(status IN ('running','success','error')),
                error_message TEXT,
                verified_count INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO search_history
                (instance_name, skill, started_at, status)
            VALUES
                ('Sonarr', 'search_missing', '2026-08-16 11:00:00', 'success'),
                ('Radarr', 'search_upgrades', '2026-08-16 11:00:00', 'running');
            """
        )

    real_connect = sqlite3.connect

    class FailingRenameConnection:
        def __init__(self, connection):
            object.__setattr__(self, "connection", connection)

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def __setattr__(self, name, value):
            setattr(self.connection, name, value)

        def execute(self, statement, parameters=()):
            if statement.strip() == (
                "ALTER TABLE search_history_rebuilt RENAME TO search_history"
            ):
                raise sqlite3.OperationalError("injected failure at rename")
            return self.connection.execute(statement, parameters)

    def failing_connect(*args, **kwargs):
        return FailingRenameConnection(real_connect(*args, **kwargs))

    monkeypatch.setattr(settings, "database_url", str(path))
    monkeypatch.setattr(database.sqlite3, "connect", failing_connect)

    with pytest.raises(sqlite3.OperationalError, match="injected failure at rename"):
        database._widen_history_status_check()

    with real_connect(path) as conn:
        rows = conn.execute(
            "SELECT id, instance_name, status FROM search_history ORDER BY id"
        ).fetchall()
        rebuilt = conn.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='search_history_rebuilt'
            """
        ).fetchone()

    assert rows == [(1, "Sonarr", "success"), (2, "Radarr", "running")]
    assert rebuilt is None
