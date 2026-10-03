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
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

import requests

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
