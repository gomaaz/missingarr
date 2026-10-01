from backend.checked_search import radarr_rules as rr
from backend.checked_search.settings import CheckedSearchSettings
from backend.checked_search.verdict import (
    REASON_EXISTING_FILE, REASON_OTHER_MOVIE, REASON_TITLE, REASON_TITLE_WITHOUT_YEAR, REASON_YEAR,
)

DEFAULT = CheckedSearchSettings()


def movie(names, year, movie_id=1, secondary=0, release_years=(), existing=()):
    return rr.MovieInfo(movie_id=movie_id, names=tuple(names), year=year, secondary_year=secondary,
                        release_years=tuple(release_years), existing_releases=tuple(existing))


def parsed(*titles, year=0, movie_id=None):
    return rr.MovieParse(titles=titles, year=year, movie_id=movie_id)


def check(m, title, p, **settings):
    return rr.evaluate(m, title, p, CheckedSearchSettings(**settings) if settings else DEFAULT)


THE_THING = movie(["Das Ding aus einer anderen Welt", "The Thing"], 1982, release_years=(1982,))
HALLOWEEN = movie(["Halloween - Die Nacht des Grauens", "Halloween"], 1978)
JOHN_CARTER = movie(["John Carter - Zwischen zwei Welten", "John Carter"], 2012)
# Made-up titles for every other case (public repository: no library data).
EXAMPLE_DAY = movie(["Beispieltag: Rückkehr", "Example Day: Return"], 2016)
SOME_FILM = movie(["Some Film"], 2005)


# ── Public examples from the spec ────────────────────────────────────────────

def test_the_thing_rejects_the_1951_film_by_year():
    v = check(THE_THING, "Das.Ding.aus.einer.anderen.Welt.1951.German.DL.1080p.BluRay.x265-GRP",
              parsed("Das Ding aus einer anderen Welt", year=1951))
    assert v.reasons == (REASON_YEAR,)


def test_the_thing_takes_the_right_film():
    v = check(THE_THING, "The.Thing.1982.German.DL.1080p.BluRay.x264-GRP", parsed("The Thing", year=1982, movie_id=1))
    assert v.ok


def test_halloween_2018_is_rejected_for_halloween_1978():
    v = check(HALLOWEEN, "Halloween.2018.German.DL.1080p.BluRay.x264-GRP",
              parsed("Halloween", year=2018, movie_id=2))
    assert v.reasons == (REASON_YEAR, REASON_OTHER_MOVIE)


def test_carter_2022_is_rejected_for_john_carter():
    v = check(JOHN_CARTER, "Carter.2022.German.DL.1080p.WEB.x264-GRP", parsed("Carter", year=2022))
    assert v.reasons == (REASON_YEAR, REASON_TITLE)


def test_umlauts_match_in_both_spellings():
    m = movie(["Schöne Tage", "Beautiful Example Days"], 1989)
    assert check(m, "Schoene.Tage.1989.German.1080p", parsed("Schoene Tage", year=1989)).ok
    assert check(m, "Schone.Tage.1989.German.1080p", parsed("Schone Tage", year=1989)).ok


def test_ampersand_matches_and_and_und():
    m = movie(["Foo & Bar"], 2009)
    assert check(m, "Foo.and.Bar.2009.1080p", parsed("Foo and Bar", year=2009)).ok
    assert check(m, "Foo.und.Bar.2009.1080p", parsed("Foo und Bar", year=2009)).ok


def test_release_without_year_needs_an_exact_title():
    assert check(EXAMPLE_DAY, "Example.Day.Return.German.1080p", parsed("Example Day Return")).ok
    v = check(EXAMPLE_DAY, "Beispieltag.Rueckkehr.Extended.German.1080p",
              parsed("Beispieltag Rueckkehr Extended"))
    assert v.reasons == (REASON_TITLE_WITHOUT_YEAR,)


def test_release_without_year_passes_when_parse_maps_this_movie():
    assert check(EXAMPLE_DAY, "ED2.Return.German.1080p", parsed("ED2 Return", movie_id=1)).ok


def test_veto_when_parse_names_another_movie():
    v = check(EXAMPLE_DAY, "Example.Day.Return.2016.1080p",
              parsed("Example Day Return", year=2016, movie_id=7))
    assert v.reasons == (REASON_OTHER_MOVIE,)


def test_prefix_match():
    v = check(SOME_FILM, "Some.Film.1.2005.German.1080p", parsed("Some Film 1", year=2005),
              word_match=False)
    assert v.ok


def test_word_match():
    v = check(EXAMPLE_DAY, "Example.Day.2.Return.2016.German.1080p",
              parsed("Example Day 2 Return", year=2016))
    assert v.ok


def test_noah_same_title_same_year_is_a_known_limit():
    m = movie(["Noah"], 2014)
    assert check(m, "Noah.2013.German.1080p.WEB-GRP", parsed("Noah", year=2013)).ok


def test_release_of_the_existing_file_is_skipped():
    m = movie(["Example Film"], 1995, existing=("Example.Film.1995.German.DL.1080p.BluRay.x264-GRP",))
    v = check(m, "Example.Film.1995.German.DL.1080p.BluRay.x264-GRP", parsed("Example Film", year=1995))
    assert v.reasons == (REASON_EXISTING_FILE,)


def test_movie_without_year_accepts_only_releases_without_year():
    m = movie(["Unknown Film"], 0)
    assert check(m, "Unknown.Film.German.1080p", parsed("Unknown Film")).ok
    assert check(m, "Unknown.Film.2020.German.1080p", parsed("Unknown Film", year=2020)).reasons == (REASON_YEAR,)


def test_secondary_year_counts():
    m = movie(["Example Film"], 1970, secondary=1971)
    assert check(m, "Example.Film.1972.1080p", parsed("Example Film", year=1972)).ok


# ── Every setting changes the verdict ────────────────────────────────────────

def test_year_tolerance():
    m = movie(["Example Film"], 2016)
    p = parsed("Example Film", year=2017)
    assert check(m, "Example.Film.2017", p).ok
    assert check(m, "Example.Film.2017", p, year_tolerance=0).reasons == (REASON_YEAR,)


def test_count_release_dates_as_years():
    m = movie(["Example Film"], 2019, release_years=(2021,))
    p = parsed("Example Film", year=2021)
    assert check(m, "Example.Film.2021", p).ok
    assert check(m, "Example.Film.2021", p, count_release_dates=False).reasons == (REASON_YEAR,)


def test_veto_can_be_switched_off():
    p = parsed("Example Day Return", year=2016, movie_id=7)
    assert check(EXAMPLE_DAY, "x", p, veto_other_movie=False).ok


def test_prefix_match_switch_and_length():
    p = parsed("Some Film 1", year=2005)
    assert check(SOME_FILM, "x", p, word_match=False, prefix_match=False).reasons == (REASON_TITLE,)
    assert check(SOME_FILM, "x", p, word_match=False, prefix_min_length=20).reasons == (REASON_TITLE,)


def test_word_match_switch_and_core_words():
    p = parsed("Example Day 2 Return", year=2016)
    assert check(EXAMPLE_DAY, "x", p, word_match=False).reasons == (REASON_TITLE,)
    assert check(EXAMPLE_DAY, "x", p, word_min_core_words=4).reasons == (REASON_TITLE,)


def test_releases_without_year_may_use_the_loose_title_rules():
    p = parsed("Beispieltag Rueckkehr Extended")
    assert check(EXAMPLE_DAY, "x", p, no_year_needs_exact=False).ok


def test_skip_existing_file_can_be_switched_off():
    m = movie(["Example Film"], 1995, existing=("Example.Film.1995.German.DL.1080p.BluRay.x264-GRP",))
    p = parsed("Example Film", year=1995)
    assert check(m, "Example.Film.1995.German.DL.1080p.BluRay.x264-GRP", p, skip_existing_file=False).ok


# ── Reading the API resources ────────────────────────────────────────────────

def test_movie_from_resource_reads_the_api_fields():
    m = rr.movie_from_resource({
        "id": 5, "title": "Das Ding aus einer anderen Welt", "originalTitle": "The Thing",
        "alternateTitles": [{"title": "John Carpenter's The Thing", "sourceType": "tmdb"}],
        "year": 1982, "secondaryYear": None,
        "inCinemas": "1982-06-25T00:00:00Z", "digitalRelease": None, "physicalRelease": "1998-05-01T00:00:00Z",
        "movieFile": {"sceneName": "The.Thing.1982.720p-OLD", "relativePath": "The Thing (1982).mkv",
                      "originalFilePath": "dl/The.Thing.1982.720p-OLD.mkv"},
    })
    assert m.movie_id == 5 and m.year == 1982 and m.secondary_year == 0
    assert m.names == ("Das Ding aus einer anderen Welt", "The Thing", "John Carpenter's The Thing")
    assert m.release_years == (1982, 1998)
    assert m.existing_releases == ("The.Thing.1982.720p-OLD", "The Thing (1982)", "The.Thing.1982.720p-OLD")


def test_parse_from_resource_and_fallback():
    p = rr.parse_from_resource({"parsedMovieInfo": {"movieTitles": ["The Thing"], "year": 1982}, "movie": {"id": 5}})
    assert p == rr.MovieParse(("The Thing",), 1982, 5)
    unparsed = rr.parse_from_resource({"title": "garbage"}, fallback_titles=["Garbage"])
    assert unparsed == rr.MovieParse(("Garbage",), 0, None)
