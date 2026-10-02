import sqlite3

import pytest

from backend import database, db
from backend.config import settings
from backend.db import history
from backend.skills.verify_commands import VerifyCommandsSkill
from backend.verification import (
    ITEM_COMPLETED, ITEM_FAILED, ITEM_GRABBED, ITEM_NO_HIT, ITEM_SUBMITTED,
    aggregate_run_status, count_verified,
)


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "missingarr.db"
    monkeypatch.setattr(settings, "database_url", str(path))
    monkeypatch.setattr(database, "_cached_secret_key", None)
    database.init_db()
    return path


@pytest.fixture(autouse=True)
def fresh_housekeeping():
    VerifyCommandsSkill._last_housekeeping.clear()
    yield
    VerifyCommandsSkill._last_housekeeping.clear()


def sql(statement, params=()):
    with database.get_db() as conn:
        return [tuple(r) for r in conn.execute(statement, params).fetchall()]


def make_instance():
    return db.instances.create({"name": "Radarr", "type": "radarr", "url": "http://127.0.0.1:9",
                                "api_key": "k" * 32})


def log_entry(inst, run, outcome="grabbed"):
    return {"instance_id": inst["id"], "run_id": run, "mode": "active", "skill": "search_missing",
            "arr_id": 1, "cache_key": "mov:1", "title": "Movie (2020)", "outcome": outcome}


class FailOn:
    """Connection wrapper that fails on the first statement containing `needle`."""

    def __init__(self, conn, needle):
        self._conn, self._needle = conn, needle

    def execute(self, statement, *args):
        if self._needle in statement:
            raise sqlite3.OperationalError("injected failure")
        return self._conn.execute(statement, *args)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_grabbed_and_no_hit_settle_like_completed():
    assert aggregate_run_status([ITEM_GRABBED, ITEM_NO_HIT]) == "success"
    assert aggregate_run_status([ITEM_GRABBED, ITEM_FAILED]) == "partial"
    assert aggregate_run_status([ITEM_NO_HIT, ITEM_SUBMITTED]) == "pending"
    assert count_verified([ITEM_COMPLETED, ITEM_GRABBED, ITEM_NO_HIT, ITEM_FAILED]) == 3


def test_record_checked_writes_item_cache_and_log_together(db_path):
    inst = make_instance()
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    entry = {**log_entry(inst, run), "profile_fingerprint": "aaaa"}
    history.record_checked(run, inst["id"], "Movie (2020)", 1, "movie", "mov:1", ITEM_GRABBED, entry,
                           profile_fingerprint="aaaa")
    assert sql("SELECT command_status, command_id, cache_key FROM search_history_items") == [
        (ITEM_GRABBED, None, "mov:1")]
    assert sql("SELECT verified_at IS NOT NULL FROM search_history_items")[0][0] == 1
    assert sql("SELECT cache_key, profile_fingerprint, grabbed_at IS NOT NULL FROM searched_items") == [
        ("mov:1", "aaaa", 1)]
    assert sql("SELECT run_id, outcome, profile_fingerprint FROM checked_search_log") == [(run, "grabbed", "aaaa")]


def test_a_grab_without_a_clear_answer_is_a_failed_item_that_blocks(db_path):
    inst = make_instance()
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", ITEM_FAILED, cache=True,
                           profile_fingerprint="aaaa")
    assert sql("SELECT command_status, cache_key FROM search_history_items") == [(ITEM_FAILED, "mov:1")]
    assert sql("SELECT cache_key, profile_fingerprint, grabbed_at IS NOT NULL FROM searched_items") == [
        ("mov:1", "aaaa", 1)]


def test_only_a_grab_marks_the_cache_entry(db_path):
    inst = make_instance()
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", ITEM_GRABBED)
    history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", ITEM_NO_HIT)
    assert sql("SELECT grabbed_at FROM searched_items") == [(None,)]
    history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", ITEM_GRABBED)
    history.record_submission(run, inst["id"], "A", 1, "movie", "mov:1", 77)
    assert sql("SELECT grabbed_at FROM searched_items") == [(None,)]


def empty_mark():
    return sql("SELECT cache_key, no_results_at IS NOT NULL FROM searched_items ORDER BY cache_key")


def test_only_an_empty_search_marks_the_cache_entry_as_empty(db_path):
    # Owner decision 02.10.2026: no_results (not one approved release) is
    # searched again after "Search again if still missing"; any other search
    # of the title clears the mark.
    inst = make_instance()
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", ITEM_NO_HIT, no_results=True)
    history.record_checked(run, inst["id"], "B", 2, "movie", "mov:2", ITEM_NO_HIT)
    history.record_checked(run, inst["id"], "C", 3, "movie", "mov:3", ITEM_GRABBED, no_results=True)
    assert empty_mark() == [("mov:1", 1), ("mov:2", 0), ("mov:3", 0)]
    history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", ITEM_NO_HIT)
    assert empty_mark()[0] == ("mov:1", 0)
    for again in (lambda: history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", ITEM_GRABBED),
                  lambda: history.record_submission(run, inst["id"], "A", 1, "movie", "mov:1", 77),
                  lambda: db.searched.add(inst["id"], "mov:1", "A", "movie")):
        history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", ITEM_NO_HIT, no_results=True)
        assert empty_mark()[0] == ("mov:1", 1)
        again()
        assert empty_mark()[0] == ("mov:1", 0)


def test_a_search_without_a_fingerprint_keeps_the_stored_one(db_path):
    # The title's profile was unknown this time: the old fingerprint is better than none.
    inst = make_instance()
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    history.record_submission(run, inst["id"], "A", 1, "movie", "mov:1", 77, profile_fingerprint="bbbb")
    history.record_submission(run, inst["id"], "A", 1, "movie", "mov:1", 78)
    history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", ITEM_NO_HIT)
    assert sql("SELECT profile_fingerprint FROM searched_items") == [("bbbb",)]


def test_a_new_search_stores_the_new_fingerprint(db_path):
    inst = make_instance()
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    history.record_submission(run, inst["id"], "A", 1, "movie", "mov:1", 77, profile_fingerprint="aaaa")
    assert sql("SELECT profile_fingerprint FROM searched_items") == [("aaaa",)]
    history.record_submission(run, inst["id"], "A", 1, "movie", "mov:1", 78, profile_fingerprint="bbbb")
    history.record_checked(run, inst["id"], "B", 2, "movie", "mov:2", ITEM_NO_HIT, profile_fingerprint="cccc")
    assert sql("SELECT cache_key, profile_fingerprint FROM searched_items ORDER BY cache_key") == [
        ("mov:1", "bbbb"), ("mov:2", "cccc")]


def test_no_hit_is_cached_failed_is_not(db_path):
    inst = make_instance()
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", ITEM_NO_HIT)
    history.record_checked(run, inst["id"], "B", 2, "movie", "mov:2", ITEM_FAILED)
    assert sql("SELECT cache_key FROM searched_items") == [("mov:1",)]
    assert history.get_item_statuses(run) == [ITEM_NO_HIT, ITEM_FAILED]


def test_unknown_status_is_refused(db_path):
    inst = make_instance()
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    with pytest.raises(ValueError):
        history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", ITEM_SUBMITTED)


def test_record_checked_is_one_transaction(db_path, monkeypatch):
    inst = make_instance()
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    real = database.get_connection
    monkeypatch.setattr(database, "get_connection", lambda: FailOn(real(), "INSERT INTO checked_search_log"))
    with pytest.raises(sqlite3.OperationalError):
        history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", ITEM_GRABBED, log_entry(inst, run))
    monkeypatch.setattr(database, "get_connection", real)
    assert sql("SELECT COUNT(*) FROM search_history_items")[0][0] == 0
    assert sql("SELECT COUNT(*) FROM searched_items")[0][0] == 0


def test_run_with_only_settled_items_finishes_without_waiting(db_path):
    inst = make_instance()
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", ITEM_GRABBED)
    history.record_checked(run, inst["id"], "B", 2, "movie", "mov:2", ITEM_NO_HIT)
    history.finish_run(run, 2, 2, "success")
    assert sql("SELECT status, verified_count FROM search_history WHERE id=?", (run,)) == [("success", 2)]


def test_run_with_a_submitted_command_still_waits(db_path):
    inst = make_instance()
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    history.record_submission(run, inst["id"], "A", 1, "movie", "mov:1", 77)
    history.finish_run(run, 1, 1, "success")
    assert sql("SELECT status FROM search_history WHERE id=?", (run,)) == [("pending",)]


def test_verification_never_asks_about_checked_items(db_path):
    inst = make_instance()
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", ITEM_GRABBED)
    assert history.get_pending_items(inst["id"]) == []
    assert history.expire_stale_items(inst["id"], 0) == 0
    assert history.get_item_statuses(run) == [ITEM_GRABBED]


def test_clear_and_purge_remove_settled_checked_runs(db_path):
    inst = make_instance()
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", ITEM_GRABBED)
    history.finish_run(run, 1, 1, "success")
    sql("UPDATE search_history SET started_at=datetime('now','localtime','-400 days')")
    assert history.purge_old_runs(inst["id"], 365) == 1
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    history.record_checked(run, inst["id"], "B", 2, "movie", "mov:2", ITEM_NO_HIT)
    history.finish_run(run, 1, 1, "success")
    assert history.clear() == {"deleted": 1, "kept_open": 0}


def test_housekeeping_purges_old_log_rows(db_path, monkeypatch):
    inst = make_instance()
    db.checked_search_log.insert({"instance_id": inst["id"], "mode": "dry_run", "skill": "search_missing",
                                  "title": "Old", "outcome": "would_grab", "cache_key": "mov:1"})
    sql("UPDATE checked_search_log SET created_at=datetime('now','localtime','-400 days')")
    monkeypatch.setattr(settings, "history_retention_days", 365)

    class Agent:
        config = db.instances.get_by_id(inst["id"])
        messages = []

        def log(self, level, skill, message):
            self.messages.append(message)

    agent = Agent()
    VerifyCommandsSkill().housekeeping(agent)
    assert sql("SELECT COUNT(*) FROM checked_search_log")[0][0] == 0
    assert agent.messages == ["Housekeeping: removed 0 run(s) older than 365 days and 0 expired cache entries; "
                              "1 pre-filter log row(s)"]


class HousekeepingAgent:
    def __init__(self, inst):
        self.config = db.instances.get_by_id(inst["id"])
        self.messages = []

    def log(self, level, skill, message):
        self.messages.append(message)


def test_housekeeping_keeps_a_grab_for_its_days_despite_a_short_retry_window(db_path):
    # "Search again if still missing after (days)" holds a grab N days,
    # also when retry_hours is shorter (Codex round 2, F1).
    inst = make_instance()
    sql("UPDATE instances SET retry_hours=24, checked_search_settings='{\"search_again_after_days\": 7}'")
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", ITEM_GRABBED)
    history.record_checked(run, inst["id"], "B", 2, "movie", "mov:2", ITEM_NO_HIT)
    sql("UPDATE searched_items SET searched_at=datetime('now','localtime','-2 days'), "
        "grabbed_at=CASE WHEN grabbed_at IS NOT NULL THEN datetime('now','localtime','-2 days') END")
    VerifyCommandsSkill().housekeeping(HousekeepingAgent(inst))
    assert sql("SELECT cache_key FROM searched_items") == [("mov:1",)]
    sql("UPDATE searched_items SET searched_at=datetime('now','localtime','-8 days'), "
        "grabbed_at=datetime('now','localtime','-8 days')")
    VerifyCommandsSkill._last_housekeeping.clear()
    VerifyCommandsSkill().housekeeping(HousekeepingAgent(inst))
    assert sql("SELECT cache_key FROM searched_items") == []


def test_purge_expired_keeps_young_grabs_only_when_asked(db_path):
    inst = make_instance()
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", ITEM_GRABBED)
    sql("UPDATE searched_items SET searched_at=datetime('now','localtime','-2 days'), "
        "grabbed_at=datetime('now','localtime','-2 days')")
    assert db.searched.purge_expired(inst["id"], 24, keep_grab_days=7) == 0
    assert db.searched.purge_expired(inst["id"], 24) == 1


def test_housekeeping_never_deletes_an_empty_search_that_still_blocks(db_path):
    # Owner decision 02.10.2026: the mark of an empty search must live until
    # "Search again if still missing" is up. Without a retry window nothing
    # is purged; with one, a row leaves only once it no longer blocks anyway
    # (an empty search never blocks longer than Retry).
    inst = make_instance()
    sql("UPDATE instances SET retry_hours=0, checked_search_settings='{\"search_again_after_days\": 7}'")
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", ITEM_NO_HIT, no_results=True)
    sql("UPDATE searched_items SET searched_at=datetime('now','localtime','-6 days'), "
        "no_results_at=datetime('now','localtime','-6 days')")
    VerifyCommandsSkill().housekeeping(HousekeepingAgent(inst))
    assert empty_mark() == [("mov:1", 1)]
    assert set(db.searched.lookup_many(inst["id"], ["mov:1"], 0, no_results_release_days=7)) == {"mov:1"}

    sql("UPDATE instances SET retry_hours=24")
    sql("UPDATE searched_items SET searched_at=datetime('now','localtime','-2 hours'), "
        "no_results_at=datetime('now','localtime','-2 hours')")
    VerifyCommandsSkill._last_housekeeping.clear()
    VerifyCommandsSkill().housekeeping(HousekeepingAgent(inst))
    assert empty_mark() == [("mov:1", 1)]
    sql("UPDATE searched_items SET searched_at=datetime('now','localtime','-2 days'), "
        "no_results_at=datetime('now','localtime','-2 days')")
    assert db.searched.lookup_many(inst["id"], ["mov:1"], 24, no_results_release_days=7) == {}
    VerifyCommandsSkill._last_housekeeping.clear()
    VerifyCommandsSkill().housekeeping(HousekeepingAgent(inst))
    assert empty_mark() == []


class FakeCommands:
    """*arr's answer to GET /command/{id}: every command it is asked about failed."""

    def __init__(self, inst, answer):
        self.config = db.instances.get_by_id(inst["id"])
        self.answer = answer
        self.state = {}
        self.messages = []

    def stop_requested(self):
        return False

    def http_get_raw(self, path):
        return 200, {"status": self.answer}

    def log(self, level, skill, message):
        self.messages.append(message)


def test_a_failed_older_command_does_not_release_a_newer_grab(db_path):
    # Codex round 2, F3: an old command still awaiting its verdict, then a
    # checked grab of the same title; *arr reports the old command orphaned
    # (restart). The grab's cache entry must stay.
    inst = make_instance()
    old_run = history.start_run(inst["id"], "Radarr", "search_missing")
    old = history.record_submission(old_run, inst["id"], "A", 1, "movie", "mov:1", 42)
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", ITEM_GRABBED)
    assert history.resolve_item(old, ITEM_FAILED, inst["id"], "mov:1") is False
    assert sql("SELECT cache_key, grabbed_at IS NOT NULL FROM searched_items") == [("mov:1", 1)]
    assert sql("SELECT command_status FROM search_history_items WHERE id=?", (old,)) == [(ITEM_FAILED,)]


def test_verification_of_an_orphaned_command_keeps_the_newer_grab(db_path):
    inst = make_instance()
    old_run = history.start_run(inst["id"], "Radarr", "search_missing")
    history.record_submission(old_run, inst["id"], "A", 1, "movie", "mov:1", 42)
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", ITEM_GRABBED)
    agent = FakeCommands(inst, "orphaned")
    VerifyCommandsSkill().execute(agent)
    assert sql("SELECT cache_key, grabbed_at IS NOT NULL FROM searched_items") == [("mov:1", 1)]
    assert not any("released" in m for m in agent.messages)


def test_a_failed_command_still_releases_its_own_entry(db_path):
    inst = make_instance()
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    item = history.record_submission(run, inst["id"], "A", 1, "movie", "mov:1", 42)
    other = history.start_run(inst["id"], "Radarr", "search_missing")
    history.record_failed_submission(other, "A", 1, "movie")          # wrote no cache entry
    history.record_submission(other, inst["id"], "A", 1, "movie", "mov:1", None)   # no command id: no entry
    history.record_checked(other, inst["id"], "A", 1, "movie", "mov:1", ITEM_FAILED)   # refused grab: no entry
    assert history.resolve_item(item, ITEM_FAILED, inst["id"], "mov:1") is True
    assert sql("SELECT COUNT(*) FROM searched_items")[0][0] == 0


def test_a_cache_entry_names_the_item_that_wrote_it(db_path):
    inst = make_instance()
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    first = history.record_submission(run, inst["id"], "A", 1, "movie", "mov:1", 42)
    assert sql("SELECT history_item_id FROM searched_items") == [(first,)]
    second = history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", ITEM_GRABBED)
    assert sql("SELECT history_item_id FROM searched_items") == [(second,)]


def older_command_then_grab(inst):
    """Command A awaits its verdict (run pending); then a checked grab B of the
    same title in a run that is settled at once."""
    old_run = history.start_run(inst["id"], "Radarr", "search_missing")
    old = history.record_submission(old_run, inst["id"], "A", 1, "movie", "mov:1", 42)
    history.finish_run(old_run, 1, 1, "success")
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", ITEM_GRABBED)
    history.finish_run(run, 1, 1, "success")
    return old, run


def test_clearing_the_history_keeps_a_newer_grab_after_a_late_failure(db_path):
    # Codex round 3, G1: clear() deletes the settled grab run with its items
    # and keeps the pending command; its late "orphaned" must not release the grab.
    inst = make_instance()
    older_command_then_grab(inst)
    assert history.clear() == {"deleted": 1, "kept_open": 1}
    agent = FakeCommands(inst, "orphaned")
    VerifyCommandsSkill().execute(agent)
    assert sql("SELECT cache_key, grabbed_at IS NOT NULL FROM searched_items") == [("mov:1", 1)]
    assert not any("released" in m for m in agent.messages)


def test_purging_old_runs_keeps_a_newer_grab_after_a_late_failure(db_path):
    inst = make_instance()
    old, run = older_command_then_grab(inst)
    sql("UPDATE search_history SET started_at=datetime('now','localtime','-2 days') WHERE id=?", (run,))
    assert history.purge_old_runs(inst["id"], 1) == 1
    assert history.resolve_item(old, ITEM_FAILED, inst["id"], "mov:1") is False
    assert sql("SELECT cache_key FROM searched_items") == [("mov:1",)]


def test_a_command_from_0_8_0_still_releases_its_entry(db_path):
    # Entries written before 0.9.0 name no item: the newer-search check decides.
    inst = make_instance()
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    item = history.record_submission(run, inst["id"], "A", 1, "movie", "mov:1", 42)
    sql("UPDATE searched_items SET history_item_id=NULL")
    assert history.resolve_item(item, ITEM_FAILED, inst["id"], "mov:1") is True
    assert sql("SELECT COUNT(*) FROM searched_items")[0][0] == 0


def test_a_grab_also_writes_its_hold_key(db_path):
    # Codex round 3, G3: a Sonarr upgrade grab holds its season for the command path.
    inst = make_instance()
    run = history.start_run(inst["id"], "Sonarr", "search_upgrades")
    grabbed = history.record_checked(run, inst["id"], "E3", 3, "episode", "upg:3", ITEM_GRABBED,
                                     profile_fingerprint="aaaa", hold_key="upg:sea-hold:10:1")
    no_hit = history.record_checked(run, inst["id"], "E4", 4, "episode", "upg:4", ITEM_NO_HIT,
                                    profile_fingerprint="aaaa", hold_key="upg:sea-hold:10:2")
    refused = history.record_checked(run, inst["id"], "E5", 5, "episode", "upg:5", ITEM_FAILED,
                                     hold_key="upg:sea-hold:10:3")
    unclear = history.record_checked(run, inst["id"], "E6", 6, "episode", "upg:6", ITEM_FAILED, cache=True,
                                     profile_fingerprint="aaaa", hold_key="upg:sea-hold:10:4")
    assert refused
    assert sql("SELECT cache_key, item_type, profile_fingerprint, grabbed_at IS NOT NULL, history_item_id "
               "FROM searched_items ORDER BY cache_key") == [
        ("upg:3", "episode", "aaaa", 1, grabbed), ("upg:4", "episode", "aaaa", 0, no_hit),
        ("upg:6", "episode", "aaaa", 1, unclear),
        ("upg:sea-hold:10:1", "season", "aaaa", 1, grabbed), ("upg:sea-hold:10:4", "season", "aaaa", 1, unclear)]
