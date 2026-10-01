"""Pre-filter V6 for Radarr releases. Pure functions, no network.

Rules (all must hold, see docs/superpowers/specs/2026-10-01-checked-search-design.md):
  a  year      release year within the tolerance of a year of the movie
  b  veto      a release with a year that /parse maps to another movie
  c  title     exact name, /parse maps to this movie, prefix, or word match
  d  no year   a release without a year needs an exact name or the /parse match
  1a file      the release of the existing file is never grabbed again

evaluate() collects every rule that rejects, so the log names all of them.
A release passes exactly when the sequential reference lets it through.
"""

from dataclasses import dataclass

from backend.checked_search.normalize import (
    STOP_WORDS, same_release, strip_extension, tokens, variants, year_of,
)
from backend.checked_search.settings import CheckedSearchSettings
from backend.checked_search.verdict import (
    REASON_EXISTING_FILE, REASON_OTHER_MOVIE, REASON_TITLE, REASON_TITLE_WITHOUT_YEAR, REASON_YEAR, Verdict,
)


@dataclass(frozen=True)
class MovieInfo:
    movie_id: int
    names: tuple[str, ...]                 # title, originalTitle, alternateTitles[].title
    year: int = 0
    secondary_year: int = 0
    release_years: tuple[int, ...] = ()    # years of inCinemas, digitalRelease, physicalRelease
    existing_releases: tuple[str, ...] = ()  # sceneName and file names without extension


@dataclass(frozen=True)
class MovieParse:
    titles: tuple[str, ...] = ()           # parsedMovieInfo.movieTitles
    year: int = 0                          # parsedMovieInfo.year, 0 = none
    movie_id: int | None = None            # movie.id: the library movie /parse maps it to


def movie_from_resource(movie: dict) -> MovieInfo:
    """GET /api/v3/movie/{id}. `title` is already the translation in Radarr's
    movie info language (or the base title)."""
    names = [movie.get("title"), movie.get("originalTitle")]
    names += [alt.get("title") for alt in movie.get("alternateTitles") or [] if isinstance(alt, dict)]
    movie_file = movie.get("movieFile") or {}
    existing = [
        movie_file.get("sceneName"),
        strip_extension(movie_file.get("relativePath")),
        strip_extension(movie_file.get("originalFilePath")),
    ]
    release_years = [year_of(movie.get(key)) for key in ("inCinemas", "digitalRelease", "physicalRelease")]
    return MovieInfo(
        movie_id=int(movie.get("id") or 0),
        names=tuple(name for name in names if name),
        year=int(movie.get("year") or 0),
        secondary_year=int(movie.get("secondaryYear") or 0),
        release_years=tuple(year for year in release_years if year),
        existing_releases=tuple(name for name in existing if name),
    )


def parse_from_resource(parse: dict | None, fallback_titles=()) -> MovieParse:
    """GET /api/v3/parse?title=…  Without parsedMovieInfo (Radarr could not
    parse the name) the release's own movieTitles stand in, like in the
    reference."""
    parse = parse or {}
    info = parse.get("parsedMovieInfo") or {}
    movie = parse.get("movie") or {}
    titles = tuple(t for t in (info.get("movieTitles") or []) if t) or tuple(t for t in fallback_titles or () if t)
    movie_id = movie.get("id") if isinstance(movie, dict) else None
    return MovieParse(titles=titles, year=int(info.get("year") or 0), movie_id=int(movie_id) if movie_id else None)


def _movie_years(movie: MovieInfo, settings: CheckedSearchSettings) -> set[int]:
    years = {y for y in (movie.year, movie.secondary_year) if y}
    if settings.count_release_dates:
        years |= set(movie.release_years)
    return years


def _year_ok(movie: MovieInfo, parse: MovieParse, settings: CheckedSearchSettings) -> bool:
    if not movie.year and not movie.secondary_year:
        return parse.year == 0      # a movie without a year: only releases without one
    if parse.year == 0:
        return True                 # rule d decides
    return any(abs(parse.year - y) <= settings.year_tolerance for y in _movie_years(movie, settings))


def _exact(movie: MovieInfo, parse: MovieParse, names: set[str]) -> bool:
    if parse.movie_id is not None and parse.movie_id == movie.movie_id:
        return True
    return any(v in names for title in parse.titles for v in variants(title))


def _loose(movie: MovieInfo, parse: MovieParse, names: set[str], settings: CheckedSearchSettings) -> bool:
    if settings.prefix_match:
        size = settings.prefix_min_length
        for title in parse.titles:
            for x in variants(title):
                for n in names:
                    if len(x) >= size and len(n) >= size and (x.startswith(n) or n.startswith(x)):
                        return True
    if settings.word_match:
        words = {w for title in parse.titles for w in tokens(title)}
        for name in movie.names:
            name_words = tokens(name)
            core = [w for w in name_words if w not in STOP_WORDS]
            if name_words and set(name_words) <= words and len(core) >= settings.word_min_core_words:
                return True
    return False


def evaluate(movie: MovieInfo, release_title: str, parse: MovieParse,
             settings: CheckedSearchSettings) -> Verdict:
    reasons: list[str] = []
    if not _year_ok(movie, parse, settings):
        reasons.append(REASON_YEAR)
    if settings.veto_other_movie and parse.year and parse.movie_id is not None and parse.movie_id != movie.movie_id:
        reasons.append(REASON_OTHER_MOVIE)

    names = {v for name in movie.names for v in variants(name)}
    exact = _exact(movie, parse, names)
    if parse.year == 0 and settings.no_year_needs_exact:
        if not exact:
            reasons.append(REASON_TITLE_WITHOUT_YEAR)
    elif not exact and not _loose(movie, parse, names, settings):
        reasons.append(REASON_TITLE)

    if settings.skip_existing_file and any(same_release(release_title, name) for name in movie.existing_releases):
        reasons.append(REASON_EXISTING_FILE)
    return Verdict(tuple(reasons))
