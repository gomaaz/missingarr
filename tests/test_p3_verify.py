import sqlite3

import pytest

from backend import database, db
from backend.agents.base import BaseAgent
from backend.config import settings
from backend.db import history
from backend.skills.verify_commands import VerifyCommandsSkill
from backend.verification import ITEM_FAILED, map_command_status


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "missingarr.db"
    monkeypatch.setattr(settings, "database_url", str(path))
    monkeypatch.setattr(database, "_cached_secret_key", None)
    database.init_db()
    return path


def make_instance(**fields):
    data = {"name": "Sonarr", "type": "sonarr", "url": "http://127.0.0.1:9", "api_key": "k" * 32}
    data.update(fields)
    return db.instances.create(data)


def sql(statement, params=()):
    with database.get_db() as conn:
        return conn.execute(statement, params).fetchall()


class FailOn:
    """Connection proxy that fails the first statement containing `needle`."""

    def __init__(self, conn, needle):
        self._conn = conn
        self._needle = needle

    def execute(self, statement, params=()):
        if self._needle in statement:
            raise sqlite3.OperationalError(f"injected failure at {self._needle}")
        return self._conn.execute(statement, params)

    def __getattr__(self, name):
        return getattr(self._conn, name)


class ScriptedAgent(BaseAgent):
    def __init__(self, config, answers=None, default=(200, {"status": "started"})):
        super().__init__(config)
        self.answers = answers or {}
        self.default = default
        self.asked = []

    def build_skills(self):
        return []

    def http_get_raw(self, path):
        command_id = int(path.rsplit("/", 1)[1])
        self.asked.append(command_id)
        return self.answers.get(command_id, self.default)


@pytest.fixture(autouse=True)
def fresh_housekeeping():
    VerifyCommandsSkill._last_housekeeping.clear()
    yield
    VerifyCommandsSkill._last_housekeeping.clear()


def submit(inst, command_ids, cache_prefix="ep"):
    run = history.start_run(inst["id"], inst["name"], "search_missing")
    for cid in command_ids:
        history.record_submission(run, inst["id"], f"T{cid}", cid, "episode", f"{cache_prefix}:{cid}", cid)
    history.finish_run(run, len(command_ids), len(command_ids), "success")
    return run


def agent_for(inst, **kwargs):
    return ScriptedAgent(db.instances.get_by_id(inst["id"]), **kwargs)


@pytest.mark.parametrize("state", ["orphaned", "Orphaned", "aborted", "cancelled", "failed"])
def test_unfinished_commands_count_as_failed(state):
    assert map_command_status(200, {"status": state}) == ITEM_FAILED


def test_orphaned_command_releases_its_cache_key(db_path):
    inst = make_instance()
    run = submit(inst, [7])
    agent = agent_for(inst, answers={7: (200, {"status": "orphaned"})})
    VerifyCommandsSkill().execute(agent)
    assert history.get_item_statuses(run) == ["failed"]
    assert sql("SELECT COUNT(*) FROM searched_items")[0][0] == 0
    assert sql("SELECT status FROM search_history WHERE id=?", (run,))[0][0] == "failed"


def test_status_and_cache_release_are_one_transaction(db_path, monkeypatch):
    inst = make_instance()
    run = submit(inst, [7])
    item_id = sql("SELECT id FROM search_history_items")[0][0]
    real = database.get_connection
    monkeypatch.setattr(database, "get_connection",
                        lambda: FailOn(real(), "DELETE FROM searched_items"))
    with pytest.raises(sqlite3.OperationalError):
        history.resolve_item(item_id, ITEM_FAILED, inst["id"], "ep:7")
    monkeypatch.setattr(database, "get_connection", real)
    assert history.get_item_statuses(run) == ["submitted"]
    assert sql("SELECT COUNT(*) FROM searched_items")[0][0] == 1


def test_resolve_item_reports_whether_a_cache_entry_was_released(db_path):
    inst = make_instance()
    run = submit(inst, [7, 8])
    first, second = (r[0] for r in sql("SELECT id FROM search_history_items ORDER BY id"))
    assert history.resolve_item(first, ITEM_FAILED, inst["id"], "ep:7") is True
    assert history.resolve_item(second, "completed", inst["id"], "ep:8") is False
    assert sorted(history.get_item_statuses(run)) == ["completed", "failed"]
    assert [r[0] for r in sql("SELECT cache_key FROM searched_items")] == ["ep:8"]
    assert sql("SELECT COUNT(*) FROM search_history_items "
               "WHERE verified_at IS NULL OR last_checked_at IS NULL")[0][0] == 0


def test_items_never_checked_come_first(db_path):
    inst = make_instance()
    submit(inst, list(range(1, 61)))
    agent = agent_for(inst)
    VerifyCommandsSkill().execute(agent)
    assert agent.asked == list(range(1, 51))
    agent.asked.clear()
    VerifyCommandsSkill().execute(agent)
    assert agent.asked[:10] == list(range(51, 61))


@pytest.mark.parametrize("no_answer", [(0, None), (502, None), (503, None), (401, None), (302, None)])
def test_unreachable_arr_does_not_use_up_the_grace_period(db_path, no_answer):
    inst = make_instance()
    run = submit(inst, [7])
    sql("UPDATE search_history_items SET created_at=datetime('now','localtime','-25 hours')")
    VerifyCommandsSkill().execute(agent_for(inst, default=no_answer))
    assert history.get_item_statuses(run) == ["submitted"]
    assert sql("SELECT last_checked_at FROM search_history_items")[0][0] is None
    VerifyCommandsSkill().execute(agent_for(inst))
    assert history.get_item_statuses(run) == ["expired"]


def test_verdict_after_a_long_pause_is_read_before_anything_expires(db_path):
    inst = make_instance()
    run = submit(inst, [7])
    sql("UPDATE search_history_items SET created_at=datetime('now','localtime','-30 hours'), "
        "last_checked_at=datetime('now','localtime','-29 hours')")
    VerifyCommandsSkill().execute(agent_for(inst, answers={7: (200, {"status": "failed"})}))
    assert history.get_item_statuses(run) == ["failed"]
    assert sql("SELECT COUNT(*) FROM searched_items")[0][0] == 0


def test_card_numbers_come_from_one_finished_run(db_path):
    inst = make_instance()
    done = submit(inst, [1, 2, 3])
    agent = agent_for(inst, default=(200, {"status": "completed"}))
    history.start_run(inst["id"], inst["name"], "search_missing")  # newer, still running
    VerifyCommandsSkill().execute(agent)
    assert agent.state["last_triggered"] == 3
    assert agent.state["last_verified"] == 3
    assert sql("SELECT status FROM search_history WHERE id=?", (done,))[0][0] == "success"


def test_abort_stops_the_pass(db_path):
    inst = make_instance()
    submit(inst, [1, 2])
    agent = agent_for(inst)
    agent.request_abort()
    VerifyCommandsSkill().execute(agent)
    assert agent.asked == []
