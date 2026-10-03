"""Own verdict on a download *arr holds back (the Imports page).

The pre-filter rules of the checked search judge one pair here: the release
name of the download and the target the app proposes for it (GET
/manualimport). Only reads, and only with GET. radarr_rules, sonarr_rules and
normalize stay as they are; what differs for a download that is already
there is adapted in this module:

- "existing file" becomes a note: the download is here already, the rule
  was made to keep a search from grabbing the same release again.
- The search gates of the runner (not mapped to this title, season pack,
  multi-episode release) do not apply: the target is the app's proposal. A
  Sonarr download with several episodes is judged episode by episode.
- Radarr gets no fallback titles (they come from GET /release, which does
  not exist here). A name the app cannot read is "unknown", not "foreign".
- Sonarr S3 needs the publish date, which only the grab history has.
  Without it S3 is skipped with a note.
- Sonarr: the season and episode numbers in the name are compared with the
  proposal, as a note only (none of S1-S4 compares them).

The verdict never locks "Import": V6 was measured on search results, S1-S4
are not measured, and in the typical case (matched by ID at grab) /parse
maps the name to no library title at all.

Secrets: grab history data carries download links with indexer keys. Only
data.publishedDate is read from it; data is never kept, logged or returned.
"""

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Callable

import requests

from backend.checked_search import radarr_rules, sonarr_rules
from backend.checked_search.normalize import parse_utc
from backend.checked_search.settings import CheckedSearchSettings
from backend.checked_search.verdict import (
    REASON_COUNTRY_SUFFIX, REASON_EXISTING_FILE, REASON_OTHER_MOVIE, REASON_OTHER_SERIES, REASON_TITLE,
    REASON_TITLE_WITHOUT_YEAR, REASON_TOO_EARLY, REASON_YEAR, REASON_YEAR_SUFFIX, Verdict,
)

VERDICT_FITS = "fits"
VERDICT_FOREIGN = "foreign"
VERDICT_UNKNOWN = "unknown"

PARSE_PATH = "/api/v3/parse"
EPISODE_PATH = "/api/v3/episode"
HISTORY_PATH = "/api/v3/history"
GRABBED_EVENT_TYPE = 1        # query value; records answer eventType as the string "grabbed"

NOTE_EXISTING_FILE = "same release as the existing file"
NOTE_NO_PUBLISH_DATE = "publish date unknown — S3 not checked"
NOTE_HISTORY_UNREADABLE = "grab history not readable — S3 not checked"
NOTE_TITLE_NOT_COMPARED = "Sonarr maps '{title}' to no series — title not compared"
NOTE_EPISODES_DIFFER = "episode numbers differ — name {name}, proposal {proposal}"
ERROR_NO_TARGET = "no single target in the proposal"
ERROR_UNREADABLE_NAME = "{app} cannot read the release name"
ERROR_READ = "could not read {what}: {short}"

_APPS = {"radarr": "Radarr", "sonarr": "Sonarr"}


@dataclass(frozen=True)
class ImportTarget:
    """What the app proposes: a movie, or a series with episodes."""

    movie_id: int | None = None
    series_id: int | None = None
    episode_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class ImportVerdict:
    """fits: no rule objects. foreign: reasons (labels of verdict.py) with one
    detail each. unknown: error says why nothing could be judged. Notes are
    remarks that object to nothing."""

    state: str
    reasons: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    details: tuple[str, ...] = ()
    error: str = ""

    def as_dict(self) -> dict:
        return {"state": self.state, "reasons": list(self.reasons), "notes": list(self.notes),
                "details": list(self.details), "error": self.error}


class _ReadFailed(Exception):
    """A read that decides "unknown"; str() is the error text."""


class _Found:
    """Reasons with one detail each, and notes: in order, without duplicates."""

    def __init__(self):
        self.reasons: list[str] = []
        self.details: list[str] = []
        self.notes: list[str] = []

    def note(self, note: str) -> None:
        if note and note not in self.notes:
            self.notes.append(note)

    def add(self, verdict: Verdict, detail_of: Callable[[str], str]) -> None:
        for reason in verdict.reasons:
            if reason == REASON_EXISTING_FILE:
                self.note(NOTE_EXISTING_FILE)
            elif reason not in self.reasons:
                self.reasons.append(reason)
                self.details.append(detail_of(reason))
        for note in verdict.notes:
            self.note(note)

    def verdict(self) -> ImportVerdict:
        state = VERDICT_FOREIGN if self.reasons else VERDICT_FITS
        return ImportVerdict(state, tuple(self.reasons), tuple(self.notes), tuple(self.details))


def _unknown(error: str) -> ImportVerdict:
    return ImportVerdict(VERDICT_UNKNOWN, error=error)


def _int(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _ints(value) -> list[int]:
    if not isinstance(value, list):
        return []
    return [number for number in value if _int(number) is not None]


def _check_type(arr_type: str) -> None:
    if arr_type not in _APPS:
        raise ValueError(f"unknown arr type '{arr_type}'")


def _parsed(parse, key: str) -> bool:
    """Did the app read the name? parsedMovieInfo / parsedEpisodeInfo is
    left out of the answer (or null) when it could not."""
    return isinstance(parse, dict) and isinstance(parse.get(key), dict) and bool(parse[key])


def _named(resource, kind: str) -> str:
    """'Title' (year) of the library movie or series /parse names."""
    if not isinstance(resource, dict):
        return f"another {kind}"
    title = resource.get("title")
    year = _int(resource.get("year"))
    if title:
        return f"'{title}' ({year})" if year else f"'{title}'"
    return f"{kind} {resource.get('id')}"


# ── Radarr ───────────────────────────────────────────────────────────────────

def _movie_detail(reason: str, info: radarr_rules.MovieInfo, parsed: radarr_rules.MovieParse, parse: dict,
                  settings: CheckedSearchSettings) -> str:
    own = f"'{info.names[0]}'" if info.names else f"movie {info.movie_id}"
    name = " / ".join(parsed.titles)
    if reason == REASON_YEAR:
        if not info.year and not info.secondary_year:
            return f"release year {parsed.year}, the movie has no year"
        years = {y for y in (info.year, info.secondary_year) if y}
        if settings.count_release_dates:
            years |= set(info.release_years)
        listed = "/".join(str(y) for y in sorted(years))
        return f"release year {parsed.year}, movie {listed} (tolerance {settings.year_tolerance})"
    if reason == REASON_OTHER_MOVIE:
        return f"Radarr maps the name to {_named(parse.get('movie'), 'movie')}"
    if reason == REASON_TITLE:
        return f"name '{name}' matches no title of {own}" if name else f"the name has no title to compare with {own}"
    if reason == REASON_TITLE_WITHOUT_YEAR:
        if not name:
            return f"the name has neither a year nor a title to compare with {own}"
        return f"name '{name}' has no year and is not an exact title of {own}"
    return reason


def _judge_radarr(release_title: str, parse, movie, settings: CheckedSearchSettings) -> ImportVerdict:
    if not _parsed(parse, "parsedMovieInfo"):
        return _unknown(ERROR_UNREADABLE_NAME.format(app="Radarr"))
    if not isinstance(movie, dict):
        return _unknown(ERROR_NO_TARGET)
    info = radarr_rules.movie_from_resource(movie)
    parsed = radarr_rules.parse_from_resource(parse)    # no fallback titles: there is no GET /release here
    found = _Found()
    found.add(radarr_rules.evaluate(info, release_title, parsed, settings),
              lambda reason: _movie_detail(reason, info, parsed, parse, settings))
    return found.verdict()


# ── Sonarr ───────────────────────────────────────────────────────────────────

def _code(season: int, numbers) -> str:
    return f"S{season:02d}" + "".join(f"E{n:02d}" for n in sorted(set(numbers)))


def _codes(pairs) -> str:
    seasons: dict[int, list[int]] = {}
    for season, number in pairs:
        seasons.setdefault(season, []).append(number)
    return " ".join(_code(season, numbers) for season, numbers in sorted(seasons.items()))


def _absolute(numbers) -> str:
    return "+".join(f"#{n}" for n in sorted(set(numbers)))


def _episode_code(episode: dict) -> str:
    season, number = _int(episode.get("seasonNumber")), _int(episode.get("episodeNumber"))
    if season is None or number is None:
        return f"episode {episode.get('id')}"
    return _code(season, [number])


def _episode_order(episode: dict) -> tuple[int, int, int]:
    season, number, episode_id = (_int(episode.get(k)) for k in ("seasonNumber", "episodeNumber", "id"))
    return (-1 if season is None else season, -1 if number is None else number, episode_id or 0)


def _numbers_note(parsed_info: dict, episodes: list[dict]) -> str:
    """NOTE_EPISODES_DIFFER when the season and episode numbers of the name
    (else its absolute numbers) differ from the proposal's episodes. A full
    season, a daily episode or a name without numbers is not compared, nor
    are episodes that lack the numbers to compare with."""
    if parsed_info.get("fullSeason") is True or parsed_info.get("isDaily") is True:
        return ""
    season = _int(parsed_info.get("seasonNumber"))
    numbers = _ints(parsed_info.get("episodeNumbers"))
    if season is not None and numbers:
        proposal = [(_int(e.get("seasonNumber")), _int(e.get("episodeNumber"))) for e in episodes]
        if any(s is None or n is None for s, n in proposal):
            return ""
        name = {(season, n) for n in numbers}
        if name == set(proposal):
            return ""
        return NOTE_EPISODES_DIFFER.format(name=_codes(name), proposal=_codes(set(proposal)))
    absolute = _ints(parsed_info.get("absoluteEpisodeNumbers"))
    if absolute:
        proposal_numbers = [_int(e.get("absoluteEpisodeNumber")) for e in episodes]
        if any(n is None for n in proposal_numbers):
            return ""
        if set(absolute) == set(proposal_numbers):
            return ""
        return NOTE_EPISODES_DIFFER.format(name=_absolute(absolute), proposal=_absolute(proposal_numbers))
    return ""


def _episode_detail(reason: str, info: sonarr_rules.EpisodeInfo, episode: dict, parsed: sonarr_rules.EpisodeParse,
                    parse: dict, series: dict, publish_date: datetime | None,
                    settings: CheckedSearchSettings) -> str:
    if reason == REASON_OTHER_SERIES:
        return f"Sonarr maps the name to {_named(parse.get('series'), 'series')}"
    if reason == REASON_YEAR_SUFFIX:
        year, _ = sonarr_rules.title_suffix(parsed.series_title, settings.country_codes)
        return f"suffix {year} vs series {info.series_year}"
    if reason == REASON_COUNTRY_SUFFIX:
        _, country = sonarr_rules.title_suffix(parsed.series_title, settings.country_codes)
        return f"suffix {country} in no title of {_named(series, 'series')}"
    if reason == REASON_TOO_EARLY and publish_date is not None and info.air_date_utc is not None:
        days = (info.air_date_utc - publish_date).days
        return f"published {days} days before {_episode_code(episode)} aired"
    return reason


def _judge_sonarr(release_title: str, parse, series, episodes, publish_date: datetime | None,
                  settings: CheckedSearchSettings) -> ImportVerdict:
    if not _parsed(parse, "parsedEpisodeInfo"):
        return _unknown(ERROR_UNREADABLE_NAME.format(app="Sonarr"))
    episodes = sorted((e for e in episodes or [] if isinstance(e, dict)), key=_episode_order)
    if not isinstance(series, dict) or not episodes:
        return _unknown(ERROR_NO_TARGET)
    parsed = sonarr_rules.parse_from_resource(parse)
    found = _Found()
    if parsed.series_id is None:
        # The usual case of a download matched by ID at grab: S1 cannot fire
        # and S1-S4 have no title rule, so nothing compared the title.
        found.note(NOTE_TITLE_NOT_COMPARED.format(title=parsed.series_title or release_title))
    found.note(_numbers_note(parse["parsedEpisodeInfo"], episodes))
    if publish_date is None:
        found.note(NOTE_NO_PUBLISH_DATE)
    for episode in episodes:
        info = sonarr_rules.episode_from_resources(episode, series)
        found.add(sonarr_rules.evaluate(info, release_title, publish_date, parsed, settings),
                  lambda reason, info=info, episode=episode: _episode_detail(
                      reason, info, episode, parsed, parse, series, publish_date, settings))
    return found.verdict()


def judge(arr_type: str, release_title: str, parse: dict | None, movie: dict | None,
          series: dict | None, episodes: list[dict], publish_date: datetime | None,
          settings: CheckedSearchSettings) -> ImportVerdict:
    """Pure. Radarr: the name unreadable (no parsedMovieInfo) or no movie ->
    unknown; else V6 without fallback titles. Sonarr: no parsedEpisodeInfo,
    no series or no episodes -> unknown; else S1-S4 once per episode
    (sorted by season and number), reasons and notes merged in order
    without duplicates. Both: "existing file" becomes NOTE_EXISTING_FILE.
    Sonarr notes first: title not compared (/parse maps no series), episode
    numbers differ, no publish date; then the notes of the rules.
    Any reason -> foreign, else fits. arr_type other than radarr/sonarr ->
    ValueError."""
    _check_type(arr_type)
    if arr_type == "radarr":
        return _judge_radarr(release_title, parse, movie, settings)
    return _judge_sonarr(release_title, parse, series, episodes, publish_date, settings)


# ── Reading ──────────────────────────────────────────────────────────────────

def _short(exc: BaseException) -> str:
    # Timeout before ConnectionError: ConnectTimeout is both.
    if isinstance(exc, requests.exceptions.Timeout):
        return "timed out"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "no connection"
    if isinstance(exc, requests.exceptions.HTTPError):
        response = exc.response
        return f"HTTP {response.status_code if response is not None else 0}"
    if isinstance(exc, ValueError):      # no JSON (JSONDecodeError is a ValueError too)
        return "unexpected answer"
    return type(exc).__name__


def _read(get: Callable[..., Any], what: str, path: str, params: dict | None = None, shape: type = dict):
    try:
        answer = get(path, params=params)
    except Exception as exc:
        raise _ReadFailed(ERROR_READ.format(what=what, short=_short(exc))) from None
    if not isinstance(answer, shape):
        raise _ReadFailed(ERROR_READ.format(what=what, short="unexpected answer"))
    return answer


def _complete(target: ImportTarget | None, arr_type: str) -> bool:
    if target is None:
        return False
    if arr_type == "radarr":
        return (_int(target.movie_id) or 0) > 0
    return ((_int(target.series_id) or 0) > 0 and len(target.episode_ids) > 0
            and all((_int(i) or 0) > 0 for i in target.episode_ids))


def _series(get: Callable[..., Any], series_id: int, cache: dict | None) -> dict:
    key = ("series", series_id)
    if cache is not None and isinstance(cache.get(key), dict):
        return cache[key]
    series = _read(get, "the series", f"/api/v3/series/{series_id}")
    if cache is not None:
        cache[key] = series
    return series


def _episodes(get: Callable[..., Any], episode_ids) -> list[dict]:
    wanted = sorted(set(episode_ids))
    answer = _read(get, "the episodes", EPISODE_PATH,
                   {"episodeIds": wanted, "includeEpisodeFile": "true"}, shape=list)
    by_id = {e.get("id"): e for e in answer if isinstance(e, dict)}
    if any(episode_id not in by_id for episode_id in wanted):
        raise _ReadFailed(ERROR_READ.format(what="the episodes", short="unexpected answer"))
    return [by_id[episode_id] for episode_id in wanted]


def _publish_date(get: Callable[..., Any], download_id: str) -> tuple[datetime | None, bool]:
    """(publishedDate of the first grab record, whether the history could be
    read). Nothing else of the records is looked at or kept: their data
    holds download links with indexer keys."""
    try:
        answer = get(HISTORY_PATH, params={"downloadId": download_id, "eventType": GRABBED_EVENT_TYPE})
    except Exception:
        return None, False
    records = answer.get("records") if isinstance(answer, dict) else None
    if not isinstance(records, list):
        return None, False
    for record in records:
        if not isinstance(record, dict) or record.get("eventType") not in ("grabbed", GRABBED_EVENT_TYPE):
            continue
        data = record.get("data")
        published = parse_utc(data.get("publishedDate")) if isinstance(data, dict) else None
        if published is not None:
            return published, True
    return None, True


def check_import(get: Callable[..., Any], arr_type: str, release_title: str,
                 target: ImportTarget | None, settings: CheckedSearchSettings, *,
                 download_id: str | None = None, cache: dict | None = None) -> ImportVerdict:
    """Reads what judge() needs through get (an agent's http_get) and judges.
    Never raises for an *arr failure: a failed read ends as unknown
    (ERROR_READ), except the grab history, which only turns the note into
    NOTE_HISTORY_UNREADABLE. Stops after the first read that decides unknown.
      target None or incomplete -> unknown(ERROR_NO_TARGET), no request
      1. GET /parse?title=…       (name unreadable -> unknown, stop)
      Radarr 2. GET /movie/{id}                                 -> 2 requests
      Sonarr 2. GET /series/{id}, unless cache[("series", id)] holds it
             3. GET /episode?episodeIds=…&includeEpisodeFile=true (every id)
             4. only with download_id: GET /history?downloadId=…&eventType=1
                                                  -> 4 requests (3 with the series cached)
    Only GET. arr_type other than radarr/sonarr -> ValueError."""
    _check_type(arr_type)
    if not _complete(target, arr_type):
        return _unknown(ERROR_NO_TARGET)
    try:
        parse = _read(get, "/parse", PARSE_PATH, {"title": release_title})
        if arr_type == "radarr":
            if not _parsed(parse, "parsedMovieInfo"):
                return _unknown(ERROR_UNREADABLE_NAME.format(app="Radarr"))
            movie = _read(get, "the movie", f"/api/v3/movie/{target.movie_id}")
            return judge(arr_type, release_title, parse, movie, None, [], None, settings)
        if not _parsed(parse, "parsedEpisodeInfo"):
            return _unknown(ERROR_UNREADABLE_NAME.format(app="Sonarr"))
        series = _series(get, target.series_id, cache)
        episodes = _episodes(get, target.episode_ids)
    except _ReadFailed as exc:
        return _unknown(str(exc))
    publish_date, history_read = _publish_date(get, download_id) if download_id else (None, True)
    verdict = judge(arr_type, release_title, parse, None, series, episodes, publish_date, settings)
    if not history_read:
        notes = tuple(NOTE_HISTORY_UNREADABLE if n == NOTE_NO_PUBLISH_DATE else n for n in verdict.notes)
        verdict = replace(verdict, notes=notes)
    return verdict
