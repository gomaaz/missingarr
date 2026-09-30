import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend import database, db
from backend.api import activity
from backend.config import settings
from backend.log_broadcaster import LogBroadcaster

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


@pytest.mark.parametrize("query", ["limit=-1", "limit=501", "offset=-1", "level=verbose"])
def test_activity_parameters_are_validated(client, query):
    login(client)
    assert client.get(f"/api/activity?{query}").status_code == 422


class FakeRequest:
    def __init__(self, broadcaster):
        self.app = SimpleNamespace(state=SimpleNamespace(broadcaster=broadcaster))

    async def is_disconnected(self):
        return False


def test_stream_delivers_entries_and_ends_on_shutdown():
    async def scenario():
        broadcaster = LogBroadcaster()
        broadcaster.set_loop(asyncio.get_running_loop())
        response = await activity.stream_activity(FakeRequest(broadcaster), debug=False)
        stream = response.body_iterator
        broadcaster.broadcast({"level": "info", "message": "hello"})
        first = await asyncio.wait_for(stream.__anext__(), 1)
        assert '"hello"' in first
        waiting = asyncio.ensure_future(stream.__anext__())
        await asyncio.sleep(0.05)
        broadcaster.request_shutdown()
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(waiting, 1)
        assert broadcaster._queues == []

    asyncio.run(scenario())


def test_request_shutdown_before_start_is_harmless():
    LogBroadcaster().request_shutdown()
