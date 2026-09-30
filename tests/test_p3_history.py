import sqlite3

import pytest

from backend import database, db
from backend.config import settings
from backend.db import history


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


class FailOn:
    """Connection proxy that fails the first statement containing `needle`."""

    def __init__(self, conn, needle):
        self._conn = conn
        self._needle = needle

    def execute(self, statement, params=()):
        if self._needle in statement:
            raise sqlite3.OperationalError(f"injected failure at {self._needle}")
        return self._conn.execute(statement, params)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def new_run(instance, skill="search_missing"):
    return history.start_run(instance["id"], instance["name"], skill)


def set_run(run_id, **fields):
    assignments = ", ".join(f"{k}=?" for k in fields)
    sql(f"UPDATE search_history SET {assignments} WHERE id=?", (*fields.values(), run_id))


def cache_keys():
    return [r[0] for r in sql("SELECT cache_key FROM searched_items ORDER BY cache_key")]


def test_submission_with_command_id_is_stored_and_cached(db_path):
    inst = make_instance()
    run = new_run(inst)
    item_id = history.record_submission(run, inst["id"], "Show S01E01", 11, "episode", "ep:11", 501)
    row = sql("SELECT command_status, command_id FROM search_history_items WHERE id=?", (item_id,))[0]
    assert tuple(row) == ("submitted", 501)
    assert cache_keys() == ["ep:11"]


def test_submission_without_command_id_is_not_cached(db_path):
    inst = make_instance()
    run = new_run(inst)
    history.record_submission(run, inst["id"], "Show S01E01", 11, "episode", "ep:11", None)
    assert sql("SELECT command_status FROM search_history_items")[0][0] == "expired"
    assert cache_keys() == []


def test_submission_is_all_or_nothing(db_path, monkeypatch):
    inst = make_instance()
    run = new_run(inst)
    real = database.get_connection
    monkeypatch.setattr(database, "get_connection",
                        lambda: FailOn(real(), "INSERT INTO searched_items"))
    with pytest.raises(sqlite3.OperationalError):
        history.record_submission(run, inst["id"], "Show S01E01", 11, "episode", "ep:11", 501)
    # Restore only this attribute; monkeypatch.undo() would also reset
    # settings.database_url to ./data and make sql() touch the wrong file.
    monkeypatch.setattr(database, "get_connection", real)
    assert sql("SELECT COUNT(*) FROM search_history_items")[0][0] == 0


def test_failed_submission_is_recorded_without_cache(db_path):
    inst = make_instance()
    run = new_run(inst)
    history.record_failed_submission(run, "Show S01E02", 12, "episode")
    row = sql("SELECT command_status, command_id, verified_at FROM search_history_items")[0]
    assert row["command_status"] == "failed"
    assert row["command_id"] is None
    assert row["verified_at"] is not None
    assert cache_keys() == []
    assert history.get_item_statuses(run) == ["failed"]


def test_interrupted_runs_are_closed_at_startup(db_path):
    inst = make_instance()
    with_items = new_run(inst)
    history.record_submission(with_items, inst["id"], "A", 1, "episode", "ep:1", 1)
    history.record_submission(with_items, inst["id"], "B", 2, "episode", "ep:2", 2)
    history.record_failed_submission(with_items, "C", 3, "episode")
    empty = new_run(inst)
    finished = new_run(inst)
    history.finish_run(finished, 0, 0, "success")

    assert history.close_interrupted_runs() == 2

    runs = {r["id"]: r for r in history.query(limit=10)}
    assert runs[with_items]["status"] == "pending"
    assert runs[with_items]["triggered_count"] == 2
    assert runs[with_items]["finished_at"] is not None
    assert runs[empty]["status"] == "error"
    assert runs[empty]["error_message"] == "Interrupted by restart"
    assert runs[finished]["status"] == "success"


def test_clear_keeps_open_runs_and_their_items(db_path):
    inst = make_instance()
    open_run = new_run(inst)
    history.record_submission(open_run, inst["id"], "A", 1, "episode", "ep:1", 1)
    history.finish_run(open_run, 1, 1, "success")  # becomes pending
    running = new_run(inst)
    done = new_run(inst)
    history.finish_run(done, 0, 0, "success")

    assert history.clear() == {"deleted": 1, "kept_open": 2}
    assert {r["id"] for r in history.query(limit=10)} == {open_run, running}
    assert sql("SELECT COUNT(*) FROM search_history_items")[0][0] == 1


def test_purge_old_runs_only_touches_old_finished_runs(db_path):
    inst = make_instance()
    old_done = new_run(inst)
    history.record_submission(old_done, inst["id"], "A", 1, "episode", "ep:1", None)
    history.finish_run(old_done, 1, 1, "error", "boom")
    old_pending = new_run(inst)
    history.finish_run(old_pending, 0, 0, "success")
    set_run(old_pending, status="pending")
    recent = new_run(inst)
    history.finish_run(recent, 0, 0, "success")
    for run in (old_done, old_pending):
        sql("UPDATE search_history SET started_at=datetime('now','localtime','-400 days') WHERE id=?", (run,))

    assert history.purge_old_runs(inst["id"], 0) == 0
    assert history.purge_old_runs(inst["id"], 365) == 1
    assert {r["id"] for r in history.query(limit=10)} == {old_pending, recent}
    assert sql("SELECT COUNT(*) FROM search_history_items")[0][0] == 0


def test_latest_verification_ignores_running_runs(db_path):
    inst = make_instance()
    done = new_run(inst)
    history.finish_run(done, 3, 3, "error")
    set_run(done, verified_count=2)
    new_run(inst)  # still running
    latest = history.get_latest_run_verification(inst["id"])
    assert (latest["id"], latest["triggered_count"], latest["verified_count"]) == (done, 3, 2)


def test_item_listing_filters_counts_and_pages(db_path):
    sonarr = make_instance(name="Sonarr")
    radarr = make_instance(name="Radarr", type="radarr")
    missing = new_run(sonarr)
    upgrades = new_run(radarr, "search_upgrades")
    for i in range(5):
        history.record_submission(missing, sonarr["id"], f"Show 100% S01E0{i}", i, "episode", f"ep:{i}", i)
    history.record_submission(upgrades, radarr["id"], "Movie (2020)", 9, "movie", "upg:9", 9)

    assert history.count_items_flat() == 6
    assert history.count_items_flat(skill="search_upgrades") == 1
    assert history.count_items_flat(instance_id=sonarr["id"], item_type="episode") == 5
    assert history.count_items_flat(search="movie") == 1
    assert history.count_items_flat(search="100%") == 5
    assert history.count_items_flat(search="100_") == 0
    assert history.count_items_flat(search="radarr") == 1

    page = history.query_items_flat(instance_id=sonarr["id"], limit=2, offset=2)
    assert len(page) == 2
    assert {"item_id", "run_id", "instance_id", "error_message", "command_status"} <= set(page[0])
