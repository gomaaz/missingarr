import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from backend import db
from backend.agents.base import BaseAgent
from backend.agents.orchestrator import Orchestrator
from backend.agents.radarr import RadarrAgent
from backend.agents.sonarr import SonarrAgent
from backend.skills.verify_commands import VerifyCommandsSkill

API_KEY = "ARRSECRETKEY1234567890"
QUEUE_ITEM = "/api/v3/queue/11"
DISCARD = {"removeFromClient": "true", "blocklist": "false", "skipRedownload": "false", "changeCategory": "false"}


class FakeAgent(BaseAgent):
    def build_skills(self):
        return []


class Arr(BaseHTTPRequestHandler):
    """Answers every DELETE with `status` and `body` (empty by default, like *arr)."""

    hits = []
    status = 200
    body = b""

    def do_DELETE(self):
        Arr.hits.append((self.command, self.path, self.headers.get("X-Api-Key")))
        self.send_response(Arr.status)
        self.send_header("Content-Length", str(len(Arr.body)))
        self.end_headers()
        if Arr.body:
            self.wfile.write(Arr.body)

    def log_message(self, *args):
        pass


class Redirect(BaseHTTPRequestHandler):
    target = ""

    def do_DELETE(self):
        self.send_response(302)
        self.send_header("Location", Redirect.target + self.path)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def servers():
    started = []

    def serve(handler):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        started.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    Arr.hits, Arr.status, Arr.body = [], 200, b""
    target = serve(Arr)
    Redirect.target = target.replace("127.0.0.1", "localhost")
    redirect = serve(Redirect)
    yield target, redirect
    for server in started:
        server.shutdown()


def agent_for(url):
    return FakeAgent({"id": 1, "name": "x", "type": "sonarr", "url": url, "api_key": API_KEY})


def config(arr_type="sonarr", instance_id=1):
    return {"id": instance_id, "name": arr_type.title(), "type": arr_type, "url": "http://127.0.0.1:9",
            "api_key": API_KEY, "enabled": 1}


def test_delete_sends_the_key_and_the_query(servers):
    target, _ = servers
    assert agent_for(target).http_delete(QUEUE_ITEM, params=DISCARD) is None
    assert Arr.hits == [("DELETE", "/api/v3/queue/11?removeFromClient=true&blocklist=false"
                                   "&skipRedownload=false&changeCategory=false", API_KEY)]


def test_delete_without_params_sends_no_query(servers):
    target, _ = servers
    agent_for(target).http_delete(QUEUE_ITEM)
    assert Arr.hits == [("DELETE", QUEUE_ITEM, API_KEY)]


@pytest.mark.parametrize("status", [200, 204])
def test_an_empty_answer_is_fine(servers, status):
    target, _ = servers
    Arr.status = status
    assert agent_for(target).http_delete(QUEUE_ITEM) is None


def test_the_answer_body_is_never_read(servers):
    target, _ = servers
    Arr.body = b"not json at all"
    assert agent_for(target).http_delete(QUEUE_ITEM) is None


def test_a_redirect_is_not_followed_and_the_key_stays_home(servers):
    _, redirect = servers
    with pytest.raises(requests.exceptions.HTTPError) as caught:
        agent_for(redirect).http_delete(QUEUE_ITEM, params=DISCARD)
    assert "redirect not followed" in str(caught.value)
    assert caught.value.response is not None
    assert caught.value.response.status_code == 302
    assert Arr.hits == []


@pytest.mark.parametrize("status", [400, 404, 500])
def test_error_answers_raise_with_their_status(servers, status):
    target, _ = servers
    Arr.status = status
    with pytest.raises(requests.exceptions.HTTPError) as caught:
        agent_for(target).http_delete(QUEUE_ITEM)
    assert caught.value.response.status_code == status


def closed_port():
    """A local port nothing listens on: the connection is refused at once."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_no_connection_raises_like_the_other_methods():
    with pytest.raises(requests.exceptions.ConnectionError):
        agent_for(f"http://127.0.0.1:{closed_port()}").http_delete(QUEUE_ITEM)


def test_delete_passes_timeout_and_never_follows_redirects(monkeypatch):
    calls = []

    def fake_delete(url, **kwargs):
        calls.append((url, kwargs))
        response = requests.Response()
        response.status_code = 200
        return response

    monkeypatch.setattr(requests, "delete", fake_delete)
    agent = agent_for("http://127.0.0.1:9/")
    assert agent.http_delete(QUEUE_ITEM) is None
    assert agent.http_delete(QUEUE_ITEM, params={"blocklist": "true"}, timeout=30) is None
    assert calls == [
        ("http://127.0.0.1:9/api/v3/queue/11",
         {"headers": {"X-Api-Key": API_KEY}, "params": {}, "timeout": 10, "allow_redirects": False}),
        ("http://127.0.0.1:9/api/v3/queue/11",
         {"headers": {"X-Api-Key": API_KEY}, "params": {"blocklist": "true"}, "timeout": 30,
          "allow_redirects": False}),
    ]


def test_detached_agent_is_never_started():
    broadcaster = object()
    orch = Orchestrator(broadcaster=broadcaster)
    sonarr = orch.detached_agent(config("sonarr", 1))
    radarr = orch.detached_agent(config("radarr", 2))
    assert type(sonarr) is SonarrAgent
    assert type(radarr) is RadarrAgent
    for agent in (sonarr, radarr):
        assert agent.broadcaster is broadcaster
        assert agent._thread is None
        assert agent._scheduler is None
        assert agent._skills == []
        assert agent.state["status"] == "starting"
    assert sonarr.config == config("sonarr", 1)


def test_detached_agent_defaults_to_sonarr_like_the_other_agents():
    cfg = config()
    del cfg["type"]
    assert type(Orchestrator().detached_agent(cfg)) is SonarrAgent


def test_detached_agent_has_a_fresh_runtime_and_is_not_registered():
    orch = Orchestrator()
    first = orch.detached_agent(config("sonarr", 1))
    second = orch.detached_agent(config("sonarr", 1))
    assert first.runtime is not second.runtime
    assert first.runtime is not orch._runtime(1)
    assert orch._agents == {}
    assert orch._adhoc == {}
    assert orch.is_running(1) is False


def test_detached_agent_rejects_an_unknown_type():
    with pytest.raises(ValueError, match="Unknown instance type"):
        Orchestrator().detached_agent(config("lidarr"))


def test_housekeeping_builds_its_agents_through_detached_agent(monkeypatch):
    configs = [config("sonarr", 1), config("lidarr", 2), config("radarr", 3)]
    monkeypatch.setattr(db.instances, "get_all", lambda include_disabled=True: configs)
    housekept = []
    monkeypatch.setattr(VerifyCommandsSkill, "housekeeping", lambda self, agent: housekept.append(agent))
    orch = Orchestrator()
    real = orch.detached_agent
    asked = []

    def spy(cfg):
        asked.append(cfg["id"])
        return real(cfg)

    monkeypatch.setattr(orch, "detached_agent", spy)
    orch.housekeeping()
    assert asked == [1, 2, 3]
    # the unknown type is skipped, the others are housekept with never-started agents
    assert [(type(a), a.config["id"]) for a in housekept] == [(SonarrAgent, 1), (RadarrAgent, 3)]
    assert all(a._thread is None and a._scheduler is None for a in housekept)
