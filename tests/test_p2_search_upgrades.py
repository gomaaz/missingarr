from datetime import datetime, timedelta, timezone

import pytest
import requests

from backend import database, db
from backend.agents.base import BaseAgent
from backend.config import settings
from backend.db import history
from backend.skills import search_upgrades
from backend.skills.search_upgrades import SearchUpgradesSkill

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

    def http_get(self, path, params=None, timeout=10):
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


def run_upgrades(agent, force=False):
    SearchUpgradesSkill().execute(agent, force=force)


def cutoff_episode(i):
    return {"id": i, "seriesId": i, "seasonNumber": 1, "episodeNumber": 1, "title": f"E{i}",
            "series": {"title": f"Show {i}"}}


def test_pages_beyond_ten_are_reached_and_cached_pages_are_topped_up(db_path, monkeypatch):
    inst = make_instance(search_upgrades_enabled=True, upgrades_per_run=1)
    monkeypatch.setattr(search_upgrades.random, "shuffle", lambda seq: None)
    cache_all(inst["id"], [f"upg:sea:{i}:1" for i in range(1, 701)])  # pages 1-14 of 20
    agent = agent_for(inst, cutoff=[cutoff_episode(i) for i in range(1, 1001)])
    run_upgrades(agent)
    assert agent.posts == [{"name": "SeasonSearch", "seriesId": 701, "seasonNumber": 1}]


def test_all_sources_failing_is_an_error_not_an_empty_result(db_path):
    inst = make_instance(name="Radarr", type="radarr", search_upgrades_enabled=True,
                         upgrade_source="both")
    down = requests.exceptions.ConnectionError("refused")
    agent = agent_for(inst, get_errors={CUTOFF: down, MOVIES: down})
    run_upgrades(agent)
    run = last_run()
    assert run["status"] == "error"
    assert "cutoff list" in run["error_message"] and "monitored movies" in run["error_message"]
    assert agent.state["last_sync"] is None


def test_sonarr_cutoff_list_failing_is_an_error(db_path):
    inst = make_instance(search_upgrades_enabled=True)
    agent = agent_for(inst, get_errors={CUTOFF: requests.exceptions.ReadTimeout("slow")})
    run_upgrades(agent)
    assert last_run()["status"] == "error"


def test_one_failing_source_is_named_but_the_other_is_used(db_path):
    inst = make_instance(name="Radarr", type="radarr", search_upgrades_enabled=True,
                         upgrade_source="both")
    movies = [{"id": 5, "title": "Kept", "year": 2020, "hasFile": True, "monitored": True}]
    agent = agent_for(inst, movies=movies,
                      get_errors={CUTOFF: requests.exceptions.ConnectionError("refused")})
    run_upgrades(agent)
    assert agent.posts == [{"name": "MoviesSearch", "movieIds": [5]}]
    assert "cutoff list" in last_run()["error_message"]


def test_unmonitored_movies_are_not_upgrade_candidates(db_path):
    inst = make_instance(name="Radarr", type="radarr", search_upgrades_enabled=True)
    movies = [{"id": 1, "title": "Off", "hasFile": True, "monitored": False},
              {"id": 2, "title": "On", "hasFile": True, "monitored": True}]
    agent = agent_for(inst, movies=movies)
    run_upgrades(agent)
    assert agent.posts == [{"name": "MoviesSearch", "movieIds": [2]}]


def test_failed_upgrade_post_is_recorded_and_the_run_is_an_error(db_path):
    inst = make_instance(name="Radarr", type="radarr", search_upgrades_enabled=True)
    movies = [{"id": 2, "title": "On", "hasFile": True, "monitored": True}]
    agent = agent_for(inst, movies=movies, all_posts_fail=True)
    run_upgrades(agent)
    run = last_run()
    assert run["status"] == "error"
    assert history.get_item_statuses(run["id"]) == ["failed"]
    assert cache_keys() == []


def test_unstored_upgrade_is_not_sent_again_while_it_waits(db_path, monkeypatch):
    inst = make_instance(name="Radarr", type="radarr", search_upgrades_enabled=True,
                         upgrades_per_run=1)
    monkeypatch.setattr(search_upgrades.random, "shuffle", lambda seq: None)
    movies = [{"id": i, "title": f"M{i}", "year": 2020, "hasFile": True, "monitored": True}
              for i in (1, 2)]
    agent = agent_for(inst, movies=movies)
    store = history.record_submission

    def broken(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(db.history, "record_submission", broken)
    run_upgrades(agent)
    assert last_run()["status"] == "error"
    run_upgrades(agent)
    assert agent.posts == [{"name": "MoviesSearch", "movieIds": [1]},
                           {"name": "MoviesSearch", "movieIds": [2]}]

    monkeypatch.setattr(db.history, "record_submission", store)
    run_upgrades(agent)
    assert len(agent.posts) == 2
    assert cache_keys() == ["upg:1", "upg:2"]


def test_zero_per_run_does_nothing(db_path):
    inst = make_instance(search_upgrades_enabled=False, upgrades_per_run=0)
    agent = agent_for(inst, cutoff=[cutoff_episode(1)])
    run_upgrades(agent)
    assert agent.gets == [] and agent.posts == []
