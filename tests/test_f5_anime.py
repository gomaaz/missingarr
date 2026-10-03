"""0.10.1: Search Missing takes at most one episode of a Sonarr series of type
anime per run, in every search order, with the search command and the
checked search (dry run and active). Sonarr searches anime by absolute
episode number and the indexers answer with thousands of foreign releases.
Further anime episodes wait: they take no place of per run and are not
remembered. The one anime episode is searched alone, also in the missing
modes that search seasons or series."""
import pytest

from backend import database, db
from backend.checked_search import runner
from backend.config import settings
from backend.skills.search_missing import SearchMissingSkill
from tests.test_g3_runner import GUEST_SERIES, agent_for as checked_agent, guest_episode, log_rows
from tests.test_g3_runner import make_instance as make_checked
from tests.test_p2_search_missing import agent_for, cache_keys, episode, episode_ids, make_instance, run_missing

ORDERS = ["random", "smart", "newest_first", "oldest_first"]


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


def anime(i, series=1, aired=10):
    """An episode as the wanted list returns it with includeSeries=true."""
    return {**episode(i, series=series, aired=aired), "series": {"title": f"Show {series}", "seriesType": "anime"}}


def summary():
    messages = [r["message"] for r in db.activity.query(limit=200, include_debug=True)]
    return next(m for m in messages if "missing item(s) on" in m)


@pytest.mark.parametrize("order", ORDERS)
def test_one_anime_episode_per_run_in_any_order(db_path, order):
    inst = make_instance(search_order=order, missing_per_run=5)
    records = [anime(1), anime(2), anime(3, series=2), episode(4, series=3), episode(5, series=3)]
    agent = agent_for(inst, missing=records)
    run_missing(agent)
    searched = episode_ids(agent)
    assert sorted(i for i in searched if i > 3) == [4, 5]
    assert len([i for i in searched if i <= 3]) == 1
    assert cache_keys() == sorted(f"ep:{i}" for i in searched)


def test_waiting_anime_episodes_take_no_place_of_per_run(db_path):
    inst = make_instance(search_order="oldest_first", missing_per_run=2)
    records = [anime(1, aired=30), anime(2, aired=29), anime(3, aired=28),
               episode(4, series=3, aired=20), episode(5, series=3, aired=19)]
    first = agent_for(inst, missing=records)
    run_missing(first)
    assert episode_ids(first) == [1, 4]
    assert cache_keys() == ["ep:1", "ep:4"]
    assert summary().endswith(", 2 more anime episode(s) wait for a later run")
    second = agent_for(inst, missing=records)
    run_missing(second)
    assert episode_ids(second) == [2, 5]


@pytest.mark.parametrize("mode, broad", [
    ("season_packs", {"name": "SeasonSearch", "seriesId": 3, "seasonNumber": 1}),
    ("smart", {"name": "SeasonSearch", "seriesId": 3, "seasonNumber": 1}),
    ("show_batch", {"name": "SeriesSearch", "seriesId": 3}),
])
def test_the_anime_episode_is_searched_alone_in_every_missing_mode(db_path, mode, broad):
    # A whole anime season is missing: a season or series search would make
    # Sonarr search every aired episode of it. Other series keep the mode.
    inst = make_instance(search_order="oldest_first", missing_mode=mode, missing_per_run=5)
    season = [anime(i, aired=30 - i) for i in (1, 2, 3, 4)]
    other = [episode(11, series=3, aired=5)]
    agent = agent_for(inst, missing=season + other, episodes=season + other)
    run_missing(agent)
    assert agent.posts == [{"name": "EpisodeSearch", "episodeIds": [1]}, broad]
    assert cache_keys() == sorted(["ep:1", "sea:3:1" if mode != "show_batch" else "ser:3"])
    assert summary().endswith(", 3 more anime episode(s) wait for a later run")


@pytest.mark.parametrize("mode", ["dry_run", "active"])
def test_the_checked_search_checks_one_anime_episode_per_run(db_path, mode):
    inst = make_checked(name="Sonarr", type="sonarr", checked_search=mode)
    show = {**GUEST_SERIES, "seriesType": "anime"}
    episodes = [guest_episode(3), guest_episode(4)]
    first = checked_agent(inst, missing=episodes, episodes=episodes, series=[show])
    SearchMissingSkill().execute(first)
    assert [r["arr_id"] for r in log_rows()] == [3]
    second = checked_agent(inst, missing=episodes, episodes=episodes, series=[show])
    SearchMissingSkill().execute(second)
    assert [r["arr_id"] for r in log_rows()] == [4, 3]
