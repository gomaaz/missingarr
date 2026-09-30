import threading
import time
from datetime import datetime, timedelta

import pytest

from backend import database, db
from backend.agents.base import (
    QUIET_HOURS_EXEMPT, TRIGGER_BUSY, TRIGGER_STARTED, TRIGGER_UNKNOWN_SKILL, BaseAgent,
)
from backend.agents.orchestrator import TRIGGER_NOT_FOUND, Orchestrator
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


def fake_orchestrator():
    orch = Orchestrator()
    orch._agent_class = lambda arr_type: FakeAgent
    return orch


def prepared_agent(inst):
    agent = FakeAgent(db.instances.get_by_id(inst["id"]))
    agent._skills = agent.build_skills()
    return agent


def test_trigger_reports_busy_unknown_and_started(db_path):
    inst = make_instance()
    agent = prepared_agent(inst)
    assert agent.trigger_now("does_not_exist") == TRIGGER_UNKNOWN_SKILL
    lock = agent.runtime.skill_lock("search_missing")
    lock.acquire()
    try:
        assert agent.trigger_now("search_missing") == TRIGGER_BUSY
    finally:
        lock.release()
    assert agent.trigger_now("search_missing") == TRIGGER_STARTED
    wait_until(lambda: agent._get_skill("search_missing").calls == [True])


def gate_config_reload(agent, gate):
    """Hold every run of this agent in its config reload, i.e. before
    _run_skill could take the skill lock itself, until the gate opens."""
    reload = agent._load_fresh_config

    def held():
        gate.wait(5)
        return reload()

    agent._load_fresh_config = held
    return agent


def trigger_twice_at_once(trigger):
    barrier = threading.Barrier(2)
    results = []

    def call():
        barrier.wait(5)
        results.append(trigger())

    callers = [threading.Thread(target=call) for _ in range(2)]
    for caller in callers:
        caller.start()
    for caller in callers:
        caller.join(5)
    return sorted(results)


def test_two_triggers_at_once_start_exactly_one_run(db_path):
    agent = prepared_agent(make_instance())
    gate = threading.Event()
    gate_config_reload(agent, gate)
    results = trigger_twice_at_once(lambda: agent.trigger_now("search_missing"))
    gate.set()
    assert results == sorted([TRIGGER_STARTED, TRIGGER_BUSY])
    wait_until(lambda: agent._get_skill("search_missing").calls == [True])
    wait_until(lambda: not agent.runtime.busy_skills())
    assert agent._get_skill("search_missing").calls == [True]


def test_two_force_runs_at_once_on_a_disabled_instance_start_exactly_one(db_path):
    inst = make_instance(enabled=False)
    orch = fake_orchestrator()
    gate = threading.Event()
    created = []
    make_agent = orch._make_agent

    def gated_agent(config):
        agent = gate_config_reload(make_agent(config), gate)
        created.append(agent)
        return agent

    orch._make_agent = gated_agent
    results = trigger_twice_at_once(lambda: orch.trigger(inst["id"], "search_missing"))
    gate.set()
    assert results == sorted([TRIGGER_STARTED, TRIGGER_BUSY])
    wait_until(lambda: inst["id"] not in orch._adhoc)
    assert not orch._runtime(inst["id"]).busy_skills()
    assert sorted(len(agent._get_skill("search_missing").calls) for agent in created) == [0, 1]


def test_a_trigger_skipped_by_its_gate_frees_the_skill(db_path):
    agent = prepared_agent(make_instance(search_missing_enabled=False))
    assert agent.trigger_now("search_missing", force=False) == TRIGGER_STARTED
    wait_until(lambda: not agent.runtime.busy_skills())
    assert agent._get_skill("search_missing").calls == []
    assert agent.trigger_now("search_missing") == TRIGGER_STARTED
    wait_until(lambda: agent._get_skill("search_missing").calls == [True])


def test_orchestrator_trigger_for_unknown_instance(db_path):
    assert fake_orchestrator().trigger(999, "search_missing") == TRIGGER_NOT_FOUND


def test_force_run_no_longer_waits_for_a_running_skill(db_path):
    agent = prepared_agent(make_instance())
    lock = agent.runtime.skill_lock("search_missing")
    lock.acquire()
    try:
        started = time.monotonic()
        agent._run_skill("search_missing", force=True)
        assert time.monotonic() - started < 0.5
    finally:
        lock.release()
    assert agent._get_skill("search_missing").calls == []


def test_verification_runs_during_quiet_hours_but_searches_do_not(db_path):
    now = datetime.now()
    start = (now - timedelta(minutes=5)).strftime("%H:%M")
    end = (now + timedelta(minutes=5)).strftime("%H:%M")
    inst = make_instance(quiet_start=start, quiet_end=end)
    agent = prepared_agent(inst)
    assert set(QUIET_HOURS_EXEMPT) == {"health_check", "verify_commands"}
    agent._run_skill("verify_commands")
    agent._run_skill("search_missing")
    assert agent._get_skill("verify_commands").calls == [False]
    assert agent._get_skill("search_missing").calls == []


def test_stop_aborts_running_work_but_reload_does_not(db_path):
    inst = make_instance()
    orch = fake_orchestrator()
    orch.start_agent(inst["id"])
    old = orch._agents[inst["id"]]
    orch.reload_agent(inst["id"])
    assert old.stop_requested() is False
    new = orch._agents[inst["id"]]
    orch.stop_agent(inst["id"])
    assert new.stop_requested() is True
    assert new.wait_or_stop(5) is True


def test_a_run_skipped_for_quiet_hours_moves_the_countdown_on(db_path):
    now = datetime.now()
    inst = make_instance(interval_minutes=1,
                         quiet_start=(now - timedelta(minutes=5)).strftime("%H:%M"),
                         quiet_end=(now + timedelta(minutes=5)).strftime("%H:%M"))
    agent = FakeAgent(db.instances.get_by_id(inst["id"]))
    agent.start()
    wait_until(lambda: agent.state["status"] == "scheduled")
    try:
        agent.state["next_run_at"] = "2000-01-01T00:00:00+00:00"   # the fire time that just passed
        agent._run_skill("search_missing")
        job = agent._scheduler.get_job(f"missing_{inst['id']}")
        assert agent.state["status"] == "quiet"
        assert agent.state["next_run_at"] == job.next_run_time.isoformat()
        assert agent._get_skill("search_missing").calls == []
    finally:
        agent.stop()


def test_wait_or_stop_times_out_without_abort(db_path):
    agent = prepared_agent(make_instance())
    started = time.monotonic()
    assert agent.wait_or_stop(0.05) is False
    assert time.monotonic() - started >= 0.05


class SlowSkill(FakeSkill):
    def execute(self, agent, force=False):
        self.calls.append(force)
        agent.wait_or_stop(5)   # a long search that ends early on abort


class SlowAgent(BaseAgent):
    def build_skills(self):
        return [SlowSkill(n) for n in
                ("search_missing", "search_upgrades", "health_check", "verify_commands")]


def test_disabling_aborts_every_throwaway_force_agent(db_path):
    inst = make_instance(enabled=False)
    orch = Orchestrator()
    orch._agent_class = lambda arr_type: SlowAgent
    assert orch.trigger(inst["id"], "search_missing") == TRIGGER_STARTED
    assert orch.trigger(inst["id"], "search_upgrades") == TRIGGER_STARTED
    adhocs = set(orch._adhoc[inst["id"]])
    assert len(adhocs) == 2
    orch.stop_agent(inst["id"])
    assert all(adhoc.stop_requested() for adhoc in adhocs)
    wait_until(lambda: not orch._runtime(inst["id"]).busy_skills(), timeout=2)


def test_finished_throwaway_agents_are_forgotten(db_path):
    inst = make_instance(enabled=False)
    orch = fake_orchestrator()
    assert orch.trigger(inst["id"], "search_missing") == TRIGGER_STARTED
    wait_until(lambda: inst["id"] not in orch._adhoc)


def test_forget_instance_waits_for_running_skills(db_path):
    inst = make_instance()
    orch = fake_orchestrator()
    orch.start_agent(inst["id"])
    lock = orch._runtime(inst["id"]).skill_lock("search_missing")
    lock.acquire()
    threading.Timer(0.3, lock.release).start()
    started = time.monotonic()
    orch.forget_instance(inst["id"], wait_seconds=3)
    assert time.monotonic() - started >= 0.25
    assert inst["id"] not in orch._runtimes
    assert not orch.is_running(inst["id"])


def test_refresh_config_updates_a_running_agent(db_path):
    inst = make_instance(search_upgrades_enabled=False)
    orch = fake_orchestrator()
    orch.start_agent(inst["id"])
    agent = orch._agents[inst["id"]]
    wait_until(lambda: agent.state["status"] == "scheduled")
    db.instances.toggle_skill(inst["id"], "upgrades", True)
    orch.refresh_config(inst["id"])
    assert agent.config["search_upgrades_enabled"] == 1
    assert orch._agents[inst["id"]] is agent
    orch.stop_all()
