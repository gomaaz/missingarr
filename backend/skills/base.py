from abc import ABC, abstractmethod
from dataclasses import dataclass, field
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


def _store_submission(skill_name: str, agent, run_id: int, result: SearchResult) -> None:
    if result.command_id is None:
        agent.log("warn", skill_name,
                  f"*arr returned no command id for {result.title} — not cached, cannot be verified")
    try:
        db.history.record_submission(
            run_id, agent.config["id"], result.title, result.arr_id,
            result.item_type, result.cache_key, result.command_id,
        )
    except Exception as exc:
        # The command is out; only the bookkeeping failed. Count it as sent and
        # name the command id so it can be traced in *arr (B2).
        agent.log("error", skill_name,
                  f"Command {result.command_id} for {result.title} was sent but could not be stored: {exc}")


def submit_candidates(
    skill_name: str,
    agent,
    run_id: int,
    candidates: Iterable,
    fire: Callable[[object], SearchResult],
    delay: float,
) -> SubmitOutcome:
    """Send one search per candidate, shared by missing and upgrade searches.

    Every command reserves its rate slot first and returns it when *arr did
    not accept the command (A10). Failed submissions are recorded as failed
    items (A6). The loop ends early on an abort (instance disabled or deleted,
    A-L5) or when the rate cap is reached.
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
            outcome.triggered += 1
            _store_submission(skill_name, agent, run_id, result)
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


def finish_search_run(
    skill_name: str,
    agent,
    run_id: int,
    wanted: int,
    outcome: SubmitOutcome,
    notes: Iterable[str] = (),
) -> str:
    """Close the run with an honest status and publish the card numbers.

    Every submission failed, or the run was stopped before sending anything:
    'error'. Otherwise 'success' (finish_run turns it into 'pending' while
    items await verification) with the failures named in error_message.
    """
    notes = list(notes)
    failed = len(outcome.errors)
    if outcome.stopped:
        notes.append("Stopped early: instance disabled or deleted")

    if failed and outcome.triggered == 0:
        status = "error"
        notes.insert(0, f"All {failed} submission(s) failed — first error: {outcome.errors[0]}")
    elif outcome.stopped and outcome.triggered == 0:
        status = "error"
    else:
        status = "success"
        if failed:
            notes.insert(0, f"{failed} of {failed + outcome.triggered} submission(s) failed "
                            f"— first error: {outcome.errors[0]}")

    db.history.finish_run(run_id, wanted, outcome.triggered, status, "; ".join(notes) or None)

    # last_triggered and last_verified always describe the same run (B-L4):
    # a run that just ended has nothing verified yet.
    agent.state["last_wanted"] = wanted
    agent.state["last_triggered"] = outcome.triggered
    agent.state["last_verified"] = 0
    if status != "error":
        agent.state["last_sync"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    return status
