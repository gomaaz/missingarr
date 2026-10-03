"""Imports: the downloads Radarr and Sonarr hold back, the app's proposal for
each one, missingarr's own verdict and the open-imports count; further down,
import, command status and discard.

Every function takes an agent and uses only agent.config, agent.http_get,
agent.http_post, agent.http_delete and agent.log. In operation that agent is
Orchestrator.detached_agent(config): never started, it only lends these
methods. Nothing goes into the database except activity log lines. The
caches below live in memory and only spare *arr repeated reads; an import or
a discard always reads the queue and the proposal again first.
"""

import itertools
import json
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Callable

import requests
import urllib3.exceptions

from backend.checked_search import import_check
from backend.checked_search.import_check import ImportTarget, ImportVerdict
from backend.checked_search.normalize import parse_utc
from backend.checked_search.settings import CheckedSearchSettings
from backend.imports import entries
from backend.imports.entries import Assessment, BlockedDownload

SKILL = "imports"                          # activity log skill

QUEUE_PATH = "/api/v3/queue"
QUEUE_STATUS_PATH = "/api/v3/queue/status"
QUEUE_DETAILS_PATH = "/api/v3/queue/details"   # the whole queue in one answer, unpaged
SYSTEM_STATUS_PATH = "/api/v3/system/status"
MANUAL_IMPORT_PATH = "/api/v3/manualimport"
COMMAND_PATH = "/api/v3/command"

QUEUE_PAGE_SIZE = 1000
QUEUE_MAX_PAGES = 20
MANUAL_IMPORT_TIMEOUT = 120                # s; *arr runs ffprobe on every video file
PROPOSAL_SLOTS = 2                         # GET /manualimport at once per app (tabs, reloads, imports)
CACHE_SECONDS = 60                         # proposals, queue snapshot, series, count
RESTART_GRACE_SECONDS = 120                # the queue is empty right after an app start
STATUS_FLAGS = ("errors", "warnings", "unknownErrors", "unknownWarnings")
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"         # UTC, every checked_at value

# 409 texts (str(ImportConflict))
ALREADY_HANDLED = "Already handled — this download no longer waits in the queue"
PROPOSAL_CHANGED = "The app's proposal has changed — reload the page"
IMPORT_RUNNING = "An import of this download is already queued or running in the app"
IMPORT_LOCKED = "Import is locked: {why_not}"
INSTANCE_OFF = "This instance is switched off — switch it on to import or discard"   # used by the API
# 503 text (str(ProposalsBusy))
PROPOSALS_BUSY = "Too many proposal requests, try again"
# 502 text (str(QueueTooLarge), also its arr_error text)
QUEUE_TOO_LARGE = "Queue too large to read completely"

# Module attribute on purpose: tests replace clock and utcnow.
clock: Callable[[], float] = time.monotonic


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def stamp_utc() -> str:
    return utcnow().strftime(TIME_FORMAT)


class ImportConflict(Exception):
    """An action refused before anything was sent to *arr -> HTTP 409, str(exc) is the detail."""


class ProposalsBusy(Exception):
    """No proposal slot of the app became free within MANUAL_IMPORT_TIMEOUT
    -> HTTP 503, str(exc) is the detail. The app was not asked."""


class QueueTooLarge(ValueError):
    """read_queue() reached QUEUE_MAX_PAGES before it had every record: an
    incomplete list is never returned. A ValueError, so the API maps it
    through arr_error() (502, QUEUE_TOO_LARGE)."""


# ─── In-memory state ──────────────────────────────────────────────────────────
# One lock guards every dict. The count has one more lock per instance, so two
# callers (menu, dashboard, several tabs) never read the same queue at once.
# Every cache is keyed by the instance's scope (id, type, URL): when an
# instance is pointed at another app, nothing of the old app is served.
Scope = tuple[int, str, str]
_lock = threading.Lock()
_queues: dict[Scope, tuple[float, tuple[BlockedDownload, ...]]] = {}
_proposals: dict[tuple[Scope, str], "Proposal"] = {}
_counts: dict[Scope, "InstanceCount"] = {}
_count_locks: dict[int, threading.Lock] = {}
_series_caches: dict[Scope, tuple[float, dict]] = {}
# Raised by invalidate(), list_open() and forget_instance(). A queue snapshot
# or count is only stored when the generation did not change while it was
# read, so a read that began before a discard cannot bring the old answer back.
_generation: dict[int, int] = {}
# The same for proposals, raised only by invalidate() and forget_instance():
# loading the list (list_open) must not throw away a proposal the app is still
# working out, or the next card load would make the app run ffprobe again.
_proposal_generation: dict[int, int] = {}
# Read order of proposals: of two overlapping reads, the later one wins.
_tickets = itertools.count(1)
# At most PROPOSAL_SLOTS proposal reads per app at once, whoever asks (cards in
# several tabs, a reload in the browser, an import). Kept when an instance is
# forgotten: a read still running at the same app keeps its slot.
_proposal_slots: dict[Scope, threading.BoundedSemaphore] = {}
# Imports this process sent: (instance id, command id) -> SentImport. Written
# by import_download, read by command_status.
_sent: dict[tuple[int, int], "SentImport"] = {}
# (instance id, download id) of every import or discard running right now:
# one action per download at a time (import_download, discard_download).
_busy: set[tuple[int, str]] = set()
# Configuration revision per instance id, raised only by forget_instance(). An
# action takes it at its start and sends nothing when it changed meanwhile.
_revision: dict[int, int] = {}
# (scope, download id) -> (clock(), ids of the download's ManualImport commands
# that GET /command showed before the POST) of an import whose POST answer got
# lost: the POST may still reach the app, so a further import or discard of the
# download waits until GET /command shows a new ManualImport of it or the guard
# is UNCERTAIN_GUARD_SECONDS old (written by import_download, Task 5). Never
# dropped because the download is missing from the queue: a download client the
# app cannot read or a restart of the app empties the queue for a while. Kept
# by forget_instance(): the POST went to that app whatever is edited here.
_uncertain: dict[tuple[Scope, str], tuple[float, frozenset[int]]] = {}


def _fresh(stamp: float) -> bool:
    return clock() - stamp < CACHE_SECONDS


def _bump(instance_id: int, *, proposals: bool = True) -> None:
    """Call with _lock held. proposals=False (list_open) leaves the
    proposal generation alone."""
    _generation[instance_id] = _generation.get(instance_id, 0) + 1
    if proposals:
        _proposal_generation[instance_id] = _proposal_generation.get(instance_id, 0) + 1


def _drop(store: dict, instance_id: int) -> None:
    """Every entry of one instance, whatever its scope. Call with _lock held.
    Keys are a scope, a (scope, download id) pair or an (instance id, command id) pair."""
    for key in list(store):
        head = key[0]
        if (head[0] if isinstance(head, tuple) else head) == instance_id:
            del store[key]


def _instance_id(agent) -> int:
    return agent.config.get("id")


def _scope(agent) -> Scope:
    """Which app the cached answers belong to: instance id, type and URL."""
    config = agent.config
    return (config.get("id"), str(config.get("type") or ""), str(config.get("url") or "").strip().rstrip("/"))


def _arr_type(agent) -> str:
    arr_type = agent.config.get("type")
    if arr_type not in entries.ARR_TYPES:
        raise ValueError(f"Unknown instance type: {arr_type}")
    return arr_type


def arr_error(exc: BaseException) -> tuple[int, str]:
    """HTTP status and text for the page. Fixed texts only: the message of a
    requests exception names the URL and must never reach the page.
    Timeout first: ConnectTimeout is a ConnectionError as well."""
    if isinstance(exc, QueueTooLarge):
        return 502, QUEUE_TOO_LARGE
    if isinstance(exc, requests.exceptions.Timeout):
        return 504, "Connection timed out"
    if isinstance(exc, requests.exceptions.ConnectionError):
        return 503, "Cannot connect to instance"
    if isinstance(exc, requests.exceptions.HTTPError):
        # Not `if exc.response`: a Response is falsy for every 4xx/5xx (A-L1).
        response = exc.response
        code = response.status_code if response is not None and isinstance(response.status_code, int) else 0
        if 300 <= code < 400:
            return 502, f"Instance answered with a redirect (HTTP {code}) — check the URL"
        if code in (401, 403):
            # Not 401: apiFetch() takes every 401 for a lost missingarr session.
            return 502, "Invalid API key"
        return 502, f"HTTP {code} from instance"
    if isinstance(exc, (json.JSONDecodeError, requests.exceptions.JSONDecodeError)):
        return 502, "Instance did not answer with JSON — is the URL correct?"
    if isinstance(exc, requests.exceptions.RequestException):
        # Before ValueError: InvalidURL, MissingSchema and InvalidHeader are both.
        return 502, "Request to instance failed"
    if isinstance(exc, ValueError):
        return 502, "Unexpected answer from instance"
    return 502, f"Unexpected error ({type(exc).__name__})"


# ─── Queue ────────────────────────────────────────────────────────────────────

def read_queue(agent) -> list[dict]:
    """Every record of the queue, page by page (the list and the count).
    includeUnknown…Items: without it the app leaves out downloads it could
    not map ("Unknown Series"), the most common reason a download is held
    back. Never cut short: QUEUE_MAX_PAGES pages without every record raise
    QueueTooLarge."""
    flag = "includeUnknownMovieItems" if _arr_type(agent) == "radarr" else "includeUnknownSeriesItems"
    records: list[dict] = []
    for page in range(1, QUEUE_MAX_PAGES + 1):
        answer = agent.http_get(QUEUE_PATH, params={"page": page, "pageSize": QUEUE_PAGE_SIZE, flag: "true"})
        if not isinstance(answer, dict) or not isinstance(answer.get("records"), list):
            raise ValueError("queue answer without a record list")
        batch = answer["records"]
        records.extend(batch)
        total = answer.get("totalRecords")
        if not batch or (isinstance(total, int) and not isinstance(total, bool) and len(records) >= total):
            return records
    raise QueueTooLarge(QUEUE_TOO_LARGE)


def read_queue_details(agent) -> list[dict]:
    """The whole queue in one answer (GET /api/v3/queue/details): unpaged,
    unknown items included (the endpoint has no filter for them), like the
    paged queue with includeUnknown…Items. One snapshot: read page by page,
    a record can slip past when records before it leave between two pages.
    Used wherever missingarr must be sure a download is there or gone: the
    re-read before an action and the evidence of an import."""
    answer = agent.http_get(QUEUE_DETAILS_PATH)
    if not isinstance(answer, list):
        raise ValueError("queue details answer is not a list")
    return answer


def _has_record(records: list[dict], download_id: str) -> bool:
    """Is any record of this download in the queue answer, in whatever state
    (importing, importPending, importBlocked …)? (download_queued, Task 5)"""
    return any(isinstance(record, dict) and record.get("downloadId") == download_id for record in records)


def _read_blocked(agent, read: Callable) -> list[BlockedDownload]:
    """read(agent) grouped. Stored as the instance's snapshot unless
    invalidate(), list_open() or forget_instance() ran while it read."""
    scope = _scope(agent)
    with _lock:
        generation = _generation.get(scope[0], 0)
    downloads = entries.group_blocked(read(agent))
    with _lock:
        if _generation.get(scope[0], 0) == generation:
            _queues[scope] = (clock(), tuple(downloads))
    return downloads


def blocked_downloads(agent, *, fresh: bool = True) -> list[BlockedDownload]:
    """The downloads the app holds back (paged queue). fresh=False serves the
    snapshot of the last read while it is younger than CACHE_SECONDS. A read
    during which the instance was invalidated is returned but not stored."""
    if not fresh:
        with _lock:
            hit = _queues.get(_scope(agent))
        if hit is not None and _fresh(hit[0]):
            return list(hit[1])
    return _read_blocked(agent, read_queue)


def list_open(agent) -> list[BlockedDownload]:
    """For the page: a fresh read. The cached count is dropped (and a count
    read meanwhile is not stored), so the next count matches what the page
    shows. Proposals stay: a proposal read still running is stored as usual."""
    downloads = blocked_downloads(agent, fresh=True)
    instance_id = _instance_id(agent)
    with _lock:
        _drop(_counts, instance_id)
        _bump(instance_id, proposals=False)
    return downloads


def app_starting(agent) -> bool:
    """Did the app start less than RESTART_GRACE_SECONDS ago? Its queue is
    still empty then. Never raises: an error counts as no."""
    try:
        system = agent.http_get(SYSTEM_STATUS_PATH)
        started = parse_utc(system.get("startTime")) if isinstance(system, dict) else None
    except Exception:
        return False
    return started is not None and utcnow() - started < timedelta(seconds=RESTART_GRACE_SECONDS)


def find_download(agent, download_id: str, *, fresh: bool) -> BlockedDownload:
    """The download as the app holds it back. fresh=True reads the whole
    queue in one answer (read_queue_details) and stores it as the snapshot;
    fresh=False may serve the snapshot. Not held back: its caches are
    dropped, ImportConflict. The guard of a lost import answer stays: a
    download missing from the queue may only be out of sight for a while
    (the app cannot read its download client, or it restarted)."""
    if fresh:
        downloads = _read_blocked(agent, read_queue_details)
    else:
        downloads = blocked_downloads(agent, fresh=False)
    for download in downloads:
        if download.download_id == download_id:
            return download
    invalidate(_instance_id(agent), download_id)
    raise ImportConflict(ALREADY_HANDLED)


# ─── Proposal and verdict ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class Proposal:
    download_id: str
    items: tuple[dict, ...] = field(repr=False)      # raw GET /manualimport answer (memory only)
    assessment: Assessment = field(repr=False)
    key: str                                         # entries.proposal_key(items)
    stamp: float = field(repr=False, compare=False)  # clock() when read
    ticket: int = field(default=0, repr=False, compare=False)   # read order: a later read wins


def _cached_proposal(scope: Scope, download_id: str) -> Proposal | None:
    with _lock:
        proposal = _proposals.get((scope, download_id))
    if proposal is not None and _fresh(proposal.stamp):
        return proposal
    return None


def _proposal_slot(scope: Scope) -> threading.BoundedSemaphore:
    with _lock:
        slot = _proposal_slots.get(scope)
        if slot is None:
            slot = _proposal_slots[scope] = threading.BoundedSemaphore(PROPOSAL_SLOTS)
        return slot


def load_proposal(agent, download_id: str, *, fresh: bool = False) -> Proposal:
    """GET /manualimport with the download id only: with seriesId Sonarr
    ignores the download id and answers with the series' existing files.
    The app runs ffprobe on every video file, hence the long timeout, the
    cache and the slots: at most PROPOSAL_SLOTS reads per app at once; a
    further read waits up to MANUAL_IMPORT_TIMEOUT for a slot, then raises
    ProposalsBusy without asking the app. Errors are not cached."""
    scope = _scope(agent)
    if not fresh:
        hit = _cached_proposal(scope, download_id)
        if hit is not None:
            return hit
    slot = _proposal_slot(scope)
    if not slot.acquire(timeout=MANUAL_IMPORT_TIMEOUT):
        raise ProposalsBusy(PROPOSALS_BUSY)
    try:
        if not fresh:
            # A read of the same download may have finished while this one waited.
            hit = _cached_proposal(scope, download_id)
            if hit is not None:
                return hit
        return _read_proposal(agent, scope, download_id)
    finally:
        slot.release()


def _read_proposal(agent, scope: Scope, download_id: str) -> Proposal:
    """One read, with a slot held until the answer is stored. Stored only
    when no invalidate() or forget_instance() ran for the instance while it
    was read (loading the list does not count) and no later read of the
    same download stored its answer first."""
    with _lock:
        generation = _proposal_generation.get(scope[0], 0)
        ticket = next(_tickets)
    answer = agent.http_get(
        MANUAL_IMPORT_PATH,
        params={"downloadId": download_id, "filterExistingFiles": "true"},
        timeout=MANUAL_IMPORT_TIMEOUT,
    )
    if not isinstance(answer, list):
        raise ValueError("manual import answer is not a list")
    items = tuple(item for item in answer if isinstance(item, dict))
    proposal = Proposal(
        download_id=download_id,
        items=items,
        assessment=entries.assess(items, _arr_type(agent)),
        key=entries.proposal_key(items),
        stamp=clock(),
        ticket=ticket,
    )
    with _lock:
        for key in [key for key, old in _proposals.items() if not _fresh(old.stamp)]:
            del _proposals[key]
        stored = _proposals.get((scope, download_id))
        if _proposal_generation.get(scope[0], 0) == generation and (stored is None or stored.ticket < ticket):
            _proposals[(scope, download_id)] = proposal
    return proposal


def import_target(assessment: Assessment) -> ImportTarget | None:
    if assessment.movie_id is not None:
        return ImportTarget(movie_id=assessment.movie_id)
    if assessment.series_id is not None and assessment.episode_ids:
        return ImportTarget(series_id=assessment.series_id, episode_ids=tuple(assessment.episode_ids))
    return None


def _series_cache(scope: Scope) -> dict:
    """One dict per instance for import_check, replaced after CACHE_SECONDS.
    invalidate() leaves it alone: an import changes no series."""
    now = clock()
    with _lock:
        hit = _series_caches.get(scope)
        if hit is None or now - hit[0] >= CACHE_SECONDS:
            hit = (now, {})
            _series_caches[scope] = hit
        return hit[1]


def verdict_for(agent, download: BlockedDownload, proposal: Proposal) -> ImportVerdict:
    return import_check.check_import(
        agent.http_get,
        _arr_type(agent),
        download.title,
        import_target(proposal.assessment),
        CheckedSearchSettings.from_stored(agent.config.get("checked_search_settings")),
        download_id=download.download_id,
        cache=_series_cache(_scope(agent)),
    )


def proposal_view(agent, download_id: str) -> dict:
    """The card of one download: the app's proposal, whether it may be
    imported as it is, and the verdict. Raises ImportConflict when the
    download is gone, *arr errors otherwise."""
    arr_type = _arr_type(agent)
    download = find_download(agent, download_id, fresh=False)
    cached = _cached_proposal(_scope(agent), download_id) is not None
    proposal = load_proposal(agent, download_id, fresh=False)
    if not proposal.items:
        # The app answers [] for a download it no longer knows, and the
        # snapshot may still hold one that just left the queue.
        download = find_download(agent, download_id, fresh=True)
    verdict = verdict_for(agent, download, proposal)
    assessment = proposal.assessment
    # Sonarr keeps a download whose episodes were not all imported: say so first.
    uncovered = (len(set(download.episode_ids) - set(assessment.episode_ids))
                 if download.episode_ids and assessment.episode_ids else 0)
    return {
        "download_id": download.download_id,
        "title": download.title,
        "target": entries.target_text(assessment.videos, arr_type),
        "candidates": [entries.candidate_view(item, arr_type) for item in proposal.items],
        "importable": assessment.importable,
        "why_not": assessment.why_not,
        "uncovered_episodes": uncovered,
        "proposal_key": proposal.key,
        "verdict": verdict.as_dict(),
        "cached": cached,
    }


# ─── Open-imports count ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class InstanceCount:
    count: int | None          # None = "?": the app started < RESTART_GRACE_SECONDS ago, or an error
    error: str                 # "" or arr_error(...)[1]
    starting: bool             # True when count is None because the app just started
    checked_at: str            # stamp_utc() when computed
    stamp: float = field(default=0.0, repr=False, compare=False)   # clock()


def count_open(agent) -> InstanceCount:
    """Never raises. The queue status flags are a cheap pre-check: every
    held-back download carries a warning or an error, so with every flag
    false nothing waits. A flag that is missing counts as set."""
    try:
        status = agent.http_get(QUEUE_STATUS_PATH)
        if not isinstance(status, dict):
            raise ValueError("queue status answer is not an object")
        if all(status.get(flag) is False for flag in STATUS_FLAGS):
            count = 0
        else:
            count = len(blocked_downloads(agent, fresh=True))
        if count == 0 and app_starting(agent):
            # Right after a start the app's queue is still empty.
            return InstanceCount(count=None, error="", starting=True, checked_at=stamp_utc(), stamp=clock())
        return InstanceCount(count=count, error="", starting=False, checked_at=stamp_utc(), stamp=clock())
    except Exception as exc:
        return InstanceCount(count=None, error=arr_error(exc)[1], starting=False, checked_at=stamp_utc(),
                             stamp=clock())


def cached_count(agent) -> InstanceCount:
    """count_open() at most once per CACHE_SECONDS per instance. A second
    caller waits for the first and takes its value. An invalidate() while
    counting wins: that result is returned but not stored."""
    scope = _scope(agent)
    with _lock:
        instance_lock = _count_locks.setdefault(scope[0], threading.Lock())
    with instance_lock:
        with _lock:
            hit = _counts.get(scope)
            generation = _generation.get(scope[0], 0)
        if hit is not None and _fresh(hit.stamp):
            return hit
        result = count_open(agent)
        with _lock:
            if _generation.get(scope[0], 0) == generation:
                _counts[scope] = result
        return result


def invalidate(instance_id: int, download_id: str | None = None) -> None:
    """After an action: count and queue snapshot of the instance, and with
    download_id its cached proposal. The series cache stays. Reads still
    running for the instance (queue, count and proposals) will not store
    their (older) answer."""
    with _lock:
        _drop(_counts, instance_id)
        _drop(_queues, instance_id)
        if download_id is not None:
            for key in [key for key in _proposals if key[0][0] == instance_id and key[1] == download_id]:
                del _proposals[key]
        _bump(instance_id)


def instance_revision(instance_id: int) -> int:
    """The configuration revision of the instance (0 until it was first
    forgotten). An action takes it at its start (the API before it reads the
    instance) and sends nothing if it changed."""
    with _lock:
        return _revision.get(instance_id, 0)


def forget_instance(instance_id: int) -> None:
    """The instance was edited, switched on or off, or deleted (called by
    backend/api/instances.py): every cache of it and every import sent to it
    are forgotten, and its configuration revision is raised, so an action
    still running sends nothing and registers no command. Reads still
    running for it do not store their answer. The proposal slots and the
    guards of lost import answers stay (they belong to the app)."""
    with _lock:
        for store in (_queues, _proposals, _counts, _series_caches, _sent):
            _drop(store, instance_id)
        _bump(instance_id)
        _revision[instance_id] = _revision.get(instance_id, 0) + 1


def reset_caches() -> None:
    """Empties every cache, the registry of sent imports, the running
    actions, the revisions, the guards and the proposal slots (tests)."""
    with _lock:
        _queues.clear()
        _proposals.clear()
        _counts.clear()
        _count_locks.clear()
        _series_caches.clear()
        _generation.clear()
        _proposal_generation.clear()
        _proposal_slots.clear()
        _sent.clear()
        _busy.clear()
        _revision.clear()
        _uncertain.clear()


# ─── Import, command status, discard ──────────────────────────────────────────

COMMAND_NAME = "ManualImport"
IMPORT_MODE = "auto"                     # with SABnzbd: move
COMMAND_TIMEOUT = 30                     # s for POST /command
RUNNING_STATUSES = ("queued", "started")
FAILED_STATUSES = ("failed", "aborted", "cancelled")
KNOWN_STATUSES = (*RUNNING_STATUSES, "completed", *FAILED_STATUSES, "orphaned")
STATUS_OTHER = "other"                   # reported instead of a status the apps do not have
CONFIRM_SECONDS = 100                    # after the POST: "confirming", then "unconfirmed"; ends inside the
                                         # page's 120 s, which start only with the import answer
UNCERTAIN_GUARD_SECONDS = 600            # a lost POST answer guards its download at most this long
SENT_KEEP_SECONDS = 24 * 3600            # registry entries older than this are dropped on insert
HISTORY_PATH = "/api/v3/history"
IMPORTED_EVENT_TYPE = 3                  # downloadFolderImported (query value)
IMPORTED_EVENT_NAMES = ("downloadFolderImported", IMPORTED_EVENT_TYPE)   # records answer the name

STATE_SENT = "sent"                      # import_download: the app took the command
STATE_UNCERTAIN = "uncertain"            # import_download: the answer got lost, the command may run
STATE_RUNNING = "running"
STATE_CONFIRMING = "confirming"          # completed, the download still queued: not final yet
STATE_IMPORTED = "imported"
STATE_UNCONFIRMED = "unconfirmed"        # completed, the queue did not confirm it within CONFIRM_SECONDS
STATE_FAILED = "failed"
STATE_UNKNOWN = "unknown"

# 409 texts (str(ImportConflict)) and 404 text (str(UnknownCommand))
ACTION_BUSY = "Another action for this download is running"
IMPORT_MAY_RUN = "An import may still be running in the app — try again in a few minutes"
INSTANCE_CHANGED = "The instance was changed — reload the page"
UNKNOWN_COMMAND = "Unknown command — missingarr did not send it to this instance"

# Fixed texts only: never a command's exception or message and never an error
# body of the app in an answer, a toast or the activity log.
MSG_UNCERTAIN = "The app may still run the import — check the queue"
MSG_IMPORTED = "Imported"
MSG_UNCONFIRMED_RADARR = "The app ran the import; the queue has not confirmed it yet — check it in the app"
MSG_UNCONFIRMED_SONARR = ("The app ran the import; the queue has not confirmed it yet — it may have imported only "
                          "some episodes; check it in the app")
UNCONFIRMED_MESSAGES = {"radarr": MSG_UNCONFIRMED_RADARR, "sonarr": MSG_UNCONFIRMED_SONARR}
MSG_NO_RECORD = "The download left the queue, but the app has no import record of it — check it in the app"
MSG_RESTARTED = "The app may have restarted since the import was sent — check it in the app"
MSG_FAILED = "The import failed in the app — see the app's log"
MSG_ABORTED = "The import was aborted in the app — check the queue"
MSG_CANCELLED = "The import was cancelled in the app — check the queue"
FAILED_MESSAGES = {"failed": MSG_FAILED, "aborted": MSG_ABORTED, "cancelled": MSG_CANCELLED}
MSG_ORPHANED = "The app restarted during the import — check the queue"
MSG_GONE = "The app no longer knows this command — check the queue"
MSG_ODD_STATUS = "The app reports an unexpected command status — check the queue"


class UnknownCommand(Exception):
    """command_status() of a command this process did not send to this app
    (or no longer remembers) -> HTTP 404, str(exc) is the detail."""


@dataclass(frozen=True)
class ImportStarted:
    state: str               # STATE_SENT or STATE_UNCERTAIN
    command_id: int | None   # None when uncertain
    files: int
    title: str               # BlockedDownload.title
    target: str              # entries.target_text(videos, type)
    message: str             # "" when sent, MSG_UNCERTAIN when uncertain


@dataclass(frozen=True)
class SentImport:
    download_id: str
    title: str
    target: str
    scope: Scope             # the app it went to: (instance id, type, URL)
    sent_at: float           # clock() right after the POST answer
    logged: bool = False     # final result written to the activity log


@dataclass(frozen=True)
class CommandState:
    command_id: int
    state: str               # STATE_RUNNING, STATE_CONFIRMING, STATE_IMPORTED, STATE_UNCONFIRMED,
                             # STATE_FAILED or STATE_UNKNOWN
    status: str              # *arr command status, one of KNOWN_STATUSES or STATUS_OTHER ("" after a 404)
    message: str             # "" while running, the unconfirmed text while confirming, else one of the MSG_* texts


@dataclass(frozen=True)
class DiscardResult:
    download_id: str
    title: str
    blocklist: bool
    queue_id: int


@contextmanager
def _action(instance_id: int, download_id: str):
    """One import or discard per download at a time. Never waits: a second
    action is refused at once (409), from the first read to the POST or
    DELETE of the first one. An outside auto-import is not covered; the
    command check right before sending narrows that race."""
    key = (instance_id, download_id)
    with _lock:
        if key in _busy:
            raise ImportConflict(ACTION_BUSY)
        _busy.add(key)
    try:
        yield
    finally:
        with _lock:
            _busy.discard(key)


def _manual_imports(agent) -> list[dict]:
    """GET COMMAND_PATH: every ManualImport the app knows, in any status (the
    name compared without case). Answer no list -> ValueError."""
    answer = agent.http_get(COMMAND_PATH)
    if not isinstance(answer, list):
        raise ValueError("command list is not a list")
    return [command for command in answer
            if isinstance(command, dict) and str(command.get("name") or "").lower() == COMMAND_NAME.lower()]


def _touches(command: dict, paths, download_id: str | None) -> bool:
    """Has the command a file with one of these paths, or a file of this download?"""
    body = command.get("body") if isinstance(command.get("body"), dict) else {}
    files = body.get("files") if isinstance(body.get("files"), list) else []
    for item in files:
        if not isinstance(item, dict):
            continue
        if item.get("path") in paths:
            return True
        if download_id is not None and item.get("downloadId") == download_id:
            return True
    return False


def _running(commands: list[dict], paths, download_id: str | None) -> bool:
    return any(command.get("status") in RUNNING_STATUSES and _touches(command, paths, download_id)
               for command in commands)


def running_import(agent, paths: set[str] | frozenset[str] = frozenset(), download_id: str | None = None) -> bool:
    """Is a ManualImport queued or started with one of these files, or with
    a file of this download? The app merges a second command with the same
    paths into the first, whatever its targets, and a file the first one
    moved makes the second fail. A discard would delete the files under a
    queued import (disk commands run one after another in the app)."""
    return _running(_manual_imports(agent), paths, download_id)


def _command_ids(commands: list[dict], download_id: str) -> frozenset[int]:
    """The ids of the ManualImport commands with a file of this download. A
    command without an integer id cannot be told apart and is left out."""
    return frozenset(command["id"] for command in commands
                     if isinstance(command.get("id"), int) and not isinstance(command["id"], bool)
                     and _touches(command, (), download_id))


def _guard(scope: Scope, download_id: str, before: frozenset[int]) -> None:
    """A POST whose answer got lost may still reach the app (a proxy can
    forward it late): the download is guarded from now on. before: the ids
    of the download's ManualImport commands that GET /command showed before
    the POST. The app keeps ended commands in that list for a few minutes,
    and none of them is the command that was sent."""
    with _lock:
        _uncertain[(scope, download_id)] = (clock(), frozenset(before))


def _check_guard(scope: Scope, download_id: str, commands: list[dict]) -> None:
    """A guarded download (see _guard) refuses a further import or discard
    (IMPORT_MAY_RUN) until GET /command shows a new ManualImport of it, one
    whose id was not in the list before the POST (then the usual check of
    running imports applies), or UNCERTAIN_GUARD_SECONDS have passed.
    An older ManualImport of the download, ended or not, ends nothing, and
    neither does a queue without the download (it may be out of sight for a
    while: download client not readable, app restarted)."""
    key = (scope, download_id)
    with _lock:
        guard = _uncertain.get(key)
        if guard is None:
            return
        stamp, before = guard
        seen = bool(_command_ids(commands, download_id) - before)
        if not seen and clock() - stamp < UNCERTAIN_GUARD_SECONDS:
            raise ImportConflict(IMPORT_MAY_RUN)
        del _uncertain[key]


def _check_revision(instance_id: int, revision: int) -> None:
    """Right before a POST or DELETE: the instance was not edited, switched
    or deleted since the action began; else nothing is sent."""
    with _lock:
        if _revision.get(instance_id, 0) != revision:
            raise ImportConflict(INSTANCE_CHANGED)


def _command_id(answer) -> int:
    value = answer.get("id") if isinstance(answer, dict) else None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("command answer without an id")
    return value


def _not_taken(exc: BaseException) -> bool:
    """The app certainly did not take the command: no connection could be
    opened (refused, name not resolved, connect timeout), or it answered
    3xx/4xx. Anything else (read timeout, connection lost after sending, 5xx,
    an answer without a command id) may have reached the app, and the command
    may run all the same. Same rule as the grab of the checked search."""
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return True
    if isinstance(exc, requests.exceptions.ConnectionError):
        inner = exc.args[0] if exc.args else None
        return isinstance(inner, urllib3.exceptions.MaxRetryError) and isinstance(
            inner.reason, urllib3.exceptions.NewConnectionError)
    response = getattr(exc, "response", None)
    return isinstance(exc, requests.exceptions.HTTPError) and response is not None and response.status_code < 500


def _remember(instance_id: int, command_id: int, entry: SentImport, revision: int) -> bool:
    """Registers a sent import, unless the instance changed since the action
    began: checked under the lock forget_instance() takes, so a command of a
    forgotten configuration is never followed."""
    now = clock()
    with _lock:
        if _revision.get(instance_id, 0) != revision:
            return False
        for key in [key for key, old in _sent.items() if now - old.sent_at >= SENT_KEEP_SECONDS]:
            del _sent[key]
        _sent[(instance_id, command_id)] = entry
        return True


def import_download(agent, download_id: str, proposal_key: str, revision: int | None = None) -> ImportStarted:
    """Sends the app's own proposal as a ManualImport command. Only the two
    strings come from the browser: queue and proposal are read again here,
    and the command is built from that fresh answer. A POST that may have
    reached the app all the same ends as STATE_UNCERTAIN, not as an error.
    revision: the instance's configuration revision when the request began
    (the API takes it before it reads the instance); None takes it now."""
    instance_id = _instance_id(agent)
    arr_type = _arr_type(agent)
    scope = _scope(agent)
    if revision is None:
        revision = instance_revision(instance_id)
    with _action(instance_id, download_id):
        download = find_download(agent, download_id, fresh=True)
        proposal = load_proposal(agent, download_id, fresh=True)
        if proposal.key != proposal_key:
            raise ImportConflict(PROPOSAL_CHANGED)
        assessment = proposal.assessment
        if not assessment.importable:
            raise ImportConflict(IMPORT_LOCKED.format(why_not=assessment.why_not))
        videos = list(assessment.videos)
        commands = _manual_imports(agent)
        _check_guard(scope, download_id, commands)
        if _running(commands, {str(video.get("path") or "") for video in videos}, download_id):
            raise ImportConflict(IMPORT_RUNNING)
        # The ManualImports of the download the app lists now: none of them is the one sent below.
        before = _command_ids(commands, download_id)
        body = entries.command_body(videos, download_id, arr_type)
        target = entries.target_text(videos, arr_type)
        _check_revision(instance_id, revision)
        try:
            command_id = _command_id(agent.http_post(COMMAND_PATH, body, timeout=COMMAND_TIMEOUT))
        except Exception as exc:
            invalidate(instance_id, download_id)
            text = arr_error(exc)[1]
            if _not_taken(exc):
                agent.log("error", SKILL,
                          f"Import of '{download.title}' failed: {text} — the app did not take the command")
                raise
            # The command may run all the same, and the POST may even reach
            # the app later: the next import or discard of this download is
            # refused until GET /command shows a new ManualImport of it (409).
            _guard(scope, download_id, before)
            agent.log("warn", SKILL,
                      f"Import sent, answer lost — '{download.title}' → {target} ({len(videos)} file(s)): {text}; "
                      "the app may still run it, check the queue")
            return ImportStarted(state=STATE_UNCERTAIN, command_id=None, files=len(videos), title=download.title,
                                 target=target, message=MSG_UNCERTAIN)
        entry = SentImport(download_id, download.title, target, scope, clock())
        registered = _remember(instance_id, command_id, entry, revision)
        invalidate(instance_id, download_id)
        if not registered:
            # The instance was edited, switched or deleted while the POST ran:
            # missingarr does not follow a command of a configuration it forgot.
            # Its id is known: it counts as new in any case, so the guard ends
            # as soon as GET /command shows this command.
            _guard(scope, download_id, before - {command_id})
            agent.log("warn", SKILL,
                      f"Import sent — '{download.title}' → {target} (command {command_id}, {len(videos)} file(s)), "
                      "but the instance was changed meanwhile: missingarr does not follow it, check the queue")
            return ImportStarted(state=STATE_UNCERTAIN, command_id=None, files=len(videos), title=download.title,
                                 target=target, message=MSG_UNCERTAIN)
        agent.log("info", SKILL,
                  f"Import sent — '{download.title}' → {target} (command {command_id}, {len(videos)} file(s))")
        return ImportStarted(state=STATE_SENT, command_id=command_id, files=len(videos), title=download.title,
                             target=target, message="")


def download_queued(agent, download_id: str) -> bool:
    """Is any queue record of this download left, in whatever state
    (importing, importPending, importBlocked …)? One snapshot of the whole
    queue (read_queue_details), never pages."""
    return _has_record(read_queue_details(agent), download_id)


def import_recorded(agent, download_id: str) -> bool:
    """Did the app record an import of this download (history eventType 3,
    downloadFolderImported)? Only each record's eventType is looked at;
    "data" is never read."""
    answer = agent.http_get(HISTORY_PATH, params={"downloadId": download_id, "eventType": IMPORTED_EVENT_TYPE})
    if not isinstance(answer, dict) or not isinstance(answer.get("records"), list):
        raise ValueError("history answer without a record list")
    return any(isinstance(record, dict) and record.get("eventType") in IMPORTED_EVENT_NAMES
               for record in answer["records"])


def _start_time(agent) -> datetime | None:
    """startTime of GET SYSTEM_STATUS_PATH; None when missing or unreadable.
    Errors of the request propagate."""
    system = agent.http_get(SYSTEM_STATUS_PATH)
    return parse_utc(system.get("startTime")) if isinstance(system, dict) else None


def _completed(agent, command_id: int, sent: SentImport, resource: dict) -> CommandState:
    """"Imported" needs evidence from one lifetime of the app: its start time,
    read before and after queue and history, is the same both times and
    earlier than the command's "queued" (right after a start the queue is
    empty, so a restart must be ruled out); no queue record of the download
    is left (in any state); the history has an import record. A download
    still in the queue proves nothing either way (the app drops an imported
    download only at its next queue refresh): "confirming" while the page
    asks (CONFIRM_SECONDS after the POST), then "unconfirmed", never "failed".
    Known limit: while the app cannot read its download client its queue is
    empty, and its health shows that only later; a partly imported Sonarr
    download can look imported then."""
    queued = parse_utc(resource.get("queued"))
    started = _start_time(agent)
    if started is None or queued is None or started >= queued:
        return CommandState(command_id, STATE_UNKNOWN, "completed", MSG_RESTARTED)
    still_queued = download_queued(agent, sent.download_id)
    recorded = not still_queued and import_recorded(agent, sent.download_id)
    if _start_time(agent) != started:
        # The app restarted while queue and history were read: they may come from two lifetimes.
        return CommandState(command_id, STATE_UNKNOWN, "completed", MSG_RESTARTED)
    if still_queued:
        message = UNCONFIRMED_MESSAGES[_arr_type(agent)]
        if clock() - sent.sent_at < CONFIRM_SECONDS:
            return CommandState(command_id, STATE_CONFIRMING, "completed", message)
        return CommandState(command_id, STATE_UNCONFIRMED, "completed", message)
    if recorded:
        return CommandState(command_id, STATE_IMPORTED, "completed", MSG_IMPORTED)
    return CommandState(command_id, STATE_UNKNOWN, "completed", MSG_NO_RECORD)


def _result_line(sent: SentImport, command_id: int, state: CommandState) -> tuple[str, str]:
    if state.state == STATE_IMPORTED:
        return "info", f"Import done — '{sent.title}' imported (command {command_id})"
    if state.state == STATE_UNCONFIRMED:
        return "warn", f"Import not confirmed — '{sent.title}' (command {command_id}): {state.message}"
    if state.state == STATE_FAILED:
        return "error", f"Import failed — '{sent.title}' (command {command_id}): {state.message}"
    return "warn", f"Import result unknown — '{sent.title}' (command {command_id}): {state.message}"


def _final(agent, command_id: int, state: CommandState) -> CommandState:
    """A final state of an import sent here: one log line, once."""
    key = (_instance_id(agent), command_id)
    with _lock:
        sent = _sent.get(key)
        first = sent is not None and not sent.logged
        if first:
            _sent[key] = replace(sent, logged=True)
    if first:
        invalidate(key[0], sent.download_id)
        level, line = _result_line(sent, command_id, state)
        agent.log(level, SKILL, line)
    return state


def command_known(instance_id: int, command_id: int) -> bool:
    """Does this process follow an import with this command id at this
    instance? forget_instance() ends that (edit, switch on or off, delete).
    The API asks before it checks whether the instance is switched on, so
    the page gets the 404 of an unknown command and stops asking."""
    with _lock:
        return (instance_id, command_id) in _sent


def command_status(agent, command_id: int) -> CommandState:
    """What became of an import this process sent to this app. Any other
    command id raises UnknownCommand before the app is asked. The command's
    "exception", "message" and "result" are never read: the answer and the
    log carry fixed texts only."""
    with _lock:
        sent = _sent.get((_instance_id(agent), command_id))
    if sent is None or sent.scope != _scope(agent):
        raise UnknownCommand(UNKNOWN_COMMAND)
    try:
        resource = agent.http_get(f"{COMMAND_PATH}/{command_id}")
    except requests.exceptions.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            # Restarted app or command older than a day.
            return _final(agent, command_id, CommandState(command_id, STATE_UNKNOWN, "", MSG_GONE))
        raise
    if not isinstance(resource, dict):
        raise ValueError("command answer is not an object")
    status = resource.get("status")
    status = status if status in KNOWN_STATUSES else STATUS_OTHER
    if status in RUNNING_STATUSES:
        return CommandState(command_id, STATE_RUNNING, status, "")
    if status == "completed":
        state = _completed(agent, command_id, sent, resource)
        if state.state == STATE_CONFIRMING:
            return state
    elif status in FAILED_STATUSES:
        state = CommandState(command_id, STATE_FAILED, status, FAILED_MESSAGES[status])
    elif status == "orphaned":
        state = CommandState(command_id, STATE_UNKNOWN, status, MSG_ORPHANED)
    else:
        state = CommandState(command_id, STATE_UNKNOWN, status, MSG_ODD_STATUS)
    return _final(agent, command_id, state)


def discard_download(agent, download_id: str, blocklist: bool, revision: int | None = None) -> DiscardResult:
    """Removes the download and its files from the download client. One
    queue id is enough: the app removes the whole download, all episode
    records. changeCategory stays false: SABnzbd does not support it. With
    the blocklist the app marks only a release it grabbed itself as failed
    (blocklist, maybe a new search); a download added by hand is only removed.
    revision: as for import_download."""
    instance_id = _instance_id(agent)
    scope = _scope(agent)
    if revision is None:
        revision = instance_revision(instance_id)
    with _action(instance_id, download_id):
        download = find_download(agent, download_id, fresh=True)
        if not download.queue_ids:
            raise ValueError("download without a queue id")
        commands = _manual_imports(agent)
        _check_guard(scope, download_id, commands)
        if _running(commands, frozenset(), download_id):
            raise ImportConflict(IMPORT_RUNNING)
        queue_id = download.queue_ids[0]
        params = {"removeFromClient": "true", "blocklist": "true" if blocklist else "false",
                  "skipRedownload": "false", "changeCategory": "false"}
        _check_revision(instance_id, revision)
        try:
            agent.http_delete(f"{QUEUE_PATH}/{queue_id}", params=params)
        except requests.exceptions.HTTPError as exc:
            invalidate(instance_id, download_id)
            if exc.response is not None and exc.response.status_code == 404:
                raise ImportConflict(ALREADY_HANDLED) from None
            agent.log("error", SKILL, f"Discard of '{download.title}' failed: {arr_error(exc)[1]}")
            raise
        except Exception as exc:
            invalidate(instance_id, download_id)
            agent.log("error", SKILL, f"Discard of '{download.title}' failed: {arr_error(exc)[1]}")
            raise
        invalidate(instance_id, download_id)
        if blocklist:
            line = (f"Discarded — '{download.title}' (blocklist on: if the app grabbed this release itself, it "
                    "marks it as failed, puts it on the blocklist and may search again; a download added by hand "
                    "is only removed)")
        else:
            line = f"Discarded — '{download.title}' (not blocklisted, no new search)"
        agent.log("info", SKILL, line)
        return DiscardResult(download_id=download_id, title=download.title, blocklist=blocklist, queue_id=queue_id)
