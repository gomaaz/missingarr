"""Search Missing (command path) leaves out every movie Radarr does not
report as available yet: isAvailable false in GET /api/v3/wanted/missing.
Radarr counts a search command sent through its API as user-invoked and
then searches regardless of availability (MoviesSearchService), so
missingarr has to leave such movies out itself."""
import pytest

from backend import database, db
from backend.config import settings
from backend.skills import search_missing
from backend.skills.search_missing import SearchMissingSkill
from tests.test_p2_search_missing import WANTED, FakeArr, cache_keys, episode, iso, last_run

ORDERS = ["random", "smart", "newest_first", "oldest_first"]


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "missingarr.db"
    monkeypatch.setattr(settings, "database_url", str(path))
    monkeypatch.setattr(database, "_cached_secret_key", None)
    database.init_db()
    return path


@pytest.fixture
def no_shuffle(monkeypatch):
    """Pages and records stay in the order the Wanted list returns them."""
    monkeypatch.setattr(search_missing.random, "shuffle", lambda items: None)


def make_radarr(**fields):
    data = {"name": "Radarr", "type": "radarr", "url": "http://127.0.0.1:9", "api_key": "k" * 32,
            "seconds_between_actions": 0, "hours_after_release": 0, "rate_cap": 1000,
            "search_order": "oldest_first", "missing_per_run": 10}
    data.update(fields)
    return db.instances.create(data)


def film(movie_id, days_ago=30, **extra):
    """A missing movie as the Wanted list returns it; extra may set isAvailable."""
    data = {"id": movie_id, "title": f"Some Film {movie_id}", "year": 2026, "hasFile": False,
            "monitored": True, "digitalRelease": iso(days_ago)}
    data.update(extra)
    return data


def run(inst, records, force=False):
    agent = FakeArr(db.instances.get_by_id(inst["id"]), missing=records)
    SearchMissingSkill().execute(agent, force=force)
    return agent


def searched(agent):
    return [p["movieIds"][0] for p in agent.posts if p["name"] == "MoviesSearch"]


def summary():
    """The newest run summary (_Stats.describe) in the activity log."""
    messages = [r["message"] for r in db.activity.query(limit=200, include_debug=True)]
    return next(m for m in messages if "missing item(s) on" in m)


@pytest.mark.parametrize("order", ORDERS)
def test_unavailable_movies_are_never_searched_in_any_order(db_path, order):
    inst = make_radarr(search_order=order)
    records = [film(i, days_ago=10 * i, isAvailable=i % 2 == 0) for i in range(1, 7)]
    agent = run(inst, records)
    assert sorted(searched(agent)) == [2, 4, 6]
    assert cache_keys() == ["mov:2", "mov:4", "mov:6"]


def test_unavailable_movies_take_no_place_of_per_run(db_path, monkeypatch):
    # Page size 2: the full-list read of oldest_first spans three pages.
    monkeypatch.setattr(search_missing, "ORDERED_PAGE_SIZE", 2)
    inst = make_radarr(search_order="oldest_first", missing_per_run=2)
    # 1 is the oldest; 1-3 are not available yet.
    records = [film(i, days_ago=100 - i, isAvailable=i > 3) for i in range(1, 7)]
    agent = run(inst, records)
    assert searched(agent) == [4, 5]
    assert len([1 for path, _ in agent.gets if path == WANTED]) == 3
    assert cache_keys() == ["mov:4", "mov:5"]


def test_random_order_reads_on_past_a_page_of_unavailable_movies(db_path, no_shuffle):
    # per_run 1 reads pages of 50; nothing on the first page is available yet.
    inst = make_radarr(search_order="random", missing_per_run=1)
    records = [film(i, isAvailable=False) for i in range(1, 51)] + [film(51, isAvailable=True)]
    agent = run(inst, records)
    assert searched(agent) == [51]
    # The probe (page 1, size 1), then pages 1 and 2.
    assert [p["page"] for path, p in agent.gets if path == WANTED] == [1, 1, 2]


def test_a_missing_or_null_is_available_counts_as_available(db_path):
    inst = make_radarr()
    records = [film(1, days_ago=40), film(2, days_ago=39, isAvailable=None),
               film(3, days_ago=38, isAvailable=True), film(4, days_ago=37, isAvailable=False)]
    agent = run(inst, records)
    assert searched(agent) == [1, 2, 3]
    assert summary().endswith(", 1 not yet available in Radarr")


def test_a_force_run_leaves_unavailable_movies_out_too(db_path):
    # A force run ignores Hours after release, never Radarr's availability.
    inst = make_radarr(hours_after_release=48)
    records = [film(1, days_ago=0, isAvailable=False), film(2, days_ago=0, isAvailable=True)]
    agent = run(inst, records, force=True)
    assert searched(agent) == [2]
    assert cache_keys() == ["mov:2"]


def test_hours_after_release_still_waits_and_is_checked_second(db_path):
    inst = make_radarr(hours_after_release=48)
    records = [film(1, days_ago=30, isAvailable=True), film(2, days_ago=1, isAvailable=True),
               film(3, days_ago=30, isAvailable=False), film(4, days_ago=1, isAvailable=False)]
    agent = run(inst, records)
    assert searched(agent) == [1]
    # Movie 4 is neither available nor out of the window: it counts as not available.
    assert "1 inside the release window" in summary()
    assert "2 not yet available in Radarr" in summary()


def test_the_release_window_still_counts_from_the_release_date(db_path):
    # Two separate conditions: the home release was seven days ago and
    # Radarr's Availability Delay runs out today. Once Radarr reports the
    # movie available it is searched at once; the 9 hours passed long ago
    # and do not start again when Radarr reports it available.
    inst = make_radarr(hours_after_release=9)
    first = run(inst, [film(1, days_ago=7, isAvailable=False)])
    assert first.posts == []

    second = run(inst, [film(1, days_ago=7, isAvailable=True)])
    assert searched(second) == [1]


def test_a_movie_is_searched_once_radarr_reports_it_available(db_path):
    inst = make_radarr()
    first = run(inst, [film(1, isAvailable=False)])
    assert first.posts == []
    assert cache_keys() == []
    assert last_run()["status"] == "success"

    second = run(inst, [film(1, isAvailable=True)])
    assert searched(second) == [1]
    assert cache_keys() == ["mov:1"]


def test_the_summary_counts_unavailable_movies(db_path):
    inst = make_radarr()
    run(inst, [film(1, isAvailable=False), film(2, isAvailable=False)])
    assert summary() == (
        "Nothing to search — checked 2 of 2 missing item(s) on 1 page(s): 0 already searched, "
        "0 inside the release window, 0 with a file, 2 not yet available in Radarr"
    )
    run(inst, [film(3, isAvailable=True)])
    assert summary() == (
        "checked 1 of 1 missing item(s) on 1 page(s): 0 already searched, "
        "0 inside the release window, 0 with a file"
    )


def test_sonarr_records_are_not_filtered(db_path):
    inst = make_radarr(name="Sonarr", type="sonarr")
    records = [{**episode(1), "isAvailable": False}]
    agent = run(inst, records)
    assert [p["episodeIds"] for p in agent.posts if p["name"] == "EpisodeSearch"] == [[1]]
