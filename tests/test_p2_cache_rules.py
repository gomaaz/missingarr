from datetime import datetime, timedelta, timezone

import pytest
import requests

from backend import database, db
from backend.agents.base import BaseAgent
from backend.config import settings
from backend.db import history
from backend.skills.search_missing import SearchMissingSkill

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


def cached_days_ago(instance_id, key, days):
    cache_all(instance_id, [key], f"-{days} days")


def rule_since_days_ago(days):
    """Pretend 0.8.0 first started `days` ago (init_db wrote the marker now)."""
    with database.get_db() as conn:
        conn.execute("UPDATE app_settings SET value=datetime('now','localtime', ?) "
                     "WHERE key='ancestor_rule_since'", (f"-{days} days",))


def test_season_search_blocks_only_episodes_that_aired_before_it(db_path):
    rule_since_days_ago(30)
    inst = make_instance()
    cached_days_ago(inst["id"], "sea:1:1", 2)
    agent = agent_for(inst, missing=[episode(1, aired=5), episode(2, aired=1)])
    run_missing(agent)
    assert episode_ids(agent) == [2]


def test_series_search_still_blocks_after_a_mode_switch(db_path):
    rule_since_days_ago(30)
    inst = make_instance(missing_mode="episode")
    cached_days_ago(inst["id"], "ser:1", 2)
    agent = agent_for(inst, missing=[episode(1, aired=5), episode(2, aired=6)])
    run_missing(agent)
    assert agent.posts == []


def test_series_keys_from_before_the_update_do_not_block(db_path):
    # Live on 30.09.2026: Sonarr switched to `episode` because old show_batch
    # `ser:` rows locked 118 of 139 series. 0.8.0 must not lock them again.
    inst = make_instance(missing_mode="episode")
    cached_days_ago(inst["id"], "ser:1", 2)
    cached_days_ago(inst["id"], "sea:1:1", 2)
    agent = agent_for(inst, missing=[episode(1, aired=5), episode(2, aired=6)])
    run_missing(agent)
    assert sorted(episode_ids(agent)) == [1, 2]


def test_season_search_inside_the_release_window_does_not_block(db_path):
    # Episode aired 26 h ago; the season search ran 2 h after the air date,
    # before hours_after_release=9 made the episode a candidate.
    rule_since_days_ago(30)
    inst = make_instance(hours_after_release=9)
    cache_all(inst["id"], ["sea:1:1"], "-24 hours")
    agent = agent_for(inst, missing=[episode(1, aired=26 / 24)])
    run_missing(agent)
    assert episode_ids(agent) == [1]
    cache_all(inst["id"], ["sea:1:2"], "-1 hours")
    later = agent_for(inst, missing=[episode(5, season=2, aired=26 / 24)])
    run_missing(later)
    assert later.posts == [], "a season search after the window does block"


def test_own_key_always_blocks(db_path):
    inst = make_instance()
    cached_days_ago(inst["id"], "ep:3", 400)
    agent = agent_for(inst, missing=[episode(3, aired=1)])
    run_missing(agent)
    assert agent.posts == []


def test_force_ignores_the_cache(db_path):
    inst = make_instance()
    cached_days_ago(inst["id"], "ep:3", 1)
    agent = agent_for(inst, missing=[episode(3, aired=5)])
    run_missing(agent, force=True)
    assert episode_ids(agent) == [3]


def test_unaired_and_unmonitored_episodes_do_not_trigger_a_season_pack(db_path):
    inst = make_instance(missing_mode="season_packs")
    season = [episode(1, aired=3)]
    season += [episode(i, aired=10, has_file=True) for i in (2, 3, 4)]
    season += [episode(i, aired=-7) for i in range(5, 11)]
    season += [episode(i, aired=20, monitored=False) for i in (11, 12, 13)]
    agent = agent_for(inst, missing=[season[0]], episodes=season)
    run_missing(agent)
    assert [p["name"] for p in agent.posts] == ["EpisodeSearch"]


def test_many_missing_aired_episodes_still_trigger_a_season_pack(db_path):
    inst = make_instance(missing_mode="smart")
    season = [episode(i, aired=10) for i in (1, 2, 3, 4)]
    season += [episode(i, aired=10, has_file=True, monitored=False) for i in range(5, 11)]
    agent = agent_for(inst, missing=[season[0]], episodes=season)
    run_missing(agent)
    assert agent.posts == [{"name": "SeasonSearch", "seriesId": 1, "seasonNumber": 1}]
    assert cache_keys() == ["sea:1:1"]


def test_specials_do_not_count_for_a_series_search(db_path):
    inst = make_instance(missing_mode="show_batch")
    specials = [episode(i, season=0, aired=30) for i in range(1, 11)]
    season_one = [episode(101, aired=5)] + [episode(i, aired=40, has_file=True) for i in range(102, 111)]
    agent = agent_for(inst, missing=[season_one[0]], episodes=specials + season_one)
    run_missing(agent)
    assert [p["name"] for p in agent.posts] == ["EpisodeSearch"]


def test_unreadable_episode_list_falls_back_to_an_episode_search(db_path):
    inst = make_instance(missing_mode="season_packs")
    agent = agent_for(inst, missing=[episode(1, aired=3)],
                      get_errors={"/api/v3/episode": requests.exceptions.ConnectionError("refused")})
    run_missing(agent)
    assert [p["name"] for p in agent.posts] == ["EpisodeSearch"]


def test_one_aired_missing_episode_with_only_future_ones_is_a_season_pack(db_path):
    # Only aired, monitored episodes count: 1 of 1 is missing. The season key
    # this writes does not block the future episodes later (air-date rule).
    rule_since_days_ago(30)  # so the moved-back season key is not simply pre-0.8.0
    inst = make_instance(missing_mode="season_packs")
    season = [episode(1, aired=1)] + [episode(i, aired=-3) for i in range(2, 8)]
    agent = agent_for(inst, missing=[season[0]], episodes=season)
    run_missing(agent)
    assert [p["name"] for p in agent.posts] == ["SeasonSearch"]
    with database.get_db() as conn:
        conn.execute("UPDATE searched_items SET searched_at=datetime('now','localtime','-2 days') "
                     "WHERE cache_key='sea:1:1'")
    later = agent_for(inst, missing=[episode(2, aired=1)], episodes=season)
    run_missing(later)
    assert later.posts, "an episode that aired after the season search must not be blocked"


def test_digital_release_counts_for_the_release_window(db_path):
    inst = make_instance(name="Radarr", type="radarr", hours_after_release=9)
    out_digitally = {"id": 1, "title": "Out", "hasFile": False, "inCinemas": iso(90),
                     "digitalRelease": iso(20), "physicalRelease": iso(-60)}
    digital_later = {"id": 2, "title": "Soon", "hasFile": False, "inCinemas": iso(30),
                     "digitalRelease": iso(-10)}
    agent = agent_for(inst, missing=[out_digitally, digital_later])
    run_missing(agent)
    assert [p["movieIds"][0] for p in agent.posts] == [1]


def test_radarr_newest_first_uses_the_home_release(db_path):
    inst = make_instance(name="Radarr", type="radarr", search_order="newest_first")
    movies = [
        {"id": 3, "title": "C", "hasFile": False, "physicalRelease": iso(30)},
        {"id": 1, "title": "A", "hasFile": False, "digitalRelease": iso(3)},
        {"id": 2, "title": "B", "hasFile": False, "physicalRelease": iso(10)},
    ]
    agent = agent_for(inst, missing=movies)
    run_missing(agent)
    assert [p["movieIds"][0] for p in agent.posts] == [1, 2, 3]
