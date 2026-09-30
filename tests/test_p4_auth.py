import logging
import re
import time
from pathlib import Path

import bcrypt
import pytest
from fastapi.testclient import TestClient

from backend import auth, database, db
from backend.config import settings

PASSWORD = "correct horse battery"
HOST = "missingarr.test"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_url", str(tmp_path / "missingarr.db"))
    monkeypatch.setattr(settings, "auth_password", PASSWORD)
    monkeypatch.setattr(settings, "secret_key", "")
    monkeypatch.setattr(settings, "cookie_secure", False)
    monkeypatch.setattr(database, "_cached_secret_key", None)
    from backend import crypto, main
    crypto._reset_cache()
    main.app.middleware_stack = None
    with TestClient(main.app, base_url=f"http://{HOST}") as test_client:
        yield test_client
    crypto._reset_cache()
    main.app.middleware_stack = None


def login(client, remember=False, next_path="/"):
    data = {"username": "admin", "password": PASSWORD, "next": next_path}
    if remember:
        data["remember"] = "true"
    return client.post("/login", data=data, follow_redirects=False)


def make_instance(**fields):
    data = {"name": "Sonarr", "type": "sonarr", "url": "http://127.0.0.1:9",
            "api_key": "SUPERSECRETKEY1234567890", "enabled": False}
    data.update(fields)
    return db.instances.create(data)


@pytest.fixture(autouse=True)
def clean_auth_state():
    auth.login_throttle.reset()
    auth._reset_token_version_cache()
    yield
    auth.login_throttle.reset()
    auth._reset_token_version_cache()


def test_remember_cookie_has_the_v2_format(client):
    resp = login(client, remember=True)
    assert re.fullmatch(r"v2\.\d+\.0\.[0-9a-f]{64}", resp.cookies["ma_remember"])


def test_remember_cookie_alone_restores_the_session(client):
    login(client, remember=True)
    client.cookies.delete("ma_session")
    assert client.get("/api/instances").status_code == 200


def tampered():
    token = auth.create_remember_token("admin")
    return token[:-1] + ("1" if token[-1] == "0" else "0")


@pytest.mark.parametrize("make_token", [
    tampered,
    lambda: "admin:" + "0" * 64,
    lambda: auth.create_remember_token("admin", now=int(time.time()) - 31 * 24 * 3600),
    lambda: auth.create_remember_token("admin", now=int(time.time()) + 3600),
])
def test_tampered_old_or_expired_tokens_are_rejected(client, make_token):
    client.get("/api/health")  # initialise the session key
    client.cookies.set("ma_remember", make_token(), domain=HOST)
    assert client.get("/api/instances").status_code == 401


def test_logout_revokes_every_session_and_token(client):
    login(client, remember=True)
    stolen = dict(client.cookies)
    resp = client.post("/logout", follow_redirects=False)
    assert (resp.status_code, resp.headers["location"]) == (303, "/login")
    client.cookies.clear()
    for name, value in stolen.items():
        client.cookies.set(name, value, domain=HOST)
    assert client.get("/api/instances").status_code == 401


def test_password_change_ends_sessions_and_tokens(client, monkeypatch):
    login(client, remember=True)
    monkeypatch.setattr(auth, "_active_password", "a new password")
    assert client.get("/api/instances").status_code == 401


def test_logout_needs_post(client):
    login(client)
    assert client.get("/logout", follow_redirects=False).status_code == 405


def test_bcrypt_hash_as_password(client, monkeypatch):
    hashed = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()
    monkeypatch.setattr(settings, "auth_password", hashed)
    auth.init_auth()
    assert login(client).status_code == 302
    assert client.post("/login", data={"username": "admin", "password": "wrong"},
                       follow_redirects=False).status_code == 401
    assert "passlib" not in Path(auth.__file__).read_text()


def test_broken_hash_is_logged(monkeypatch, caplog):
    monkeypatch.setattr(auth, "_active_password", "$2b$04$not-a-real-hash")
    with caplog.at_level(logging.ERROR, logger="missingarr.auth"):
        assert auth.verify_password("x") is False
    assert "bcrypt check failed" in caplog.text


def test_long_passwords_are_cut_like_older_bcrypt(monkeypatch, caplog):
    long_password = "p" * 100
    # A hash made the way htpasswd, passlib and bcrypt < 5 did: only 72 bytes count.
    hashed = bcrypt.hashpw(long_password.encode()[:72], bcrypt.gensalt(rounds=4)).decode()
    monkeypatch.setattr(auth, "_active_password", hashed)
    with caplog.at_level(logging.ERROR, logger="missingarr.auth"):
        assert auth.verify_password(long_password) is True
        assert auth.verify_password("q" * 100) is False
    assert "bcrypt check failed" not in caplog.text


def test_failed_logins_lock_the_address_and_are_logged(client, monkeypatch, caplog):
    now = [1000.0]
    monkeypatch.setattr(auth.login_throttle, "_clock", lambda: now[0])
    wrong = {"username": "admin", "password": "wrong", "next": "/"}
    with caplog.at_level(logging.WARNING, logger="missingarr"):
        for _ in range(5):
            assert client.post("/login", data=wrong, follow_redirects=False).status_code == 401
        locked = login(client)
    assert locked.status_code == 429
    assert locked.headers["Retry-After"] == "30"
    assert "Failed sign-in" in caplog.text and "testclient" in caplog.text
    now[0] += 31
    assert login(client).status_code == 302


def test_lock_doubles_up_to_fifteen_minutes():
    now = [0.0]
    throttle = auth.LoginThrottle(clock=lambda: now[0])
    waits = []
    for _ in range(12):
        throttle.record_failure("1.2.3.4")
        waits.append(throttle.retry_after("1.2.3.4"))
    assert waits[:4] == [0, 0, 0, 0]
    assert waits[4:8] == [30, 60, 120, 240]
    assert waits[-1] == 900
    throttle.record_success("1.2.3.4")
    assert throttle.retry_after("1.2.3.4") == 0


@pytest.mark.parametrize("next_value,expected", [
    ("//evil.example/x", "/"), ("https://evil.example", "/"), ("/\\evil.example", "/"),
    ("javascript:alert(1)", "/"), ("", "/"), ("/history?x=1", "/history?x=1"),
])
def test_next_only_allows_local_paths(client, next_value, expected):
    resp = login(client, next_path=next_value)
    assert resp.headers["location"] == expected
    assert client.get(f"/login?next={next_value}", follow_redirects=False).headers["location"] == expected


def test_cookie_secure_flag(client, monkeypatch):
    monkeypatch.setattr(settings, "cookie_secure", True)
    resp = login(client, remember=True)
    cookies = resp.headers.get_list("set-cookie")
    assert all("secure" in c.lower() for c in cookies)
    assert {c.split("=", 1)[0] for c in cookies} == {"ma_session", "ma_remember"}
