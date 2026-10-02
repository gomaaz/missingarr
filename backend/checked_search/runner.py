"""Checked search: one title at a time, through the agent's HTTP methods.

Per title: load the movie (or episode and series), run the indexer search
via GET /release, check every approved release with the pre-filter, and in
active mode grab the first clean one via POST /release. Every title gets one
row in the pre-filter log.

GET /release is an interactive search for *arr: it asks the indexers with
"Interactive Search" on, the old search command asked those with
"Automatic Search" on. While an indexer has the two switches set
differently (or the indexer list cannot be read) a run checks nothing at
all: it pauses until the next run.

*arr keeps the releases of a search for 30 minutes under indexer id + guid,
mapped to the title of whichever search returned them last. The grab
therefore names its target itself (shouldOverride with the movie, or the
series and episodes, plus quality and languages exactly as GET /release
reported them): a search for another title in between cannot redirect it.
A release GET /release did not map to this very title is never grabbed.

A POST that failed was either refused (never sent, or 3xx/4xx: nothing was
grabbed, the title stays free) or has no clear answer (timeout, connection
lost after sending, 5xx: it may be downloading, the title is cached like
after a grab). Neither tries a second release.

A release /parse could not check (timeout, HTTP error) is never grabbed,
and it is no rule rejection either: when no release passes and at least one
could not be checked, the title ends as an error (not cached, not counted
for the dry-run round), so a later run searches it again. A release that
passes after such a one is still grabbed: it passed every rule, only its
rank may be lower, and a /parse failing for one release name every time
would otherwise keep the title from ever being grabbed.

An indexer failure does not fail GET /release: *arr catches it per indexer
and answers with what the others found, and it leaves an indexer blocked
after failures out of the search. Both show only in the health checks
(IndexerStatusCheck, IndexerLongTermStatusCheck: blocked indexers by name;
Radarr and Sonarr have no indexer status endpoint). So before a title ends
without a clean hit or without results, the runner waits until *arr has
refreshed its health checks and reads them: is an indexer the release search
asks named there, or can the health or indexer list not be read, the title
ends as an error (not cached, not counted for the dry-run round).

Secrets: a release from *arr carries downloadUrl (with the indexer's API
key), infoUrl, magnetUrl and guid. Only the fields below are kept; guid,
the mapping, quality and languages live in memory for the one POST and are
never logged or stored.
"""

import copy
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests
import urllib3.exceptions

from backend import db
from backend.checked_search import radarr_rules, sonarr_rules
from backend.checked_search.normalize import parse_utc
from backend.checked_search.settings import CheckedSearchSettings
from backend.checked_search.verdict import (
    REASON_MULTI_EPISODE, REASON_PARSE_ERROR, REASON_SEASON_PACK, REASON_TARGET, Verdict,
)
from backend.skills.base import SubmitOutcome, UnsavedCheckedGrab
from backend.verification import ITEM_FAILED, ITEM_GRABBED, ITEM_NO_HIT

MODE_DRY_RUN = "dry_run"
MODE_ACTIVE = "active"

OUTCOME_GRABBED = "grabbed"
OUTCOME_WOULD_GRAB = "would_grab"
OUTCOME_NO_CLEAN_HIT = "no_clean_hit"
OUTCOME_NO_RESULTS = "no_results"
OUTCOME_ERROR = "error"
OUTCOME_GRAB_FAILED = "grab_failed"
OUTCOME_GRAB_UNCERTAIN = "grab_uncertain"

RELEASE_PATH = "/api/v3/release"
PARSE_PATH = "/api/v3/parse"
INDEXER_PATH = "/api/v3/indexer"
HEALTH_PATH = "/api/v3/health"
# Health checks naming the indexers *arr blocks after failures (backoff of at
# least a minute per failure): within six hours of the first failure, and after.
INDEXER_HEALTH_SOURCES = ("IndexerStatusCheck", "IndexerLongTermStatusCheck")
# *arr re-evaluates these checks up to 5 s after an indexer status changed
# (debounced): a failure late in the search shows only after that.
HEALTH_SETTLE_SECONDS = 6

VERDICT_PASS = "pass"
VERDICT_REJECT = "reject"
VERDICT_UNCHECKED = "unchecked"
VERDICT_ERROR = "error"       # /parse failed: not checked, not a rule rejection


@dataclass(frozen=True)
class CheckedTask:
    """One title to check. arr_id: movie id (Radarr) or episode id (Sonarr)."""

    arr_id: int
    title: str
    item_type: str            # 'movie' or 'episode'
    cache_key: str            # mov:<id>, ep:<id>, upg:<id>
    series_id: int | None = None
    profile_fingerprint: str | None = None   # the title's quality profile (spec addendum)
    # A second key a grab holds (Sonarr upgrade: upg:sea-hold:<series>:<season>,
    # so the command path waits with the season search while the grab blocks).
    hold_key: str | None = None


@dataclass
class CheckedRunOutcome(SubmitOutcome):
    checked: int = 0          # titles that ended without an error
    would_grab: int = 0
    grabbed: int = 0
    no_hit: int = 0           # no clean hit or no results (active)
    budget_exhausted: bool = False
    notes: list = field(default_factory=list)


@dataclass
class _Release:
    title: str
    indexer: str
    indexer_id: int | None
    score: int | None
    size: int | None
    quality: str
    movie_titles: tuple
    publish_date: datetime | None
    full_season: bool = False
    # What GET /release mapped the release to, and its quality and languages
    # as reported: the grab names this target (shouldOverride). Memory only.
    mapped_movie_id: int | None = None
    mapped_series_id: int | None = None
    mapped_episode_ids: tuple = ()
    quality_raw: object = field(default=None, repr=False)
    languages_raw: object = field(default=None, repr=False)
    guid: str = field(default="", repr=False)


@dataclass
class _Result:
    outcome: str
    error: str
    entry: dict
    history_status: str | None
    # *arr ran (or still runs) the indexer search: the rate slot stays used.
    searched: bool = True
    # Active: cache the title although the item failed (a grab without a
    # clear answer may be downloading). None: the default of record_checked.
    cache: bool | None = None


class _Stopped(Exception):
    """Abort requested (instance off, deleted, shutdown) while a title was checked."""


class _ParseFailed(Exception):
    """GET /parse failed for one release: it could not be checked."""


def _int(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _approved(payload) -> list[_Release]:
    """Approved releases in *arr's order — its ranking."""
    releases: list[_Release] = []
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, dict) or item.get("approved") is not True:
            continue
        quality = ((item.get("quality") or {}).get("quality") or {}).get("name") or ""
        episodes = item.get("mappedEpisodeInfo")
        releases.append(_Release(
            title=str(item.get("title") or ""),
            indexer=str(item.get("indexer") or ""),
            indexer_id=_int(item.get("indexerId")),
            score=_int(item.get("customFormatScore")),
            size=_int(item.get("size")),
            quality=str(quality),
            movie_titles=tuple(t for t in item.get("movieTitles") or () if isinstance(t, str)),
            publish_date=parse_utc(item.get("publishDate")),
            full_season=item.get("fullSeason") is True,
            mapped_movie_id=_int(item.get("mappedMovieId")),
            mapped_series_id=_int(item.get("mappedSeriesId")),
            mapped_episode_ids=tuple(
                e["id"] for e in episodes if isinstance(e, dict) and _int(e.get("id")) is not None
            ) if isinstance(episodes, list) else (),
            quality_raw=copy.deepcopy(item.get("quality")),
            languages_raw=copy.deepcopy(item.get("languages")),
            guid=str(item.get("guid") or ""),
        ))
    return releases


def _not_sent(exc: Exception) -> bool:
    """The request never reached *arr: no connection could be opened
    (refused, name not resolved, connect timeout). requests raises
    ConnectionError for much more — a reset or close after the request was
    sent, a stalled answer body — and those may have reached *arr."""
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return True
    if isinstance(exc, requests.exceptions.ConnectionError):
        inner = exc.args[0] if exc.args else None
        return isinstance(inner, urllib3.exceptions.MaxRetryError) and isinstance(
            inner.reason, urllib3.exceptions.NewConnectionError)
    return False


def _refused(exc: Exception) -> bool:
    """*arr did not act on the request: it never got it, or answered 3xx/4xx.
    For GET /release the rate slot goes back then; for the grab nothing was
    grabbed. Anything else (read timeout, connection lost after sending, 5xx,
    unreadable answer): *arr searched (or still searches without a
    cancellation signal), or may have grabbed."""
    if _not_sent(exc):
        return True
    response = getattr(exc, "response", None)
    return isinstance(exc, requests.exceptions.HTTPError) and response is not None and response.status_code < 500


def indexer_pause(agent) -> str:
    """'' when the checked search may run, else why it pauses. The skills ask
    before they collect candidates, so a run with nothing to search pauses
    (and shows it) as well. GET /release
    asks the indexers with interactive search on, the search command asked
    those with automatic search on; while an indexer has the two switches
    set differently its results would not be the command's (decision of
    01.10.2026: pause instead of checking with a different indexer set)."""
    try:
        indexers = agent.http_get(INDEXER_PATH)
    except Exception as exc:
        return f"Checked search paused — could not read the indexer list: {exc}"
    if not isinstance(indexers, list):
        return "Checked search paused — the indexer list was no list"
    differing = []
    for indexer in indexers:
        if not isinstance(indexer, dict):
            continue
        auto = indexer.get("enableAutomaticSearch") is True
        interactive = indexer.get("enableInteractiveSearch") is True
        if auto != interactive:
            differing.append(f"{indexer.get('name') or indexer.get('id')} (automatic search "
                             f"{'on' if auto else 'off'}, interactive search {'on' if interactive else 'off'})")
    if not differing:
        return ""
    return ("Checked search paused — indexer " + ", ".join(differing) + ": the release search would not ask "
            "the indexers the search command asks. Nothing was searched or remembered; set both switches alike.")


def _named_indexers(message: str) -> list[str] | None:
    """The indexer names a health message lists after its colon ("Indexers
    unavailable due to failures: A, B"); None when it names none ("All
    indexers are unavailable …", or a translation without ': ')."""
    _, colon, names = message.partition(": ")
    return [name.strip() for name in names.split(", ")] if colon else None


def _asked(indexer: dict, title_tags) -> bool:
    """Does the release search ask this indexer for the title? Interactive
    search on, and untagged or sharing a tag with the movie or series
    (ReleaseSearchService.Dispatch). Unknown title tags: asked."""
    if indexer.get("enableInteractiveSearch") is not True:
        return False
    own = indexer.get("tags")
    if not isinstance(title_tags, list) or not isinstance(own, list) or not own:
        return True
    return any(tag in title_tags for tag in own)


def _candidate(release: _Release, verdict: str, reasons=(), notes=(), chosen=False, arr_choice=False) -> dict:
    return {
        "title": release.title, "indexer": release.indexer, "score": release.score, "size": release.size,
        "quality": release.quality, "verdict": verdict, "reasons": list(reasons), "notes": list(notes),
        "chosen": chosen, "arr_choice": arr_choice,
    }


def _pick(release: _Release | None) -> dict | None:
    if release is None:
        return None
    return {"title": release.title, "indexer": release.indexer, "score": release.score,
            "size": release.size, "quality": release.quality}


class _TitleCheck:
    def __init__(self, skill_name, agent, run_id, mode, settings: CheckedSearchSettings, config: dict,
                 profiles=None):
        self.skill_name, self.agent, self.run_id, self.mode, self.settings = skill_name, agent, run_id, mode, settings
        self.profiles = profiles
        self.arr_type = config["type"]
        self.instance_id = config["id"]
        # The round the run began in: rows of a run that is still going after
        # "Reset dry run" stay in that round (spec: round as a counter).
        self.dry_run_round = int(config.get("dry_run_round") or 0) if mode == MODE_DRY_RUN else None
        self.settings_fingerprint = settings.rules_fingerprint(self.arr_type)

    # ── *arr calls ───────────────────────────────────────────────────────

    def load(self, task: CheckedTask):
        """(title data for the rules, quality profile id and tags of the
        loaded movie or series)."""
        if self.arr_type == "radarr":
            movie = self.agent.http_get(f"/api/v3/movie/{task.arr_id}")
            return radarr_rules.movie_from_resource(movie), movie.get("qualityProfileId"), movie.get("tags")
        episode = self.agent.http_get(f"/api/v3/episode/{task.arr_id}")
        series_id = task.series_id or episode.get("seriesId")
        series = self.agent.http_get(f"/api/v3/series/{series_id}")
        return (sonarr_rules.episode_from_resources(episode, series), series.get("qualityProfileId"),
                series.get("tags"))

    def fingerprint(self, task: CheckedTask, profile_id) -> str | None:
        """The profile fingerprint the title is checked and stored under: from
        the resource just loaded (the Sonarr series list may have failed);
        the record's value only when that profile is not in the state."""
        if self.profiles is not None:
            current = self.profiles.fingerprint(profile_id)
            if current is not None:
                return current
        return task.profile_fingerprint

    def search(self, task: CheckedTask) -> list[_Release]:
        key = "movieId" if self.arr_type == "radarr" else "episodeId"
        payload = self.agent.http_get(RELEASE_PATH, params={key: task.arr_id},
                                      timeout=self.settings.release_timeout_seconds)
        return _approved(payload)

    def mapped_here(self, task: CheckedTask, info, release: _Release) -> bool:
        """Did GET /release map the release to this very title, with quality
        and languages to send back? Only then can the grab name its target."""
        if not isinstance(release.quality_raw, dict) or not isinstance(release.languages_raw, list):
            return False
        if self.arr_type == "radarr":
            return release.mapped_movie_id == task.arr_id
        return release.mapped_series_id == info.series_id and task.arr_id in release.mapped_episode_ids

    def verdict(self, task: CheckedTask, info, release: _Release) -> Verdict:
        if not self.mapped_here(task, info, release):
            return Verdict((REASON_TARGET,))
        if release.full_season:
            return Verdict((REASON_SEASON_PACK,))
        if len(set(release.mapped_episode_ids)) > 1:
            # Single episodes only (decision of 01.10.2026): the rules and the
            # cache would cover the searched episode alone.
            return Verdict((REASON_MULTI_EPISODE,))
        try:
            parsed = self.agent.http_get(PARSE_PATH, params={"title": release.title})
        except Exception as exc:
            raise _ParseFailed(f"{release.title}: {exc}") from exc
        if self.arr_type == "radarr":
            return radarr_rules.evaluate(info, release.title,
                                         radarr_rules.parse_from_resource(parsed, release.movie_titles),
                                         self.settings)
        return sonarr_rules.evaluate(info, release.title, release.publish_date,
                                     sonarr_rules.parse_from_resource(parsed), self.settings)

    def grab(self, task: CheckedTask, release: _Release) -> None:
        """POST /release with the target named: *arr's cache holds the
        release mapped to whichever search returned it last."""
        body = {"guid": release.guid, "indexerId": release.indexer_id, "shouldOverride": True,
                "quality": release.quality_raw, "languages": release.languages_raw}
        if self.arr_type == "radarr":
            body["movieId"] = task.arr_id
        else:
            # Exactly the searched episode: a multi-episode release never gets here.
            body["seriesId"] = release.mapped_series_id
            body["episodeIds"] = [task.arr_id]
        self.agent.http_post(RELEASE_PATH, body, timeout=self.settings.release_timeout_seconds)

    def indexer_failure(self, tags) -> str:
        """'' when no indexer the release search asks is blocked after
        failures, else why the miss is no clean one. Waits for *arr to
        refresh its health checks first (an abort meanwhile ends the run).
        Unreadable health or indexer list: a failure too (in doubt, do not
        remember). tags: those of the movie or series; surely not asked is
        an indexer with interactive search off or with tags the title has
        none of."""
        if self.agent.wait_or_stop(HEALTH_SETTLE_SECONDS):
            raise _Stopped()
        try:
            health = self.agent.http_get(HEALTH_PATH)
            if not isinstance(health, list):
                raise ValueError("the health list was no list")
            messages = [str(check.get("message") or "") for check in health
                        if isinstance(check, dict) and check.get("source") in INDEXER_HEALTH_SOURCES]
            if not messages:
                return ""
            indexers = self.agent.http_get(INDEXER_PATH)
            if not isinstance(indexers, list):
                raise ValueError("the indexer list was no list")
        except Exception as exc:
            return f"could not read the indexer status, the miss may be an indexer failure: {exc}"
        not_asked = {i.get("name") for i in indexers if isinstance(i, dict) and not _asked(i, tags)}
        asked = [m for m in messages
                 if (names := _named_indexers(m)) is None or any(n not in not_asked for n in names)]
        if not asked:
            return ""
        return f"indexer failure during search — *arr reports: {'; '.join(asked)}"

    def stop_check(self) -> None:
        """The release search can take minutes: an abort that arrived
        meanwhile ends the run before anything is parsed or grabbed."""
        if self.agent.stop_requested():
            raise _Stopped()

    # ── One title ────────────────────────────────────────────────────────

    def entry(self, task: CheckedTask, outcome: str, fingerprint: str | None, **extra) -> dict:
        data = {"instance_id": self.instance_id, "run_id": self.run_id, "mode": self.mode,
                "skill": self.skill_name, "arr_id": task.arr_id, "cache_key": task.cache_key,
                "title": task.title, "outcome": outcome, "arr_pick": None, "pick": None,
                "candidates": [], "error_message": None, "profile_fingerprint": fingerprint,
                "dry_run_round": self.dry_run_round, "settings_fingerprint": self.settings_fingerprint}
        data.update(extra)
        return data

    def store(self, task: CheckedTask, result: _Result) -> None:
        if self.mode == MODE_ACTIVE and result.history_status is not None:
            db.history.record_checked(self.run_id, self.instance_id, task.title, task.arr_id,
                                      task.item_type, task.cache_key, result.history_status, result.entry,
                                      profile_fingerprint=result.entry.get("profile_fingerprint"),
                                      cache=result.cache, hold_key=task.hold_key,
                                      no_results=result.outcome == OUTCOME_NO_RESULTS)
        else:
            db.checked_search_log.insert(result.entry)

    def run(self, task: CheckedTask) -> _Result:
        """Raises _Stopped when an abort arrives after the search, before a
        /parse call or before the grab."""
        try:
            info, profile_id, tags = self.load(task)
        except Exception as exc:
            error = f"could not load the title: {exc}"
            # A failed item (no cache entry): a run with a grab next to it ends
            # partial, not as a clean success (A6). No search ran.
            return _Result(OUTCOME_ERROR, error,
                           self.entry(task, OUTCOME_ERROR, task.profile_fingerprint, error_message=error),
                           ITEM_FAILED, searched=False)
        fingerprint = self.fingerprint(task, profile_id)

        def entry(outcome: str, **extra) -> dict:
            # The row names the profile it was checked under: the page's
            # "profile changed" compares with this very profile.
            return self.entry(task, outcome, fingerprint, profile_id=_int(profile_id), **extra)

        try:
            releases = self.search(task)
        except Exception as exc:
            error = f"release search failed: {exc}"
            return _Result(OUTCOME_ERROR, error, entry(OUTCOME_ERROR, error_message=error),
                           ITEM_FAILED, searched=not _refused(exc))
        self.stop_check()
        if not releases:
            failure = self.indexer_failure(tags)
            if failure:
                return _Result(OUTCOME_ERROR, failure, entry(OUTCOME_ERROR, error_message=failure), ITEM_FAILED)
            return _Result(OUTCOME_NO_RESULTS, "", entry(OUTCOME_NO_RESULTS), ITEM_NO_HIT)

        limit = self.settings.dry_run_max_releases if self.mode == MODE_DRY_RUN else len(releases)
        candidates: list[dict] = []
        pick: _Release | None = None
        parse_failures: list[str] = []
        for index, release in enumerate(releases):
            # The first approved release is what the search command would have grabbed.
            arr_choice = index == 0
            done = (pick is not None and self.mode == MODE_ACTIVE) or index >= limit
            if done:
                candidates.append(_candidate(release, VERDICT_UNCHECKED, arr_choice=arr_choice))
                continue
            self.stop_check()
            try:
                verdict = self.verdict(task, info, release)
            except _ParseFailed as exc:
                parse_failures.append(str(exc))
                candidates.append(_candidate(release, VERDICT_ERROR, (REASON_PARSE_ERROR,),
                                             arr_choice=arr_choice))
                continue
            chosen = verdict.ok and pick is None
            if chosen:
                pick = release
            candidates.append(_candidate(release, VERDICT_PASS if verdict.ok else VERDICT_REJECT,
                                         verdict.reasons, verdict.notes, chosen, arr_choice))

        common = {"arr_pick": releases[0].title, "pick": _pick(pick), "candidates": candidates}
        if pick is None and parse_failures:
            # Not a clean miss: the release /parse could not check may be the
            # right one. Failed item without a cache entry, searched again later.
            error = (f"/parse failed for {len(parse_failures)} release(s) and no release passed — "
                     f"first: {parse_failures[0]}")
            return _Result(OUTCOME_ERROR, error, entry(OUTCOME_ERROR, error_message=error, **common), ITEM_FAILED)
        if pick is None:
            # A failing indexer may have had the clean release: not a clean miss.
            failure = self.indexer_failure(tags)
            if failure:
                return _Result(OUTCOME_ERROR, failure, entry(OUTCOME_ERROR, error_message=failure, **common),
                               ITEM_FAILED)
            return _Result(OUTCOME_NO_CLEAN_HIT, "", entry(OUTCOME_NO_CLEAN_HIT, **common), ITEM_NO_HIT)
        if self.mode == MODE_DRY_RUN:
            return _Result(OUTCOME_WOULD_GRAB, "", entry(OUTCOME_WOULD_GRAB, **common), None)
        self.stop_check()
        try:
            self.grab(task, pick)
        except Exception as exc:
            # Never a second candidate: a grab that did happen must not turn
            # into a second download.
            if _refused(exc):
                error = f"grab failed: {exc}"
                return _Result(OUTCOME_GRAB_FAILED, error,
                               entry(OUTCOME_GRAB_FAILED, error_message=error, **common), ITEM_FAILED)
            error = ("grab sent, no clear answer — it may be downloading; check the *arr queue, "
                     f"reset the cache to retry sooner: {exc}")
            return _Result(OUTCOME_GRAB_UNCERTAIN, error,
                           entry(OUTCOME_GRAB_UNCERTAIN, error_message=error, **common), ITEM_FAILED, cache=True)
        return _Result(OUTCOME_GRABBED, "", entry(OUTCOME_GRABBED, **common), ITEM_GRABBED)


def run_checked(skill_name: str, agent, run_id: int, tasks: list[CheckedTask], mode: str,
                clock=time.monotonic, *, profiles=None, started: float | None = None,
                config: dict | None = None, check_indexers: bool = True) -> CheckedRunOutcome:
    """Check the titles in order. Pauses (checks nothing) while an indexer's
    search switches differ. Stops on an abort (between titles, after a
    release search, before a /parse call and before a grab), at the rate
    cap, when the time budget is used up (no new title is started) or when
    the database refuses a write.

    profiles: the run's ProfileState — the fingerprint of a title comes from
    the movie or series it loads. started: clock() when the skill began, so
    the budget includes reading the profiles and collecting candidates.
    config: the instance as the run began (dry-run round, settings); the
    agent's config may be swapped while the run goes on. check_indexers:
    False when the skill read the indexer list already (before collecting)."""
    config = config if config is not None else agent.config
    settings = CheckedSearchSettings.from_stored(config.get("checked_search_settings"))
    delay = int(config.get("seconds_between_actions", 2) or 0)
    deadline = (clock() if started is None else started) + settings.time_budget_minutes * 60
    outcome = CheckedRunOutcome()
    pause = indexer_pause(agent) if check_indexers else ""
    if pause:
        agent.log("warn", skill_name, pause)
        outcome.paused = pause
        return outcome
    check = _TitleCheck(skill_name, agent, run_id, mode, settings, config, profiles)

    for index, task in enumerate(tasks):
        if agent.stop_requested():
            outcome.stopped = True
            break
        if clock() >= deadline:
            outcome.budget_exhausted = True
            note = (f"Time budget of {settings.time_budget_minutes} min used up — "
                    f"{len(tasks) - index} title(s) left for the next run")
            outcome.notes.append(note)
            agent.log("warn", skill_name, note)
            break
        token = agent.reserve_action()
        if token is None:
            agent.log("warn", skill_name, "Rate cap reached — stopping run")
            outcome.rate_capped = True
            break

        try:
            result = check.run(task)
        except _Stopped:
            outcome.stopped = True
            break

        if not result.searched:
            agent.release_action(token)
        try:
            check.store(task, result)
        except Exception as exc:
            message = f"Could not store the checked search for {task.title} ({result.outcome}): {exc}"
            agent.log("error", skill_name, message)
            if mode == MODE_ACTIVE and result.outcome in (OUTCOME_GRABBED, OUTCOME_GRAB_UNCERTAIN):
                _keep_unsaved(agent, check.instance_id, run_id, task, result)
            outcome.store_error = message
            break
        _count(outcome, mode, result.outcome, task, result.error)
        _log_title(agent, skill_name, mode, task, result.outcome, result.entry, result.error)

        if delay > 0 and index < len(tasks) - 1 and agent.wait_or_stop(delay):
            outcome.stopped = True
            break

    if mode == MODE_DRY_RUN:
        outcome.handled = outcome.checked
        agent.log("info", skill_name,
                  f"Dry run: {outcome.checked} title(s) checked, {outcome.would_grab} would grab")
    else:
        outcome.triggered = outcome.grabbed + outcome.no_hit
        agent.log("info", skill_name,
                  f"Checked search: {outcome.grabbed} grabbed, {outcome.no_hit} without a clean hit, "
                  f"{len(outcome.errors)} failed")
    return outcome


def _keep_unsaved(agent, instance_id: int, run_id: int, task: CheckedTask, result: _Result) -> None:
    """The grab is out (or may be); only the bookkeeping failed. Keep it with
    the unsaved commands: it blocks the title, and a later run stores it."""
    with agent.runtime.unsaved_lock:
        agent.runtime.unsaved_submissions.append(UnsavedCheckedGrab(
            run_id=run_id, instance_id=instance_id, title=task.title, arr_id=task.arr_id,
            item_type=task.item_type, cache_key=task.cache_key, status=result.history_status,
            log_entry=result.entry, profile_fingerprint=result.entry.get("profile_fingerprint"),
            sent_at=datetime.now(timezone.utc), hold_key=task.hold_key,
        ))


def _count(outcome: CheckedRunOutcome, mode: str, result: str, task: CheckedTask, error: str) -> None:
    if result in (OUTCOME_ERROR, OUTCOME_GRAB_FAILED, OUTCOME_GRAB_UNCERTAIN):
        outcome.errors.append(f"{task.title}: {error}")
        return
    outcome.checked += 1
    if result == OUTCOME_WOULD_GRAB:
        outcome.would_grab += 1
    elif result == OUTCOME_GRABBED:
        outcome.grabbed += 1
    elif mode == MODE_ACTIVE:
        outcome.no_hit += 1


def _log_title(agent, skill_name, mode, task, result, entry, error) -> None:
    pick = (entry.get("pick") or {}).get("title")
    if result == OUTCOME_GRABBED:
        agent.log("info", skill_name, f"Grabbed {pick} for {task.title}")
    elif result == OUTCOME_GRAB_FAILED:
        agent.log("warn", skill_name, f"Could not grab {pick} for {task.title}: {error}")
    elif result == OUTCOME_GRAB_UNCERTAIN:
        agent.log("warn", skill_name, f"Grab of {pick} for {task.title} without a clear answer, "
                                      f"the title stays blocked: {error}")
    elif result == OUTCOME_ERROR:
        agent.log("warn", skill_name, f"Checked search for {task.title} failed: {error}")
    elif result == OUTCOME_WOULD_GRAB:
        agent.log("debug", skill_name, f"Dry run — {task.title}: would grab {pick} (*arr: {entry.get('arr_pick')})")
    elif result == OUTCOME_NO_CLEAN_HIT:
        rejected = sum(1 for c in entry["candidates"] if c["verdict"] == VERDICT_REJECT)
        agent.log("debug" if mode == MODE_DRY_RUN else "info", skill_name,
                  f"No clean release for {task.title} — {rejected} rejected")
    else:
        agent.log("debug", skill_name, f"No approved release for {task.title}")
