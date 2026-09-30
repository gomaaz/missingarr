import time
from datetime import timedelta

import pytest

from backend import database, db
from backend.agents.base import BaseAgent
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


@pytest.fixture
def running_agent():
    agents = []

    def start(config):
        agent = FakeAgent(config)
        agent.start()
        wait_until(lambda: agent.state["status"] in ("scheduled", "error"))
        agents.append(agent)
        return agent

    yield start
    for agent in agents:
        agent.stop()


def job_ids(agent):
    return sorted(job.id for job in agent._scheduler.get_jobs())


def log_messages():
    return [row["message"] for row in db.activity.query(include_debug=True, limit=500)]


def test_both_search_jobs_exist_even_when_both_skills_are_off(db_path, running_agent):
    inst = make_instance(search_missing_enabled=False, search_upgrades_enabled=False)
    agent = running_agent(db.instances.get_by_id(inst["id"]))
    i = inst["id"]
    assert job_ids(agent) == sorted([f"health_{i}", f"missing_{i}", f"upgrades_{i}", f"verify_{i}"])
    assert agent.state["next_run_at"] is None


def test_next_run_follows_the_active_search_jobs(db_path, running_agent):
    inst = make_instance(interval_minutes=30, search_missing_enabled=False,
                         search_upgrades_enabled=True)
    agent = running_agent(db.instances.get_by_id(inst["id"]))
    upgrades = agent._scheduler.get_job(f"upgrades_{inst['id']}").next_run_time
    assert agent.state["next_run_at"] == upgrades.isoformat()

    db.instances.toggle_skill(inst["id"], "missing", True)
    agent.refresh_config()
    missing = agent._scheduler.get_job(f"missing_{inst['id']}").next_run_time
    assert missing < upgrades
    assert agent.state["next_run_at"] == missing.isoformat()

    db.instances.toggle_skill(inst["id"], "missing", False)
    db.instances.toggle_skill(inst["id"], "upgrades", False)
    agent.refresh_config()
    assert agent.state["next_run_at"] is None


def test_a_skill_switched_on_later_runs_through_its_existing_job(db_path):
    inst = make_instance(search_missing_enabled=False)
    agent = FakeAgent(db.instances.get_by_id(inst["id"]))
    agent._skills = agent.build_skills()
    agent._run_skill("search_missing")
    assert agent._get_skill("search_missing").calls == []
    db.instances.toggle_skill(inst["id"], "missing", True)
    agent._run_skill("search_missing")
    assert agent._get_skill("search_missing").calls == [False]


def test_scheduler_failure_is_reported_instead_of_agent_started(db_path, monkeypatch):
    inst = make_instance()
    agent = FakeAgent(db.instances.get_by_id(inst["id"]))

    def boom():
        raise OverflowError("date value out of range")

    monkeypatch.setattr(agent, "_build_scheduler", boom)
    agent.start()
    wait_until(lambda: agent.state["status"] == "error")
    messages = log_messages()
    assert any("Scheduler failed to start" in m for m in messages)
    assert not any(m.startswith("Agent started") for m in messages)
    agent.stop()


def test_agent_started_is_logged_once_the_scheduler_runs(db_path, running_agent):
    inst = make_instance()
    agent = running_agent(db.instances.get_by_id(inst["id"]))
    wait_until(lambda: any(m.startswith("Agent started") for m in log_messages()))
    assert agent._scheduler.running


@pytest.mark.parametrize("stored,expected", [(0, 1), (-5, 1), (10_000_000_000, 10080)])
def test_stored_interval_outside_bounds_is_clamped(db_path, running_agent, stored, expected):
    inst = make_instance()
    with database.get_db() as conn:
        conn.execute("UPDATE instances SET interval_minutes=? WHERE id=?", (stored, inst["id"]))
    agent = running_agent(db.instances.get_by_id(inst["id"]))
    assert agent.state["status"] == "scheduled"
    job = agent._scheduler.get_job(f"missing_{inst['id']}")
    assert job.trigger.interval == timedelta(minutes=expected)
