from datetime import datetime, timezone

from backend.checked_search.normalize import (
    compact, parse_utc, same_release, strip_extension, tokens, variants, year_of,
)


def test_compact_keeps_only_letters_and_digits():
    assert compact("The Thing (1982)") == "thething1982"
    assert compact("Foo & Bar") == "fooandbar"
    assert compact("Exémple") == "exemple"
    assert compact(None) == ""


def test_variants_spell_umlauts_both_ways():
    assert variants("Schöne Tage") == {"schonetage", "schoenetage"}


def test_variants_spell_ampersand_as_and_and_und():
    assert variants("Foo & Bar") == {"fooandbar", "fooundbar"}


def test_sharp_s_follows_the_reference():
    # ss in the umlaut spelling, dropped in the plain one (reference V6)
    assert variants("Straße") == {"strasse", "strae"}


def test_variants_drop_empty_spellings():
    assert variants("…") == set()


def test_tokens_split_into_words():
    assert tokens("Example Day: Return") == ["example", "day", "return"]
    assert tokens("Foo & Bar") == ["foo", "und", "bar"]
    assert tokens("Die Schöne") == ["die", "schoene"]


def test_same_release_ignores_separators_and_case():
    assert same_release("Movie.2020.1080p.BluRay-GRP", "movie 2020 1080p bluray grp")
    assert not same_release("Movie.2020.1080p-GRP", "Movie.2020.720p-GRP")
    assert not same_release("", "")


def test_strip_extension_and_directory():
    assert strip_extension("Movie (2020)/Movie.2020.1080p-GRP.mkv") == "Movie.2020.1080p-GRP"
    assert strip_extension(None) == ""


def test_year_of_arr_dates():
    assert year_of("2016-06-23T00:00:00Z") == 2016
    assert year_of(None) == 0
    assert year_of("unknown") == 0


def test_parse_utc():
    assert parse_utc("2026-09-27T18:00:00Z") == datetime(2026, 9, 27, 18, tzinfo=timezone.utc)
    assert parse_utc("nonsense") is None
