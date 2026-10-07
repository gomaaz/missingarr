"""0.12.0, Sonarr rules S6 and S7 in a run: a release whose episode title
names another episode is rejected, and with it every untitled release of
the list in the same languages (invented series)."""
import copy

import pytest

from backend import database, db
from backend.checked_search import sonarr_rules as sr
from backend.checked_search.settings import CheckedSearchSettings
from backend.checked_search.verdict import NOTE_NO_EPISODE_FITS, REASON_NUMBERING, REASON_OTHER_EPISODE
from backend.config import settings
from backend.skills.search_missing import SearchMissingSkill
from tests.test_g3_runner import FakeArr, episode_grab, log_rows, make_instance, release

EPISODES = "/api/v3/episode"
SERIES = {"id": 10, "title": "Paw Friends", "year": 2019, "alternateTitles": [], "qualityProfileId": 1}
TITLES = ["The Big Race", "Lost in the Woods", "Birthday Surprise", "Rainy Day Blues", "The Missing Bone"]
OTHER = "Paw.Friends.S01E03.Lost.in.the.Woods.1080p.WEB-DL.x264-GRP"
UNTITLED = "Paw.Friends.S01E03.1080p.WEB-DL.x264-GRP"
GERMAN = "Paw.Friends.S01E03.GERMAN.1080p.WEB-DL.x264-GRP"
ENGLISH = [{"id": 1, "name": "English"}]
UNKNOWN = [{"id": 0, "name": "Unknown"}]


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
    from backend.checked_search import runner
    monkeypatch.setattr(runner, "HEALTH_SETTLE_SECONDS", 0, raising=False)


def paw_episode(number, title=None):
    return {"id": number, "seriesId": 10, "seasonNumber": 1, "episodeNumber": number,
            "title": TITLES[number - 1] if title is None else title, "airDateUtc": "2019-10-11T12:00:00Z",
            "hasFile": False, "monitored": True, "series": {"title": "Paw Friends"}}


class EpisodeListArr(FakeArr):
    """FakeArr that also answers GET /episode?seriesId= with the episodes of
    the series (Sonarr returns the whole series in one list)."""

    def http_get(self, path, params=None, timeout=10):
        if path != EPISODES:
            return super().http_get(path, params, timeout)
        params = dict(params or {})
        self.gets.append((path, params))
        self.timeouts[path] = timeout
        for pattern, error in self.get_errors.items():
            if self._matches(path, pattern):
                raise error
        return [copy.deepcopy(e) for e in self.episodes.values() if e["seriesId"] == params["seriesId"]]


def paw_release(title, guid, languages=None):
    data = release(title, guid, series_id=10, episode_ids=(3,))
    if languages is not None:
        data["languages"] = copy.deepcopy(languages)
    return data


def paw_agent(inst, releases, **kwargs):
    episodes = [paw_episode(n) for n in range(1, 6)]
    parses = {r["title"]: {"parsedEpisodeInfo": {"seriesTitle": "Paw Friends"}, "series": {"id": 10}}
              for r in releases}
    defaults = dict(missing=[paw_episode(3)], episodes=episodes, series=[SERIES], parses=parses,
                    releases={3: releases})
    defaults.update(kwargs)
    return EpisodeListArr(db.instances.get_by_id(inst["id"]), **defaults)


def sonarr(**fields):
    return make_instance(**{"name": "Sonarr", "type": "sonarr", "checked_search": "active", **fields})


def reasons(row):
    return {c["title"]: c["reasons"] for c in row["candidates"]}


def test_an_untitled_release_of_the_same_language_is_rejected_before_the_pick(db_path):
    # The untitled release comes first: without the look at the whole list
    # it would be grabbed before the title of the second one is seen.
    agent = paw_agent(sonarr(), [paw_release(UNTITLED, "g1", ENGLISH), paw_release(OTHER, "g2", ENGLISH),
                                 paw_release(GERMAN, "g3")])
    SearchMissingSkill().execute(agent)
    [row] = log_rows()
    assert reasons(row) == {UNTITLED: [REASON_NUMBERING], OTHER: [REASON_OTHER_EPISODE], GERMAN: []}
    assert row["pick"] == GERMAN
    assert agent.posts == [episode_grab("g3")]


def test_other_languages_and_unknown_stay_free(db_path):
    dual = [{"id": 1, "name": "English"}, {"id": 4, "name": "German"}]
    agent = paw_agent(sonarr(), [paw_release(OTHER, "g1", ENGLISH), paw_release(UNTITLED, "g2", dual)])
    SearchMissingSkill().execute(agent)
    assert agent.posts[0]["guid"] == "g2"

    inst = sonarr(name="Sonarr 2")
    agent = paw_agent(inst, [paw_release(OTHER, "g1", ENGLISH), paw_release(UNTITLED, "g2", UNKNOWN)])
    SearchMissingSkill().execute(agent)
    assert agent.posts[0]["guid"] == "g2"


def test_a_rejected_release_without_known_language_blocks_nothing(db_path):
    agent = paw_agent(sonarr(), [paw_release(OTHER, "g1", UNKNOWN), paw_release(UNTITLED, "g2", ENGLISH)])
    SearchMissingSkill().execute(agent)
    assert agent.posts[0]["guid"] == "g2"


@pytest.mark.parametrize("off", [{"check_episode_title": False}, {"untitled_after_other_episode": False}])
def test_s7_needs_both_switches(db_path, off):
    agent = paw_agent(sonarr(checked_search_settings=off),
                      [paw_release(UNTITLED, "g1", ENGLISH), paw_release(OTHER, "g2", ENGLISH)])
    SearchMissingSkill().execute(agent)
    [row] = log_rows()
    assert reasons(row)[UNTITLED] == []
    assert agent.posts == [{**episode_grab("g1"), "languages": ENGLISH}]


def test_a_title_that_fits_no_episode_is_only_a_note(db_path):
    german_title = "Paw.Friends.S01E03.Geburtstags.Ueberraschung.GERMAN.1080p.WEB-DL-GRP"
    agent = paw_agent(sonarr(), [paw_release(german_title, "g1")])
    SearchMissingSkill().execute(agent)
    [row] = log_rows()
    assert row["candidates"][0]["notes"] == [NOTE_NO_EPISODE_FITS]
    assert agent.posts == [episode_grab("g1")]


def test_evaluate_without_context_ignores_s6_and_s7():
    info = sr.EpisodeInfo(10, ("Paw Friends",), 2019, season_number=1, episode_number=3,
                          episode_title="Birthday Surprise")
    parse = sr.EpisodeParse(10, "Paw Friends")
    assert sr.evaluate(info, OTHER, None, parse, CheckedSearchSettings()).ok
    context = sr.RuleContext(release_languages=frozenset({"English"}), doubt_languages=(frozenset({"English"}),))
    assert sr.evaluate(info, UNTITLED, None, parse, CheckedSearchSettings(), context).reasons == (REASON_NUMBERING,)
    assert sr.evaluate(info, UNTITLED, None, parse, CheckedSearchSettings()).ok


@pytest.mark.parametrize("rejected", [UNKNOWN, ENGLISH + UNKNOWN])
def test_an_untitled_release_of_unknown_language_stays_free(db_path, rejected):
    agent = paw_agent(sonarr(), [paw_release(OTHER, "g1", rejected), paw_release(UNTITLED, "g2", UNKNOWN)])
    SearchMissingSkill().execute(agent)
    [row] = log_rows()
    assert reasons(row)[UNTITLED] == []
    assert agent.posts[0]["guid"] == "g2"


def test_releases_the_rules_never_see_raise_no_doubt(db_path):
    # A double episode named after its first part (E02 + E03), a release of
    # another episode and a season pack: the gates reject them before S6.
    double = {**paw_release("Paw.Friends.S01E02E03.Lost.in.the.Woods.1080p.WEB-DL.x264-GRP", "g1", ENGLISH),
              "mappedEpisodeInfo": [{"id": 2, "seasonNumber": 1, "episodeNumber": 2},
                                    {"id": 3, "seasonNumber": 1, "episodeNumber": 3}]}
    elsewhere = {**paw_release("Paw.Friends.S01E04.Lost.in.the.Woods.1080p.WEB-DL.x264-GRP", "g2", ENGLISH),
                 "mappedEpisodeInfo": [{"id": 4, "seasonNumber": 1, "episodeNumber": 4}]}
    pack = {**paw_release("Paw.Friends.S01E03.Lost.in.the.Woods.1080p.WEB-DL.x264-PCK", "g3", ENGLISH),
            "fullSeason": True}
    agent = paw_agent(sonarr(), [double, elsewhere, pack, paw_release(UNTITLED, "g4", ENGLISH)])
    SearchMissingSkill().execute(agent)
    [row] = log_rows()
    assert reasons(row)[UNTITLED] == []
    assert agent.posts == [{**episode_grab("g4"), "languages": ENGLISH}]


def test_a_title_that_starts_with_a_quality_word_is_not_taken_for_untitled(db_path):
    spanish = "Paw.Friends.S01E03.Spanish.Birthday.Surprise.1080p.WEB-DL.x264-GRP"
    agent = paw_agent(sonarr(), [paw_release(OTHER, "g1", ENGLISH), paw_release(spanish, "g2", ENGLISH)])
    SearchMissingSkill().execute(agent)
    [row] = log_rows()
    assert reasons(row) == {OTHER: [REASON_OTHER_EPISODE], spanish: []}
    assert agent.posts[0]["guid"] == "g2"
