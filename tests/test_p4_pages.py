import signal

import pytest
from fastapi.testclient import TestClient

from backend import database, db
from backend.config import settings
from backend.db import history
from backend.models.instance import FIELD_BOUNDS

PASSWORD = "correct horse battery"
HOST = "missingarr.test"
SECRET = "SUPERSECRETKEY1234567890"


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


def test_no_page_contains_the_api_key(client):
    login(client)
    instance_id = make_instance(api_key=SECRET)["id"]
    for path in ("/", "/instances", f"/instances/{instance_id}/edit", f"/instances/{instance_id}/card",
                 "/instances/new", "/history", "/logs", "/searched", "/imports"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert SECRET not in resp.text, path


def test_form_gets_the_bounds_and_a_masked_instance(client):
    login(client)
    instance_id = make_instance(api_key=SECRET)["id"]
    resp = client.get(f"/instances/{instance_id}/edit")
    assert resp.context["bounds"] == FIELD_BOUNDS
    assert resp.context["instance"]["api_key"] == "********"
    assert resp.context["instance"]["api_key_set"] is True
    assert client.get("/instances/new").context["bounds"] == FIELD_BOUNDS


def test_logs_page_gets_recent_entries_and_instances(client):
    login(client)
    make_instance()
    resp = client.get("/logs")
    assert isinstance(resp.context["recent"], list)
    assert [i["name"] for i in resp.context["instances"]] == ["Sonarr"]


def test_interrupted_runs_are_closed_on_start(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_url", str(tmp_path / "missingarr.db"))
    monkeypatch.setattr(settings, "auth_password", PASSWORD)
    monkeypatch.setattr(settings, "secret_key", "")
    monkeypatch.setattr(database, "_cached_secret_key", None)
    database.init_db()
    inst = make_instance()
    run = history.start_run(inst["id"], inst["name"], "search_missing")
    from backend import crypto, main
    crypto._reset_cache()
    main.app.middleware_stack = None
    with TestClient(main.app, base_url=f"http://{HOST}"):
        pass
    row = history.query(limit=1)[0]
    assert (row["id"], row["status"], row["error_message"]) == (run, "error", "Interrupted by restart")
    crypto._reset_cache()
    main.app.middleware_stack = None


def test_missing_secret_key_stops_the_start(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_url", str(tmp_path / "missingarr.db"))
    monkeypatch.setattr(settings, "secret_key", "")
    database.init_db()
    with database.get_db() as conn:
        conn.execute("INSERT INTO app_settings (key, value) VALUES ('key_source', 'env')")
    from backend import crypto, main
    crypto._reset_cache()
    main.app.middleware_stack = None
    with pytest.raises(BaseException) as caught:
        with TestClient(main.app):
            pass
    crypto._reset_cache()
    main.app.middleware_stack = None

    def messages_of(exc):
        found = [str(exc)]
        for inner in getattr(exc, "exceptions", ()):  # anyio may wrap it in an ExceptionGroup
            found += messages_of(inner)
        if exc.__cause__ is not None:
            found += messages_of(exc.__cause__)
        return found

    assert any("SECRET_KEY is not set" in m for m in messages_of(caught.value))


def test_signal_hook_wakes_streams_and_chains(monkeypatch):
    from backend import main
    calls = []

    class FakeBroadcaster:
        def request_shutdown(self):
            calls.append("shutdown")

    def previous(signum, frame):
        calls.append(("previous", signum))

    saved = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        signal.signal(signal.SIGTERM, previous)
        main.install_shutdown_signal_hook(FakeBroadcaster())
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
    finally:
        for sig, handler in saved.items():
            signal.signal(sig, handler)
    assert calls == ["shutdown", ("previous", signal.SIGTERM)]
