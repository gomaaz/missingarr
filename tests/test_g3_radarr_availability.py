"""Checked search (dry run and active, force runs too) leaves out movies
Radarr does not report as available yet, like the command path. Radarr's
release search (GET /api/v3/release) is a user-invoked search and skips
its own availability check (AvailabilitySpecification)."""
import pytest

from backend import database
from backend.checked_search import runner
from backend.config import settings
from backend.skills.search_missing import SearchMissingSkill
from tests.test_g3_runner import (
    THE_THING, activity_messages, log_rows, make_instance, movie, pauses, searched_ids, sql,
    the_thing_agent,
)

# Not out yet: newest_first puts it before The Thing (1982).
UPCOMING = movie(2, "Some Film", 2027, isAvailable=False)


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "missingarr.db"
    monkeypatch.setattr(settings, "database_url", str(path))
    monkeypatch.setattr(database, "_cached_secret_key", None)
    database.init_db()
    return path


@pytest.fixture(autouse=True)
def no_health_settle(monkeypatch):
    """*arr refreshes its health checks within 5 s; the fake answers at once."""
    monkeypatch.setattr(runner, "HEALTH_SETTLE_SECONDS", 0, raising=False)


def two_movies(inst, upcoming=UPCOMING):
    return the_thing_agent(inst, missing=[upcoming, THE_THING], movies=[upcoming, THE_THING])


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("mode,outcome", [("dry_run", "would_grab"), ("active", "grabbed")])
def test_checked_search_leaves_out_unavailable_movies(db_path, mode, outcome, force):
    inst = make_instance(checked_search=mode, search_order="newest_first", missing_per_run=1)
    agent = two_movies(inst)
    SearchMissingSkill().execute(agent, force=force)
    # The upcoming movie comes first but takes no place of per run.
    assert searched_ids(agent) == [1]
    assert [(r["arr_id"], r["outcome"]) for r in log_rows()] == [(1, outcome)]
    assert pauses() == []
    assert sql("SELECT cache_key FROM searched_items WHERE cache_key='mov:2'") == []
    assert any("1 not yet available in Radarr" in m for m in activity_messages())


def test_a_dry_run_checks_the_movie_once_radarr_reports_it_available(db_path):
    inst = make_instance(checked_search="dry_run", search_order="newest_first", missing_per_run=1)
    first = two_movies(inst)
    SearchMissingSkill().execute(first)
    assert searched_ids(first) == [1]

    released = two_movies(inst, upcoming={**UPCOMING, "isAvailable": True})
    SearchMissingSkill().execute(released)
    assert searched_ids(released) == [2]
    assert [r["arr_id"] for r in log_rows()] == [2, 1]
