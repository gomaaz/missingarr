"""Pre-filter log of the checked search: one row per title and run.

Never stored here: downloadUrl, infoUrl, guid, headers. The runner hands in
plain titles and numbers only (backend/checked_search/runner.py).
"""

import csv
import io
import json
import sqlite3
from typing import Iterator, Optional

from backend.database import get_connection, get_db

MODES = ("dry_run", "active")
OUTCOMES = ("grabbed", "would_grab", "no_clean_hit", "no_results", "error", "grab_failed", "grab_uncertain")

CSV_HEADER = ["time", "instance", "mode", "title", "outcome", "release", "indexer", "score", "size",
              "quality", "verdict", "reasons", "notes", "chosen", "arr_would_grab"]
_CSV_PAGE = 500

_INSERT = """
    INSERT INTO checked_search_log
        (instance_id, run_id, mode, skill, arr_id, cache_key, title, outcome,
         arr_pick, pick, pick_indexer, pick_score, pick_size, pick_quality, candidates, error_message,
         profile_fingerprint, profile_id, dry_run_round, settings_fingerprint)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

# A dry-run row belongs to the current round when it carries the instance's
# round counter (the round its run began in). Active rows always count.
_CURRENT_ROUND = "(l.mode != 'dry_run' OR l.dry_run_round = i.dry_run_round)"

# The rows of the page and of the CSV, newest first. fingerprints_known: the
# instance has read its profiles at least once (baseline set) — an empty
# profile list that was read is no "never read".
_SELECT = """
    SELECT l.*, COALESCE(i.name, '') AS instance_name, COALESCE(i.type, '') AS arr_type,
           i.profile_fingerprints AS current_fingerprints,
           i.profile_fingerprints_baseline IS NOT NULL AS fingerprints_known
    FROM checked_search_log l
    LEFT JOIN instances i ON i.id = l.instance_id
    {where}
    ORDER BY l.created_at DESC, l.id DESC
"""


def insert_with(conn: sqlite3.Connection, entry: dict) -> int:
    """Write one log row on an open connection (so a caller can put it in the
    same transaction as the history item and the cache entry).

    entry keys: instance_id, run_id, mode, skill, arr_id, cache_key, title,
    outcome, arr_pick, pick (dict with title/indexer/score/size/quality, or
    None), candidates (list of dicts), error_message, profile_fingerprint
    and profile_id (the title's quality profile when it was checked),
    dry_run_round (the round its run began in, dry run only),
    settings_fingerprint (the rule settings it was checked under)."""
    pick = entry.get("pick") or {}
    profile_id = entry.get("profile_id")
    cursor = conn.execute(_INSERT, (
        entry["instance_id"], entry.get("run_id"), entry["mode"], entry["skill"], entry.get("arr_id"),
        entry.get("cache_key") or "", entry["title"], entry["outcome"], entry.get("arr_pick"),
        pick.get("title"), pick.get("indexer"), pick.get("score"), pick.get("size"), pick.get("quality"),
        json.dumps(list(entry.get("candidates") or []), ensure_ascii=False), entry.get("error_message"),
        entry.get("profile_fingerprint"),
        profile_id if isinstance(profile_id, int) and not isinstance(profile_id, bool) else None,
        entry.get("dry_run_round"), entry.get("settings_fingerprint"),
    ))
    return cursor.lastrowid


def insert(entry: dict) -> int:
    with get_db() as conn:
        return insert_with(conn, entry)


def dry_run_keys(instance_id: int, dry_run_round: int) -> dict[str, set]:
    """cache_key -> {(profile fingerprint, settings fingerprint)} of its
    dry-run rows in the given round (the round counter the run began with).

    Errors do not count: a title whose load or release search failed was
    not checked and comes up again in the next dry run. The caller decides
    with the pairs whether the title was checked under its current quality
    profile and the current rule settings (a change means check again)."""
    keys: dict[str, set] = {}
    with get_db() as conn:
        rows = conn.execute(
            "SELECT cache_key, profile_fingerprint, settings_fingerprint FROM checked_search_log "
            "WHERE instance_id=? AND mode='dry_run' AND dry_run_round=? AND cache_key != '' "
            "AND outcome != 'error'",
            (instance_id, dry_run_round),
        )
        for cache_key, profile_fingerprint, settings_fingerprint in rows:
            keys.setdefault(cache_key, set()).add((profile_fingerprint, settings_fingerprint))
    return keys


def _json_dict(raw) -> dict:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _filter(instance_id, mode, outcome, search, only_differences, current_round=False) -> tuple[str, list]:
    conditions: list[str] = []
    params: list = []
    if instance_id is not None:
        conditions.append("l.instance_id=?")
        params.append(instance_id)
    if mode:
        conditions.append("l.mode=?")
        params.append(mode)
    if outcome:
        conditions.append("l.outcome=?")
        params.append(outcome)
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        conditions.append("(l.title LIKE ? ESCAPE '\\' OR i.name LIKE ? ESCAPE '\\')")
        params += [pattern, pattern]
    if only_differences:
        # The filter takes something else than *arr would have, or nothing.
        conditions.append("l.arr_pick IS NOT NULL AND (l.pick IS NULL OR l.pick != l.arr_pick)")
    if current_round:
        conditions.append(_CURRENT_ROUND)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    return where, params


def _row(row: sqlite3.Row) -> dict:
    out = dict(row)
    try:
        candidates = json.loads(out.get("candidates") or "[]")
    except ValueError:
        candidates = []
    out["candidates"] = candidates if isinstance(candidates, list) else []
    out["rejected_count"] = sum(1 for c in out["candidates"] if isinstance(c, dict) and c.get("verdict") == "reject")
    # Checked under a fingerprint its profile no longer has: the profile
    # changed since (spec addendum, badge "profile changed"). Compared with
    # the very profile the row was checked under, so an unchanged twin with
    # the same rules does not hide the change; a removed profile counts as
    # changed, the last one too. Rows without a profile id compare with
    # every profile. Unknown = never read (no baseline), not an empty list.
    current = _json_dict(out.pop("current_fingerprints", None))
    known = bool(out.pop("fingerprints_known", 0))
    fingerprint = out.get("profile_fingerprint")
    profile_id = out.get("profile_id")
    if not fingerprint or not known:
        changed = False
    elif profile_id is not None:
        changed = current.get(str(profile_id)) != fingerprint
    else:
        changed = fingerprint not in set(current.values())
    out["profile_changed"] = changed
    return out


def query(instance_id: Optional[int] = None, mode: Optional[str] = None, outcome: Optional[str] = None,
          search: Optional[str] = None, only_differences: bool = False, current_round: bool = False,
          limit: int = 50, offset: int = 0) -> list[dict]:
    where, params = _filter(instance_id, mode, outcome, search, only_differences, current_round)
    with get_db() as conn:
        rows = conn.execute(_SELECT.format(where=where) + " LIMIT ? OFFSET ?",
                            [*params, limit, offset]).fetchall()
        return [_row(r) for r in rows]


def count(instance_id: Optional[int] = None, mode: Optional[str] = None, outcome: Optional[str] = None,
          search: Optional[str] = None, only_differences: bool = False, current_round: bool = False) -> int:
    where, params = _filter(instance_id, mode, outcome, search, only_differences, current_round)
    with get_db() as conn:
        return conn.execute(
            f"SELECT COUNT(*) FROM checked_search_log l LEFT JOIN instances i ON i.id = l.instance_id {where}",
            params,
        ).fetchone()[0]


def summary(current_round: bool = False) -> list[dict]:
    """Counters for the page: rows per instance, mode and outcome
    (current_round: dry-run rows of the running round only)."""
    where = f"WHERE {_CURRENT_ROUND}" if current_round else ""
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT l.instance_id, COALESCE(i.name, '') AS instance_name, l.mode, l.outcome, COUNT(*) AS n
            FROM checked_search_log l
            LEFT JOIN instances i ON i.id = l.instance_id
            {where}
            GROUP BY l.instance_id, l.mode, l.outcome
            ORDER BY instance_name, l.mode, l.outcome
            """
        ).fetchall()
        return [dict(r) for r in rows]


def purge_old(instance_id: int, days: int) -> int:
    """Same retention as the history (HISTORY_RETENTION_DAYS); 0 keeps all.
    Dry-run rows of the running round are kept until the next reset: the
    round lives in them, and purging them would check their titles again
    without a reset (owner decision 01.10.2026)."""
    if days <= 0:
        return 0
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM checked_search_log WHERE instance_id=? "
            "AND created_at < datetime('now','localtime', ? || ' days') "
            "AND NOT (mode='dry_run' AND dry_run_round IS "
            "(SELECT dry_run_round FROM instances WHERE id=?))",
            (instance_id, f"-{days}", instance_id),
        )
        return cursor.rowcount


def _cell(value) -> str:
    """Spreadsheet-safe text: a leading = + - @ would start a formula."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _yes(value) -> str:
    return "yes" if value else "no"


def iter_csv(instance_id: Optional[int] = None, mode: Optional[str] = None, outcome: Optional[str] = None,
             search: Optional[str] = None, only_differences: bool = False,
             current_round: bool = False) -> Iterator[str]:
    """CSV text with the current filter: one line per checked candidate; a
    title without candidates (no results, error) gets one line without a
    release. Read in pages so a long log is never held in memory at once —
    all pages from one read transaction: under WAL the export sees one state
    of the database, so a row a running search writes meanwhile, a purge or
    a "Reset dry run" during the download neither repeats nor drops a row
    (pages with OFFSET did). Writers are not blocked."""
    where, params = _filter(instance_id, mode, outcome, search, only_differences, current_round)
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    def flush() -> str:
        text = buffer.getvalue()
        buffer.seek(0)
        buffer.truncate()
        return text

    writer.writerow(CSV_HEADER)
    yield flush()
    conn = get_connection()
    try:
        conn.execute("BEGIN")
        cursor = conn.execute(_SELECT.format(where=where), params)
        while True:
            rows = cursor.fetchmany(_CSV_PAGE)
            if not rows:
                return
            for row in map(_row, rows):
                base = [row["created_at"][:19], row["instance_name"], row["mode"], row["title"], row["outcome"]]
                candidates = [c for c in row["candidates"] if isinstance(c, dict)]
                if not candidates:
                    writer.writerow([_cell(v) for v in base + ["", "", "", "", "", "",
                                                               row.get("error_message") or "", "", "no", "no"]])
                for c in candidates:
                    writer.writerow([_cell(v) for v in base + [
                        c.get("title"), c.get("indexer"), c.get("score"), c.get("size"), c.get("quality"),
                        c.get("verdict"), "; ".join(c.get("reasons") or []), "; ".join(c.get("notes") or []),
                        _yes(c.get("chosen")), _yes(c.get("arr_choice")),
                    ]])
            yield flush()
    finally:
        # Also when the download is cancelled (GeneratorExit).
        conn.rollback()
        conn.close()
