import base64

import pytest
from cryptography.fernet import Fernet

from backend import crypto, database, db
from backend.config import settings


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "missingarr.db"
    monkeypatch.setattr(settings, "database_url", str(path))
    monkeypatch.setattr(settings, "secret_key", "")
    monkeypatch.setattr(database, "_cached_secret_key", None)
    crypto._reset_cache()
    database.init_db()
    yield path
    crypto._reset_cache()


def make_instance(name, api_key):
    return db.instances.create({"name": name, "type": "sonarr", "url": "http://s:8989", "api_key": api_key})


def raw_keys():
    with database.get_db() as conn:
        return [r[0] for r in conn.execute("SELECT api_key FROM instances ORDER BY id")]


def stored_settings():
    with database.get_db() as conn:
        return dict(conn.execute("SELECT key, value FROM app_settings").fetchall())


def switch_to(monkeypatch, secret):
    monkeypatch.setattr(settings, "secret_key", secret)
    crypto._reset_cache()
    crypto.init_crypto()


def test_without_secret_key_the_keys_stay_in_the_database(db_path):
    crypto.init_crypto()
    inst = make_instance("Sonarr", "plain-key-123")
    assert db.instances.get_by_id(inst["id"])["api_key"] == "plain-key-123"
    rows = stored_settings()
    assert "encryption_key" in rows and "key_source" not in rows
    assert crypto.get_session_secret() == rows["secret_key"]


def test_secret_key_reencrypts_once_and_removes_the_stored_keys(db_path, monkeypatch):
    crypto.init_crypto()
    first = make_instance("Sonarr", "first-key")
    with database.get_db() as conn:
        conn.execute("INSERT INTO instances (name, type, url, api_key) "
                     "VALUES ('Legacy', 'radarr', 'http://r:7878', 'legacy-plain')")
    old_session = crypto.get_session_secret()

    switch_to(monkeypatch, "s3cret-value")

    rows = stored_settings()
    assert rows["key_source"] == "env"
    assert "encryption_key" not in rows and "secret_key" not in rows
    derived = Fernet(base64.urlsafe_b64encode(crypto._derive("s3cret-value", crypto.FERNET_INFO)))
    assert [derived.decrypt(v[4:].encode()).decode() for v in raw_keys()] == ["first-key", "legacy-plain"]
    assert db.instances.get_by_id(first["id"])["api_key"] == "first-key"
    assert crypto.get_session_secret() != old_session

    before = raw_keys()
    switch_to(monkeypatch, "s3cret-value")
    assert raw_keys() == before


def test_missing_secret_key_after_the_switch_stops_the_start(db_path, monkeypatch):
    crypto.init_crypto()
    make_instance("Sonarr", "first-key")
    switch_to(monkeypatch, "s3cret-value")
    with pytest.raises(RuntimeError, match="SECRET_KEY is not set"):
        switch_to(monkeypatch, "")


def test_a_different_secret_key_stops_the_start(db_path, monkeypatch):
    crypto.init_crypto()
    switch_to(monkeypatch, "s3cret-value")
    with pytest.raises(RuntimeError, match="does not match"):
        switch_to(monkeypatch, "another-value")


def test_encrypt_works_without_an_explicit_init(db_path):
    crypto._reset_cache()
    assert crypto.decrypt(crypto.encrypt("abc")) == "abc"


def test_lost_secret_key_recovery_path_works(db_path, monkeypatch):
    # The way out documented in README and Risiken: without the old value the
    # stored API keys are lost, so they are cleared and entered again. Only
    # deleting the marker rows is not enough: a fresh Fernet key would then
    # fail on every enc: row and stop the start in orchestrator.start_all().
    crypto.init_crypto()
    make_instance("Sonarr", "first-key")
    switch_to(monkeypatch, "s3cret-value")
    with database.get_db() as conn:
        conn.execute("DELETE FROM app_settings WHERE key IN ('key_source', 'secret_key_check')")
        conn.execute("UPDATE instances SET api_key=''")
    switch_to(monkeypatch, "")
    assert [inst["api_key"] for inst in db.instances.get_all()] == [""]
