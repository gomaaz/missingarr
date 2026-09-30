import threading
import time

import pytest
import requests

from backend import database, db
from backend.agents.base import BaseAgent
from backend.agents.orchestrator import Orchestrator
from backend.config import settings
from backend.db import history
from backend.skills.verify_commands import VerifyCommandsSkill


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


class QuietAgent(BaseAgent):
    def build_skills(self):
        return []

    def http_get_raw(self, path):
        return 200, {"status": "started"}  # nothing gets settled during these tests


@pytest.fixture(autouse=True)
def fresh_housekeeping():
    VerifyCommandsSkill._last_housekeeping.clear()
    yield
    VerifyCommandsSkill._last_housekeeping.clear()


def old_cache(instance_id, key, hours):
    sql("INSERT INTO searched_items (instance_id, cache_key, title, item_type, searched_at) "
        "VALUES (?, ?, ?, 'episode', datetime('now','localtime', ?))", (instance_id, key, key, f"-{hours} hours"))


def old_run(inst, status, days):
    run = history.start_run(inst["id"], inst["name"], "search_missing")
    sql("UPDATE search_history SET status=?, finished_at=datetime('now','localtime'), "
        "started_at=datetime('now','localtime', ?) WHERE id=?", (status, f"-{days} days", run))
    return run


def run_housekeeping(inst):
    agent = QuietAgent(db.instances.get_by_id(inst["id"]))
    VerifyCommandsSkill().execute(agent)
    return agent


def activity_messages():
    return [r[0] for r in sql("SELECT message FROM activity_log WHERE skill='verify_commands'")]


def test_old_runs_and_expired_cache_rows_are_removed(db_path, monkeypatch):
    monkeypatch.setattr(settings, "history_retention_days", 365)
    inst = make_instance(retry_hours=24)
    old_cache(inst["id"], "ep:old", 30)
    old_cache(inst["id"], "ep:new", 1)
    gone = old_run(inst, "success", 400)
    kept_pending = old_run(inst, "pending", 400)
    # An open command keeps the run pending; a pending run without open items
    # would be settled by the same verify pass and then count as finished.
    history.record_submission(kept_pending, inst["id"], "Open", 1, "episode", "", 77)
    kept_recent = old_run(inst, "error", 10)

    run_housekeeping(inst)

    assert [r[0] for r in sql("SELECT cache_key FROM searched_items")] == ["ep:new"]
    assert {r["id"] for r in history.query(limit=10)} == {kept_pending, kept_recent}
    assert gone not in {r["id"] for r in history.query(limit=10)}
    assert any(m.startswith("Housekeeping: removed 1 run(s) older than 365 days and 1 expired cache entry")
               for m in activity_messages())


def test_housekeeping_runs_at_most_hourly(db_path):
    inst = make_instance(retry_hours=24)
    run_housekeeping(inst)
    old_cache(inst["id"], "ep:old", 30)
    run_housekeeping(inst)
    assert sql("SELECT COUNT(*) FROM searched_items")[0][0] == 1
    VerifyCommandsSkill._last_housekeeping.clear()
    run_housekeeping(inst)
    assert sql("SELECT COUNT(*) FROM searched_items")[0][0] == 0


def test_housekeeping_is_throttled_per_instance(db_path):
    first = make_instance(retry_hours=24)
    second = make_instance(name="Sonarr 4K", retry_hours=24)
    run_housekeeping(first)
    old_cache(second["id"], "ep:old", 30)
    run_housekeeping(second)
    assert sql("SELECT COUNT(*) FROM searched_items")[0][0] == 0


def test_permanent_cache_and_zero_retention_keep_everything(db_path, monkeypatch):
    monkeypatch.setattr(settings, "history_retention_days", 0)
    inst = make_instance(retry_hours=0)
    old_cache(inst["id"], "ep:old", 30_000)
    old_run(inst, "success", 4000)
    run_housekeeping(inst)
    assert sql("SELECT COUNT(*) FROM searched_items")[0][0] == 1
    assert len(history.query(limit=10)) == 1


def test_housekeeping_failure_is_logged_not_raised(db_path, monkeypatch):
    inst = make_instance(retry_hours=24)

    def broken(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(db.searched, "purge_expired", broken)
    run_housekeeping(inst)
    assert "Housekeeping failed: disk full" in activity_messages()


# A disabled instance has no agent and so never runs verify_commands; the
# orchestrator does its housekeeping instead, from the database alone.

def refuse_arr(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("housekeeping must not contact *arr")

    monkeypatch.setattr(requests, "get", refuse)
    monkeypatch.setattr(requests, "post", refuse)


def cache_rows():
    return sql("SELECT COUNT(*) FROM searched_items")[0][0]


def wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not reached in time")


def test_orchestrator_housekeeps_disabled_instances_without_asking_arr(db_path, monkeypatch):
    refuse_arr(monkeypatch)
    monkeypatch.setattr(settings, "history_retention_days", 365)
    inst = make_instance(enabled=False, retry_hours=24)
    old_cache(inst["id"], "ep:old", 30)
    old_run(inst, "success", 400)

    Orchestrator().housekeeping()

    assert cache_rows() == 0
    assert history.query(limit=10) == []
    assert any(m.startswith("Housekeeping: removed 1 run(s) older than 365 days and 1 expired cache entry")
               for m in activity_messages())


def test_orchestrator_housekeeping_shares_the_skill_throttle(db_path):
    inst = make_instance(retry_hours=24)
    run_housekeeping(inst)  # the agent's verify pass did it a moment ago
    old_cache(inst["id"], "ep:old", 30)
    Orchestrator().housekeeping()
    assert cache_rows() == 1


def test_housekeeping_runs_at_start_then_periodically_and_stops_with_the_app(db_path, monkeypatch):
    refuse_arr(monkeypatch)
    monkeypatch.setattr(VerifyCommandsSkill, "HOUSEKEEPING_INTERVAL_SECONDS", 0.05)
    inst = make_instance(enabled=False, retry_hours=24)
    old_cache(inst["id"], "ep:first", 30)
    orch = Orchestrator()
    orch.start_all()
    try:
        wait_until(lambda: cache_rows() == 0)
        old_cache(inst["id"], "ep:second", 30)
        wait_until(lambda: cache_rows() == 0)
    finally:
        orch.stop_all()
    assert not any(t.name == "housekeeping" and t.is_alive() for t in threading.enumerate())
