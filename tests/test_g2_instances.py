import pytest

from backend import database, db
from backend.config import settings


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "missingarr.db"
    monkeypatch.setattr(settings, "database_url", str(path))
    monkeypatch.setattr(database, "_cached_secret_key", None)
    database.init_db()
    return path


def sql(statement, params=()):
    with database.get_db() as conn:
        return [tuple(r) for r in conn.execute(statement, params).fetchall()]


BASE = {"name": "Radarr", "type": "radarr", "url": "http://127.0.0.1:9", "api_key": "k" * 32}


def test_new_instance_defaults_to_off(db_path):
    inst = db.instances.create(dict(BASE))
    assert inst["checked_search"] == "off"
    assert inst["checked_search_settings"] == {}
    assert (inst["dry_run_round"], inst["dry_run_round_started_at"]) == (0, None)
    assert inst["search_again_after_profile_change"] == 1
    assert (inst["profile_fingerprints"], inst["profile_fingerprints_baseline"]) == ({}, None)


def test_search_again_after_profile_change_is_kept_unless_sent(db_path):
    inst = db.instances.create({**BASE, "search_again_after_profile_change": False})
    assert inst["search_again_after_profile_change"] == 0
    assert db.instances.update(inst["id"], dict(BASE))["search_again_after_profile_change"] == 0
    updated = db.instances.update(inst["id"], {**BASE, "search_again_after_profile_change": True})
    assert updated["search_again_after_profile_change"] == 1


def test_profile_fingerprints_set_the_baseline_only_once(db_path):
    inst = db.instances.create(dict(BASE))
    assert db.instances.store_profile_fingerprints(inst["id"], {"1": "aaaa"}) == {"1": "aaaa"}
    assert db.instances.store_profile_fingerprints(inst["id"], {"1": "bbbb", "2": "cccc"}) == {"1": "aaaa"}
    stored = db.instances.get_by_id(inst["id"])
    assert stored["profile_fingerprints"] == {"1": "bbbb", "2": "cccc"}
    assert stored["profile_fingerprints_baseline"] == {"1": "aaaa"}
    # saving the form does not touch them
    db.instances.update(inst["id"], {**BASE, "interval_minutes": 30})
    assert db.instances.get_by_id(inst["id"])["profile_fingerprints"] == {"1": "bbbb", "2": "cccc"}
    assert db.instances.store_profile_fingerprints(999, {"1": "x"}) is None


def test_create_in_dry_run_starts_a_round_and_stores_settings(db_path):
    inst = db.instances.create({**BASE, "checked_search": "dry_run",
                                "checked_search_settings": {"year_tolerance": 2}})
    assert inst["checked_search"] == "dry_run"
    assert inst["checked_search_settings"] == {"year_tolerance": 2}
    assert len(inst["dry_run_round_started_at"]) == 23


def test_update_without_the_fields_keeps_them(db_path):
    inst = db.instances.create({**BASE, "checked_search": "active", "checked_search_settings": {"prefix_match": False}})
    updated = db.instances.update(inst["id"], {**BASE, "interval_minutes": 30})
    assert updated["checked_search"] == "active"
    assert updated["checked_search_settings"] == {"prefix_match": False}


def test_switching_to_dry_run_starts_a_new_round_staying_keeps_it(db_path):
    inst = db.instances.create(dict(BASE))
    first = db.instances.update(inst["id"], {**BASE, "checked_search": "dry_run"})
    assert first["dry_run_round_started_at"] and first["dry_run_round"] == 1
    sql("UPDATE instances SET dry_run_round_started_at='2026-01-01 00:00:00.000'")
    again = db.instances.update(inst["id"], {**BASE, "checked_search": "dry_run"})
    assert (again["dry_run_round"], again["dry_run_round_started_at"]) == (1, "2026-01-01 00:00:00.000")
    db.instances.update(inst["id"], {**BASE, "checked_search": "active"})
    back = db.instances.update(inst["id"], {**BASE, "checked_search": "dry_run"})
    assert back["dry_run_round"] == 2
    assert back["dry_run_round_started_at"] > "2026-01-01 00:00:00.000"


def test_reset_dry_run_counts_the_round_up(db_path):
    inst = db.instances.create({**BASE, "checked_search": "dry_run"})
    sql("UPDATE instances SET dry_run_round_started_at='2026-01-01 00:00:00.000'")
    value = db.instances.reset_dry_run(inst["id"])
    assert value > "2026-01-01 00:00:00.000"
    stored = db.instances.get_by_id(inst["id"])
    assert (stored["dry_run_round"], stored["dry_run_round_started_at"]) == (1, value)
    db.instances.reset_dry_run(inst["id"])
    assert db.instances.get_by_id(inst["id"])["dry_run_round"] == 2
    assert db.instances.reset_dry_run(999) is None


def test_unreadable_stored_settings_read_as_empty(db_path):
    inst = db.instances.create(dict(BASE))
    sql("UPDATE instances SET checked_search_settings='not json'")
    assert db.instances.get_by_id(inst["id"])["checked_search_settings"] == {}


def test_unknown_mode_from_a_caller_is_stored_as_off(db_path):
    inst = db.instances.create({**BASE, "checked_search": "sometimes"})
    assert inst["checked_search"] == "off"
