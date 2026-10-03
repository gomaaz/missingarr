"""0.10.1: activity_log, searched_items and instances write their timestamps
explicitly in local time. A database created before 25.03.2026 still carries
the UTC column default datetime('now') (CREATE TABLE IF NOT EXISTS never
changed it); the rows it wrote in UTC are converted once, guarded by a
marker in app_settings. A database created with the local default is never
converted."""
import os
import sqlite3
import time
from datetime import datetime, timedelta

import pytest

from backend import database, db
from backend.config import settings

LOCAL_DEFAULT = "DEFAULT (datetime('now','localtime'))"


@pytest.fixture(autouse=True)
def berlin():
    """A zone that is never UTC: local time is one or two hours ahead."""
    old = os.environ.get("TZ")
    os.environ["TZ"] = "Europe/Berlin"
    time.tzset()
    yield
    if old is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old
    time.tzset()


@pytest.fixture
def db_file(tmp_path, monkeypatch):
    path = tmp_path / "missingarr.db"
    monkeypatch.setattr(settings, "database_url", str(path))
    monkeypatch.setattr(database, "_cached_secret_key", None)
    return path


def old_database(path) -> sqlite3.Connection:
    """The tables as a database created before 25.03.2026 has them: instances,
    activity_log and searched_items with the UTC default datetime('now')."""
    assert database._SCHEMA.count(LOCAL_DEFAULT) == 4
    conn = sqlite3.connect(path)
    conn.executescript(database._SCHEMA.replace(LOCAL_DEFAULT, "DEFAULT (datetime('now'))"))
    return conn


def rows(statement, params=()):
    with database.get_db() as conn:
        return [tuple(r) for r in conn.execute(statement, params)]


def is_local_now(value) -> bool:
    stamp = datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S")
    return abs(stamp - datetime.now()) < timedelta(minutes=5)


def test_an_old_database_converts_its_utc_log_lines_once(db_file):
    conn = old_database(db_file)
    conn.execute("INSERT INTO activity_log (instance_name, level, message) VALUES ('Sonarr', 'info', 'old line')")
    conn.commit()
    conn.close()
    database.init_db()
    [(created,)] = rows("SELECT created_at FROM activity_log")
    assert is_local_now(created)
    database.init_db()          # the next start converts nothing again
    assert rows("SELECT created_at FROM activity_log") == [(created,)]
    assert db.app_settings.get_value(database.LOCAL_TIMESTAMPS_SETTING)


def test_an_unparsable_log_time_stays_and_the_start_succeeds(db_file):
    conn = old_database(db_file)
    conn.execute("INSERT INTO activity_log (instance_name, level, message) VALUES ('Sonarr', 'info', 'old line')")
    conn.execute("INSERT INTO activity_log (instance_name, level, message, created_at) "
                 "VALUES ('Sonarr', 'info', 'odd line', 'not a time')")
    conn.commit()
    conn.close()
    database.init_db()
    stored = dict(rows("SELECT message, created_at FROM activity_log"))
    assert stored["odd line"] == "not a time"
    assert is_local_now(stored["old line"])
    assert db.app_settings.get_value(database.LOCAL_TIMESTAMPS_SETTING)


def test_cache_rows_take_the_time_of_the_history_item_that_wrote_them(db_file):
    conn = old_database(db_file)
    conn.execute("INSERT INTO instances (name, type, url, api_key) VALUES ('Sonarr', 'sonarr', 'http://127.0.0.1:9', 'x')")
    conn.execute("INSERT INTO search_history (instance_id, instance_name, skill, started_at) "
                 "VALUES (1, 'Sonarr', 'search_missing', datetime('now','localtime'))")
    conn.execute("INSERT INTO search_history_items (run_id, title, item_type, cache_key, created_at) "
                 "VALUES (1, 'E1', 'episode', 'ep:1', datetime('now','localtime','-3 hours'))")
    # Written through the UTC default by the history item above.
    conn.execute("INSERT INTO searched_items (instance_id, cache_key, title, item_type, history_item_id) "
                 "VALUES (1, 'ep:1', 'E1', 'episode', 1)")
    # No known writer (before 0.9.0, or its history was cleared): left as it is.
    conn.execute("INSERT INTO searched_items (instance_id, cache_key, title, item_type, searched_at) "
                 "VALUES (1, 'ep:2', 'E2', 'episode', '2026-01-01 10:00:00')")
    conn.commit()
    conn.close()
    database.init_db()
    [(item_time,)] = rows("SELECT created_at FROM search_history_items")
    assert rows("SELECT cache_key, searched_at FROM searched_items ORDER BY cache_key") == [
        ("ep:1", item_time), ("ep:2", "2026-01-01 10:00:00")]


def test_new_rows_are_written_in_local_time_on_an_old_database(db_file):
    old_database(db_file).close()
    database.init_db()
    inst = db.instances.create({"name": "Sonarr", "type": "sonarr", "url": "http://127.0.0.1:9",
                                "api_key": "k" * 32})
    db.activity.insert(inst["id"], "Sonarr", "info", "new line")
    run = db.history.start_run(inst["id"], "Sonarr", "search_missing")
    db.history.record_submission(run, inst["id"], "Some Show S01E01", 1, "episode", "ep:1", 101)
    db.searched.add(inst["id"], "ep:2", "Some Show S01E02", "episode")
    [(created,)] = rows("SELECT created_at FROM activity_log WHERE message='new line'")
    assert is_local_now(created)
    assert [is_local_now(s) for (s,) in rows("SELECT searched_at FROM searched_items ORDER BY cache_key")] == [
        True, True]
    [(inst_created, inst_updated)] = rows("SELECT created_at, updated_at FROM instances")
    assert is_local_now(inst_created) and is_local_now(inst_updated)
    db.instances.toggle_skill(inst["id"], "missing", False)
    [(inst_updated,)] = rows("SELECT updated_at FROM instances")
    assert is_local_now(inst_updated)


def test_a_new_database_is_never_converted(db_file):
    database.init_db()
    db.activity.insert(None, "system", "info", "line")
    [(created,)] = rows("SELECT created_at FROM activity_log")
    with database.get_db() as conn:
        conn.execute("DELETE FROM app_settings WHERE key=?", (database.LOCAL_TIMESTAMPS_SETTING,))
    database.init_db()
    assert rows("SELECT created_at FROM activity_log") == [(created,)]
