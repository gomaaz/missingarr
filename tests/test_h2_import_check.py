from datetime import datetime, timedelta, timezone

import pytest

from backend.checked_search import import_check as ic
from backend.checked_search.settings import CheckedSearchSettings
from backend.checked_search.verdict import (
    REASON_COUNTRY_SUFFIX, REASON_EXISTING_FILE, REASON_MULTI_EPISODE, REASON_OTHER_MOVIE, REASON_OTHER_SERIES,
    REASON_SEASON_PACK, REASON_TARGET, REASON_TITLE, REASON_TITLE_WITHOUT_YEAR, REASON_TOO_EARLY, REASON_YEAR,
    REASON_YEAR_SUFFIX,
)
from tests.imports_fake_arr import (
    API_KEY, COMMAND, EPISODE, HISTORY, INDEXER_KEY, MANUAL_IMPORT, PARSE, QUEUE, QUEUE_DETAILS, QUEUE_STATUS,
    SYSTEM_STATUS, FakeArr, config, delay_record, episode, grab_record, http_error, movie, queue_record, quality,
    radarr_item, refused, series, sonarr_item, timed_out,
)

DEFAULT = CheckedSearchSettings()
UTC = timezone.utc
AIRED = datetime(2026, 9, 27, 18, 0, tzinfo=UTC)
AIRED_TEXT = "2026-09-27T18:00:00Z"

# Made-up titles only (public repository: no library data).
SOME_FILM = movie(5, "Some Film", 2005)
FILM_RELEASE = "Some.Film.2005.German.DL.1080p.BluRay.x264-GRP"
SOME_SHOW = series(10, "Some Show", 2020)
E01 = episode(3, 10, 1, 1, AIRED_TEXT)
E02 = episode(4, 10, 1, 2, "2027-11-01T18:00:00Z")
SHOW_RELEASE = "Some.Show.S01E01.German.1080p.WEB.h264-GRP"


def radarr_parse(title, year, movie_res=None):
    """GET /parse (Radarr): parsedMovieInfo, and movie when the name maps to
    a library movie."""
    data = {"title": title, "parsedMovieInfo": {"movieTitles": [title], "year": year}}
    if movie_res is not None:
        data["movie"] = movie_res
    return data


def sonarr_parse(series_title, season=1, numbers=(1,), *, absolute=(), series_res=None, **flags):
    """GET /parse (Sonarr): parsedEpisodeInfo, and series when the name maps
    to a library series."""
    info = {"seriesTitle": series_title, "seasonNumber": season, "episodeNumbers": list(numbers),
            "absoluteEpisodeNumbers": list(absolute), "fullSeason": False, "isDaily": False, "special": False}
    info.update(flags)
    data = {"title": series_title, "parsedEpisodeInfo": info}
    if series_res is not None:
        data["series"] = series_res
    return data


def judge_movie(release, parse, movie_res=SOME_FILM, **settings):
    return ic.judge("radarr", release, parse, movie_res, None, [], None,
                    CheckedSearchSettings(**settings) if settings else DEFAULT)


def judge_show(release, parse, episodes=(E01,), published=AIRED - timedelta(hours=15), series_res=SOME_SHOW,
               **settings):
    return ic.judge("sonarr", release, parse, None, series_res, list(episodes), published,
                    CheckedSearchSettings(**settings) if settings else DEFAULT)


NOT_COMPARED = ic.NOTE_TITLE_NOT_COMPARED.format(title="Some Show")


# ── judge: Radarr (V6, no fallback titles) ───────────────────────────────────

def test_radarr_the_right_movie_fits():
    v = judge_movie(FILM_RELEASE, radarr_parse("Some Film", 2005, movie(5, "Some Film", 2005)))
    assert v == ic.ImportVerdict(ic.VERDICT_FITS)


def test_radarr_a_foreign_year_is_foreign_with_the_years_as_detail():
    v = judge_movie("Some.Film.2009.German.1080p.BluRay.x264-GRP", radarr_parse("Some Film", 2009))
    assert v.state == ic.VERDICT_FOREIGN
    assert v.reasons == (REASON_YEAR,)
    assert v.details == ("release year 2009, movie 2005 (tolerance 1)",)
    assert v.error == ""


def test_radarr_a_movie_parse_maps_elsewhere_is_foreign():
    other = movie(9, "Other Film", 2005)
    v = judge_movie("Other.Film.2005.German.1080p.BluRay.x264-GRP", radarr_parse("Other Film", 2005, other))
    assert v.state == ic.VERDICT_FOREIGN
    assert v.reasons == (REASON_OTHER_MOVIE, REASON_TITLE)
    assert v.details == ("Radarr maps the name to 'Other Film' (2005)",
                         "name 'Other Film' matches no title of 'Some Film'")


def test_radarr_a_name_without_year_needs_an_exact_title():
    v = judge_movie("Some.Films.German.1080p.BluRay.x264-GRP", radarr_parse("Some Films", 0))
    assert v.reasons == (REASON_TITLE_WITHOUT_YEAR,)
    assert v.details == ("name 'Some Films' has no year and is not an exact title of 'Some Film'",)


def test_radarr_the_existing_file_is_only_a_note():
    with_file = movie(5, "Some Film", 2005, movieFile={"sceneName": FILM_RELEASE,
                                                        "relativePath": "Some Film (2005).mkv"})
    v = judge_movie(FILM_RELEASE, radarr_parse("Some Film", 2005), with_file)
    assert v.state == ic.VERDICT_FITS
    assert v.reasons == ()
    assert v.notes == (ic.NOTE_EXISTING_FILE,)
    assert REASON_EXISTING_FILE not in v.reasons
    assert judge_movie(FILM_RELEASE, radarr_parse("Some Film", 2005), with_file, skip_existing_file=False).notes == ()


@pytest.mark.parametrize("parse", [{"title": FILM_RELEASE}, {"title": FILM_RELEASE, "parsedMovieInfo": None},
                                   None])
def test_radarr_an_unreadable_name_is_unknown_not_foreign(parse):
    # Without the fallback titles of GET /release, V6 would call it "title without year".
    v = judge_movie(FILM_RELEASE, parse)
    assert v == ic.ImportVerdict(ic.VERDICT_UNKNOWN, error="Radarr cannot read the release name")


def test_radarr_year_tolerance_is_the_instance_setting():
    release, parse = "Some.Film.2006.German.1080p.BluRay.x264-GRP", radarr_parse("Some Film", 2006)
    assert judge_movie(release, parse).state == ic.VERDICT_FITS
    strict = judge_movie(release, parse, year_tolerance=0)
    assert strict.state == ic.VERDICT_FOREIGN
    assert strict.details == ("release year 2006, movie 2005 (tolerance 0)",)


def test_radarr_without_a_movie_is_unknown():
    v = judge_movie(FILM_RELEASE, radarr_parse("Some Film", 2005), None)
    assert v == ic.ImportVerdict(ic.VERDICT_UNKNOWN, error=ic.ERROR_NO_TARGET)


# ── judge: Sonarr (S1-S4 per episode) ────────────────────────────────────────

def test_sonarr_fits_with_the_note_that_the_title_was_not_compared():
    v = judge_show(SHOW_RELEASE, sonarr_parse("Some Show"))
    assert v.state == ic.VERDICT_FITS
    assert v.reasons == ()
    assert v.notes == ("Sonarr maps 'Some Show' to no series — title not compared",)


def test_sonarr_mapped_to_this_series_has_no_title_note():
    v = judge_show(SHOW_RELEASE, sonarr_parse("Some Show", series_res=SOME_SHOW))
    assert v == ic.ImportVerdict(ic.VERDICT_FITS)


def test_sonarr_another_series_is_foreign():
    other = series(99, "Other Show", 2019)
    v = judge_show("Other.Show.S01E01.German.1080p.WEB.h264-GRP", sonarr_parse("Other Show", series_res=other))
    assert v.state == ic.VERDICT_FOREIGN
    assert v.reasons == (REASON_OTHER_SERIES,)
    assert v.details == ("Sonarr maps the name to 'Other Show' (2019)",)
    assert v.notes == ()


def test_sonarr_published_too_early_is_foreign():
    v = judge_show(SHOW_RELEASE, sonarr_parse("Some Show"), published=AIRED - timedelta(days=400))
    assert v.state == ic.VERDICT_FOREIGN
    assert v.reasons == (REASON_TOO_EARLY,)
    assert v.details == ("published 400 days before S01E01 aired",)


def test_sonarr_without_a_publish_date_skips_s3_with_a_note():
    v = judge_show(SHOW_RELEASE, sonarr_parse("Some Show", series_res=SOME_SHOW), published=None)
    assert v.state == ic.VERDICT_FITS
    assert v.notes == ("publish date unknown — S3 not checked",)


def test_sonarr_a_rule_note_is_kept():
    v = judge_show(SHOW_RELEASE, sonarr_parse("Some Show", series_res=SOME_SHOW),
                   published=AIRED - timedelta(days=30))
    assert v.state == ic.VERDICT_FITS
    assert v.notes == ("published 30 days before air date",)


def test_sonarr_two_episodes_with_one_too_early_is_foreign_without_a_search_gate():
    published = datetime(2026, 9, 20, 3, 0, tzinfo=UTC)
    v = judge_show("Some.Show.S01E01E02.German.1080p.WEB.h264-GRP",
                   sonarr_parse("Some Show", numbers=(1, 2), series_res=SOME_SHOW),
                   episodes=(E02, E01), published=published)
    assert v.state == ic.VERDICT_FOREIGN
    assert v.reasons == (REASON_TOO_EARLY,)
    assert v.details == ("published 407 days before S01E02 aired",)
    for gate in (REASON_MULTI_EPISODE, REASON_SEASON_PACK, REASON_TARGET):
        assert gate not in v.reasons


def test_sonarr_a_wrong_episode_number_is_only_a_note():
    v = judge_show("Some.Show.S01E05.German.1080p.WEB.h264-GRP", sonarr_parse("Some Show", numbers=(5,)))
    assert v.state == ic.VERDICT_FITS
    assert v.notes == (NOT_COMPARED, "episode numbers differ — name S01E05, proposal S01E01")


def test_sonarr_episode_numbers_of_several_episodes_and_seasons():
    s02e01 = episode(7, 10, 2, 1, AIRED_TEXT)
    v = judge_show("Some.Show.S01E01E02.German.1080p.WEB.h264-GRP",
                   sonarr_parse("Some Show", numbers=(1, 2), series_res=SOME_SHOW), episodes=(E01, s02e01))
    assert v.notes == ("episode numbers differ — name S01E01E02, proposal S01E01 S02E01",)


def test_sonarr_absolute_numbers_are_compared_when_the_name_has_no_season():
    anime = episode(3, 10, 1, 1, AIRED_TEXT, absolute=13)
    parse = sonarr_parse("Some Show", season=None, numbers=(), absolute=(12,), series_res=SOME_SHOW)
    assert judge_show("Some.Show.12.German.1080p.WEB.h264-GRP", parse, episodes=(anime,)).notes == (
        "episode numbers differ — name #12, proposal #13",)
    parse_13 = sonarr_parse("Some Show", season=None, numbers=(), absolute=(13,), series_res=SOME_SHOW)
    assert judge_show("Some.Show.13.German.1080p.WEB.h264-GRP", parse_13, episodes=(anime,)).notes == ()
    # An episode without an absolute number cannot be compared: no note.
    assert judge_show("Some.Show.12.German.1080p.WEB.h264-GRP", parse).notes == ()


@pytest.mark.parametrize("flags", [{"fullSeason": True}, {"isDaily": True}])
def test_sonarr_full_season_and_daily_names_are_not_compared(flags):
    parse = sonarr_parse("Some Show", numbers=(5,), series_res=SOME_SHOW, **flags)
    assert judge_show(SHOW_RELEASE, parse).notes == ()


def test_sonarr_notes_come_in_a_fixed_order():
    v = judge_show("Some.Show.S01E05.German.1080p.WEB.h264-GRP", sonarr_parse("Some Show", numbers=(5,)),
                   published=None)
    assert v.notes == (NOT_COMPARED, "episode numbers differ — name S01E05, proposal S01E01",
                       ic.NOTE_NO_PUBLISH_DATE)


def test_sonarr_suffix_reasons_each_get_a_detail():
    v = judge_show("Some.Show.UK.2024.S01E01.German.1080p.WEB.h264-GRP", sonarr_parse("Some Show UK 2024"))
    assert v.reasons == (REASON_YEAR_SUFFIX, REASON_COUNTRY_SUFFIX)
    assert v.details == ("suffix 2024 vs series 2020", "suffix UK in no title of 'Some Show' (2020)")


def test_sonarr_the_existing_file_is_only_a_note():
    with_file = episode(3, 10, 1, 1, AIRED_TEXT, scene_name=SHOW_RELEASE)
    v = judge_show(SHOW_RELEASE, sonarr_parse("Some Show", series_res=SOME_SHOW), episodes=(with_file,))
    assert v == ic.ImportVerdict(ic.VERDICT_FITS, notes=(ic.NOTE_EXISTING_FILE,))


def test_sonarr_an_unreadable_name_is_unknown():
    v = judge_show(SHOW_RELEASE, {"title": SHOW_RELEASE})
    assert v == ic.ImportVerdict(ic.VERDICT_UNKNOWN, error="Sonarr cannot read the release name")


@pytest.mark.parametrize("series_res, episodes", [(None, (E01,)), (SOME_SHOW, ())])
def test_sonarr_without_series_or_episodes_is_unknown(series_res, episodes):
    v = judge_show(SHOW_RELEASE, sonarr_parse("Some Show"), episodes=episodes, series_res=series_res)
    assert v == ic.ImportVerdict(ic.VERDICT_UNKNOWN, error=ic.ERROR_NO_TARGET)


def test_judge_refuses_an_unknown_app():
    with pytest.raises(ValueError):
        ic.judge("lidarr", SHOW_RELEASE, sonarr_parse("Some Show"), None, SOME_SHOW, [E01], AIRED, DEFAULT)


def test_verdict_as_dict_has_lists():
    v = ic.ImportVerdict(ic.VERDICT_FOREIGN, (REASON_YEAR,), ("a note",), ("a detail",))
    assert v.as_dict() == {"state": "foreign", "reasons": ["year"], "notes": ["a note"],
                           "details": ["a detail"], "error": ""}


# ── check_import: reading through the agent's http_get ───────────────────────

SHOW_TARGET = ic.ImportTarget(series_id=10, episode_ids=(4, 3))
FILM_TARGET = ic.ImportTarget(movie_id=5)


def radarr_fake(**kwargs):
    defaults = dict(parses={FILM_RELEASE: radarr_parse("Some Film", 2005)}, movies=[SOME_FILM])
    defaults.update(kwargs)
    return FakeArr(config(arr_type="radarr"), **defaults)


def sonarr_fake(**kwargs):
    defaults = dict(parses={SHOW_RELEASE: sonarr_parse("Some Show", numbers=(1, 2))}, series_list=[SOME_SHOW],
                    episodes=[E01, episode(4, 10, 1, 2, "2026-10-04T18:00:00Z")],
                    history={"dl-1": [grab_record("dl-1", published="2026-09-27T03:00:00Z")]})
    defaults.update(kwargs)
    return FakeArr(config(arr_type="sonarr"), **defaults)


def check_show(fake, target=SHOW_TARGET, **kwargs):
    kwargs.setdefault("download_id", "dl-1")
    return ic.check_import(fake.http_get, "sonarr", SHOW_RELEASE, target, DEFAULT, **kwargs)


def test_radarr_reads_parse_and_the_movie_two_requests():
    fake = radarr_fake()
    v = ic.check_import(fake.http_get, "radarr", FILM_RELEASE, FILM_TARGET, DEFAULT, download_id="dl-1")
    assert v.state == ic.VERDICT_FITS
    assert fake.gets == [(PARSE, {"title": FILM_RELEASE}), ("/api/v3/movie/5", {})]
    assert fake.posts == [] and fake.deletes == []


def test_sonarr_reads_parse_series_episodes_and_history_four_requests():
    fake = sonarr_fake()
    v = check_show(fake)
    assert v.state == ic.VERDICT_FITS
    assert v.notes == (NOT_COMPARED,)
    assert fake.gets == [
        (PARSE, {"title": SHOW_RELEASE}),
        ("/api/v3/series/10", {}),
        (EPISODE, {"episodeIds": [3, 4], "includeEpisodeFile": "true"}),
        (HISTORY, {"downloadId": "dl-1", "eventType": 1}),
    ]
    assert fake.posts == [] and fake.deletes == []


def test_sonarr_uses_the_publish_date_of_the_grab():
    fake = sonarr_fake(history={"dl-1": [{"eventType": "downloadFolderImported", "data": {}},
                                         grab_record("dl-1", published="2025-08-01T03:00:00Z")]})
    v = check_show(fake)
    assert v.state == ic.VERDICT_FOREIGN
    assert v.reasons == (REASON_TOO_EARLY,)
    assert v.details == ("published 422 days before S01E01 aired",)


def test_sonarr_series_comes_from_the_cache_the_second_time():
    fake, cache = sonarr_fake(), {}
    check_show(fake, cache=cache)
    check_show(fake, cache=cache)
    series_reads = [path for path, _ in fake.gets if path == "/api/v3/series/10"]
    assert series_reads == ["/api/v3/series/10"]
    assert len(fake.gets) == 4 + 3
    assert cache[("series", 10)]["title"] == "Some Show"


def test_sonarr_without_download_id_reads_no_history():
    fake = sonarr_fake()
    v = check_show(fake, download_id=None)
    assert [path for path, _ in fake.gets] == [PARSE, "/api/v3/series/10", EPISODE]
    assert v.notes == (NOT_COMPARED, ic.NOTE_NO_PUBLISH_DATE)


def test_sonarr_history_without_a_grab_gives_the_no_date_note():
    v = check_show(sonarr_fake(history={}))
    assert v.state == ic.VERDICT_FITS
    assert v.notes == (NOT_COMPARED, ic.NOTE_NO_PUBLISH_DATE)


@pytest.mark.parametrize("error", [http_error(500), timed_out(), ValueError("no JSON")])
def test_sonarr_unreadable_history_is_only_a_note(error):
    v = check_show(sonarr_fake(get_errors={HISTORY: error}))
    assert v.state == ic.VERDICT_FITS
    assert v.error == ""
    assert v.notes == (NOT_COMPARED, "grab history not readable — S3 not checked")
    assert ic.NOTE_NO_PUBLISH_DATE not in v.notes


@pytest.mark.parametrize("arr_type, pattern, error, message, requests_made", [
    ("radarr", PARSE, timed_out(), "could not read /parse: timed out", 1),
    ("radarr", "/api/v3/movie/", http_error(404), "could not read the movie: HTTP 404", 2),
    ("radarr", PARSE, ValueError("no JSON"), "could not read /parse: unexpected answer", 1),
    ("sonarr", PARSE, RuntimeError("boom"), "could not read /parse: RuntimeError", 1),
    ("sonarr", "/api/v3/series/", refused(), "could not read the series: no connection", 2),
    ("sonarr", EPISODE, http_error(500), "could not read the episodes: HTTP 500", 3),
])
def test_every_failed_read_is_unknown_and_stops(arr_type, pattern, error, message, requests_made):
    if arr_type == "radarr":
        fake, release, target = radarr_fake(get_errors={pattern: error}), FILM_RELEASE, FILM_TARGET
    else:
        fake, release, target = sonarr_fake(get_errors={pattern: error}), SHOW_RELEASE, SHOW_TARGET
    v = ic.check_import(fake.http_get, arr_type, release, target, DEFAULT, download_id="dl-1")
    assert v == ic.ImportVerdict(ic.VERDICT_UNKNOWN, error=message)
    assert len(fake.gets) == requests_made


def test_a_missing_movie_is_unknown():
    fake = radarr_fake(movies=[])
    v = ic.check_import(fake.http_get, "radarr", FILM_RELEASE, FILM_TARGET, DEFAULT)
    assert v == ic.ImportVerdict(ic.VERDICT_UNKNOWN, error="could not read the movie: HTTP 404")


def test_an_episode_missing_from_the_answer_is_unknown():
    fake = sonarr_fake(episodes=[E01])
    v = check_show(fake)
    assert v == ic.ImportVerdict(ic.VERDICT_UNKNOWN, error="could not read the episodes: unexpected answer")
    assert [path for path, _ in fake.gets] == [PARSE, "/api/v3/series/10", EPISODE]


def test_an_answer_of_the_wrong_shape_is_unknown():
    fake = radarr_fake(parses={FILM_RELEASE: ["not", "a", "dict"]})
    v = ic.check_import(fake.http_get, "radarr", FILM_RELEASE, FILM_TARGET, DEFAULT)
    assert v.error == "could not read /parse: unexpected answer"


@pytest.mark.parametrize("arr_type, release", [("radarr", FILM_RELEASE), ("sonarr", SHOW_RELEASE)])
def test_an_unreadable_name_stops_after_parse(arr_type, release):
    fake = FakeArr(config(arr_type=arr_type), movies=[SOME_FILM], series_list=[SOME_SHOW], episodes=[E01])
    target = FILM_TARGET if arr_type == "radarr" else SHOW_TARGET
    v = ic.check_import(fake.http_get, arr_type, release, target, DEFAULT, download_id="dl-1")
    assert v.state == ic.VERDICT_UNKNOWN
    assert v.error == ic.ERROR_UNREADABLE_NAME.format(app=arr_type.capitalize())
    assert fake.gets == [(PARSE, {"title": release})]


@pytest.mark.parametrize("arr_type, target", [
    ("radarr", None), ("radarr", ic.ImportTarget()), ("radarr", ic.ImportTarget(series_id=10, episode_ids=(3,))),
    ("sonarr", None), ("sonarr", ic.ImportTarget(series_id=10)), ("sonarr", ic.ImportTarget(movie_id=5)),
    ("sonarr", ic.ImportTarget(series_id=10, episode_ids=(0,))),
])
def test_without_a_single_target_nothing_is_read(arr_type, target):
    fake = FakeArr(config(arr_type=arr_type))
    v = ic.check_import(fake.http_get, arr_type, SHOW_RELEASE, target, DEFAULT, download_id="dl-1")
    assert v == ic.ImportVerdict(ic.VERDICT_UNKNOWN, error="no single target in the proposal")
    assert fake.gets == []


def test_check_import_refuses_an_unknown_app():
    fake = FakeArr(config(arr_type="sonarr"))
    with pytest.raises(ValueError):
        ic.check_import(fake.http_get, "lidarr", SHOW_RELEASE, SHOW_TARGET, DEFAULT)
    assert fake.gets == []


def test_history_data_never_leaves_the_check():
    fake, cache = sonarr_fake(), {}
    v = check_show(fake, cache=cache)
    # The fake really handed out the indexer key ...
    assert INDEXER_KEY in str(fake.history)
    # ... and nothing of it is in the verdict or the cache.
    assert INDEXER_KEY not in repr(v)
    assert INDEXER_KEY not in str(v.as_dict())
    assert INDEXER_KEY not in repr(cache)
    assert "downloadUrl" not in repr(v) and "nzbInfoUrl" not in repr(v)
    assert fake.posts == [] and fake.deletes == [] and fake.logged == []


# ── The shared fake behaves like the apps where the feature relies on it ─────

def test_fake_queue_comes_in_pages_and_hides_unknown_items_without_the_switch():
    fake = FakeArr(config(arr_type="sonarr"), queue=[
        queue_record(1, "a", series_id=10, episode_id=3), queue_record(2, "a", series_id=10, episode_id=4),
        queue_record(3, "b"), delay_record(4, "Some.Show.S01E03", series_id=10)])
    first = fake.http_get(QUEUE, params={"page": 1, "pageSize": 3, "includeUnknownSeriesItems": "true"})
    second = fake.http_get(QUEUE, params={"page": 2, "pageSize": 3, "includeUnknownSeriesItems": "true"})
    assert first["totalRecords"] == 4 and [r["id"] for r in first["records"]] == [1, 2, 3]
    assert [r["id"] for r in second["records"]] == [4]
    hidden = fake.http_get(QUEUE, params={"page": 1, "pageSize": 10})
    assert [r["id"] for r in hidden["records"]] == [1, 2, 4]
    with pytest.raises(AssertionError):
        fake.http_get(QUEUE)


def test_fake_queue_details_is_the_whole_queue_in_one_answer():
    fake = FakeArr(config(arr_type="radarr"), queue=[
        queue_record(1, "a", movie_id=5), queue_record(2, "b"), delay_record(3, "Some.Film.2005", movie_id=5)])
    assert [r["id"] for r in fake.http_get(QUEUE_DETAILS)] == [1, 2, 3]       # unknown item and delay included
    with pytest.raises(AssertionError):
        fake.http_get(QUEUE_DETAILS, params={"page": 1})
    with pytest.raises(type(refused())):
        FakeArr(config(), get_errors={QUEUE: refused()}).http_get(QUEUE_DETAILS)      # QUEUE is a prefix


def test_fake_queue_status_follows_the_records():
    fake = FakeArr(config(), queue=[delay_record(4, "Some.Show.S01E03", series_id=10)])
    status = fake.http_get(QUEUE_STATUS)
    assert (status["errors"], status["warnings"], status["unknownErrors"], status["unknownWarnings"]) == (
        False, False, False, False)
    fake.queue.append(queue_record(1, "a", series_id=10))
    assert fake.http_get(QUEUE_STATUS)["warnings"] is True
    assert fake.http_get(SYSTEM_STATUS)["startTime"] == "2026-09-30T12:00:00Z"


def test_fake_records_leave_out_what_is_none():
    record = queue_record(1, "a", added=None, messages=(), size=5)
    assert "added" not in record and "statusMessages" not in record and "movieId" not in record
    assert "downloadId" not in delay_record(2, "x") and "trackedDownloadState" not in delay_record(2, "x")
    fallback = sonarr_item("/downloads/complete/x/x.mkv", None, None, None, quality_res=None, languages=None)
    assert {"series", "seasonNumber", "episodes", "quality", "languages"}.isdisjoint(fallback)
    assert fallback["rejections"] == [] and fallback["relativePath"] == "x.mkv"
    item = radarr_item("/downloads/complete/y/y.mkv", SOME_FILM, rejections=("Sample",))
    assert item["movie"]["id"] == 5 and item["quality"] == quality()
    assert item["rejections"] == [{"reason": "Sample", "type": "permanent"}]


def test_fake_manual_import_insists_on_download_id_alone():
    items = [sonarr_item("/downloads/complete/a/a.mkv", SOME_SHOW, 1, [E01])]
    fake = FakeArr(config(), manual_imports={"a": items})
    assert fake.http_get(MANUAL_IMPORT, params={"downloadId": "a", "filterExistingFiles": "true"}) == items
    assert fake.http_get(MANUAL_IMPORT, params={"downloadId": "gone", "filterExistingFiles": "true"}) == []
    with pytest.raises(AssertionError):
        fake.http_get(MANUAL_IMPORT, params={"downloadId": "a", "filterExistingFiles": "true", "seriesId": 10})


def test_fake_keeps_posted_commands():
    fake = FakeArr(config())
    started = fake.http_post(COMMAND, {"name": "ManualImport", "importMode": "auto", "files": []}, timeout=30)
    assert started["id"] == 500 and started["status"] == "queued"
    assert fake.timeouts[COMMAND] == 30
    assert [c["id"] for c in fake.http_get(COMMAND)] == [500]
    fake.finish_command(500, "failed", exception="File not found")
    assert fake.http_get(f"{COMMAND}/500")["exception"] == "File not found"
    with pytest.raises(Exception) as missing:
        fake.http_get(f"{COMMAND}/999")
    assert missing.value.response.status_code == 404


def test_fake_keeps_a_command_whose_answer_got_lost_and_records_imports():
    fake = FakeArr(config(), queue=[queue_record(1, "a", series_id=10, episode_id=3), queue_record(2, "b")],
                   history={"a": [grab_record("a")]}, post_lost=timed_out())
    with pytest.raises(type(timed_out())):
        fake.http_post(COMMAND, {"name": "ManualImport", "importMode": "auto", "files": []})
    assert [c["status"] for c in fake.http_get(COMMAND)] == ["queued"]     # the app has it all the same
    fake.imported("a")
    assert [r["id"] for r in fake.queue] == [2]
    answer = fake.http_get(HISTORY, params={"downloadId": "a", "eventType": 3})
    assert [r["eventType"] for r in answer["records"]] == ["grabbed", "downloadFolderImported"]   # not filtered
    with pytest.raises(AssertionError):
        fake.http_get(HISTORY, params={"downloadId": "a", "eventType": 4})


def test_fake_delays_a_post_and_runs_a_hook_before_answering():
    fake = FakeArr(config(), post_delayed=timed_out())
    with pytest.raises(type(timed_out())):
        fake.http_post(COMMAND, {"name": "ManualImport", "importMode": "auto", "files": []})
    assert fake.http_get(COMMAND) == [] and len(fake.delayed) == 1     # the app has nothing yet
    assert fake.arrive() == [500]                                      # the proxy forwards it late
    assert [c["status"] for c in fake.http_get(COMMAND)] == ["queued"] and fake.delayed == []
    seen = []
    fake = FakeArr(config(), on_post={COMMAND: lambda f: seen.append(sorted(f.commands))})
    assert fake.http_post(COMMAND, {"name": "ManualImport", "importMode": "auto", "files": []})["id"] == 500
    assert seen == [[500]]                                             # kept before the hook ran


def test_fake_delete_removes_the_whole_download():
    fake = FakeArr(config(), queue=[queue_record(1, "a", series_id=10, episode_id=3),
                                    queue_record(2, "a", series_id=10, episode_id=4), queue_record(3, "b")])
    assert fake.http_delete(f"{QUEUE}/1", params={"blocklist": "true"}) is None
    assert [r["id"] for r in fake.queue] == [3]
    with pytest.raises(Exception) as gone:
        fake.http_delete(f"{QUEUE}/1")
    assert gone.value.response.status_code == 404
    assert fake.deletes[0] == (f"{QUEUE}/1", {"blocklist": "true"})


def test_fake_log_only_records():
    fake = FakeArr(config())
    fake.log("info", "imports", "Import sent")
    assert fake.logged == [("info", "imports", "Import sent")]
    assert fake.config["api_key"] == API_KEY
