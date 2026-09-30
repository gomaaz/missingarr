import sqlite3
import threading
import time

import pytest

from backend import database, db
from backend.agents.base import BaseAgent, InstanceRuntime
from backend.agents.orchestrator import Orchestrator
from backend.config import settings
from backend.skills.base import BaseSkill


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "missingarr.db"
    monkeypatch.setattr(settings, "database_url", str(path))
    monkeypatch.setattr(database, "_cached_secret_key", None)
    database.init_db()
    return path


def make_instance(**fields):
    data = {
        "name": "Sonarr", "type": "sonarr", "url": "http://127.0.0.1:9",
        "api_key": "k" * 32, "seconds_between_actions": 0,
    }
    data.update(fields)
    return db.instances.create(data)


class FakeSkill(BaseSkill):
    def __init__(self, name):
        self.name = name
        self.calls = []

    def execute(self, agent, force=False):
        self.calls.append(force)


class FakeAgent(BaseAgent):
    def build_skills(self):
        return [FakeSkill(n) for n in
                ("search_missing", "search_upgrades", "health_check", "verify_commands")]


def wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not reached in time")


class RecordingBroadcaster:
    def __init__(self):
        self.entries = []

    def broadcast(self, entry):
        self.entries.append(entry)


def config(**fields):
    return {"id": 1, "name": "Sonarr", "type": "sonarr", "url": "http://127.0.0.1:9",
            "api_key": "k" * 32, "rate_cap": 1, "rate_window_minutes": 60, **fields}


def test_concurrent_reservations_respect_the_cap():
    agent = FakeAgent(config(rate_cap=1))
    barrier = threading.Barrier(8)
    tokens = []

    def reserve():
        barrier.wait()
        tokens.append(agent.reserve_action())

    threads = [threading.Thread(target=reserve) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len([t for t in tokens if t is not None]) == 1
    assert agent.get_rate_used() == 1


def test_released_reservation_frees_the_slot():
    agent = FakeAgent(config(rate_cap=1))
    token = agent.reserve_action()
    assert agent.reserve_action() is None
    agent.release_action(token)
    assert agent.reserve_action() is not None


def test_old_actions_leave_the_window():
    agent = FakeAgent(config(rate_cap=1, rate_window_minutes=60))
    agent.runtime.action_timestamps.append(time.monotonic() - 3601)
    assert agent.reserve_action() is not None


def test_runtime_is_shared_between_agents_of_one_instance():
    runtime = InstanceRuntime()
    first = FakeAgent(config(rate_cap=2), runtime=runtime)
    first.reserve_action()
    second = FakeAgent(config(rate_cap=2), runtime=runtime)
    assert second.get_rate_used() == 1
    assert second.runtime.skill_lock("search_missing") is first.runtime.skill_lock("search_missing")


def test_rate_window_survives_reload_and_disable(db_path):
    inst = make_instance(rate_cap=5)
    orch = Orchestrator()
    orch._agent_class = lambda arr_type: FakeAgent
    orch.start_agent(inst["id"])
    old = orch._agents[inst["id"]]
    assert old.reserve_action() is not None
    orch.reload_agent(inst["id"])
    new = orch._agents[inst["id"]]
    assert new is not old
    assert new.get_rate_used() == 1
    orch.stop_agent(inst["id"])
    orch.start_agent(inst["id"])
    assert orch._agents[inst["id"]].get_rate_used() == 1
    orch.stop_all()


def test_throwaway_force_agent_uses_the_instance_runtime(db_path):
    inst = make_instance(enabled=False)
    orch = Orchestrator()
    orch._agent_class = lambda arr_type: FakeAgent
    agent = orch._make_agent(db.instances.get_by_id(inst["id"]))
    assert agent.runtime is orch._runtime(inst["id"])


def test_log_never_raises_and_still_broadcasts(db_path, monkeypatch):
    broadcaster = RecordingBroadcaster()
    agent = FakeAgent(config(), broadcaster=broadcaster)

    def locked(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(db.activity, "insert", locked)
    agent.log("debug", "search_missing", "EpisodeSearch: Show S01E01")
    assert broadcaster.entries[0]["message"] == "EpisodeSearch: Show S01E01"
    assert len(broadcaster.entries[0]["created_at"]) == 19
