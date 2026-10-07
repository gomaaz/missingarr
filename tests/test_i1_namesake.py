"""0.12.0, Sonarr rule S5: a name without year that /parse maps to no series
and that is also the title of another series of the library (pure functions,
invented series)."""
from datetime import datetime, timezone

import pytest

from backend.checked_search import sonarr_rules as sr
from backend.checked_search.settings import DEFAULT_COUNTRY_CODES, CheckedSearchSettings
from backend.checked_search.verdict import REASON_NAMESAKE

CODES = DEFAULT_COUNTRY_CODES
LIBRARY = [
    {"id": 1, "title": "Some Show (2020)", "year": 2020, "alternateTitles": []},
    {"id": 2, "title": "Some Show (AU)", "year": 2004, "alternateTitles": []},
    {"id": 3, "title": "Some Show (2026)", "year": 2026, "alternateTitles": []},
    {"id": 4, "title": "Grün & Blau", "year": 2015, "alternateTitles": [{"title": "Green and Blue (US)"}]},
    {"id": 5, "title": "My Royal Some Show", "year": 2026, "alternateTitles": []},
    {"title": "no id"}, "garbage",
]
INDEX = sr.namesake_index(LIBRARY, CODES)
TARGET = sr.EpisodeInfo(3, ("Some Show (2026)",), 2026, datetime(2026, 9, 1, tzinfo=timezone.utc))
NAME = "Some.Show.S01E05.1080p.WEB-DL.x264-GRP"
NO_SERIES = sr.EpisodeParse(None, "Some Show")


def namesakes(parse=NO_SERIES, name=NAME, info=TARGET, settings=None):
    return sr.namesakes(info, name, parse, INDEX, settings or CheckedSearchSettings())


def test_base_keys_are_the_title_with_and_without_its_suffix():
    assert sr.base_keys("Some Show (2020)", CODES) == {"someshow2020", "someshow"}
    assert sr.base_keys("Some Show (AU)", CODES) == {"someshowau", "someshow"}
    assert sr.base_keys("Some Show", CODES) == {"someshow"}
    # umlaut and '&' spellings survive the cut
    assert {"gruenundblau", "grunundblau"} <= sr.base_keys("Grün & Blau (2015)", CODES)


def test_the_index_holds_titles_and_alternate_titles():
    assert INDEX["someshow"] == {(1, 2020), (2, 2004), (3, 2026)}
    assert INDEX["greenandblue"] == {(4, 2015)}
    assert INDEX["gruenundblau"] == {(4, 2015)}


def test_a_name_that_is_the_title_of_other_series_is_a_namesake():
    assert namesakes() == {1, 2}


def test_the_series_searched_for_does_not_count():
    lone = sr.namesake_index([LIBRARY[2]], CODES)
    assert sr.namesakes(TARGET, NAME, NO_SERIES, lone, CheckedSearchSettings()) == frozenset()


def test_each_of_the_first_three_conditions_is_needed():
    assert namesakes(sr.EpisodeParse(1, "Some Show")) == frozenset()            # 1: /parse names a series
    assert namesakes(sr.EpisodeParse(None, "Some Show", 2020)) == frozenset()   # 2: a year was parsed
    assert namesakes(sr.EpisodeParse(None, "Some Show AU")) == frozenset()      # 3: a suffix
    assert namesakes(sr.EpisodeParse(None, "")) == frozenset()


def test_umlaut_spellings_find_the_namesake():
    info = sr.EpisodeInfo(9, ("Gruen und Blau (2024)",), 2024)
    assert namesakes(sr.EpisodeParse(None, "Gruen und Blau"), "Gruen.und.Blau.S01E01-GRP", info) == {4}


def test_no_prefix_or_word_match():
    # 'My Royal Some Show' is no namesake of 'Some Show', nor the other way round
    assert namesakes(sr.EpisodeParse(None, "Royal Some Show")) == frozenset()
    info = sr.EpisodeInfo(5, ("My Royal Some Show",), 2026)
    assert namesakes(sr.EpisodeParse(None, "My Royal Some Show"), "My.Royal.Some.Show.S01E01-GRP", info) == frozenset()


def test_the_year_of_the_series_in_the_name_lifts_the_veto():
    assert namesakes(name="Some.Show.S01E05.2026.1080p.WEB-DL-GRP") == frozenset()
    assert namesakes(name="Some.Show.S01E05.(2026).1080p.WEB-DL-GRP") == frozenset()
    # only as a word of its own
    assert namesakes(name="Some.Show.S01E05.X2026.1080p.WEB-DL-GRP") == {1, 2}
    # not when a namesake has that year as well
    twin = sr.namesake_index(LIBRARY + [{"id": 8, "title": "Some Show (US)", "year": 2026}], CODES)
    assert sr.namesakes(TARGET, "Some.Show.S01E05.2026-GRP", NO_SERIES, twin, CheckedSearchSettings()) == {1, 2, 8}


def test_needs_the_index_only_for_names_that_could_fire():
    settings = CheckedSearchSettings()
    assert sr.needs_namesake_index(NO_SERIES, settings)
    assert not sr.needs_namesake_index(sr.EpisodeParse(1, "Some Show"), settings)
    assert not sr.needs_namesake_index(sr.EpisodeParse(None, "Some Show", 2020), settings)
    assert not sr.needs_namesake_index(sr.EpisodeParse(None, "Some Show 2020"), settings)
    assert not sr.needs_namesake_index(NO_SERIES, CheckedSearchSettings(veto_namesake=False))


def test_evaluate_rejects_with_the_index_and_only_then():
    settings = CheckedSearchSettings()
    context = sr.RuleContext(namesake_index=INDEX)
    assert sr.evaluate(TARGET, NAME, None, NO_SERIES, settings, context).reasons == (REASON_NAMESAKE,)
    # without a context (Imports page) S5 checks nothing
    assert sr.evaluate(TARGET, NAME, None, NO_SERIES, settings).ok
    assert sr.evaluate(TARGET, NAME, None, NO_SERIES, CheckedSearchSettings(veto_namesake=False), context).ok


def test_the_parsed_year_is_read():
    parse = {"parsedEpisodeInfo": {"seriesTitle": "Some Show", "seriesTitleInfo": {"year": 2020}}, "series": None}
    assert sr.parse_from_resource(parse) == sr.EpisodeParse(None, "Some Show", 2020)
    assert sr.parse_from_resource({"parsedEpisodeInfo": {"seriesTitle": "Some Show"}}).year == 0


@pytest.mark.parametrize("title, year, keys", [
    # only a suffix written as one is cut: a code in capitals or brackets, a year in brackets or the series' own
    ("Killing It (2022)", 2022, {"killingit2022", "killingit"}),
    ("Among Us", 2020, {"amongus"}),
    ("Lost in 1949", 2023, {"lostin1949"}),
    ("Some Show US", 2020, {"someshowus", "someshow"}),
    ("Some Show 2020", 2020, {"someshow2020", "someshow"}),
    ("Some Show 2020", 2010, {"someshow2020"}),
    # a code is a word of its own, never the end of one
    ("WAR (2026)", 2026, {"war2026", "war"}),
    ("HIT", 2020, {"hit"}),
])
def test_base_keys_cut_only_what_is_written_as_a_suffix(title, year, keys):
    assert sr.base_keys(title, CODES, year) == keys


def test_the_cut_keeps_an_ampersand_before_the_code():
    keys = sr.base_keys("Alex & CO", CODES, 2015)
    assert "alexund" in keys and "alex" not in keys
    assert sr.base_keys("Alex & Co", CODES, 2015) == {"alexandco", "alexundco"}


def test_a_title_that_only_begins_with_the_name_is_no_namesake():
    library = [{"id": 1, "title": "Killing It (2022)", "year": 2022}, {"id": 2, "title": "Killing (2026)", "year": 2026}]
    index = sr.namesake_index(library, CODES)
    info = sr.EpisodeInfo(2, ("Killing (2026)",), 2026)
    assert sr.namesakes(info, "Killing.S01E01.1080p.WEB-DL-GRP", sr.EpisodeParse(None, "Killing"), index,
                        CheckedSearchSettings()) == frozenset()
