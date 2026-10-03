from datetime import datetime, timedelta, timezone

import pytest
import requests

from backend import database, db
from backend.agents.base import BaseAgent
from backend.config import settings
from backend.db import history
from backend.skills import search_missing
from backend.skills.search_missing import RANDOM_PAGE_BUDGET, SearchMissingSkill

WANTED = "/api/v3/wanted/missing"
CUTOFF = "/api/v3/wanted/cutoff"
MOVIES = "/api/v3/movie"


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "missingarr.db"
    monkeypatch.setattr(settings, "database_url", str(path))
    monkeypatch.setattr(database, "_cached_secret_key", None)
    database.init_db()
    return path


def make_instance(**fields):
    data = {
        "name": "Sonarr", "type": "sonarr", "url": "http://127.0.0.1:9", "api_key": "k" * 32,
        "seconds_between_actions": 0, "hours_after_release": 0, "rate_cap": 1000,
        "search_order": "random", "missing_mode": "episode", "missing_per_run": 5,
    }
    data.update(fields)
    return db.instances.create(data)


def iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def episode(i, series=1, season=1, aired=10, has_file=False, monitored=True):
    return {
        "id": i, "seriesId": series, "seasonNumber": season, "episodeNumber": i,
        "title": f"E{i}", "airDateUtc": iso(aired), "hasFile": has_file,
        "monitored": monitored, "series": {"title": f"Show {series}"},
    }


class FakeArr(BaseAgent):
    """A Sonarr/Radarr stand-in: paginates like *arr and records every call."""

    def __init__(self, config, missing=(), cutoff=(), movies=(), episodes=(),
                 fail_posts=(), all_posts_fail=False, command_ids=True,
                 stop_after_posts=None, get_errors=None):
        super().__init__(config)
        self.missing, self.cutoff = list(missing), list(cutoff)
        self.movies, self.episodes = list(movies), list(episodes)
        self.fail_posts, self.all_posts_fail = set(fail_posts), all_posts_fail
        self.command_ids, self.stop_after_posts = command_ids, stop_after_posts
        self.get_errors = get_errors or {}
        self.gets, self.posts = [], []

    def build_skills(self):
        return []

    @staticmethod
    def _page(records, params):
        size, page = int(params["pageSize"]), int(params["page"])
        return {"totalRecords": len(records), "records": records[(page - 1) * size: page * size]}

    def http_get(self, path, params=None):
        params = dict(params or {})
        self.gets.append((path, params))
        if path in self.get_errors:
            raise self.get_errors[path]
        if path == WANTED:
            return self._page(self.missing, params)
        if path == CUTOFF:
            return self._page(self.cutoff, params)
        if path == MOVIES:
            return list(self.movies)
        if path == "/api/v3/episode":
            found = [e for e in self.episodes if e["seriesId"] == params["seriesId"]]
            if "seasonNumber" in params:
                found = [e for e in found if e["seasonNumber"] == params["seasonNumber"]]
            return found
        if path.startswith("/api/v3/series/"):
            return {"title": "Looked up"}
        raise AssertionError(f"unexpected GET {path}")

    def http_post(self, path, body):
        self.posts.append(body)
        number = len(self.posts)
        if self.stop_after_posts is not None and number >= self.stop_after_posts:
            self.request_abort()
        if self.all_posts_fail or number in self.fail_posts:
            raise requests.exceptions.HTTPError("500 Server Error")
        return {"id": 1000 + number} if self.command_ids else {}


def agent_for(inst, **kwargs):
    return FakeArr(db.instances.get_by_id(inst["id"]), **kwargs)


def episode_ids(agent):
    return [p["episodeIds"][0] for p in agent.posts if p["name"] == "EpisodeSearch"]


def cache_keys():
    with database.get_db() as conn:
        return sorted(r[0] for r in conn.execute("SELECT cache_key FROM searched_items"))


def last_run():
    return history.query(limit=1)[0]


def cache_all(instance_id, keys, age_sql="-1 minutes"):
    with database.get_db() as conn:
        conn.executemany(
            "INSERT INTO searched_items (instance_id, cache_key, title, item_type, searched_at) "
            "VALUES (?, ?, ?, 'episode', datetime('now','localtime', ?))",
            [(instance_id, key, key, age_sql) for key in keys],
        )


# ── Tests ────────────────────────────────────────────────────────────────────


def run_missing(agent, force=False):
    SearchMissingSkill().execute(agent, force=force)


def backlog(n):
    """id 1 aired yesterday, id n aired n days ago; server returns id order."""
    return [episode(i, series=i, aired=i) for i in range(1, n + 1)]


def test_oldest_first_starts_with_the_globally_oldest(db_path):
    inst = make_instance(search_order="oldest_first")
    agent = agent_for(inst, missing=backlog(1500))
    run_missing(agent)
    assert episode_ids(agent) == [1500, 1499, 1498, 1497, 1496]
    wanted_params = [p for path, p in agent.gets if path == WANTED]
    assert {p["page"] for p in wanted_params} == {1, 2}
    # No server-side sort key; Sonarr embeds the series (profile, series type).
    assert all(p == {"page": p["page"], "pageSize": 1000, "monitored": "true", "includeSeries": "true"}
               for p in wanted_params)


def test_newest_first_starts_with_the_globally_newest(db_path):
    inst = make_instance(search_order="newest_first")
    agent = agent_for(inst, missing=list(reversed(backlog(1500))))
    run_missing(agent)
    assert episode_ids(agent) == [1, 2, 3, 4, 5]


def test_ordered_modes_reach_the_whole_backlog(db_path):
    inst = make_instance(search_order="oldest_first")
    seen = set()
    for _ in range(20):
        agent = agent_for(inst, missing=backlog(1000))
        run_missing(agent)
        seen.update(episode_ids(agent))
    assert len(seen) == 100


def test_ordered_modes_read_past_the_old_hundred_page_limit(db_path, monkeypatch):
    # Page size 10 makes the old ceiling of 100 pages 1000 records; the
    # oldest items sit behind it.
    monkeypatch.setattr(search_missing, "ORDERED_PAGE_SIZE", 10)
    inst = make_instance(search_order="oldest_first")
    agent = agent_for(inst, missing=backlog(1500))
    run_missing(agent)
    assert episode_ids(agent) == [1500, 1499, 1498, 1497, 1496]
    assert len([1 for path, _ in agent.gets if path == WANTED]) == 150
    run = last_run()
    assert run["status"] == "pending"
    assert run["error_message"] is None


def test_ordered_modes_stop_as_error_when_the_page_brake_holds(db_path, monkeypatch):
    # The page ceiling is only an emergency brake: sorting a slice of the
    # list would silently skip the rest, so the run fails visibly instead.
    monkeypatch.setattr(search_missing, "ORDERED_PAGE_SIZE", 10)
    monkeypatch.setattr(search_missing, "ORDERED_MAX_PAGES", 3)
    inst = make_instance(search_order="newest_first")
    agent = agent_for(inst, missing=backlog(50))
    run_missing(agent)
    assert agent.posts == []
    assert len([1 for path, _ in agent.gets if path == WANTED]) == 3
    run = last_run()
    assert run["status"] == "error"
    assert "more than 30 missing items" in run["error_message"]


def test_smart_puts_recent_items_from_later_pages_first(db_path):
    inst = make_instance(search_order="smart", missing_per_run=10)
    records = [episode(i, series=i, aired=100 + i) for i in range(1, 1498)]
    records += [episode(i, series=i, aired=i - 1497) for i in range(1498, 1501)]  # 1-3 days ago
    agent = agent_for(inst, missing=records)
    run_missing(agent)
    assert {1498, 1499, 1500} <= set(episode_ids(agent))
    assert len(episode_ids(agent)) == 10


def test_random_mode_reads_a_bounded_number_of_pages(db_path, monkeypatch):
    inst = make_instance(search_order="random")
    cache_all(inst["id"], [f"ep:{i}" for i in range(1, 5001)])
    lookups = []
    real = db.searched.lookup_many
    monkeypatch.setattr(db.searched, "lookup_many", lambda *a, **k: lookups.append(1) or real(*a, **k))
    agent = agent_for(inst, missing=[episode(i, series=i) for i in range(1, 5001)])
    run_missing(agent)
    wanted_gets = [1 for path, _ in agent.gets if path == WANTED]
    assert len(wanted_gets) <= 1 + RANDOM_PAGE_BUDGET
    assert len(lookups) <= RANDOM_PAGE_BUDGET
    assert agent.posts == []
    assert last_run()["status"] == "success"


def test_random_mode_still_finds_the_last_uncached_item(db_path):
    inst = make_instance(search_order="random")
    cache_all(inst["id"], [f"ep:{i}" for i in range(1, 501) if i != 250])
    agent = agent_for(inst, missing=[episode(i, series=i) for i in range(1, 501)])
    run_missing(agent)
    assert episode_ids(agent) == [250]


def test_zero_per_run_does_nothing(db_path):
    inst = make_instance(search_missing_enabled=False, missing_per_run=0)
    agent = agent_for(inst, missing=backlog(10))
    run_missing(agent)
    assert agent.gets == [] and agent.posts == []


def test_unreachable_arr_makes_the_run_an_error(db_path):
    inst = make_instance()
    agent = agent_for(inst, get_errors={WANTED: requests.exceptions.ConnectionError("refused")})
    run_missing(agent)
    assert last_run()["status"] == "error"


def test_every_post_failing_makes_the_run_an_error(db_path):
    inst = make_instance(search_order="oldest_first", missing_per_run=3)
    agent = agent_for(inst, missing=backlog(3), all_posts_fail=True)
    run_missing(agent)
    run = last_run()
    assert run["status"] == "error"
    assert run["error_message"].startswith("All 3 submission(s) failed")
    assert history.get_item_statuses(run["id"]) == ["failed", "failed", "failed"]
    assert cache_keys() == []


def test_some_posts_failing_are_named_on_the_run(db_path):
    inst = make_instance(search_order="oldest_first", missing_per_run=3)
    agent = agent_for(inst, missing=backlog(3), fail_posts={2})
    run_missing(agent)
    run = last_run()
    assert run["status"] == "pending"
    assert run["error_message"].startswith("1 of 3 submission(s) failed")
    assert agent.state["last_triggered"] == 2
    assert agent.state["last_verified"] == 0


def test_response_without_command_id_is_not_cached(db_path):
    inst = make_instance(search_order="oldest_first", missing_per_run=1)
    agent = agent_for(inst, missing=backlog(1), command_ids=False)
    run_missing(agent)
    assert cache_keys() == []
    second = agent_for(inst, missing=backlog(1))
    run_missing(second)
    assert episode_ids(second) == [1]


def test_unstored_submission_is_not_sent_again_and_stored_later(db_path, monkeypatch):
    # *arr accepted the command but the database refused it. The run stops
    # there as an error; later runs of the same instance do not send the
    # title again and store it as soon as the database works again.
    inst = make_instance(search_order="oldest_first", missing_per_run=2)
    agent = agent_for(inst, missing=backlog(4))
    store = history.record_submission

    def broken(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(db.history, "record_submission", broken)
    run_missing(agent)
    first = last_run()
    assert episode_ids(agent) == [4]
    assert first["status"] == "error"
    assert "could not be stored" in first["error_message"]

    run_missing(agent)
    second = last_run()
    assert episode_ids(agent) == [4, 3]
    assert second["status"] == "error"

    monkeypatch.setattr(db.history, "record_submission", store)
    run_missing(agent)
    assert episode_ids(agent) == [4, 3, 2, 1]
    assert cache_keys() == ["ep:1", "ep:2", "ep:3", "ep:4"]
    stored = history.get_items_for_run(first["id"])
    assert [(i["cache_key"], i["command_id"], i["command_status"]) for i in stored] == [
        ("ep:4", 1001, "submitted")
    ]
    assert [i["cache_key"] for i in history.get_items_for_run(second["id"])] == ["ep:3"]


def test_a_run_stopped_by_a_store_failure_survives_clearing_the_history(db_path, monkeypatch):
    # The run ends as an error, but the command it stored before the failure
    # still awaits its verdict. Clearing the history must not cut the link
    # from that command id to its cache key (B-L2), or a failed command would
    # keep its title blocked for good with retry_hours=0.
    inst = make_instance(search_order="oldest_first", missing_per_run=2)
    agent = agent_for(inst, missing=backlog(4))
    store = history.record_submission
    calls = []

    def second_fails(*args, **kwargs):
        calls.append(args)
        if len(calls) == 2:
            raise RuntimeError("database is locked")
        return store(*args, **kwargs)

    monkeypatch.setattr(db.history, "record_submission", second_fails)
    run_missing(agent)
    run = last_run()
    assert episode_ids(agent) == [4, 3]
    assert run["status"] == "error"

    history.clear()
    items = history.get_items_for_run(run["id"])
    assert [(i["cache_key"], i["command_status"]) for i in items] == [("ep:4", "submitted")]
    assert history.resolve_item(items[0]["id"], "failed", inst["id"], "ep:4") is True
    assert cache_keys() == []


def test_rate_cap_is_respected_and_failed_posts_give_their_slot_back(db_path):
    inst = make_instance(search_order="oldest_first", missing_per_run=3, rate_cap=1)
    agent = agent_for(inst, missing=backlog(3), fail_posts={1})
    run_missing(agent)
    assert len(agent.posts) == 2
    assert agent.get_rate_used() == 1


def test_disabling_stops_a_running_search(db_path):
    inst = make_instance(search_order="oldest_first", missing_per_run=5, seconds_between_actions=1)
    agent = agent_for(inst, missing=backlog(5), stop_after_posts=1)
    run_missing(agent)
    assert len(agent.posts) == 1
    assert "Stopped early" in last_run()["error_message"]


def test_live_like_radarr_settings_run_through(db_path):
    inst = make_instance(name="Radarr", type="radarr", missing_per_run=600,
                         rate_cap=999_999_999, interval_minutes=30)
    movies = [{"id": i, "title": f"M{i}", "year": 2020, "hasFile": False,
               "digitalRelease": iso(30)} for i in range(1, 51)]
    agent = agent_for(inst, missing=movies)
    run_missing(agent)
    assert len(agent.posts) == 50
    assert all(p["name"] == "MoviesSearch" for p in agent.posts)


def test_live_like_sonarr_settings_run_through(db_path):
    inst = make_instance(missing_per_run=4, rate_cap=300, interval_minutes=60)
    agent = agent_for(inst, missing=backlog(20))
    run_missing(agent)
    assert len(episode_ids(agent)) == 4
