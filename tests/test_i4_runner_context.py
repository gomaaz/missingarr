"""0.12.0: what the runner reads for S5-S7. The series list only when a name
could be a namesake, kept for a day per instance; the episode list once per
series and run; a list that cannot be read leaves the release unchecked
(parse error). The Imports page judges as before (invented series)."""
import pytest

from backend import database, db
from backend.checked_search import runner
from backend.checked_search.settings import CheckedSearchSettings
from backend.checked_search.verdict import (
    REASON_NAMESAKE, REASON_NUMBERING, REASON_OTHER_EPISODE, REASON_PARSE_ERROR,
)
from backend.config import settings
from backend.skills.search_missing import SearchMissingSkill
from tests.test_g3_runner import episode_grab, http_error, log_rows, make_instance, release, sql
from tests.test_i3_numbering import (
    ENGLISH, EPISODES, GERMAN, OTHER, UNTITLED, EpisodeListArr, paw_agent, paw_release, reasons,
)

SERIES_LIST = "/api/v3/series"
TARGET = {"id": 10, "title": "Some Show (2026)", "year": 2026, "alternateTitles": [], "qualityProfileId": 1}
NAMESAKE = {"id": 11, "title": "Some Show (2020)", "year": 2020, "alternateTitles": [], "qualityProfileId": 1}
BARE = "Some.Show.S01E03.1080p.WEB-DL.x264-GRP"
BARE_2 = "Some.Show.S01E03.720p.WEB-DL.x264-GRP"
WITH_YEAR = "Some.Show.2026.S01E03.GERMAN.1080p.WEB-DL.x264-GRP"
NO_SERIES = {"parsedEpisodeInfo": {"seriesTitle": "Some Show"}}
MAPPED = {"parsedEpisodeInfo": {"seriesTitle": "Some Show 2026", "seriesTitleInfo": {"year": 2026}},
          "series": {"id": 10}}


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


def sonarr(**fields):
    return make_instance(**{"name": "Sonarr", "type": "sonarr", "checked_search": "active", **fields})


def show_episode():
    return {"id": 3, "seriesId": 10, "seasonNumber": 1, "episodeNumber": 3, "title": "Birthday Surprise",
            "airDateUtc": "2026-09-01T12:00:00Z", "hasFile": False, "monitored": True,
            "series": {"title": "Some Show (2026)"}}


def show_agent(inst, names, **kwargs):
    parses = {BARE: NO_SERIES, BARE_2: NO_SERIES, WITH_YEAR: MAPPED}
    defaults = dict(missing=[show_episode()], episodes=[show_episode()], series=[TARGET, NAMESAKE],
                    parses=parses, releases={3: [release(n, f"g{i}", series_id=10, episode_ids=(3,))
                                                 for i, n in enumerate(names, 1)]})
    defaults.update(kwargs)
    return EpisodeListArr(db.instances.get_by_id(inst["id"]), **defaults)


def reads(agent, path):
    return [params for p, params in agent.gets if p == path]


def test_a_namesake_is_rejected_and_the_series_list_read_once(db_path):
    agent = show_agent(sonarr(), [BARE, BARE_2, WITH_YEAR])
    SearchMissingSkill().execute(agent)
    [row] = log_rows()
    assert reasons(row) == {BARE: [REASON_NAMESAKE], BARE_2: [REASON_NAMESAKE], WITH_YEAR: []}
    assert agent.posts == [episode_grab("g3")]
    assert len(reads(agent, SERIES_LIST)) == 1
    assert agent.timeouts[SERIES_LIST] == runner.SERIES_TIMEOUT
    assert reads(agent, EPISODES) == []          # no name carries an episode title


def test_the_series_list_is_not_read_without_need(db_path):
    agent = show_agent(sonarr(), [WITH_YEAR])
    SearchMissingSkill().execute(agent)
    assert agent.posts == [episode_grab("g1")]
    assert reads(agent, SERIES_LIST) == []
    agent = show_agent(sonarr(name="Sonarr 2", checked_search_settings={"veto_namesake": False}), [BARE])
    SearchMissingSkill().execute(agent)
    assert agent.posts == [episode_grab("g1")]
    assert reads(agent, SERIES_LIST) == []


def test_the_index_is_kept_for_a_day_per_instance(db_path, monkeypatch):
    inst = sonarr()
    now = [1000.0]
    monkeypatch.setattr(runner, "_namesake_clock", lambda: now[0])

    def check():
        config = db.instances.get_by_id(inst["id"])
        agent = show_agent(inst, [])
        title_check = runner._TitleCheck("search_missing", agent, 1, runner.MODE_ACTIVE,
                                         CheckedSearchSettings(), config)
        index = title_check.namesake_index()
        return agent, index

    agent, index = check()
    assert len(reads(agent, SERIES_LIST)) == 1 and index["someshow"] == {(10, 2026), (11, 2020)}
    agent, _ = check()                                    # a later run within the day
    assert reads(agent, SERIES_LIST) == []
    now[0] += runner.NAMESAKE_TTL_SECONDS
    agent, _ = check()                                    # a day later
    assert len(reads(agent, SERIES_LIST)) == 1
    sql("UPDATE instances SET updated_at='2030-01-01 00:00:00'")
    agent, _ = check()                                    # the instance was saved
    assert len(reads(agent, SERIES_LIST)) == 1
    assert [key for key in runner._NAMESAKE_CACHE if key[:2] == (str(db_path), inst["id"])] == [
        (str(db_path), inst["id"], "2030-01-01 00:00:00", tuple(sorted(CheckedSearchSettings().country_codes)))]


def test_an_unreadable_series_list_leaves_the_name_unchecked(db_path):
    agent = show_agent(sonarr(), [BARE, BARE_2, WITH_YEAR], get_errors={SERIES_LIST + "$": http_error(500)})
    SearchMissingSkill().execute(agent)
    [row] = log_rows()
    assert reasons(row) == {BARE: [REASON_PARSE_ERROR], BARE_2: [REASON_PARSE_ERROR], WITH_YEAR: []}
    assert agent.posts == [episode_grab("g3")]
    assert len(reads(agent, SERIES_LIST)) == 1          # not read again for the second name


def test_the_episode_list_is_read_once_per_series_and_run(db_path):
    titled = "Paw.Friends.S01E03.Birthday.Surprise.1080p.WEB-DL.x264-GRP"
    agent = paw_agent(sonarr(), [paw_release(OTHER, "g1", ENGLISH), paw_release(UNTITLED, "g2", ENGLISH),
                                 paw_release(titled, "g3", ENGLISH)])
    SearchMissingSkill().execute(agent)
    [row] = log_rows()
    assert reasons(row) == {OTHER: [REASON_OTHER_EPISODE], UNTITLED: [REASON_NUMBERING], titled: []}
    assert reads(agent, EPISODES) == [{"seriesId": 10}]


def test_an_unreadable_episode_list_leaves_titled_names_unchecked(db_path):
    agent = paw_agent(sonarr(), [paw_release(OTHER, "g1", ENGLISH), paw_release(UNTITLED, "g2", ENGLISH),
                                 paw_release(GERMAN, "g3")],
                      get_errors={EPISODES + "$": http_error(500)})
    SearchMissingSkill().execute(agent)
    [row] = log_rows()
    # S7 needs the list as well: the untitled release is taken
    assert reasons(row) == {OTHER: [REASON_PARSE_ERROR], UNTITLED: [], GERMAN: []}
    assert agent.posts == [{**episode_grab("g2"), "languages": ENGLISH}]
    assert reads(agent, EPISODES) == [{"seriesId": 10}]


def test_radarr_reads_neither_list(db_path):
    from tests.test_g3_runner import the_thing_agent
    agent = the_thing_agent(make_instance(checked_search="active"))
    SearchMissingSkill().execute(agent)
    assert reads(agent, SERIES_LIST) == [] and reads(agent, EPISODES) == []


def test_the_imports_page_judges_as_before():
    from backend.checked_search import import_check as ic
    from tests.test_h2_import_check import judge_show, sonarr_parse
    v = judge_show("Some.Show.S01E01.Lost.in.the.Woods.German.1080p.WEB.h264-GRP", sonarr_parse("Some Show"))
    assert v.state == ic.VERDICT_FITS
    assert not {REASON_NAMESAKE, REASON_OTHER_EPISODE, REASON_NUMBERING} & set(v.reasons)
