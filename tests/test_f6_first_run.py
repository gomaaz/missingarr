"""0.10.1: after a start of the agent (an instance saved or switched on, a
container restart) a search job first runs one interval after the skill's
last run started, but not sooner than 30 seconds from now; without an
earlier run one interval from now, as before. The card shows the numbers
and the last sync of the last runs right away instead of "Wanted 0"."""
from datetime import datetime, timedelta, timezone

import pytest

from backend import database, db
from backend.config import settings
from tests.test_p1_scheduler import make_instance, running_agent  # noqa: F401 (running_agent is a fixture)


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "missingarr.db"
    monkeypatch.setattr(settings, "database_url", str(path))
    monkeypatch.setattr(database, "_cached_secret_key", None)
    database.init_db()
    return path


def add_run(inst, skill, minutes_ago, status="success", wanted=0, note=None):
    """A run that started minutes_ago and finished a minute later."""
    with database.get_db() as conn:
        conn.execute(
            "INSERT INTO search_history (instance_id, instance_name, skill, started_at, finished_at, status, "
            "wanted_count, error_message) VALUES (?, ?, ?, datetime('now','localtime', ?), "
            "datetime('now','localtime', ?), ?, ?, ?)",
            (inst["id"], inst["name"], skill, f"-{minutes_ago} minutes", f"-{minutes_ago - 1} minutes",
             status, wanted, note),
        )


def first_runs(agent, inst):
    """Seconds from now to the first run of the missing and the upgrade job."""
    now = datetime.now(timezone.utc)
    return [round((agent._scheduler.get_job(f"{prefix}_{inst['id']}").next_run_time - now).total_seconds())
            for prefix in ("missing", "upgrades")]


def near(seconds, expected):
    return abs(seconds - expected) <= 5


def test_without_an_earlier_run_the_first_run_waits_one_interval(db_path, running_agent):
    inst = make_instance(interval_minutes=30)
    agent = running_agent(db.instances.get_by_id(inst["id"]))
    missing, upgrades = first_runs(agent, inst)
    assert near(missing, 30 * 60) and near(upgrades, 120 * 60)


def test_the_first_run_follows_the_last_start_of_each_skill(db_path, running_agent):
    inst = make_instance(interval_minutes=30)
    add_run(inst, "search_missing", 20)
    add_run(inst, "search_upgrades", 60)
    agent = running_agent(db.instances.get_by_id(inst["id"]))
    missing, upgrades = first_runs(agent, inst)
    assert near(missing, 10 * 60) and near(upgrades, 60 * 60)


def test_an_overdue_run_comes_30_seconds_after_the_start(db_path, running_agent):
    inst = make_instance(interval_minutes=30)
    add_run(inst, "search_missing", 180)
    agent = running_agent(db.instances.get_by_id(inst["id"]))
    missing, _ = first_runs(agent, inst)
    assert near(missing, 30)


def test_the_card_shows_the_last_runs_after_a_start(db_path, running_agent):
    inst = make_instance()
    add_run(inst, "search_missing", 70, wanted=12)
    # A paused run and a failed one name their numbers, but searched nothing.
    add_run(inst, "search_missing", 50, note="Checked search paused — indexer X")
    add_run(inst, "search_missing", 30, status="error", wanted=40, note="All 1 submission(s) failed")
    with database.get_db() as conn:
        synced = conn.execute("SELECT finished_at FROM search_history WHERE wanted_count=12").fetchone()[0]
    agent = running_agent(db.instances.get_by_id(inst["id"]))
    assert (agent.state["last_wanted"], agent.state["last_sync"]) == (40, synced[:16])


def test_a_fresh_instance_keeps_an_empty_card(db_path, running_agent):
    inst = make_instance()
    agent = running_agent(db.instances.get_by_id(inst["id"]))
    assert (agent.state["last_wanted"], agent.state["last_sync"]) == (0, None)
