from datetime import datetime, timedelta, timezone

import pytest
import requests

from backend import database, db
from backend.agents.base import BaseAgent
from backend.config import settings
from backend.db import history
from backend.skills.base import (
    SearchResult, SubmitOutcome, finish_search_run, parse_arr_date, release_date,
    store_unsaved_submissions, submit_candidates,
)

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


def cache_keys():
    with database.get_db() as conn:
        return sorted(r[0] for r in conn.execute("SELECT cache_key FROM searched_items"))


def last_run():
    return history.query(limit=1)[0]


# ── Tests ────────────────────────────────────────────────────────────────────


def test_parse_arr_date_handles_z_offset_and_garbage():
    assert parse_arr_date("2026-09-30T10:00:00Z") == datetime(2026, 9, 30, 10, tzinfo=timezone.utc)
    assert parse_arr_date("2026-09-30T10:00:00") == datetime(2026, 9, 30, 10, tzinfo=timezone.utc)
    assert parse_arr_date("") is None
    assert parse_arr_date("not a date") is None
    assert parse_arr_date(None) is None


def test_radarr_release_is_the_earlier_home_release_then_cinema():
    both = {"digitalRelease": "2026-08-01T00:00:00Z", "physicalRelease": "2026-10-01T00:00:00Z",
            "inCinemas": "2026-06-01T00:00:00Z"}
    assert release_date(both, "radarr") == datetime(2026, 8, 1, tzinfo=timezone.utc)
    only_cinema = {"inCinemas": "2026-06-01T00:00:00Z"}
    assert release_date(only_cinema, "radarr") == datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert release_date({"airDateUtc": "2026-05-01T20:00:00Z"}, "sonarr") == datetime(2026, 5, 1, 20, tzinfo=timezone.utc)
    assert release_date({}, "sonarr") is None


def fire_with(results):
    queue = list(results)

    def fire(candidate):
        return queue.pop(0)

    return fire


def ok(i, command_id=True):
    return SearchResult(True, f"T{i}", "episode", f"ep:{i}", i, 500 + i if command_id else None)


def failed(i):
    return SearchResult(False, f"T{i}", "episode", "", i, None, "500 Server Error")


def test_successes_are_stored_failures_recorded_and_slots_returned(db_path):
    inst = make_instance(rate_cap=2)
    agent = agent_for(inst)
    run = history.start_run(inst["id"], inst["name"], "search_missing")
    outcome = submit_candidates("search_missing", agent, run, [1, 2, 3],
                                fire_with([failed(1), ok(2), ok(3)]), 0)
    assert outcome.triggered == 2
    assert outcome.errors == ["T1: 500 Server Error"]
    assert agent.get_rate_used() == 2
    assert history.get_item_statuses(run) == ["failed", "submitted", "submitted"]
    assert cache_keys() == ["ep:2", "ep:3"]


def test_rate_cap_stops_the_loop(db_path):
    inst = make_instance(rate_cap=1)
    agent = agent_for(inst)
    run = history.start_run(inst["id"], inst["name"], "search_missing")
    outcome = submit_candidates("search_missing", agent, run, [1, 2], fire_with([ok(1), ok(2)]), 0)
    assert (outcome.triggered, outcome.rate_capped) == (1, True)


def test_abort_during_the_pause_stops_the_loop(db_path):
    inst = make_instance()
    agent = agent_for(inst)
    run = history.start_run(inst["id"], inst["name"], "search_missing")

    def fire(candidate):
        agent.request_abort()
        return ok(candidate)

    outcome = submit_candidates("search_missing", agent, run, [1, 2, 3], fire, delay=5)
    assert (outcome.triggered, outcome.stopped) == (1, True)


def test_missing_command_id_is_stored_but_not_cached(db_path):
    inst = make_instance()
    agent = agent_for(inst)
    run = history.start_run(inst["id"], inst["name"], "search_missing")
    submit_candidates("search_missing", agent, run, [1], fire_with([ok(1, command_id=False)]), 0)
    assert history.get_item_statuses(run) == ["expired"]
    assert cache_keys() == []


def test_store_failure_after_a_sent_command_still_counts_it(db_path, monkeypatch):
    # The command is out when record_submission fails; it must still count and
    # the log must name the command id (B2).
    inst = make_instance()
    agent = agent_for(inst)
    run = history.start_run(inst["id"], inst["name"], "search_missing")

    def broken(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(db.history, "record_submission", broken)
    outcome = submit_candidates("search_missing", agent, run, [1], fire_with([ok(1)]), 0)
    assert outcome.triggered == 1 and outcome.errors == []
    assert agent.get_rate_used() == 1
    logged = [r["message"] for r in db.activity.query(include_debug=True, limit=20)]
    assert any("Command 501" in m and "could not be stored" in m for m in logged)


def test_store_failure_stops_the_run_as_an_error(db_path, monkeypatch):
    # After a sent command could not be stored nothing more is sent: every
    # further command would be just as untracked (B2 follow-up).
    inst = make_instance()
    agent = agent_for(inst)
    run = history.start_run(inst["id"], inst["name"], "search_missing")

    def broken(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(db.history, "record_submission", broken)
    fired = []

    def fire(candidate):
        fired.append(candidate)
        return ok(candidate)

    outcome = submit_candidates("search_missing", agent, run, [1, 2, 3], fire, 0)
    assert fired == [1]
    assert outcome.triggered == 1
    assert "Command 501" in outcome.store_error
    status = finish_search_run("search_missing", agent, run, 3, outcome)
    assert status == "error"
    assert "could not be stored" in last_run()["error_message"]


def test_unstored_submission_of_a_deleted_run_is_filed_under_the_current_run(db_path, monkeypatch):
    # The run the command belonged to may be gone by the time the database
    # works again (history cleared); the entry must not stay queued for good.
    inst = make_instance()
    agent = agent_for(inst)
    first = history.start_run(inst["id"], inst["name"], "search_missing")
    store = history.record_submission

    def broken(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(db.history, "record_submission", broken)
    submit_candidates("search_missing", agent, first, [1], fire_with([ok(1)]), 0)
    monkeypatch.setattr(db.history, "record_submission", store)
    history.finish_run(first, 1, 1, "error", "stored nothing")
    history.clear()

    second = history.start_run(inst["id"], inst["name"], "search_missing")
    store_unsaved_submissions("search_missing", agent, second)
    assert history.get_item_statuses(second) == ["submitted"]
    assert cache_keys() == ["ep:1"]
    third = history.start_run(inst["id"], inst["name"], "search_missing")
    store_unsaved_submissions("search_missing", agent, third)
    assert history.get_item_statuses(third) == []


def test_run_status_rules(db_path):
    inst = make_instance()
    agent = agent_for(inst)

    def finish(outcome, notes=()):
        run = history.start_run(inst["id"], inst["name"], "search_missing")
        status = finish_search_run("search_missing", agent, run, 3, outcome, notes)
        return status, last_run()["error_message"]

    assert finish(SubmitOutcome(errors=["A: x", "B: y"]))[0] == "error"
    status, message = finish(SubmitOutcome(triggered=2, errors=["A: x"]))
    assert status == "success" and message.startswith("1 of 3 submission(s) failed")
    assert finish(SubmitOutcome(stopped=True))[0] == "error"
    assert finish(SubmitOutcome(), ["cutoff list: down"]) == ("success", "cutoff list: down")
    assert agent.state["last_verified"] == 0
    assert agent.state["last_triggered"] == 0
