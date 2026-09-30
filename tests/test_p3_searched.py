import os
import time
from datetime import datetime, timezone

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


def put(instance_id, key, searched_at):
    sql("INSERT INTO searched_items (instance_id, cache_key, title, item_type, searched_at) "
        "VALUES (?, ?, ?, 'episode', ?)", (instance_id, key, key, searched_at))


def local(delta_hours=0):
    return datetime.fromtimestamp(time.time() - delta_hours * 3600).strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture
def berlin():
    # Not via monkeypatch.undo(): that would also undo db_path's
    # settings.database_url and point later statements at ./data.
    old = os.environ.get("TZ")
    os.environ["TZ"] = "Europe/Berlin"
    time.tzset()
    yield
    if old is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old
    time.tzset()


def test_local_timestamps_are_converted_to_utc(berlin):
    assert db.searched.local_to_utc("2026-09-30 12:00:00") == datetime(2026, 9, 30, 10, tzinfo=timezone.utc)
    assert db.searched.local_to_utc("2026-01-15 12:00:00") == datetime(2026, 1, 15, 11, tzinfo=timezone.utc)


def test_lookup_returns_utc_times_of_present_keys(db_path, berlin):
    inst = make_instance()
    put(inst["id"], "ep:1", "2026-09-30 12:00:00")
    put(inst["id"], "sea:5:1", "2026-09-29 08:30:00")
    found = db.searched.lookup_many(inst["id"], ["ep:1", "sea:5:1", "ser:5", "", "ep:1"])
    assert found == {
        "ep:1": datetime(2026, 9, 30, 10, tzinfo=timezone.utc),
        "sea:5:1": datetime(2026, 9, 29, 6, 30, tzinfo=timezone.utc),
    }


def test_lookup_respects_the_retry_window(db_path):
    inst = make_instance()
    put(inst["id"], "ep:old", local(30))
    put(inst["id"], "ep:new", local(1))
    assert set(db.searched.lookup_many(inst["id"], ["ep:old", "ep:new"], retry_hours=24)) == {"ep:new"}
    assert set(db.searched.lookup_many(inst["id"], ["ep:old", "ep:new"], retry_hours=0)) == {"ep:old", "ep:new"}


def test_lookup_of_many_keys_uses_one_connection(db_path, monkeypatch):
    inst = make_instance()
    with database.get_db() as conn:
        conn.executemany(
            "INSERT INTO searched_items (instance_id, cache_key, title, item_type) VALUES (?, ?, 'x', 'episode')",
            [(inst["id"], f"ep:{i}") for i in range(1200)],
        )
    opened = []
    real = database.get_connection
    monkeypatch.setattr(database, "get_connection", lambda: opened.append(1) or real())
    found = db.searched.lookup_many(inst["id"], [f"ep:{i}" for i in range(1500)])
    assert len(found) == 1200
    assert len(opened) == 1


def test_purge_expired_only_with_a_retry_window(db_path):
    inst = make_instance()
    put(inst["id"], "ep:old", local(30))
    put(inst["id"], "ep:new", local(1))
    assert db.searched.purge_expired(inst["id"], 0) == 0
    assert db.searched.purge_expired(inst["id"], 24) == 1
    assert [r[0] for r in sql("SELECT cache_key FROM searched_items")] == ["ep:new"]


def test_count_filtered(db_path):
    inst = make_instance()
    put(inst["id"], "ep:1", local())
    sql("INSERT INTO searched_items (instance_id, cache_key, title, item_type) VALUES (?, 'mov:1', 'm', 'movie')",
        (inst["id"],))
    assert db.searched.count_filtered() == 2
    assert db.searched.count_filtered(instance_id=inst["id"], item_type="movie") == 1


def test_app_settings_roundtrip(db_path):
    assert db.app_settings.get_value("token_version") is None
    db.app_settings.set_value("token_version", "1")
    db.app_settings.set_value("token_version", "2")
    assert db.app_settings.get_value("token_version") == "2"
    db.app_settings.delete_value("token_version")
    assert db.app_settings.get_value("token_version") is None
