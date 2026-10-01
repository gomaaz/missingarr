from datetime import datetime, timedelta, timezone

from backend.checked_search import sonarr_rules as sr
from backend.checked_search.settings import CheckedSearchSettings
from backend.checked_search.verdict import (
    REASON_COUNTRY_SUFFIX, REASON_EXISTING_FILE, REASON_OTHER_SERIES, REASON_TOO_EARLY, REASON_YEAR_SUFFIX,
)

DEFAULT = CheckedSearchSettings()
AIRED = datetime(2026, 9, 27, 18, 0, tzinfo=timezone.utc)


def info(titles=("The Guest",), year=2018, series_id=10, aired=AIRED, existing=()):
    return sr.EpisodeInfo(series_id=series_id, series_titles=tuple(titles), series_year=year,
                          air_date_utc=aired, existing_releases=tuple(existing))


def check(i, title, published, p, **settings):
    return sr.evaluate(i, title, published, p, CheckedSearchSettings(**settings) if settings else DEFAULT)


GUEST = "The.Guest.CO.2025.S01E10.MULTI.1080p.WEB.X264-GRP"


def test_the_guest_co_2025_is_rejected_by_the_suffix():
    v = check(info(), GUEST, AIRED, sr.EpisodeParse(series_id=None, series_title="The Guest CO 2025"))
    assert v.reasons == (REASON_YEAR_SUFFIX, REASON_COUNTRY_SUFFIX)


def test_suffix_check_can_be_switched_off():
    v = check(info(), GUEST, AIRED, sr.EpisodeParse(series_title="The Guest CO 2025"), check_suffix=False)
    assert v.ok


def test_suffix_year_tolerance():
    i = info(year=2024)
    p = sr.EpisodeParse(series_title="The Guest 2025")
    assert check(i, "x", AIRED, p).ok
    assert check(i, "x", AIRED, p, suffix_year_tolerance=0).reasons == (REASON_YEAR_SUFFIX,)


def test_matching_country_suffix_passes():
    i = info(titles=("Example Show (US)",), year=2005)
    assert check(i, "Example.Show.US.S01E01.1080p", AIRED, sr.EpisodeParse(series_title="Example Show US")).ok


def test_country_code_list_is_a_setting():
    p = sr.EpisodeParse(series_title="The Guest CO")
    assert check(info(), "x", AIRED, p).reasons == (REASON_COUNTRY_SUFFIX,)
    assert check(info(), "x", AIRED, p, country_codes=["US"]).ok


def test_a_title_that_is_only_a_year_has_no_suffix():
    i = info(titles=("1901",), year=2022)
    assert check(i, "1901.S01E01.1080p", AIRED, sr.EpisodeParse(series_title="1901")).ok


def test_release_long_before_the_air_date_is_rejected():
    published = AIRED - timedelta(days=517)
    v = check(info(), "Show.S06E24.German.1080p.WEB.h264-GRP", published, sr.EpisodeParse())
    assert v.reasons == (REASON_TOO_EARLY,)


def test_a_better_place_14_days_early_only_gets_a_note():
    # A single episode: a season pack never reaches the rules in an episode
    # search (Sonarr rejects it as "Full season pack" itself).
    published = AIRED - timedelta(days=14, hours=3)
    v = check(info(titles=("A Better Place",), year=2026), "A.Better.Place.S01E03.German.1080p.WEB-DL.x264-GRP",
              published, sr.EpisodeParse(series_title="A Better Place"))
    assert v.ok
    assert v.notes == ("published 14 days before air date",)


def test_early_thresholds_are_settings():
    published = AIRED - timedelta(days=400)
    assert check(info(), "x", published, sr.EpisodeParse(), reject_days_before_air=500).notes == (
        "published 400 days before air date",)
    assert check(info(), "x", AIRED - timedelta(days=14), sr.EpisodeParse(), note_days_before_air=20).notes == ()


def test_the_reject_threshold_compares_the_exact_span():
    # "more than 365 days": one hour past the threshold is rejected, the
    # threshold itself only gets a note (no rounding down before comparing).
    over = check(info(), "x", AIRED - timedelta(days=365, hours=1), sr.EpisodeParse())
    assert over.reasons == (REASON_TOO_EARLY,)
    at = check(info(), "x", AIRED - timedelta(days=365), sr.EpisodeParse())
    assert at.ok
    assert at.notes == ("published 365 days before air date",)
    assert check(info(), "x", AIRED - timedelta(days=13, hours=23), sr.EpisodeParse()).notes == ()


def test_release_of_the_existing_file_is_skipped():
    i = info(existing=("The.Guest.S01E10.German.1080p.WEB.x264-GRP",))
    v = check(i, "The.Guest.S01E10.German.1080p.WEB.x264-GRP", AIRED, sr.EpisodeParse())
    assert v.reasons == (REASON_EXISTING_FILE,)
    assert check(i, "The.Guest.S01E10.German.1080p.WEB.x264-GRP", AIRED, sr.EpisodeParse(),
                 skip_existing_file=False).ok


def test_veto_when_parse_names_another_series():
    p = sr.EpisodeParse(series_id=99, series_title="Other Show")
    assert check(info(), "x", AIRED, p).reasons == (REASON_OTHER_SERIES,)
    assert check(info(), "x", AIRED, p, veto_other_series=False).ok


def test_every_rule_that_fires_is_reported():
    i = info(existing=(GUEST,))
    v = check(i, GUEST, AIRED - timedelta(days=400), sr.EpisodeParse(series_id=99, series_title="The Guest CO 2025"))
    assert v.reasons == (REASON_OTHER_SERIES, REASON_YEAR_SUFFIX, REASON_COUNTRY_SUFFIX,
                         REASON_TOO_EARLY, REASON_EXISTING_FILE)


def test_resources_are_read_from_the_api_fields():
    i = sr.episode_from_resources(
        {"id": 3, "seriesId": 10, "airDateUtc": "2026-09-27T18:00:00Z",
         "episodeFile": {"sceneName": "The.Guest.S01E10-GRP", "relativePath": "Season 1/The Guest - S01E10.mkv"}},
        {"id": 10, "title": "The Guest", "year": 2018, "alternateTitles": [{"title": "Example Guest", "seasonNumber": -1}]},
    )
    assert i == sr.EpisodeInfo(10, ("The Guest", "Example Guest"), 2018, AIRED,
                               ("The.Guest.S01E10-GRP", "The Guest - S01E10"))
    p = sr.parse_from_resource({"parsedEpisodeInfo": {"seriesTitle": "The Guest CO 2025",
                                                      "seriesTitleInfo": {"year": 2025}}, "series": {"id": 10}})
    assert p == sr.EpisodeParse(10, "The Guest CO 2025")
