import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable, Iterable, Optional

from backend import db

if TYPE_CHECKING:
    from backend.agents.base import BaseAgent


@dataclass(frozen=True)
class SearchResult:
    """Outcome of a single triggered search.

    arr_id is the entity the command actually addressed — the series id for a
    SeriesSearch, not the episode that happened to trigger it. command_id is
    the id *arr returned and the only thing that makes the entry checkable
    later. error is set when ok is False.
    """

    ok: bool
    title: str = ""
    item_type: str = ""
    cache_key: str = ""
    arr_id: int | None = None
    command_id: int | None = None
    error: str = ""
    # Quality profile the title was searched under (0.9.0); stored with the
    # cache entry so a later profile change can release it.
    profile_fingerprint: str | None = None


class BaseSkill(ABC):
    name: str = ""

    @abstractmethod
    def execute(self, agent: "BaseAgent", force: bool = False) -> None:
        """Execute this skill using the provided agent context."""
        ...


def parse_arr_date(value) -> Optional[datetime]:
    """*arr sends ISO timestamps in UTC, mostly with a trailing Z."""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def release_date(record: dict, arr_type: str) -> Optional[datetime]:
    """When a title became available — used for hours_after_release and for
    newest/oldest ordering.

    Radarr: the earlier of digital and physical release, like Radarr itself
    treats a film as released; the cinema date only when neither is known
    (A-L2). Sonarr: the episode's air date.
    """
    if arr_type == "radarr":
        home = [
            d for d in (parse_arr_date(record.get("digitalRelease")),
                        parse_arr_date(record.get("physicalRelease")))
            if d is not None
        ]
        if home:
            return min(home)
        return parse_arr_date(record.get("inCinemas"))
    return parse_arr_date(record.get("airDateUtc"))


@dataclass
class SubmitOutcome:
    triggered: int = 0
    errors: list = field(default_factory=list)
    stopped: bool = False
    rate_capped: bool = False
    # A command *arr accepted could not be stored; the run stopped there.
    store_error: str = ""
    # Titles that ended without an error but are not counted in `triggered`
    # (a checked-search dry run grabs nothing, so its triggered_count stays 0).
    handled: int = 0
    # The checked search did not start (an indexer's search switches differ,
    # or the indexer list could not be read): the message closes the run.
    paused: str = ""


@dataclass(frozen=True)
class UnsavedSubmission:
    """A command *arr accepted whose history item and cache entry could not
    be written. Kept in the instance runtime until a later run stores it."""

    run_id: int
    result: SearchResult
    sent_at: datetime


@dataclass(frozen=True)
class UnsavedCheckedGrab:
    """A grab of the checked search (or one without a clear answer) whose
    history item, cache entry and log row could not be written. Kept in the
    same list as the unsaved commands: it blocks its title until a later run
    stores it. Like them it lives in memory only."""

    run_id: int
    instance_id: int
    title: str
    arr_id: int | None
    item_type: str
    cache_key: str
    status: str
    log_entry: dict
    profile_fingerprint: str | None
    sent_at: datetime
    hold_key: str | None = None      # Sonarr upgrade: the season it holds for the command path


def _record(agent, run_id: int, result: SearchResult) -> None:
    db.history.record_submission(
        run_id, agent.config["id"], result.title, result.arr_id,
        result.item_type, result.cache_key, result.command_id,
        profile_fingerprint=result.profile_fingerprint,
    )


def _store_unsaved(agent, run_id: int, entry) -> None:
    """Store one entry of runtime.unsaved_submissions under run_id."""
    if isinstance(entry, UnsavedCheckedGrab):
        db.history.record_checked(
            run_id, entry.instance_id, entry.title, entry.arr_id, entry.item_type, entry.cache_key,
            entry.status, {**entry.log_entry, "run_id": run_id},
            profile_fingerprint=entry.profile_fingerprint, cache=True, hold_key=entry.hold_key,
        )
    else:
        _record(agent, run_id, entry.result)


def _store_submission(skill_name: str, agent, run_id: int, result: SearchResult) -> str:
    """Store a command *arr accepted. Returns "" or, when the database
    refused it, the message the run is closed with."""
    if result.command_id is None:
        agent.log("warn", skill_name,
                  f"*arr returned no command id for {result.title} — not cached, cannot be verified")
    try:
        _record(agent, run_id, result)
        return ""
    except Exception as exc:
        # The command is out; only the bookkeeping failed. Count it as sent and
        # name the command id so it can be traced in *arr (B2). Keep it for the
        # next run: without its cache entry the title would be searched again,
        # without its item the command could never be verified.
        message = f"Command {result.command_id} for {result.title} was sent but could not be stored: {exc}"
        agent.log("error", skill_name, message)
        with agent.runtime.unsaved_lock:
            agent.runtime.unsaved_submissions.append(
                UnsavedSubmission(run_id, result, datetime.now(timezone.utc))
            )
        return message


def store_unsaved_submissions(skill_name: str, agent, run_id: int) -> None:
    """Store what earlier runs sent but could not record, before this run
    reads the cache. Each entry goes to the run that sent it, or to run_id
    when that run is gone (history cleared meanwhile). Stops at the first
    failure: the database is still unavailable and every further attempt
    would wait out the busy timeout.

    The list lives in memory only (InstanceRuntime); after a restart of the
    process it is gone and those titles can be searched once more.
    """
    runtime = agent.runtime
    stored: list = []
    failure = ""
    with runtime.unsaved_lock:
        while runtime.unsaved_submissions:
            entry = runtime.unsaved_submissions[0]
            try:
                try:
                    _store_unsaved(agent, entry.run_id, entry)
                except sqlite3.IntegrityError:
                    _store_unsaved(agent, run_id, entry)
            except Exception as exc:
                failure = (f"{len(runtime.unsaved_submissions)} sent command(s) or checked grab(s) "
                           f"still could not be stored: {exc}")
                break
            runtime.unsaved_submissions.pop(0)
            stored.append(entry)
    for entry in stored:
        if isinstance(entry, UnsavedCheckedGrab):
            agent.log("info", skill_name, f"Stored the checked search for {entry.title} ({entry.status}), "
                                          f"grabbed earlier")
        else:
            agent.log("info", skill_name,
                      f"Stored command {entry.result.command_id} for {entry.result.title}, sent earlier")
    if failure:
        agent.log("error", skill_name, failure)


def unsaved_cache_keys(agent) -> dict[str, datetime]:
    """cache_key -> time sent, for commands still waiting to be stored. They
    block their titles like cache entries. Without a command id nothing is
    cached (A12), so such entries block nothing."""
    with agent.runtime.unsaved_lock:
        keys: dict[str, datetime] = {}
        for entry in agent.runtime.unsaved_submissions:
            if isinstance(entry, UnsavedCheckedGrab):
                # A grab (or one without a clear answer) always blocks its
                # title, and the season it holds for the command path.
                for key in (entry.cache_key, entry.hold_key):
                    if key:
                        keys[key] = entry.sent_at
            elif entry.result.command_id is not None and entry.result.cache_key:
                keys[entry.result.cache_key] = entry.sent_at
        return keys


def submit_candidates(
    skill_name: str,
    agent,
    run_id: int,
    candidates: Iterable,
    fire: Callable[[object], SearchResult],
    delay: float,
    fingerprint_of: Optional[Callable[[object], Optional[str]]] = None,
) -> SubmitOutcome:
    """Send one search per candidate, shared by missing and upgrade searches.

    Every command reserves its rate slot first and returns it when *arr did
    not accept the command (A10). Failed submissions are recorded as failed
    items (A6). The loop ends early on an abort (instance disabled or deleted,
    A-L5), when the rate cap is reached, or when a sent command could not be
    stored: every further one would be just as untracked.
    """
    outcome = SubmitOutcome()
    candidates = list(candidates)
    for index, candidate in enumerate(candidates):
        if agent.stop_requested():
            outcome.stopped = True
            break
        token = agent.reserve_action()
        if token is None:
            agent.log("warn", skill_name, "Rate cap reached — stopping run")
            outcome.rate_capped = True
            break

        result = fire(candidate)
        if result.ok:
            if fingerprint_of is not None:
                result = replace(result, profile_fingerprint=fingerprint_of(candidate))
            outcome.triggered += 1
            outcome.store_error = _store_submission(skill_name, agent, run_id, result)
            if outcome.store_error:
                break
        else:
            agent.release_action(token)
            outcome.errors.append(f"{result.title}: {result.error}")
            agent.log("warn", skill_name, f"Failed to trigger search for {result.title}: {result.error}")
            try:
                db.history.record_failed_submission(run_id, result.title, result.arr_id, result.item_type)
            except Exception as exc:
                agent.log("error", skill_name, f"Could not record the failed submission: {exc}")

        if delay > 0 and index < len(candidates) - 1 and agent.wait_or_stop(delay):
            outcome.stopped = True
            break
    return outcome


def _verified_now(agent, run_id: int) -> int:
    try:
        latest = db.history.get_latest_run_verification(agent.config["id"])
    except Exception:
        return 0
    return int(latest["verified_count"] or 0) if latest and latest["id"] == run_id else 0


def finish_search_run(
    skill_name: str,
    agent,
    run_id: int,
    wanted: int,
    outcome: SubmitOutcome,
    notes: Iterable[str] = (),
) -> str:
    """Close the run with an honest status and publish the card numbers.

    Every submission failed, the run was stopped before sending anything, or
    a sent command could not be stored: 'error'. Otherwise 'success'
    (finish_run turns it into 'pending' while items await verification) with
    the failures named in error_message.
    """
    notes = list(notes)
    failed = len(outcome.errors)
    succeeded = outcome.triggered + outcome.handled
    if outcome.stopped:
        notes.append("Stopped early: instance disabled or deleted")

    if outcome.store_error:
        status = "error"
        notes.insert(0, f"Stopped: {outcome.store_error}")
    elif outcome.paused:
        # A deliberate pause, no fault (decision Daniel 01.10.2026): the run
        # is a success carrying the reason; last_sync stays (see below).
        status = "success"
        notes.insert(0, outcome.paused)
    elif failed and succeeded == 0:
        status = "error"
        notes.insert(0, f"All {failed} submission(s) failed — first error: {outcome.errors[0]}")
    elif outcome.stopped and succeeded == 0:
        status = "error"
    else:
        status = "success"
        if failed:
            notes.insert(0, f"{failed} of {failed + succeeded} submission(s) failed "
                            f"— first error: {outcome.errors[0]}")

    db.history.finish_run(run_id, wanted, outcome.triggered, status, "; ".join(notes) or None)

    # last_triggered and last_verified always describe the same run (B-L4).
    # A command run that just ended has nothing verified yet (0); a checked
    # search settles its titles at once, finish_run stored them already.
    agent.state["last_wanted"] = wanted
    agent.state["last_triggered"] = outcome.triggered
    agent.state["last_verified"] = _verified_now(agent, run_id)
    # A paused checked search searched nothing: the card's "Last sync" keeps
    # the last run that did, so a pause that lasts shows there.
    if status != "error" and not outcome.paused:
        agent.state["last_sync"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    return status
