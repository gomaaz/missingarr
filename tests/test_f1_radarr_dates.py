"""0.10.1: Search Missing leaves a Radarr movie out only when Radarr reports
it not available (isAvailable false) and the movie has at least one of
inCinemas, digitalRelease and physicalRelease. Without any of these dates
Radarr never reports a movie available (Minimum Availability "Released"),
so such a movie counts as available; the checked search's rules still
guard against foreign releases. The run summary is an info line whenever
it counts movies Radarr keeps back."""
import pytest

from backend import database, db
from backend.config import settings
from backend.skills.search_missing import SearchMissingSkill
from tests.test_g3_runner import THE_THING, log_rows, make_instance, the_thing_agent
from tests.test_p2_radarr_availability import film, make_radarr, run, searched
from tests.test_p2_search_missing import cache_keys, iso

ORDERS = ["random", "smart", "newest_first", "oldest_first"]
DATES = ["inCinemas", "digitalRelease", "physicalRelease"]


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "missingarr.db"
    monkeypatch.setattr(settings, "database_url", str(path))
    monkeypatch.setattr(database, "_cached_secret_key", None)
    database.init_db()
    return path


def undated(movie_id, **extra):
    """A missing movie Radarr reports not available and without any date."""
    data = {"id": movie_id, "title": f"Some Film {movie_id}", "year": 2026, "hasFile": False,
            "monitored": True, "isAvailable": False}
    data.update(extra)
    return data


def info_lines():
    return [r["message"] for r in db.activity.query(limit=200)]


@pytest.mark.parametrize("order", ORDERS)
def test_a_movie_without_any_date_is_searched_in_any_order(db_path, order):
    inst = make_radarr(search_order=order)
    agent = run(inst, [undated(1)])
    assert searched(agent) == [1]
    assert cache_keys() == ["mov:1"]


def test_empty_dates_count_as_no_date(db_path):
    inst = make_radarr()
    agent = run(inst, [undated(1, inCinemas=None, digitalRelease="", physicalRelease=None)])
    assert searched(agent) == [1]


@pytest.mark.parametrize("field", DATES)
def test_one_known_date_is_enough_to_wait_for_radarr(db_path, field):
    inst = make_radarr()
    agent = run(inst, [undated(1, **{field: iso(10)}), undated(2)])
    assert searched(agent) == [2]


def test_a_dry_run_checks_a_movie_without_any_date(db_path):
    inst = make_instance(checked_search="dry_run")
    thing = {**THE_THING, "inCinemas": None, "digitalRelease": None, "isAvailable": False}
    agent = the_thing_agent(inst, missing=[thing], movies=[thing])
    SearchMissingSkill().execute(agent)
    assert [(r["arr_id"], r["outcome"]) for r in log_rows()] == [(1, "would_grab")]


def test_the_summary_is_an_info_line_while_movies_wait_for_radarr(db_path):
    inst = make_radarr()
    run(inst, [film(1, isAvailable=False), film(2, isAvailable=True)])
    assert ("checked 2 of 2 missing item(s) on 1 page(s): 0 already searched, 0 inside the release window, "
            "0 with a file, 1 not yet available in Radarr") in info_lines()
    run(inst, [film(3, isAvailable=True)])
    assert not any(line.startswith("checked 1 of 1") for line in info_lines())
