import copy
import itertools
import json
import sqlite3

import pytest
import requests
from urllib3.exceptions import MaxRetryError, NewConnectionError, ProtocolError, ReadTimeoutError

from backend import database, db
from backend.agents.base import BaseAgent
from backend.config import settings
from backend.db import history
from backend.skills.search_missing import SearchMissingSkill
from backend.skills.search_upgrades import SearchUpgradesSkill

WANTED = "/api/v3/wanted/missing"
CUTOFF = "/api/v3/wanted/cutoff"
RELEASE = "/api/v3/release"
PARSE = "/api/v3/parse"
COMMAND = "/api/v3/command"
QUALITY_PROFILES = "/api/v3/qualityprofile"
INDEXER_KEY = "PROWLARRSECRET123"
API_KEY = "ARRSECRETKEY1234567890"

PROFILES = [{"id": 1, "name": "HD", "cutoff": 7, "minFormatScore": 0,
             "formatItems": [{"format": 1, "name": "German", "score": 100}]}]
FORMATS = [{"id": 1, "name": "German", "specifications": []}]
INDEXERS = [{"id": 7, "name": "Indexer", "enableAutomaticSearch": True, "enableInteractiveSearch": True}]
# Quality and languages as GET /release reports them; the grab sends them back unchanged.
QUALITY = {"quality": {"id": 7, "name": "Bluray-1080p"}, "revision": {"version": 1, "real": 0, "isRepack": False}}
LANGUAGES = [{"id": 4, "name": "German"}]


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "missingarr.db"
    monkeypatch.setattr(settings, "database_url", str(path))
    monkeypatch.setattr(database, "_cached_secret_key", None)
    database.init_db()
    return path


def sql(statement, params=()):
    with database.get_db() as conn:
        return [tuple(r) for r in conn.execute(statement, params).fetchall()]


def make_instance(**fields):
    data = {"name": "Radarr", "type": "radarr", "url": "http://127.0.0.1:9", "api_key": API_KEY,
            "seconds_between_actions": 0, "hours_after_release": 0, "rate_cap": 1000,
            "search_order": "oldest_first", "missing_per_run": 5, "checked_search": "dry_run"}
    data.update(fields)
    return db.instances.create(data)


def movie(movie_id, title, year, **extra):
    data = {"id": movie_id, "title": title, "originalTitle": title, "alternateTitles": [], "year": year,
            "hasFile": False, "monitored": True, "digitalRelease": f"{year}-06-01T00:00:00Z",
            "qualityProfileId": 1}
    data.update(extra)
    return data


def release(title, guid, approved=True, indexer_id=7, score=100, published="2026-01-01T00:00:00Z",
            movie_id=1, series_id=None, episode_ids=()):
    """A release as GET /release returns it. Radarr maps it to movie_id;
    with series_id it is a Sonarr release mapped to that series and episodes."""
    data = {"title": title, "guid": guid, "indexerId": indexer_id, "indexer": "Indexer",
            "approved": approved, "customFormatScore": score, "size": 4_000_000_000,
            "quality": copy.deepcopy(QUALITY), "languages": copy.deepcopy(LANGUAGES),
            "movieTitles": [], "publishDate": published,
            "downloadUrl": f"http://127.0.0.1:9696/1/download?apikey={INDEXER_KEY}&link=abc",
            "infoUrl": f"http://127.0.0.1:9696/info?apikey={INDEXER_KEY}",
            "magnetUrl": f"magnet:?xt=urn:btih:abc&tr=http://127.0.0.1:9696/{INDEXER_KEY}/announce"}
    if series_id is None:
        data["mappedMovieId"] = movie_id
    else:
        data["mappedSeriesId"] = series_id
        data["mappedEpisodeInfo"] = [{"id": e, "seasonNumber": 1, "episodeNumber": e} for e in episode_ids]
    return data


def movie_grab(guid, movie_id=1, indexer_id=7):
    """The POST body of a grab with its target named (shouldOverride)."""
    return {"guid": guid, "indexerId": indexer_id, "shouldOverride": True, "quality": QUALITY,
            "languages": LANGUAGES, "movieId": movie_id}


def episode_grab(guid, series_id=10, episode_ids=(3,), indexer_id=7):
    return {"guid": guid, "indexerId": indexer_id, "shouldOverride": True, "quality": QUALITY,
            "languages": LANGUAGES, "seriesId": series_id, "episodeIds": list(episode_ids)}


def http_error(status):
    response = requests.Response()
    response.status_code = status
    return requests.exceptions.HTTPError(f"{status} answer", response=response)


def refused():
    """What requests raises when nothing listens: the request never left."""
    return requests.exceptions.ConnectionError(
        MaxRetryError(None, RELEASE, NewConnectionError(None, "Connection refused")))


def changed_profiles(score=50):
    profiles = copy.deepcopy(PROFILES)
    profiles[0]["formatItems"][0]["score"] = score
    return profiles


class FakeArr(BaseAgent):
    """Radarr/Sonarr stand-in for the checked search. parses: release title ->
    /parse answer; anything missing parses to nothing. get_errors: path
    prefix (or exact path with a trailing $) -> exception. on_get: path ->
    hook(fake) run before that GET is answered.

    Like *arr it keeps every release of a GET /release under (indexerId,
    guid), mapped to the title of the search that returned it last; a POST
    without shouldOverride grabs for that cached title, with shouldOverride
    for the title in the body (required fields checked like upstream).
    grabbed: the titles really grabbed (movie id, or (series id, episode ids))."""

    def __init__(self, config, missing=(), cutoff=(), movies=(), episodes=(), series=(), releases=None,
                 parses=None, get_errors=None, post_error=None, abort_after_parses=None, abort_on_release=False,
                 profiles=None, custom_formats=None, release_profiles=None, indexers=None, on_get=None):
        super().__init__(config)
        self.missing, self.cutoff = list(missing), list(cutoff)
        self.movies = {m["id"]: m for m in movies}
        self.episodes = {e["id"]: e for e in episodes}
        self.series = {s["id"]: s for s in series}
        self.releases = releases or {}
        self.parses = parses or {}
        self.get_errors = get_errors or {}
        self.post_error = post_error
        self.abort_after_parses = abort_after_parses
        self.abort_on_release = abort_on_release
        self.profiles = copy.deepcopy(PROFILES if profiles is None else profiles)
        self.custom_formats = copy.deepcopy(FORMATS if custom_formats is None else custom_formats)
        self.release_profiles = list(release_profiles or [])
        self.indexers = copy.deepcopy(INDEXERS if indexers is None else indexers)
        self.on_get = on_get or {}
        self.gets, self.posts, self.commands, self.timeouts = [], [], [], {}
        self.release_cache, self.grabbed = {}, []

    def build_skills(self):
        return []

    @staticmethod
    def _page(records, params):
        size, page = int(params["pageSize"]), int(params["page"])
        return {"totalRecords": len(records), "records": records[(page - 1) * size: page * size]}

    @staticmethod
    def _matches(path, pattern):
        return path == pattern[:-1] if pattern.endswith("$") else path.startswith(pattern)

    @staticmethod
    def _target(item):
        if "mappedSeriesId" in item:
            return item.get("mappedSeriesId"), tuple(e["id"] for e in item.get("mappedEpisodeInfo") or [])
        return item.get("mappedMovieId")

    def http_get(self, path, params=None, timeout=10):
        params = dict(params or {})
        self.gets.append((path, params))
        self.timeouts[path] = timeout
        if path in self.on_get:
            self.on_get[path](self)
        for pattern, error in self.get_errors.items():
            if self._matches(path, pattern):
                raise error
        if path == WANTED:
            return self._page(self.missing, params)
        if path == CUTOFF:
            return self._page(self.cutoff, params)
        if path == QUALITY_PROFILES:
            return copy.deepcopy(self.profiles)
        if path == "/api/v3/customformat":
            return copy.deepcopy(self.custom_formats)
        if path == "/api/v3/releaseprofile":
            return copy.deepcopy(self.release_profiles)
        if path == "/api/v3/indexer":
            return copy.deepcopy(self.indexers)
        if path == "/api/v3/movie":
            return list(self.movies.values())
        if path.startswith("/api/v3/movie/"):
            return self.movies[int(path.rsplit("/", 1)[1])]
        if path.startswith("/api/v3/episode/"):
            return self.episodes[int(path.rsplit("/", 1)[1])]
        if path == "/api/v3/series":
            return list(self.series.values())
        if path.startswith("/api/v3/series/"):
            return self.series[int(path.rsplit("/", 1)[1])]
        if path == RELEASE:
            if self.abort_on_release:
                self.request_abort()   # instance switched off while *arr searches
            key = params.get("movieId") or params.get("episodeId")
            answer = copy.deepcopy(self.releases.get(key, []))
            for item in answer:
                self.release_cache[(item.get("indexerId"), item.get("guid"))] = self._target(item)
            return answer
        if path == PARSE:
            parses = [p for p, _ in self.gets if p == PARSE]
            if self.abort_after_parses is not None and len(parses) >= self.abort_after_parses:
                self.request_abort()
            answer = self.parses.get(params["title"], {})
            if isinstance(answer, Exception):
                raise answer
            return answer
        raise AssertionError(f"unexpected GET {path}")

    def http_post(self, path, body, timeout=10):
        if path == COMMAND:   # the old search path (checked search off)
            self.commands.append(body)
            return {"id": 100 + len(self.commands)}
        assert path == RELEASE, path
        self.posts.append(copy.deepcopy(body))
        if self.post_error is not None:
            raise self.post_error
        key = (body.get("indexerId"), body.get("guid"))
        if key not in self.release_cache:
            raise http_error(404)        # "Couldn't find requested release in cache"
        if body.get("shouldOverride"):
            if self.config["type"] == "radarr":
                missing = body.get("movieId") is None
                target = body.get("movieId")
            else:
                missing = body.get("seriesId") is None or not body.get("episodeIds")
                target = (body.get("seriesId"), tuple(body.get("episodeIds") or ()))
            if missing or body.get("quality") is None or body.get("languages") is None:
                raise http_error(400)
        else:
            target = self.release_cache[key]
        self.grabbed.append(target)
        return dict(body)


def agent_for(inst, **kwargs):
    return FakeArr(db.instances.get_by_id(inst["id"]), **kwargs)


def radarr_parse(title, year, movie_id=None):
    return {"parsedMovieInfo": {"movieTitles": [title], "year": year},
            "movie": {"id": movie_id} if movie_id else None}


THE_THING = movie(1, "Das Ding aus einer anderen Welt", 1982, originalTitle="The Thing",
                  inCinemas="1982-06-25T00:00:00Z")
WRONG = "Das.Ding.aus.einer.anderen.Welt.1951.German.DL.1080p.BluRay.x265-GRP"
RIGHT = "The.Thing.1982.German.DL.1080p.BluRay.x264-GRP"
PARSES = {WRONG: radarr_parse("Das Ding aus einer anderen Welt", 1951),
          RIGHT: radarr_parse("The Thing", 1982, movie_id=1)}


def the_thing_agent(inst, **kwargs):
    defaults = dict(missing=[THE_THING], movies=[THE_THING],
                    releases={1: [release(WRONG, "guid-wrong"), release(RIGHT, "guid-right")]}, parses=PARSES)
    defaults.update(kwargs)
    return agent_for(inst, **defaults)


def log_rows():
    return db.checked_search_log.query(limit=100)


def last_run():
    return history.query(limit=1)[0]


def activity_messages():
    return [r["message"] for r in db.activity.query(limit=200, include_debug=True)]


def stored_fingerprint(inst, profile_id="1"):
    return db.instances.get_by_id(inst["id"])["profile_fingerprints"][profile_id]


# ── Dry run ──────────────────────────────────────────────────────────────────

def test_dry_run_grabs_nothing_and_remembers_nothing(db_path):
    inst = make_instance()
    agent = the_thing_agent(inst)
    SearchMissingSkill().execute(agent)

    assert agent.posts == []
    assert sql("SELECT COUNT(*) FROM search_history_items")[0][0] == 0
    assert sql("SELECT COUNT(*) FROM searched_items")[0][0] == 0
    [row] = log_rows()
    assert (row["mode"], row["outcome"], row["arr_pick"], row["pick"]) == ("dry_run", "would_grab", WRONG, RIGHT)
    assert [(c["verdict"], c["reasons"], c["chosen"], c["arr_choice"]) for c in row["candidates"]] == [
        ("reject", ["year"], False, True), ("pass", [], True, False)]
    assert row["profile_fingerprint"] == stored_fingerprint(inst)
    run = last_run()
    assert (run["status"], run["triggered_count"], run["error_message"]) == ("success", 0, None)
    assert "Dry run: 1 title(s) checked, 1 would grab" in activity_messages()


def test_dry_run_checks_every_title_once_per_round(db_path):
    inst = make_instance()
    SearchMissingSkill().execute(the_thing_agent(inst))
    second = the_thing_agent(inst)
    SearchMissingSkill().execute(second)
    assert [p for p, _ in second.gets if p == RELEASE] == []
    assert len(log_rows()) == 1

    db.instances.reset_dry_run(inst["id"])
    third = the_thing_agent(inst)
    SearchMissingSkill().execute(third)
    assert len([p for p, _ in third.gets if p == RELEASE]) == 1
    assert [r["dry_run_round"] for r in log_rows()] == [1, 0]


def test_force_run_in_dry_run_respects_the_round(db_path):
    inst = make_instance()
    SearchMissingSkill().execute(the_thing_agent(inst), force=True)
    forced = the_thing_agent(inst)
    SearchMissingSkill().execute(forced, force=True)
    assert [p for p, _ in forced.gets if p == RELEASE] == []
    assert len(log_rows()) == 1


def test_reset_during_a_running_dry_run_keeps_its_rows_in_the_old_round(db_path):
    inst = make_instance()
    # "Reset dry run" while *arr is still searching for the title
    agent = the_thing_agent(inst, on_get={RELEASE: lambda fake: db.instances.reset_dry_run(inst["id"])})
    SearchMissingSkill().execute(agent)
    [row] = log_rows()
    assert row["dry_run_round"] == 0
    assert db.instances.get_by_id(inst["id"])["dry_run_round"] == 1
    assert db.checked_search_log.query(current_round=True) == []
    again = the_thing_agent(inst)
    SearchMissingSkill().execute(again)
    assert len([p for p, _ in again.gets if p == RELEASE]) == 1


def test_dry_run_checks_again_after_the_rule_settings_changed(db_path):
    inst = make_instance()
    SearchMissingSkill().execute(the_thing_agent(inst))
    sql("UPDATE instances SET checked_search_settings=?", (json.dumps({"release_timeout_seconds": 300}),))
    same = the_thing_agent(inst)
    SearchMissingSkill().execute(same)
    assert [p for p, _ in same.gets if p == RELEASE] == []          # not a rule: nothing to check again
    sql("UPDATE instances SET checked_search_settings=?", (json.dumps({"year_tolerance": 2}),))
    changed = the_thing_agent(inst)
    SearchMissingSkill().execute(changed)
    assert len([p for p, _ in changed.gets if p == RELEASE]) == 1
    newest, oldest = log_rows()
    assert newest["settings_fingerprint"] != oldest["settings_fingerprint"]


def test_dry_run_checks_a_title_again_after_an_error(db_path):
    inst = make_instance()
    SearchMissingSkill().execute(the_thing_agent(inst, get_errors={RELEASE: requests.exceptions.ReadTimeout("slow")}))
    assert log_rows()[0]["outcome"] == "error"
    second = the_thing_agent(inst)
    SearchMissingSkill().execute(second)
    assert len([p for p, _ in second.gets if p == RELEASE]) == 1
    assert [r["outcome"] for r in log_rows()] == ["would_grab", "error"]


def test_dry_run_ignores_the_search_cache(db_path):
    inst = make_instance()
    db.searched.add(inst["id"], "mov:1", "The Thing", "movie")
    agent = the_thing_agent(inst)
    SearchMissingSkill().execute(agent)
    assert len(log_rows()) == 1


def test_dry_run_checks_at_most_the_configured_number_of_releases(db_path):
    inst = make_instance(checked_search_settings={"dry_run_max_releases": 1})
    agent = the_thing_agent(inst)
    SearchMissingSkill().execute(agent)
    [row] = log_rows()
    assert row["outcome"] == "no_clean_hit"
    assert [c["verdict"] for c in row["candidates"]] == ["reject", "unchecked"]
    assert len([p for p, _ in agent.gets if p == PARSE]) == 1


def test_release_search_uses_its_own_timeout(db_path):
    inst = make_instance(checked_search_settings={"release_timeout_seconds": 300})
    agent = the_thing_agent(inst)
    SearchMissingSkill().execute(agent)
    assert agent.timeouts[RELEASE] == 300
    assert agent.timeouts[PARSE] == 10


# ── Active ───────────────────────────────────────────────────────────────────

def test_active_grabs_the_first_clean_release(db_path):
    inst = make_instance(checked_search="active")
    agent = the_thing_agent(inst)
    SearchMissingSkill().execute(agent)

    assert agent.posts == [movie_grab("guid-right")]
    assert agent.grabbed == [1]
    assert sql("SELECT command_status, cache_key FROM search_history_items") == [("grabbed", "mov:1")]
    assert sql("SELECT cache_key, profile_fingerprint FROM searched_items") == [("mov:1", stored_fingerprint(inst))]
    [row] = log_rows()
    assert (row["mode"], row["outcome"], row["run_id"]) == ("active", "grabbed", last_run()["id"])
    run = last_run()
    assert (run["status"], run["triggered_count"], run["verified_count"]) == ("success", 1, 1)
    # the card shows the confirmed title at once, not after the next verification
    assert (agent.state["last_triggered"], agent.state["last_verified"]) == (1, 1)


def test_active_stops_checking_after_the_pick(db_path):
    inst = make_instance(checked_search="active")
    third = "The.Thing.1982.720p.WEB.x264-OTHER"
    agent = the_thing_agent(inst, releases={1: [release(RIGHT, "g1"), release(third, "g2")]})
    SearchMissingSkill().execute(agent)
    assert [params["title"] for p, params in agent.gets if p == PARSE] == [RIGHT]
    assert [c["verdict"] for c in log_rows()[0]["candidates"]] == ["pass", "unchecked"]


def test_an_overlapping_search_cannot_redirect_the_grab(db_path):
    # *arr keeps a release under indexer + guid for whichever search returned
    # it last. A search for another movie between check and grab maps the same
    # release to that movie; the grab still goes to the checked one.
    inst = make_instance(checked_search="active")
    other = movie(2, "Example Film", 2011)
    agent = the_thing_agent(inst, movies=[THE_THING, other],
                            releases={1: [release(RIGHT, "shared", movie_id=1)],
                                      2: [release(RIGHT, "shared", movie_id=2)]},
                            on_get={PARSE: lambda fake: fake.http_get(RELEASE, {"movieId": 2})})
    SearchMissingSkill().execute(agent)
    assert agent.release_cache[(7, "shared")] == 2
    assert agent.grabbed == [1]
    assert agent.posts == [movie_grab("shared")]


def test_an_overlapping_search_cannot_redirect_an_episode_grab(db_path):
    inst = make_instance(name="Sonarr", type="sonarr", checked_search="active")
    other_series = {**GUEST_SERIES, "id": 11, "year": 2023}
    other_episode = {**guest_episode(30), "seriesId": 11}
    right = "The.Guest.S01E03.German.1080p.WEB.x264-GRP"
    agent = agent_for(
        inst, missing=[guest_episode()], episodes=[guest_episode(), other_episode],
        series=[GUEST_SERIES, other_series],
        releases={3: [release(right, "shared", series_id=10, episode_ids=(3,))],
                  30: [release(right, "shared", series_id=11, episode_ids=(30,))]},
        parses={right: {"parsedEpisodeInfo": {"seriesTitle": "The Guest"}, "series": {"id": 10}}},
        on_get={PARSE: lambda fake: fake.http_get(RELEASE, {"episodeId": 30})},
    )
    SearchMissingSkill().execute(agent)
    assert agent.release_cache[(7, "shared")] == (11, (30,))
    assert agent.grabbed == [(10, (3,))]
    assert agent.posts == [episode_grab("shared")]


@pytest.mark.parametrize("mapping", [{"mappedMovieId": 2}, {"mappedMovieId": None}, {"languages": None},
                                     {"quality": None}],
                         ids=["other movie", "no movie", "no languages", "no quality"])
def test_a_release_not_mapped_to_this_movie_is_never_grabbed(db_path, mapping):
    inst = make_instance(checked_search="active")
    agent = the_thing_agent(inst, releases={1: [{**release(RIGHT, "g1"), **mapping}]})
    SearchMissingSkill().execute(agent)
    assert agent.posts == []
    [row] = log_rows()
    assert (row["outcome"], row["candidates"][0]["reasons"]) == ("no_clean_hit", ["not mapped to this title"])
    assert [p for p, _ in agent.gets if p == PARSE] == []


def test_a_release_mapped_to_other_episodes_is_never_grabbed(db_path):
    inst = make_instance(name="Sonarr", type="sonarr", checked_search="active")
    right = "The.Guest.S01E03.German.1080p.WEB.x264-GRP"
    agent = agent_for(inst, missing=[guest_episode()], episodes=[guest_episode()], series=[GUEST_SERIES],
                      releases={3: [release(right, "g1", series_id=10, episode_ids=(4,))]},
                      parses={right: {"parsedEpisodeInfo": {"seriesTitle": "The Guest"}, "series": {"id": 10}}})
    SearchMissingSkill().execute(agent)
    assert agent.posts == []
    assert log_rows()[0]["candidates"][0]["reasons"] == ["not mapped to this title"]


def test_active_without_a_clean_release_marks_the_title_searched(db_path):
    inst = make_instance(checked_search="active")
    agent = the_thing_agent(inst, releases={1: [release(WRONG, "g1")]})
    SearchMissingSkill().execute(agent)
    assert agent.posts == []
    assert sql("SELECT command_status FROM search_history_items") == [("no_hit",)]
    assert sql("SELECT cache_key FROM searched_items") == [("mov:1",)]
    assert log_rows()[0]["outcome"] == "no_clean_hit"


def test_active_without_results_marks_the_title_searched(db_path):
    inst = make_instance(checked_search="active")
    agent = the_thing_agent(inst, releases={1: [release(RIGHT, "g1", approved=False)]})
    SearchMissingSkill().execute(agent)
    assert sql("SELECT command_status FROM search_history_items") == [("no_hit",)]
    assert log_rows()[0]["outcome"] == "no_results"
    assert last_run()["status"] == "success"


def test_error_before_the_search_is_a_failed_item(db_path):
    inst = make_instance(checked_search="active")
    agent = the_thing_agent(inst, get_errors={"/api/v3/movie/": requests.exceptions.ConnectionError("down")})
    SearchMissingSkill().execute(agent)
    assert sql("SELECT command_status, cache_key FROM search_history_items") == [("failed", "")]
    assert sql("SELECT COUNT(*) FROM searched_items")[0][0] == 0
    assert log_rows()[0]["outcome"] == "error"
    run = last_run()
    assert run["status"] == "error"
    assert run["error_message"].startswith("All 1 submission(s) failed")
    assert agent.get_rate_used() == 0


def test_a_load_failure_next_to_a_grab_makes_the_run_partial(db_path):
    inst = make_instance(checked_search="active")
    other = movie(2, "Example Film", 2005)
    clean = "Example.Film.2005.German.1080p.WEB.x264-GRP"
    agent = agent_for(inst, missing=[THE_THING, other], movies=[THE_THING, other],
                      releases={2: [release(clean, "g-example", movie_id=2)]},
                      parses={clean: radarr_parse("Example Film", 2005, 2)},
                      get_errors={"/api/v3/movie/1$": requests.exceptions.ConnectionError("down")})
    SearchMissingSkill().execute(agent)
    run = last_run()
    assert run["status"] == "partial"
    assert (run["triggered_count"], run["verified_count"]) == (1, 1)
    assert run["error_message"].startswith("1 of 2 submission(s) failed")
    assert sorted(r[0] for r in sql("SELECT command_status FROM search_history_items")) == ["failed", "grabbed"]
    assert agent.state["last_verified"] == 1


def test_failed_release_search_is_a_failed_item(db_path):
    inst = make_instance(checked_search="active")
    agent = the_thing_agent(inst, get_errors={RELEASE: requests.exceptions.ReadTimeout("slow")})
    SearchMissingSkill().execute(agent)
    assert sql("SELECT command_status FROM search_history_items") == [("failed",)]
    assert sql("SELECT COUNT(*) FROM searched_items")[0][0] == 0
    assert "release search failed" in log_rows()[0]["error_message"]


@pytest.mark.parametrize("error,used", [
    (requests.exceptions.ReadTimeout("slow"), 1),
    (refused(), 0),
    (requests.exceptions.ConnectTimeout("connect timeout"), 0),
    (requests.exceptions.ConnectionError(ProtocolError("Connection aborted.", ConnectionResetError())), 1),
    (requests.exceptions.ConnectionError(ReadTimeoutError(None, RELEASE, "Read timed out.")), 1),
    (requests.exceptions.ConnectionError("no inner error"), 1),
    (http_error(400), 0),
    (http_error(503), 1),
], ids=["read timeout", "refused", "connect timeout", "reset after send", "stalled body", "bare",
        "4xx", "5xx"])
def test_a_failed_release_search_keeps_the_rate_slot_unless_it_never_ran(db_path, error, used):
    # *arr runs the indexer search without a cancellation signal: once the
    # request reached it, the search counts, even if the answer got lost.
    inst = make_instance(checked_search="active")
    agent = the_thing_agent(inst, get_errors={RELEASE: error})
    SearchMissingSkill().execute(agent)
    assert agent.get_rate_used() == used


def test_parse_error_rejects_only_that_candidate(db_path):
    inst = make_instance(checked_search="active")
    parses = {**PARSES, WRONG: requests.exceptions.ConnectionError("parse down")}
    agent = the_thing_agent(inst, parses=parses)
    SearchMissingSkill().execute(agent)
    [row] = log_rows()
    assert row["candidates"][0]["reasons"] == ["parse error"]
    assert row["outcome"] == "grabbed"


def test_a_grab_without_a_clear_answer_blocks_the_title(db_path):
    # The POST may have reached *arr: no second release, and the title stays
    # blocked like after a grab (the queue of *arr does not reliably stop a
    # second grab of another release).
    inst = make_instance(checked_search="active")
    other = "The.Thing.1982.720p.WEB.x264-OTHER"
    agent = the_thing_agent(inst, releases={1: [release(RIGHT, "g1"), release(other, "g2")]},
                            parses={**PARSES, other: radarr_parse("The Thing", 1982, 1)},
                            post_error=requests.exceptions.ReadTimeout("no answer"))
    SearchMissingSkill().execute(agent)
    assert len(agent.posts) == 1
    assert sql("SELECT command_status, cache_key FROM search_history_items") == [("failed", "mov:1")]
    assert sql("SELECT cache_key, grabbed_at IS NOT NULL FROM searched_items") == [("mov:1", 1)]
    [row] = log_rows()
    assert row["outcome"] == "grab_uncertain" and "check the *arr queue" in row["error_message"]
    assert last_run()["status"] == "error"
    again = the_thing_agent(inst)
    SearchMissingSkill().execute(again)
    assert [p for p, _ in again.gets if p == RELEASE] == []


@pytest.mark.parametrize("error", [http_error(404), http_error(409), refused()],
                         ids=["release expired", "indexer refused", "not sent"])
def test_a_refused_grab_leaves_the_title_free(db_path, error):
    inst = make_instance(checked_search="active")
    agent = the_thing_agent(inst, post_error=error)
    SearchMissingSkill().execute(agent)
    assert sql("SELECT command_status, cache_key FROM search_history_items") == [("failed", "")]
    assert sql("SELECT COUNT(*) FROM searched_items")[0][0] == 0
    assert log_rows()[0]["outcome"] == "grab_failed"
    again = the_thing_agent(inst)
    SearchMissingSkill().execute(again)
    assert len([p for p, _ in again.gets if p == RELEASE]) == 1


def test_an_unsaved_grab_is_stored_by_the_next_run(db_path, monkeypatch):
    inst = make_instance(checked_search="active")
    agent = the_thing_agent(inst)
    real = db.history.record_checked

    def broken(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(db.history, "record_checked", broken)
    SearchMissingSkill().execute(agent)
    first = last_run()
    assert first["status"] == "error" and len(agent.posts) == 1
    monkeypatch.setattr(db.history, "record_checked", real)
    SearchMissingSkill().execute(agent)          # same instance runtime, the next run
    assert len(agent.posts) == 1                 # no second grab
    assert sql("SELECT run_id, command_status FROM search_history_items") == [(first["id"], "grabbed")]
    assert sql("SELECT cache_key FROM searched_items") == [("mov:1",)]
    assert [(r["run_id"], r["outcome"]) for r in log_rows()] == [(first["id"], "grabbed")]
    assert agent.runtime.unsaved_submissions == []


def test_an_unsaved_grab_blocks_its_title_while_the_database_refuses(db_path, monkeypatch):
    inst = make_instance(checked_search="active")
    agent = the_thing_agent(inst)

    def broken(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(db.history, "record_checked", broken)
    SearchMissingSkill().execute(agent)
    SearchMissingSkill().execute(agent)
    assert len([p for p, _ in agent.gets if p == RELEASE]) == 1
    assert len(agent.posts) == 1
    assert len(agent.runtime.unsaved_submissions) == 1


def test_a_grabbed_title_still_missing_is_searched_again_after_the_set_days(db_path):
    # Replaces *arr's "Redownload Failed from Interactive Search", which is
    # switched off for the checked search.
    inst = make_instance(checked_search="active", checked_search_settings={"search_again_after_days": 3})
    SearchMissingSkill().execute(the_thing_agent(inst))
    same = the_thing_agent(inst)
    SearchMissingSkill().execute(same)
    assert [p for p, _ in same.gets if p == RELEASE] == []
    sql("UPDATE searched_items SET grabbed_at=datetime('now','localtime','-4 days')")
    later = the_thing_agent(inst)
    SearchMissingSkill().execute(later)
    assert len([p for p, _ in later.gets if p == RELEASE]) == 1
    assert later.posts == [movie_grab("guid-right")]


@pytest.mark.parametrize("indexers,errors,hint", [
    ([*INDEXERS, {"id": 9, "name": "Weekly check", "enableAutomaticSearch": True,
                  "enableInteractiveSearch": False}], {}, "Checked search paused — indexer Weekly check"),
    ([*INDEXERS, {"id": 9, "name": "Manual only", "enableAutomaticSearch": False,
                  "enableInteractiveSearch": True}], {}, "Checked search paused — indexer Manual only"),
    (None, {"/api/v3/indexer": requests.exceptions.ConnectionError("down")},
     "Checked search paused — could not read the indexer list: down"),
], ids=["interactive off", "automatic off", "list unreadable"])
def test_differing_indexer_switches_pause_the_checked_search(db_path, indexers, errors, hint):
    # A pause is no fault (decision Daniel 01.10.2026): the run is a success
    # with the reason, a warning goes to the activity log, last_sync stays.
    inst = make_instance(checked_search="active")
    agent = the_thing_agent(inst, indexers=indexers, get_errors=errors)
    SearchMissingSkill().execute(agent)
    assert [p for p, _ in agent.gets if p in (RELEASE, PARSE)] == []
    assert agent.posts == [] and log_rows() == []
    assert sql("SELECT COUNT(*) FROM search_history_items")[0][0] == 0
    assert sql("SELECT COUNT(*) FROM searched_items")[0][0] == 0
    assert agent.get_rate_used() == 0
    run = last_run()
    assert run["status"] == "success" and run["error_message"].startswith(hint)
    assert any(r["level"] == "warn" and r["message"].startswith(hint)
               for r in db.activity.query(limit=200, include_debug=True))
    assert agent.state["last_sync"] is None
    alike = the_thing_agent(inst)                                    # switches alike again
    SearchMissingSkill().execute(alike)
    assert alike.state["last_sync"] is not None


WEEKLY_CHECK = [*INDEXERS, {"id": 9, "name": "Weekly check", "enableAutomaticSearch": True,
                            "enableInteractiveSearch": False}]


@pytest.mark.parametrize("mode,first_run,wanted", [
    ("dry_run", True, True),       # the round is done: nothing left to check
    ("active", True, True),        # every title blocked by the cache
    ("active", False, False),      # empty wanted list
], ids=["round done", "all cached", "nothing wanted"])
def test_a_checked_run_without_titles_still_pauses(db_path, mode, first_run, wanted):
    # Codex round 3, G4: every checked run reads the indexer list, also one
    # that finds nothing to search — a lasting pause must show (spec).
    inst = make_instance(checked_search=mode)
    agent = the_thing_agent(inst) if wanted else agent_for(inst)
    if first_run:
        SearchMissingSkill().execute(agent)
    agent.indexers = copy.deepcopy(WEEKLY_CHECK)
    agent.state["last_sync"] = "2000-01-01 00:00"
    searches = len([p for p, _ in agent.gets if p == RELEASE])
    SearchMissingSkill().execute(agent)
    assert len([p for p, _ in agent.gets if p == RELEASE]) == searches
    run = last_run()
    assert run["status"] == "success"
    assert run["error_message"].startswith("Checked search paused — indexer Weekly check")
    assert any(r["level"] == "warn" and r["message"].startswith("Checked search paused")
               for r in db.activity.query(limit=200, include_debug=True))
    assert agent.state["last_sync"] == "2000-01-01 00:00"


# ── Limits ───────────────────────────────────────────────────────────────────

def three_movies():
    return [movie(i, f"Film {i}", 2000 + i) for i in (1, 2, 3)]


def test_time_budget_starts_no_new_title(db_path, monkeypatch):
    from backend.checked_search import runner
    inst = make_instance(checked_search_settings={"time_budget_minutes": 1})
    ticks = itertools.chain([0, 61], itertools.repeat(61))
    real = runner.run_checked
    monkeypatch.setattr("backend.skills.search_missing.run_checked",
                        lambda *a, **k: real(*a, **{**k, "clock": lambda: next(ticks), "started": 0}))
    films = three_movies()
    agent = agent_for(inst, missing=films, movies=films)
    SearchMissingSkill().execute(agent)
    assert len([p for p, _ in agent.gets if p == RELEASE]) == 1
    run = last_run()
    assert run["status"] == "success"
    assert "Time budget of 1 min used up — 2 title(s) left for the next run" in run["error_message"]


def test_time_budget_counts_from_the_start_of_the_run(db_path):
    # Reading the profiles and collecting the candidates used the budget up.
    from backend.checked_search.runner import CheckedTask, run_checked
    inst = make_instance(checked_search_settings={"time_budget_minutes": 1})
    films = three_movies()
    agent = agent_for(inst, missing=films, movies=films)
    run_id = history.start_run(inst["id"], "Radarr", "search_missing")
    tasks = [CheckedTask(f["id"], f["title"], "movie", f"mov:{f['id']}") for f in films]
    outcome = run_checked("search_missing", agent, run_id, tasks, "dry_run", clock=lambda: 61, started=0)
    assert [p for p, _ in agent.gets if p == RELEASE] == []
    assert outcome.notes == ["Time budget of 1 min used up — 3 title(s) left for the next run"]


def test_abort_between_parse_calls_stops_the_run(db_path):
    inst = make_instance(checked_search="active")
    agent = the_thing_agent(inst, abort_after_parses=1)
    SearchMissingSkill().execute(agent)
    assert agent.posts == []
    assert log_rows() == []
    assert last_run()["status"] == "error"


def test_abort_during_release_search_grabs_nothing(db_path):
    inst = make_instance(checked_search="active")
    agent = the_thing_agent(inst, abort_on_release=True)
    SearchMissingSkill().execute(agent)
    assert agent.posts == []
    assert [p for p, _ in agent.gets if p == PARSE] == []
    assert log_rows() == []
    assert last_run()["status"] == "error"


def test_abort_after_the_last_parse_grabs_nothing(db_path):
    inst = make_instance(checked_search="active")
    agent = the_thing_agent(inst, releases={1: [release(RIGHT, "g1")]}, abort_after_parses=1)
    SearchMissingSkill().execute(agent)
    assert agent.posts == []
    assert log_rows() == []


def test_rate_cap_counts_one_action_per_title(db_path):
    inst = make_instance(rate_cap=1)
    films = three_movies()
    agent = agent_for(inst, missing=films, movies=films)
    SearchMissingSkill().execute(agent)
    assert len([p for p, _ in agent.gets if p == RELEASE]) == 1
    assert agent.get_rate_used() == 1


def test_a_store_failure_stops_the_run(db_path, monkeypatch):
    inst = make_instance(checked_search="active")
    films = three_movies()
    agent = agent_for(inst, missing=films, movies=films)

    def broken(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(db.history, "record_checked", broken)
    SearchMissingSkill().execute(agent)
    assert len([p for p, _ in agent.gets if p == RELEASE]) == 1
    run = last_run()
    assert run["status"] == "error"
    assert "database is locked" in run["error_message"]


# ── Sonarr ───────────────────────────────────────────────────────────────────

GUEST_SERIES = {"id": 10, "title": "The Guest", "year": 2018, "alternateTitles": [], "qualityProfileId": 1}


def guest_episode(ep_id=3, season=1):
    return {"id": ep_id, "seriesId": 10, "seasonNumber": season, "episodeNumber": ep_id, "title": f"E{ep_id}",
            "airDateUtc": "2018-10-11T12:00:00Z", "hasFile": False, "monitored": True,
            "series": {"title": "The Guest"}}


def test_sonarr_checks_single_episodes_even_with_another_stored_mode(db_path):
    inst = make_instance(name="Sonarr", type="sonarr", checked_search="active")
    sql("UPDATE instances SET missing_mode='show_batch'")
    foreign = "The.Guest.CO.2025.S01E03.MULTI.1080p.WEB.X264-GRP"
    right = "The.Guest.S01E03.German.1080p.WEB.x264-GRP"
    agent = agent_for(
        inst, missing=[guest_episode()], episodes=[guest_episode()], series=[GUEST_SERIES],
        # Sonarr maps the foreign release to the searched episode too (stamped tvdbId)
        releases={3: [release(foreign, "g1", published="2025-06-01T00:00:00Z", series_id=10, episode_ids=(3,)),
                      release(right, "g2", published="2018-10-11T20:00:00Z", series_id=10, episode_ids=(3,))]},
        parses={foreign: {"parsedEpisodeInfo": {"seriesTitle": "The Guest CO 2025"}},
                right: {"parsedEpisodeInfo": {"seriesTitle": "The Guest"}, "series": {"id": 10}}},
    )
    SearchMissingSkill().execute(agent)
    assert ("/api/v3/release", {"episodeId": 3}) in agent.gets
    assert agent.posts == [episode_grab("g2")]
    assert agent.grabbed == [(10, (3,))]
    [row] = log_rows()
    assert row["candidates"][0]["reasons"] == ["year suffix", "country suffix"]
    assert sql("SELECT item_type, cache_key FROM search_history_items") == [("episode", "ep:3")]


def test_sonarr_season_packs_are_not_taken(db_path):
    # Sonarr itself rejects packs in an episode search, but not for specials
    # (SearchSpecial): a pack can come back approved there.
    inst = make_instance(name="Sonarr", type="sonarr", checked_search="active")
    special = guest_episode(5, season=0)
    pack = "The.Guest.S00.German.1080p.WEB.x264-GRP"
    right = "The.Guest.S00E05.German.1080p.WEB.x264-GRP"
    agent = agent_for(
        inst, missing=[special], episodes=[special], series=[GUEST_SERIES],
        releases={5: [{**release(pack, "g1", series_id=10, episode_ids=(5,)), "fullSeason": True},
                      release(right, "g2", series_id=10, episode_ids=(5,))]},
        parses={right: {"parsedEpisodeInfo": {"seriesTitle": "The Guest"}, "series": {"id": 10}}},
    )
    SearchMissingSkill().execute(agent)
    assert agent.posts == [episode_grab("g2", episode_ids=(5,))]
    [row] = log_rows()
    assert row["arr_pick"] == pack
    assert row["candidates"][0]["reasons"] == ["season pack"]
    assert [params["title"] for p, params in agent.gets if p == PARSE] == [right]


DOUBLE = "The.Guest.S01E03E04.German.1080p.WEB.x264-GRP"


@pytest.mark.parametrize("checked", ["active", "dry_run"])
def test_a_multi_episode_release_is_never_taken(db_path, checked):
    # 0.9.0 grabs single episodes only (decision Daniel 01.10.2026): a release
    # mapped to E03 and E04 would be checked and cached for E03 alone.
    # Sonarr approves it in a search for E03 (SingleEpisodeSearchMatchSpecification).
    inst = make_instance(name="Sonarr", type="sonarr", checked_search=checked)
    right = "The.Guest.S01E03.German.1080p.WEB.x264-GRP"
    agent = agent_for(
        inst, missing=[guest_episode()], episodes=[guest_episode()], series=[GUEST_SERIES],
        releases={3: [release(DOUBLE, "g1", series_id=10, episode_ids=(3, 4)),
                      release(right, "g2", series_id=10, episode_ids=(3,))]},
        parses={right: {"parsedEpisodeInfo": {"seriesTitle": "The Guest"}, "series": {"id": 10}}},
    )
    SearchMissingSkill().execute(agent)
    [row] = log_rows()
    assert row["arr_pick"] == DOUBLE
    assert row["candidates"][0]["reasons"] == ["multi-episode release"]
    assert row["pick"] == right
    assert [params["title"] for p, params in agent.gets if p == PARSE] == [right]
    if checked == "active":
        assert agent.posts == [episode_grab("g2")]
        assert agent.grabbed == [(10, (3,))]
    else:
        assert agent.posts == [] and row["outcome"] == "would_grab"


# ── Profile changes (spec addendum) ──────────────────────────────────────────

def test_cache_frees_a_title_after_its_profile_changed(db_path):
    inst = make_instance(checked_search="off")
    first = the_thing_agent(inst)
    SearchMissingSkill().execute(first)
    assert first.commands == [{"name": "MoviesSearch", "movieIds": [1]}]
    assert sql("SELECT profile_fingerprint FROM searched_items") == [(stored_fingerprint(inst),)]

    same = the_thing_agent(inst)
    SearchMissingSkill().execute(same)
    assert same.commands == []

    changed = the_thing_agent(inst, profiles=changed_profiles())
    SearchMissingSkill().execute(changed)
    assert changed.commands == [{"name": "MoviesSearch", "movieIds": [1]}]
    assert any(m.startswith("Quality profile changed: HD (") for m in activity_messages())
    assert sql("SELECT profile_fingerprint FROM searched_items") == [(stored_fingerprint(inst),)]


def test_cache_entries_from_before_0_9_0_block_until_the_first_change(db_path):
    inst = make_instance(checked_search="off")
    db.searched.add(inst["id"], "mov:1", "The Thing", "movie")      # cached by 0.8.0, no fingerprint
    first = the_thing_agent(inst)
    SearchMissingSkill().execute(first)
    assert first.commands == []                                       # the update alone releases nothing
    assert db.instances.get_by_id(inst["id"])["profile_fingerprints_baseline"] == {"1": stored_fingerprint(inst)}

    changed = the_thing_agent(inst, profiles=changed_profiles())
    SearchMissingSkill().execute(changed)
    assert changed.commands == [{"name": "MoviesSearch", "movieIds": [1]}]


def test_with_the_setting_off_the_cache_ignores_profile_changes(db_path):
    inst = make_instance(checked_search="off", search_again_after_profile_change=False)
    SearchMissingSkill().execute(the_thing_agent(inst))
    changed = the_thing_agent(inst, profiles=changed_profiles())
    SearchMissingSkill().execute(changed)
    assert changed.commands == []


def test_dry_run_checks_again_after_a_profile_change(db_path):
    inst = make_instance()
    SearchMissingSkill().execute(the_thing_agent(inst))
    same = the_thing_agent(inst)
    SearchMissingSkill().execute(same)
    assert [p for p, _ in same.gets if p == RELEASE] == []

    changed = the_thing_agent(inst, profiles=changed_profiles())
    SearchMissingSkill().execute(changed)
    assert len([p for p, _ in changed.gets if p == RELEASE]) == 1
    assert [r["profile_changed"] for r in log_rows()] == [False, True]   # newest first


def test_log_rows_name_the_profile_they_were_checked_under(db_path):
    # The badge compares with this very profile (Codex round 2, F7).
    inst = make_instance()
    SearchMissingSkill().execute(the_thing_agent(inst))
    assert [r["profile_id"] for r in log_rows()] == [1]


def test_profiles_that_cannot_be_read_release_nothing(db_path):
    inst = make_instance(checked_search="off")
    SearchMissingSkill().execute(the_thing_agent(inst))
    broken = the_thing_agent(inst, profiles=changed_profiles(),
                             get_errors={QUALITY_PROFILES: requests.exceptions.ConnectionError("down")})
    SearchMissingSkill().execute(broken)
    assert broken.commands == []
    assert any(m.startswith("Could not read the quality profiles") for m in activity_messages())


@pytest.mark.parametrize("checked", ["off", "active"])
def test_a_failed_first_baseline_write_releases_nothing(db_path, monkeypatch, checked):
    # Codex round 3, G2: the first run of 0.9.0 reads the profiles, but the
    # database refuses to store them. Entries from before 0.9.0 must keep
    # blocking (spec: the update alone releases nothing), in either mode.
    inst = make_instance(checked_search=checked)
    db.searched.add(inst["id"], "mov:1", "The Thing", "movie")      # cached by 0.8.0, no fingerprint
    real = db.instances.store_profile_fingerprints

    def locked(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(db.instances, "store_profile_fingerprints", locked)
    first = the_thing_agent(inst)
    SearchMissingSkill().execute(first)
    assert first.commands == [] and [p for p, _ in first.gets if p == RELEASE] == []
    assert any(m.startswith("Could not store the quality profile fingerprints") for m in activity_messages())
    assert db.instances.get_by_id(inst["id"])["profile_fingerprints_baseline"] is None

    monkeypatch.setattr(db.instances, "store_profile_fingerprints", real)
    again = the_thing_agent(inst)
    SearchMissingSkill().execute(again)
    assert again.commands == [] and [p for p, _ in again.gets if p == RELEASE] == []
    assert db.instances.get_by_id(inst["id"])["profile_fingerprints_baseline"] == {"1": stored_fingerprint(inst)}


def test_sonarr_takes_the_profile_from_the_series_list(db_path):
    inst = make_instance(name="Sonarr", type="sonarr", checked_search="off")
    first = agent_for(inst, missing=[guest_episode()], episodes=[guest_episode()], series=[GUEST_SERIES])
    SearchMissingSkill().execute(first)
    assert first.commands == [{"name": "EpisodeSearch", "episodeIds": [3]}]
    assert sql("SELECT profile_fingerprint FROM searched_items") == [(stored_fingerprint(inst),)]
    changed = agent_for(inst, missing=[guest_episode()], episodes=[guest_episode()], series=[GUEST_SERIES],
                        profiles=changed_profiles())
    SearchMissingSkill().execute(changed)
    assert changed.commands == [{"name": "EpisodeSearch", "episodeIds": [3]}]


BASELINE = {"1": "aaaaaaaaaaaaaaaa"}   # a profile state from before a change
SERIES_LIST_DOWN = {"/api/v3/series$": requests.exceptions.ConnectionError("down")}


@pytest.mark.parametrize("checked", ["active", "dry_run"])
def test_a_checked_episode_keeps_its_profile_when_the_series_list_fails(db_path, checked):
    # Without the list the episode's profile is unknown while candidates are
    # picked, but the checked search loads the series anyway and stores its
    # fingerprint — not NULL, which would count as searched under the baseline.
    inst = make_instance(name="Sonarr", type="sonarr", checked_search=checked)
    db.instances.store_profile_fingerprints(inst["id"], BASELINE)
    episode = guest_episode()
    broken = agent_for(inst, missing=[episode], episodes=[episode], series=[GUEST_SERIES],
                       get_errors=SERIES_LIST_DOWN)
    SearchMissingSkill().execute(broken)
    current = stored_fingerprint(inst)
    assert current != BASELINE["1"]
    assert log_rows()[0]["profile_fingerprint"] == current
    if checked == "active":
        assert sql("SELECT profile_fingerprint FROM searched_items") == [(current,)]
    again = agent_for(inst, missing=[episode], episodes=[episode], series=[GUEST_SERIES])
    SearchMissingSkill().execute(again)
    assert [p for p, _ in again.gets if p == RELEASE] == []


def test_the_command_search_asks_for_the_series_when_the_list_fails(db_path):
    inst = make_instance(name="Sonarr", type="sonarr", checked_search="off")
    db.instances.store_profile_fingerprints(inst["id"], BASELINE)
    episode = guest_episode()
    broken = agent_for(inst, missing=[episode], episodes=[episode], series=[GUEST_SERIES],
                       get_errors=SERIES_LIST_DOWN)
    SearchMissingSkill().execute(broken)
    assert broken.commands == [{"name": "EpisodeSearch", "episodeIds": [3]}]
    assert ("/api/v3/series/10", {}) in broken.gets
    assert sql("SELECT profile_fingerprint FROM searched_items") == [(stored_fingerprint(inst),)]


# ── Upgrades ─────────────────────────────────────────────────────────────────

def test_radarr_upgrade_skips_the_release_of_the_existing_file(db_path):
    inst = make_instance(checked_search="active", search_upgrades_enabled=True, upgrades_per_run=1,
                         upgrade_source="monitored_items_only")
    current = "The.Thing.1982.720p.BluRay.x264-OLD"
    owned = movie(1, "The Thing", 1982, hasFile=True, movieFile={"sceneName": current})
    agent = agent_for(inst, movies=[owned],
                      releases={1: [release(current, "g1"), release(RIGHT, "g2")]},
                      parses={current: radarr_parse("The Thing", 1982, 1), RIGHT: radarr_parse("The Thing", 1982, 1)})
    SearchUpgradesSkill().execute(agent)
    assert agent.posts == [movie_grab("g2")]
    assert log_rows()[0]["candidates"][0]["reasons"] == ["existing file"]
    assert sql("SELECT cache_key FROM searched_items") == [("upg:1",)]


def test_sonarr_upgrade_works_per_episode(db_path):
    inst = make_instance(name="Sonarr", type="sonarr", checked_search="dry_run", search_upgrades_enabled=True)
    episode = {**guest_episode(), "hasFile": True}
    agent = agent_for(inst, cutoff=[episode], episodes=[episode], series=[GUEST_SERIES],
                      releases={3: [release("The.Guest.S01E03.1080p-GRP", "g1", series_id=10, episode_ids=(3,))]})
    SearchUpgradesSkill().execute(agent)
    [row] = log_rows()
    assert (row["skill"], row["cache_key"]) == ("search_upgrades", "upg:3")
    assert row["profile_fingerprint"] == stored_fingerprint(inst)


def test_force_run_of_upgrades_in_dry_run_respects_the_round(db_path):
    inst = make_instance(search_upgrades_enabled=True, upgrades_per_run=1, upgrade_source="monitored_items_only")
    owned = movie(1, "The Thing", 1982, hasFile=True)
    SearchUpgradesSkill().execute(agent_for(inst, movies=[owned]), force=True)
    forced = agent_for(inst, movies=[owned])
    SearchUpgradesSkill().execute(forced, force=True)
    assert [p for p, _ in forced.gets if p == RELEASE] == []
    assert len(log_rows()) == 1


@pytest.mark.parametrize("source,searched_again", [("wanted_list_only", 1), ("monitored_items_only", 0)])
def test_a_grabbed_upgrade_is_searched_again_only_from_the_cutoff_list(db_path, source, searched_again):
    # The cutoff list names the movie only while it is still below the
    # cutoff; the movie list names every movie with a file.
    inst = make_instance(checked_search="active", search_upgrades_enabled=True, upgrades_per_run=1,
                         upgrade_source=source, checked_search_settings={"search_again_after_days": 1})
    owned = movie(1, "The Thing", 1982, hasFile=True)
    kwargs = dict(cutoff=[owned], movies=[owned], releases={1: [release(RIGHT, "g1")]}, parses=PARSES)
    first = agent_for(inst, **kwargs)
    SearchUpgradesSkill().execute(first)
    assert first.posts == [movie_grab("g1")]
    sql("UPDATE searched_items SET grabbed_at=datetime('now','localtime','-2 days')")
    again = agent_for(inst, **kwargs)
    SearchUpgradesSkill().execute(again)
    assert len([p for p, _ in again.gets if p == RELEASE]) == searched_again


def test_upgrade_cache_frees_a_movie_after_its_profile_changed(db_path):
    inst = make_instance(checked_search="off", search_upgrades_enabled=True, upgrades_per_run=1,
                         upgrade_source="monitored_items_only")
    owned = movie(1, "The Thing", 1982, hasFile=True)
    first = agent_for(inst, movies=[owned])
    SearchUpgradesSkill().execute(first)
    assert first.commands == [{"name": "MoviesSearch", "movieIds": [1]}]
    assert sql("SELECT cache_key, profile_fingerprint FROM searched_items") == [("upg:1", stored_fingerprint(inst))]
    same = agent_for(inst, movies=[owned])
    SearchUpgradesSkill().execute(same)
    assert same.commands == []
    changed = agent_for(inst, movies=[owned], profiles=changed_profiles())
    SearchUpgradesSkill().execute(changed)
    assert changed.commands == [{"name": "MoviesSearch", "movieIds": [1]}]


@pytest.mark.parametrize("search_again", [True, False])
def test_a_season_upgrade_cached_by_the_command_blocks_its_episodes_in_checked_mode(db_path, search_again):
    # Codex round 2, F5: 0.8.0 cached Sonarr upgrades per season; switching to
    # the checked search (per episode) must not release them early.
    inst = make_instance(name="Sonarr", type="sonarr", checked_search="active", search_upgrades_enabled=True,
                         upgrades_per_run=1, search_again_after_profile_change=search_again)
    db.searched.add(inst["id"], "upg:sea:10:1", "The Guest S01", "season")      # 0.8.0: no fingerprint
    episode = {**guest_episode(), "hasFile": True}
    kwargs = dict(cutoff=[episode], episodes=[episode], series=[GUEST_SERIES],
                  releases={3: [release("The.Guest.S01E03.1080p-GRP", "g1", series_id=10, episode_ids=(3,))]})
    first = agent_for(inst, **kwargs)
    SearchUpgradesSkill().execute(first)
    assert [p for p, _ in first.gets if p == RELEASE] == []                    # the update releases nothing
    changed = agent_for(inst, profiles=changed_profiles(), **kwargs)
    SearchUpgradesSkill().execute(changed)
    assert len([p for p, _ in changed.gets if p == RELEASE]) == (1 if search_again else 0)


def test_an_episode_upgrade_without_a_grab_holds_only_itself(db_path):
    # A checked search without a grab (no_hit) blocks its own episode; a free
    # episode of the same season still brings the season search (decision
    # Daniel 02.10.2026: only a grab holds the season).
    inst = make_instance(name="Sonarr", type="sonarr", checked_search="off", search_upgrades_enabled=True,
                         upgrades_per_run=1)
    run = history.start_run(inst["id"], "Sonarr", "search_upgrades")
    history.record_checked(run, inst["id"], "The Guest S01E03", 3, "episode", "upg:3", "no_hit")
    episode = {**guest_episode(), "hasFile": True}
    agent = agent_for(inst, cutoff=[episode], episodes=[episode], series=[GUEST_SERIES])
    SearchUpgradesSkill().execute(agent)
    assert agent.commands == []
    free = {**guest_episode(4), "hasFile": True}                                 # a second, uncached episode
    other = agent_for(inst, cutoff=[episode, free], episodes=[episode, free], series=[GUEST_SERIES])
    SearchUpgradesSkill().execute(other)
    assert other.commands == [{"name": "SeasonSearch", "seriesId": 10, "seasonNumber": 1}]


E03_UPGRADE = "The.Guest.S01E03.German.1080p.WEB.x264-GRP"


def grab_e03_upgrade(inst, outcome="grabbed", then_off=True):
    """The checked search upgrades E03 (grabbed, or sent without a clear
    answer); then, if asked, the instance goes back to the command path."""
    episode = {**guest_episode(), "hasFile": True}
    kwargs = dict(cutoff=[episode], episodes=[episode], series=[GUEST_SERIES],
                  releases={3: [release(E03_UPGRADE, "g1", series_id=10, episode_ids=(3,))]},
                  parses={E03_UPGRADE: {"parsedEpisodeInfo": {"seriesTitle": "The Guest"}, "series": {"id": 10}}})
    if outcome == "grab_uncertain":
        kwargs["post_error"] = requests.exceptions.ReadTimeout("no answer")
    SearchUpgradesSkill().execute(agent_for(inst, **kwargs))
    assert [r["outcome"] for r in log_rows()] == [outcome]
    if then_off:
        sql("UPDATE instances SET checked_search='off'")
    return episode, {**guest_episode(4), "hasFile": True}


@pytest.mark.parametrize("outcome", ["grabbed", "grab_uncertain"])
def test_a_checked_episode_upgrade_holds_its_season_search_in_off_mode(db_path, outcome):
    # Codex round 3, G3, decision Daniel 02.10.2026: SeasonSearch covers every
    # monitored episode, the grabbed one too, and *arr skips its history check
    # during searches. While the grab blocks, the command path leaves the
    # season alone — no single episode commands instead.
    inst = make_instance(name="Sonarr", type="sonarr", checked_search="active", search_upgrades_enabled=True,
                         upgrades_per_run=1, checked_search_settings={"search_again_after_days": 3})
    episode, free = grab_e03_upgrade(inst, outcome)
    left = agent_for(inst, cutoff=[free], episodes=[episode, free], series=[GUEST_SERIES])   # E03 off the list
    SearchUpgradesSkill().execute(left)
    assert left.commands == []
    both = agent_for(inst, cutoff=[episode, free], episodes=[episode, free], series=[GUEST_SERIES])
    SearchUpgradesSkill().execute(both)
    assert both.commands == []
    sql("UPDATE searched_items SET grabbed_at=datetime('now','localtime','-4 days') WHERE grabbed_at IS NOT NULL")
    later = agent_for(inst, cutoff=[free], episodes=[episode, free], series=[GUEST_SERIES])
    SearchUpgradesSkill().execute(later)
    assert later.commands == [{"name": "SeasonSearch", "seriesId": 10, "seasonNumber": 1}]


def test_a_held_season_upgrade_is_free_after_a_profile_change(db_path):
    inst = make_instance(name="Sonarr", type="sonarr", checked_search="active", search_upgrades_enabled=True,
                         upgrades_per_run=1)
    episode, free = grab_e03_upgrade(inst)
    changed = agent_for(inst, cutoff=[free], episodes=[episode, free], series=[GUEST_SERIES],
                        profiles=changed_profiles())
    SearchUpgradesSkill().execute(changed)
    assert changed.commands == [{"name": "SeasonSearch", "seriesId": 10, "seasonNumber": 1}]


def test_a_checked_episode_upgrade_does_not_hold_its_siblings_in_checked_mode(db_path):
    inst = make_instance(name="Sonarr", type="sonarr", checked_search="active", search_upgrades_enabled=True,
                         upgrades_per_run=1)
    episode, free = grab_e03_upgrade(inst, then_off=False)
    sibling = agent_for(inst, cutoff=[free], episodes=[episode, free], series=[GUEST_SERIES])
    SearchUpgradesSkill().execute(sibling)
    assert [params for p, params in sibling.gets if p == RELEASE] == [{"episodeId": 4}]


def test_an_unsaved_checked_upgrade_grab_holds_its_season(db_path, monkeypatch):
    # The database refuses the grab's bookkeeping: until a later run stores
    # it, the grab in memory holds its season for the command path as well.
    inst = make_instance(name="Sonarr", type="sonarr", checked_search="active", search_upgrades_enabled=True,
                         upgrades_per_run=1)
    episode, free = {**guest_episode(), "hasFile": True}, {**guest_episode(4), "hasFile": True}
    agent = agent_for(inst, cutoff=[episode], episodes=[episode, free], series=[GUEST_SERIES],
                      releases={3: [release(E03_UPGRADE, "g1", series_id=10, episode_ids=(3,))]},
                      parses={E03_UPGRADE: {"parsedEpisodeInfo": {"seriesTitle": "The Guest"}, "series": {"id": 10}}})

    def broken(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(db.history, "record_checked", broken)
    SearchUpgradesSkill().execute(agent)
    assert len(agent.posts) == 1 and len(agent.runtime.unsaved_submissions) == 1
    agent.config = {**agent.config, "checked_search": "off"}
    agent.cutoff = [free]
    SearchUpgradesSkill().execute(agent)
    assert agent.commands == []


def test_an_empty_checked_upgrade_run_still_checks_the_indexers(db_path):
    # Codex round 3, G4: no candidates, but an indexer's switches differ.
    inst = make_instance(checked_search="active", search_upgrades_enabled=True, upgrades_per_run=1,
                         upgrade_source="monitored_items_only")
    agent = agent_for(inst, indexers=WEEKLY_CHECK)
    agent.state["last_sync"] = "2000-01-01 00:00"
    SearchUpgradesSkill().execute(agent)
    run = last_run()
    assert run["status"] == "success"
    assert run["error_message"].startswith("Checked search paused — indexer Weekly check")
    assert agent.state["last_sync"] == "2000-01-01 00:00"


# ── Secrets ──────────────────────────────────────────────────────────────────

def all_text():
    chunks = []
    with database.get_db() as conn:
        for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            if table == "instances":
                continue  # holds the encrypted API key by design
            for row in conn.execute(f"SELECT * FROM {table}"):
                chunks.append(json.dumps([str(v) for v in tuple(row)]))
    return "\n".join(chunks)


@pytest.mark.parametrize("mode,failure", [("dry_run", None), ("active", None), ("active", "grab"),
                                          ("active", "search"), ("active", "load")])
def test_no_secret_reaches_log_history_or_activity(db_path, mode, failure):
    inst = make_instance(checked_search=mode)
    errors = {
        "grab": {"post_error": requests.exceptions.ReadTimeout("no answer")},
        "search": {"get_errors": {RELEASE: requests.exceptions.ReadTimeout("slow")}},
        "load": {"get_errors": {"/api/v3/movie/": requests.exceptions.ConnectionError("down")}},
    }
    agent = the_thing_agent(inst, **errors.get(failure, {}))
    SearchMissingSkill().execute(agent)
    if mode == "active" and failure in (None, "grab"):
        assert agent.posts == [movie_grab("guid-right")]
    text = all_text()
    for secret in (INDEXER_KEY, "apikey", "downloadUrl", "infoUrl", "magnetUrl", "magnet:",
                   "guid-right", "guid-wrong", API_KEY):
        assert secret not in text
