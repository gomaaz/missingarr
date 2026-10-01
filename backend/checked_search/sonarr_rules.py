"""Pre-filter S1-S4 for Sonarr releases of single episodes. Pure functions.

  S1 veto    /parse maps the release to another series of the library
  S2 suffix  a year or country code at the end of the parsed series title
             must fit the series (year within tolerance, code also at the
             end of the series title or an alternate title)
  S3 early   published more than N days before the episode aired (between
             the note threshold and N only a note)
  S4 file    the release of the existing file is never grabbed again

The rules are not measured yet. evaluate() therefore always returns every
rule that fires, so a dry run shows each rule on its own.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from backend.checked_search.normalize import parse_utc, same_release, strip_extension, tokens
from backend.checked_search.settings import CheckedSearchSettings
from backend.checked_search.verdict import (
    REASON_COUNTRY_SUFFIX, REASON_EXISTING_FILE, REASON_OTHER_SERIES, REASON_TOO_EARLY, REASON_YEAR_SUFFIX, Verdict,
)

_YEAR = re.compile(r"^(18|19|20)\d\d$")


@dataclass(frozen=True)
class EpisodeInfo:
    series_id: int
    series_titles: tuple[str, ...]          # series title + alternateTitles[].title
    series_year: int = 0
    air_date_utc: datetime | None = None
    existing_releases: tuple[str, ...] = ()  # episodeFile.sceneName, file name without extension


@dataclass(frozen=True)
class EpisodeParse:
    series_id: int | None = None            # series.id from /parse
    series_title: str = ""                  # parsedEpisodeInfo.seriesTitle


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
    )


def parse_from_resource(parse: dict | None) -> EpisodeParse:
    parse = parse or {}
    info = parse.get("parsedEpisodeInfo") or {}
    series = parse.get("series") or {}
    series_id = series.get("id") if isinstance(series, dict) else None
    return EpisodeParse(series_id=int(series_id) if series_id else None, series_title=info.get("seriesTitle") or "")


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


def _country_fits(info: EpisodeInfo, country: str, codes) -> bool:
    for title in info.series_titles:
        _, own = title_suffix(title, codes)
        if own == country:
            return True
    return False


def evaluate(info: EpisodeInfo, release_title: str, publish_date: datetime | None,
             parse: EpisodeParse, settings: CheckedSearchSettings) -> Verdict:
    reasons: list[str] = []
    notes: list[str] = []

    if settings.veto_other_series and parse.series_id is not None and parse.series_id != info.series_id:
        reasons.append(REASON_OTHER_SERIES)

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
    return Verdict(tuple(reasons), tuple(notes))
