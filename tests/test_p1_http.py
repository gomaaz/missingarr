import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from backend.agents.base import BaseAgent


class FakeAgent(BaseAgent):
    def build_skills(self):
        return []


class Target(BaseHTTPRequestHandler):
    hits = []

    def _answer(self):
        Target.hits.append((self.path, self.headers.get("X-Api-Key")))
        body = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _answer
    do_POST = _answer

    def log_message(self, *args):
        pass


class Redirect(BaseHTTPRequestHandler):
    target = ""

    def _answer(self):
        self.send_response(302)
        self.send_header("Location", Redirect.target + self.path)
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_GET = _answer
    do_POST = _answer

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

    Target.hits = []
    target = serve(Target)
    Redirect.target = target.replace("127.0.0.1", "localhost")
    redirect = serve(Redirect)
    yield target, redirect
    for server in started:
        server.shutdown()


def agent_for(url):
    return FakeAgent({"id": 1, "name": "x", "type": "sonarr", "url": url, "api_key": "SECRET"})


def test_direct_requests_still_work(servers):
    target, _ = servers
    assert agent_for(target).http_get("/api/v3/system/status") == {"ok": True}
    assert Target.hits == [("/api/v3/system/status", "SECRET")]


def test_redirects_are_not_followed_and_the_key_stays_home(servers):
    _, redirect = servers
    agent = agent_for(redirect)
    with pytest.raises(requests.exceptions.HTTPError) as caught:
        agent.http_get("/api/v3/wanted/missing")
    assert caught.value.response is not None
    assert caught.value.response.status_code == 302
    with pytest.raises(requests.exceptions.HTTPError):
        agent.http_post("/api/v3/command", {"name": "EpisodeSearch"})
    assert agent.http_get_raw("/api/v3/command/1") == (302, None)
    assert Target.hits == []
