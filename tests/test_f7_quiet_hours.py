"""0.10.1: the card's quiet status comes from the clock (inside the quiet
hours "quiet", outside the normal state), not from the last skipped or
finished run. A run skipped for quiet hours writes one info line per quiet
window; further skips stay debug lines."""
from datetime import datetime, timedelta

import pytest

from backend import database, db
from backend.config import settings
from tests.test_p1_control import fake_orchestrator, make_instance, prepared_agent, wait_until


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "missingarr.db"
    monkeypatch.setattr(settings, "database_url", str(path))
    monkeypatch.setattr(database, "_cached_secret_key", None)
    database.init_db()
    return path


def window(inside: bool) -> dict:
    """Quiet hours around now, or an hour from now."""
    now = datetime.now()
    start, end = (now - timedelta(minutes=5), now + timedelta(minutes=5)) if inside else (
        now + timedelta(minutes=60), now + timedelta(minutes=90))
    return {"quiet_start": start.strftime("%H:%M"), "quiet_end": end.strftime("%H:%M")}


@pytest.fixture
def started():
    """start(inst) -> (orchestrator, agent) once the agent's scheduler runs."""
    orchestrators = []

    def start(inst):
        orch = fake_orchestrator()
        orchestrators.append(orch)
        orch.start_agent(inst["id"])
        agent = orch._agents[inst["id"]]
        wait_until(lambda: agent.state["status"] == "scheduled")
        return orch, agent

    yield start
    for orch in orchestrators:
        orch.stop_all()


def info_lines():
    return [r["message"] for r in db.activity.query(limit=100) if r["skill"] in ("search_missing", "search_upgrades")]


def test_the_card_is_quiet_as_soon_as_the_window_starts(db_path, started):
    inst = make_instance(**window(inside=True))
    orch, _ = started(inst)
    assert orch.get_agent_state(inst["id"])["status"] == "quiet"


def test_the_card_leaves_quiet_when_the_window_ends(db_path, started):
    inst = make_instance(**window(inside=False))
    orch, agent = started(inst)
    agent.state["status"] = "quiet"          # a run skipped while the window lasted
    assert orch.get_agent_state(inst["id"])["status"] == "scheduled"


@pytest.mark.parametrize("status", ["running", "error", "off"])
def test_other_states_stay_inside_the_window(db_path, started, status):
    inst = make_instance(**window(inside=True))
    orch, agent = started(inst)
    agent.state["status"] = status
    assert orch.get_agent_state(inst["id"])["status"] == status


def test_a_skipped_run_leaves_a_running_search_running(db_path):
    """A forced search runs on into the quiet hours; scheduled searches
    skipped meanwhile leave its status RUNNING."""
    inst = make_instance(**window(inside=True))
    agent = prepared_agent(inst)
    skill = agent._get_skill("search_missing")
    seen = []

    def execute(agent_, force=False):
        skill.calls.append(force)
        for name in ("search_missing", "search_upgrades"):   # the scheduler fires meanwhile
            agent_._run_skill(name)
            seen.append(agent_.state["status"])

    skill.execute = execute
    agent._run_skill("search_missing", force=True)
    assert seen == ["running", "running"]
    assert skill.calls == [True]
    assert agent.state["status"] == "scheduled"


def test_one_info_line_per_quiet_window(db_path):
    quiet = window(inside=True)
    inst = make_instance(**quiet)
    agent = prepared_agent(inst)
    for skill in ("search_missing", "search_missing", "search_upgrades"):
        agent._run_skill(skill)
    line = f"Quiet hours {quiet['quiet_start']}–{quiet['quiet_end']}: searches are skipped until {quiet['quiet_end']}"
    assert info_lines() == [line]

    # The window ends (health_check runs every 5 minutes), the next one starts.
    with database.get_db() as conn:
        conn.execute("UPDATE instances SET quiet_start=?, quiet_end=?",
                     (window(inside=False)["quiet_start"], window(inside=False)["quiet_end"]))
    agent._run_skill("health_check")
    with database.get_db() as conn:
        conn.execute("UPDATE instances SET quiet_start=?, quiet_end=?", (quiet["quiet_start"], quiet["quiet_end"]))
    agent._run_skill("search_missing")
    assert info_lines() == [line, line]
    assert agent._get_skill("search_missing").calls == []
