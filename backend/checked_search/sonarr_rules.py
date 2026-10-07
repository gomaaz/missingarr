"""Pre-filter S1-S4 for Sonarr releases of single episodes. Pure functions.

  S1 veto    /parse maps the release to another series of the library
  S2 suffix  a year or country code at the end of the parsed series title
             must fit the series (year within tolerance, code also at the
             end of the series title or an alternate title)
  S3 early   published more than N days before the episode aired (between
             the note threshold and N only a note)
  S4 file    the release of the existing file is never grabbed again
  S5 name    a name without year that /parse maps to no series and that is
             also the title of another series of the library (0.12.0)
  S6 title   the episode title in the name names another episode (0.12.0)
  S7 number  untitled releases of a list in which S6 found another episode,
             in the same languages (0.12.0)

The rules are not measured yet. evaluate() therefore always returns every
rule that fires, so a dry run shows each rule on its own.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from backend.checked_search import episode_titles
from backend.checked_search.normalize import parse_utc, same_release, strip_extension, tokens, variants
from backend.checked_search.settings import CheckedSearchSettings
from backend.checked_search.verdict import (
    NOTE_NO_EPISODE_FITS, REASON_COUNTRY_SUFFIX, REASON_EXISTING_FILE, REASON_NAMESAKE, REASON_NUMBERING,
    REASON_OTHER_EPISODE, REASON_OTHER_SERIES, REASON_TOO_EARLY, REASON_YEAR_SUFFIX, Verdict,
)

_YEAR = re.compile(r"^(18|19|20)\d\d$")
# The last word of a title, bracketed or not, and what stands before it
# ("Nemesis (2026)" -> "Nemesis", "(", "2026", ")"; "Alex & CO" -> "Alex &", "", "CO", "").
_TAIL = re.compile(r"^(?P<head>.*?[^\W_].*?)[\s._-]*(?P<open>[(\[]?)\s*(?P<word>[^\W_]+)\s*(?P<close>[)\]]?)[\s._-]*$")
_NAME_SPLIT = re.compile(r"[ ._\-()\[\]]+")


@dataclass(frozen=True)
class EpisodeInfo:
    series_id: int
    series_titles: tuple[str, ...]          # series title + alternateTitles[].title
    series_year: int = 0
    air_date_utc: datetime | None = None
    existing_releases: tuple[str, ...] = ()  # episodeFile.sceneName, file name without extension
    season_number: int | None = None         # of the episode searched for (S6, S7)
    episode_number: int | None = None
    episode_title: str = ""


@dataclass(frozen=True)
class EpisodeParse:
    series_id: int | None = None            # series.id from /parse
    series_title: str = ""                  # parsedEpisodeInfo.seriesTitle
    year: int = 0                           # parsedEpisodeInfo.seriesTitleInfo.year


@dataclass(frozen=True)
class RuleContext:
    """What S5-S7 need beyond the one release, filled by the runner. A rule
    whose data is missing (None, empty) checks nothing: without a context
    evaluate() decides as before 0.12.0 (the Imports page)."""
    namesake_index: dict | None = None                         # namesake_index() of the library (S5)
    episodes: tuple | None = None                              # episode_titles.episode_list() of the series (S6)
    release_languages: frozenset = frozenset()                 # of this release, "Unknown" left out (S7)
    doubt_languages: tuple = ()                                # of the releases S6 rejected in the list (S7)


def episode_from_resources(episode: dict, series: dict) -> EpisodeInfo:
    """GET /api/v3/episode/{id} (includes episodeFile) and GET /api/v3/series/{seriesId}
    (includes alternateTitles)."""
    titles = [series.get("title")]
    titles += [alt.get("title") for alt in series.get("alternateTitles") or [] if isinstance(alt, dict)]
    episode_file = episode.get("episodeFile") or {}
    existing = [episode_file.get("sceneName"), strip_extension(episode_file.get("relativePath"))]
    return EpisodeInfo(
        series_id=int(series.get("id") or episode.get("seriesId") or 0),
        series_titles=tuple(t for t in titles if t),
        series_year=int(series.get("year") or 0),
        air_date_utc=parse_utc(episode.get("airDateUtc")),
        existing_releases=tuple(name for name in existing if name),
        season_number=_number(episode.get("seasonNumber")),
        episode_number=_number(episode.get("episodeNumber")),
        episode_title=str(episode.get("title") or ""),
    )


def _number(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def parse_from_resource(parse: dict | None) -> EpisodeParse:
    parse = parse or {}
    info = parse.get("parsedEpisodeInfo") or {}
    series = parse.get("series") or {}
    series_id = series.get("id") if isinstance(series, dict) else None
    year = _number((info.get("seriesTitleInfo") or {}).get("year")) or 0
    return EpisodeParse(series_id=int(series_id) if series_id else None, series_title=info.get("seriesTitle") or "",
                        year=year)


def title_suffix(series_title: str, codes) -> tuple[int, str]:
    """(year, country code) at the end of a series title, 0/'' when absent.
    At most one of each, in either order; the first word never counts, so
    a series whose title is just a year keeps its name."""
    words = tokens(series_title)
    known = {c.upper() for c in codes}
    year, country = 0, ""
    for _ in range(2):
        if len(words) < 2:
            break
        last = words[-1]
        if not year and _YEAR.match(last):
            year = int(last)
        elif not country and last.upper() in known:
            country = last.upper()
        else:
            break
        words.pop()
    return year, country


def base_keys(title: str, codes, series_year: int = 0) -> set[str]:
    """Compact spellings of a series title, with and without its year or
    country suffix ('Nemesis (2026)' -> nemesis2026, nemesis). Only what is
    written as a suffix is cut: a country code in capitals or brackets
    ('Some Show US', 'Some Show (AU)', not 'Among Us'), a year in brackets or
    within a year of the series' own ('Lost in 1949' stays whole). At most
    one of each; the first word always stays. The cut is made in the
    original text, so umlaut and '&' spellings stay."""
    known = {c.upper() for c in codes}
    base, year, country = title, False, False
    for _ in range(2):
        match = _TAIL.match(base)
        if not match:
            break
        word, bracketed = match.group("word"), bool(match.group("open") and match.group("close"))
        if not year and _YEAR.match(word) and (bracketed or (series_year and abs(int(word) - series_year) <= 1)):
            year = True
        elif not country and word.upper() in known and (bracketed or word.isupper()):
            country = True
        else:
            break
        base = match.group("head")
    return variants(title) | variants(base)


def namesake_index(series_list, codes) -> dict[str, frozenset[tuple[int, int]]]:
    """GET /api/v3/series -> compact title (with and without suffix) ->
    {(series id, year)}, from the title and every alternate title (S5)."""
    index: dict[str, set[tuple[int, int]]] = {}
    for series in series_list or []:
        if not isinstance(series, dict) or _number(series.get("id")) is None:
            continue
        year = _number(series.get("year")) or 0
        titles = [series.get("title")]
        titles += [alt.get("title") for alt in series.get("alternateTitles") or [] if isinstance(alt, dict)]
        for title in titles:
            if isinstance(title, str) and title:
                for key in base_keys(title, codes, year):
                    index.setdefault(key, set()).add((series["id"], year))
    return {key: frozenset(pairs) for key, pairs in index.items()}


def needs_namesake_index(parse: EpisodeParse, settings: CheckedSearchSettings) -> bool:
    """S5 can only fire on a name /parse maps to no series, without a year
    and without a suffix: only then is the library's series list needed."""
    return (settings.veto_namesake and parse.series_id is None and bool(parse.series_title) and not parse.year
            and not any(title_suffix(parse.series_title, settings.country_codes)))


def namesakes(info: EpisodeInfo, release_title: str, parse: EpisodeParse, index, settings) -> frozenset[int]:
    """Other series of the library whose title (with or without its suffix)
    the name has as well ('Nemesis' for 'Nemesis (2024)' and 'Nemesis (AU)'
    while 'Nemesis (2026)' is searched). None when the year of the series
    searched for stands in the name as a word of its own and no namesake
    has that year."""
    if index is None or not needs_namesake_index(parse, settings):
        return frozenset()
    found = {pair for key in variants(parse.series_title) for pair in index.get(key, ())}
    others = {pair for pair in found if pair[0] != info.series_id}
    if not others:
        return frozenset()
    words = {int(w) for w in _NAME_SPLIT.split(release_title or "") if _YEAR.match(w)}
    if info.series_year and info.series_year in words and all(year != info.series_year for _, year in others):
        return frozenset()
    return frozenset(series_id for series_id, _ in others)


def _country_fits(info: EpisodeInfo, country: str, codes) -> bool:
    for title in info.series_titles:
        _, own = title_suffix(title, codes)
        if own == country:
            return True
    return False


def evaluate(info: EpisodeInfo, release_title: str, publish_date: datetime | None,
             parse: EpisodeParse, settings: CheckedSearchSettings, context: RuleContext | None = None) -> Verdict:
    reasons: list[str] = []
    notes: list[str] = []
    context = context or RuleContext()

    if settings.veto_other_series and parse.series_id is not None and parse.series_id != info.series_id:
        reasons.append(REASON_OTHER_SERIES)

    if settings.veto_namesake and namesakes(info, release_title, parse, context.namesake_index, settings):
        reasons.append(REASON_NAMESAKE)

    if settings.check_suffix and parse.series_title:
        year, country = title_suffix(parse.series_title, settings.country_codes)
        if year and info.series_year and abs(year - info.series_year) > settings.suffix_year_tolerance:
            reasons.append(REASON_YEAR_SUFFIX)
        if country and not _country_fits(info, country, settings.country_codes):
            reasons.append(REASON_COUNTRY_SUFFIX)

    if publish_date is not None and info.air_date_utc is not None:
        # Exact span against the thresholds; only the note rounds down to whole days.
        early = info.air_date_utc - publish_date
        if early > timedelta(days=settings.reject_days_before_air):
            reasons.append(REASON_TOO_EARLY)
        elif early >= timedelta(days=settings.note_days_before_air) and early.days > 0:
            notes.append(f"published {early.days} days before air date")

    if settings.skip_existing_file and any(same_release(release_title, name) for name in info.existing_releases):
        reasons.append(REASON_EXISTING_FILE)

    series_title = info.series_titles[0] if info.series_titles else ""
    if settings.check_episode_title and context.episodes is not None and info.episode_number is not None:
        category = episode_titles.judge(release_title, (info.season_number, info.episode_number),
                                        info.episode_title, series_title, context.episodes)
        if category == episode_titles.OTHER_EPISODE:
            reasons.append(REASON_OTHER_EPISODE)
        elif category == episode_titles.NO_MATCH:
            notes.append(NOTE_NO_EPISODE_FITS)

    if (settings.check_episode_title and settings.untitled_after_other_episode and context.release_languages
            and episode_titles.is_untitled(release_title, series_title)
            and any(doubt and context.release_languages <= doubt for doubt in context.doubt_languages)):
        reasons.append(REASON_NUMBERING)
    return Verdict(tuple(reasons), tuple(notes))
