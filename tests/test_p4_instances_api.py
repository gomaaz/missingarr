import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from backend import database, db
from backend.config import settings

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


class FakeOrchestrator:
    def __init__(self):
        self.calls = []
        self.trigger_result = "started"
        self.forget_result = True

    def get_agent_state(self, instance_id):
        return {}

    def start_agent(self, instance_id):
        self.calls.append(("start", instance_id))

    def stop_agent(self, instance_id, abort_running=True, wait_seconds=0.0):
        self.calls.append(("stop", instance_id, abort_running))

    def reload_agent(self, instance_id):
        self.calls.append(("reload", instance_id))

    def refresh_config(self, instance_id):
        self.calls.append(("refresh", instance_id))

    def forget_instance(self, instance_id, wait_seconds=15.0):
        self.calls.append(("forget", instance_id, db.instances.get_by_id(instance_id) is not None))
        return self.forget_result

    def trigger(self, instance_id, skill_name, force=True):
        self.calls.append(("trigger", instance_id, skill_name, force))
        return self.trigger_result

    def stop_all(self):
        pass


@pytest.fixture
def api(client):
    client.app.state.orchestrator.stop_all()
    fake = FakeOrchestrator()
    client.app.state.orchestrator = fake
    login(client)
    return client, fake


def body(**fields):
    data = {"name": "Sonarr", "type": "sonarr", "url": "http://sonarr:8989", "api_key": SECRET,
            "enabled": False}
    data.update(fields)
    return data


def test_api_key_never_leaves_the_server(api):
    client, _ = api
    created = client.post("/api/instances", json=body())
    assert created.status_code == 201
    instance_id = created.json()["id"]
    for resp in (created, client.get("/api/instances"), client.get(f"/api/instances/{instance_id}"),
                 client.put(f"/api/instances/{instance_id}", json=body(api_key=""))):
        assert SECRET not in resp.text
    one = client.get(f"/api/instances/{instance_id}").json()
    assert (one["api_key"], one["api_key_set"]) == ("********", True)


@pytest.mark.parametrize("key", ["", "********"])
def test_empty_or_masked_key_keeps_the_stored_one(api, key):
    client, fake = api
    instance_id = client.post("/api/instances", json=body()).json()["id"]
    assert client.put(f"/api/instances/{instance_id}", json=body(api_key=key)).status_code == 200
    assert db.instances.get_by_id(instance_id)["api_key"] == SECRET
    assert ("reload", instance_id) in fake.calls


def test_changed_url_needs_the_key_again(api):
    client, _ = api
    instance_id = client.post("/api/instances", json=body()).json()["id"]
    resp = client.put(f"/api/instances/{instance_id}", json=body(url="http://elsewhere:8989", api_key=""))
    assert resp.status_code == 400
    assert db.instances.get_by_id(instance_id)["url"] == "http://sonarr:8989"
    resp = client.put(f"/api/instances/{instance_id}",
                      json=body(url="http://elsewhere:8989", api_key="NEWKEY"))
    assert resp.status_code == 200
    assert db.instances.get_by_id(instance_id)["api_key"] == "NEWKEY"


def test_out_of_range_settings_are_rejected(api):
    client, _ = api
    assert client.post("/api/instances", json=body(interval_minutes=0)).status_code == 422


@pytest.mark.parametrize("result,status", [("busy", 409), ("not_found", 404), ("unknown_skill", 400)])
def test_trigger_reports_why_nothing_started(api, result, status):
    client, fake = api
    instance_id = make_instance()["id"]
    fake.trigger_result = result
    resp = client.post(f"/api/instances/{instance_id}/trigger?skill=search_missing&force=true")
    assert resp.status_code == status
    if status == 409:
        assert "already running" in resp.json()["detail"]


def test_unknown_skill_name_is_rejected(api):
    client, _ = api
    instance_id = make_instance()["id"]
    assert client.post(f"/api/instances/{instance_id}/trigger?skill=bogus").status_code == 400


def test_documented_curl_flow_still_works(client):
    client.app.state.orchestrator.stop_all()
    fake = FakeOrchestrator()
    client.app.state.orchestrator = fake
    instance_id = make_instance()["id"]
    assert login(client).status_code == 302
    resp = client.post(f"/api/instances/{instance_id}/trigger?skill=search_missing&force=false")
    assert resp.status_code == 200
    assert resp.json() == {"status": "triggered", "skill": "search_missing"}
    assert fake.calls[-1] == ("trigger", instance_id, "search_missing", False)


def test_skill_switch_refreshes_the_agent_and_checks_per_run(api):
    client, fake = api
    instance_id = make_instance(search_upgrades_enabled=False, upgrades_per_run=0)["id"]
    resp = client.post(f"/api/instances/{instance_id}/toggle-skill?skill=upgrades&enabled=true")
    assert resp.status_code == 409
    with database.get_db() as conn:
        conn.execute("UPDATE instances SET upgrades_per_run=1 WHERE id=?", (instance_id,))
    resp = client.post(f"/api/instances/{instance_id}/toggle-skill?skill=upgrades&enabled=true")
    assert resp.status_code == 200
    assert db.instances.get_by_id(instance_id)["search_upgrades_enabled"] == 1
    assert ("refresh", instance_id) in fake.calls
    assert not any(call[0] == "reload" for call in fake.calls)


def test_disabling_aborts_and_deleting_waits_for_the_agent(api):
    client, fake = api
    instance_id = make_instance(enabled=True)["id"]
    client.post(f"/api/instances/{instance_id}/toggle?enabled=false")
    assert ("stop", instance_id, True) in fake.calls
    assert client.delete(f"/api/instances/{instance_id}").status_code == 204
    assert ("forget", instance_id, True) in fake.calls
    assert db.instances.get_by_id(instance_id) is None


def test_delete_keeps_the_instance_while_a_search_does_not_stop(api):
    client, fake = api
    instance_id = make_instance(enabled=True)["id"]
    fake.forget_result = False
    resp = client.delete(f"/api/instances/{instance_id}")
    assert resp.status_code == 409
    assert "still" in resp.json()["detail"]
    assert db.instances.get_by_id(instance_id) is not None
    # The agent was stopped for the delete; an enabled instance gets it back.
    assert ("reload", instance_id) in fake.calls


class Handler(BaseHTTPRequestHandler):
    status = 401
    hits = []

    def do_GET(self):
        Handler.hits.append(self.path)
        self.send_response(Handler.status)
        if Handler.status == 302:
            self.send_header("Location", "http://localhost:1/elsewhere")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def arr_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    Handler.hits = []
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.mark.parametrize("status,expected,detail", [
    (401, 401, "Invalid API key"), (403, 401, "Invalid API key"),
    (500, 502, "HTTP 500 from instance"), (302, 502, "redirect"),
])
def test_connection_test_keeps_the_real_status(api, arr_server, status, expected, detail):
    client, _ = api
    Handler.status = status
    instance_id = make_instance(url=arr_server)["id"]
    resp = client.get(f"/api/instances/{instance_id}/test")
    assert resp.status_code == expected
    assert detail in resp.json()["detail"]
    assert Handler.hits == ["/api/v3/system/status"]
