import html.parser
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import database, db
from backend.config import settings

PASSWORD = "correct horse battery"
HOST = "missingarr.test"
HOSTILE = "x'); alert(1);//\"><img src=x onerror=alert(2)>"


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
        test_client.post("/login", data={"username": "admin", "password": PASSWORD, "next": "/"})
        yield test_client
    crypto._reset_cache()
    main.app.middleware_stack = None


def make_instance(**fields):
    data = {"name": "Sonarr", "type": "sonarr", "url": "http://127.0.0.1:9",
            "api_key": "SUPERSECRETKEY1234567890", "enabled": False}
    data.update(fields)
    return db.instances.create(data)


class AttributeCollector(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.attributes = []   # (tag, name, value)
        self.tags = []         # (tag, attrs dict)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        for name, value in attrs:
            self.attributes.append((tag, name, value or ""))


def parse(text):
    collector = AttributeCollector()
    collector.feed(text)
    return collector


def script_attributes(text):
    """Attributes whose value is executed as JavaScript by the browser or by Alpine."""
    return [(tag, name, value) for tag, name, value in parse(text).attributes
            if name.startswith(("on", "@", "x-", ":"))]


NODE = shutil.which("node")
ROOT = Path(__file__).resolve().parent.parent

PRELUDE = r"""
const fs = require('fs');
const vm = require('vm');
const timers = [];
globalThis.setTimeout = (fn, ms) => { const t = { fn, ms, id: timers.length + 1, cleared: false }; timers.push(t); return t.id; };
globalThis.clearTimeout = (id) => { const t = timers.find(x => x.id === id); if (t) t.cleared = true; };
globalThis.setInterval = () => 0;
globalThis.clearInterval = () => {};
const activeTimers = () => timers.filter(t => !t.cleared).length;
const sources = [];
function FakeEventSource(url) { this.url = url; this.readyState = 0; sources.push(this); }
FakeEventSource.prototype.close = function () { this.readyState = 2; };
FakeEventSource.CONNECTING = 0; FakeEventSource.OPEN = 1; FakeEventSource.CLOSED = 2;
globalThis.EventSource = FakeEventSource;
globalThis.window = globalThis;
globalThis.location = { pathname: '/logs', search: '?x=1', href: 'http://t/logs?x=1' };
const listeners = {};
globalThis.document = { addEventListener: (name, fn) => { listeners[name] = fn; }, getElementById: () => null };
const stores = {};
const toasts = [];
globalThis.Alpine = {
  store: (name, value) => { if (value !== undefined) stores[name] = value; return stores[name]; },
  $data: () => ({}),
};
const responses = [];
globalThis.fetch = async (url, options) => responses.shift() || { status: 200, ok: true, redirected: false, url, json: async () => ({}) };
vm.runInThisContext(fs.readFileSync('static/js/app.js', 'utf8'));
listeners['alpine:init']();
stores.toasts.add = (message, type) => toasts.push([message, type]);
const out = {};
"""


def run_js(tmp_path, body):
    script = tmp_path / "case.js"
    script.write_text(PRELUDE + "(async () => {\n" + body +
                      "\nconsole.log(JSON.stringify(out));\n})().catch(e => { console.error(e); process.exit(1); });\n")
    result = subprocess.run([NODE, str(script)], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def card_html(client, **fields):
    inst = make_instance(**fields)
    return client.get(f"/instances/{inst['id']}/card").text


def test_force_button_is_locked_while_running(client):
    page = card_html(client, enabled=True)
    force = [attrs for tag, attrs in parse(page).tags if tag == "button" and "btn-force" in attrs.get("class", "")]
    assert force[0][":disabled"] == "false || status === 'running'"


def test_countdown_starts_once(client):
    # Alpine calls init()/destroy() of an x-data component by itself; an extra
    # x-init="init()" started a second interval that destroy() never cleared.
    page = card_html(client, enabled=True)
    roots = [attrs for tag, attrs in parse(page).tags if attrs.get("id", "").startswith("icard-")]
    assert roots[0]["x-data"].startswith("countdownComponent(")
    assert "x-init" not in roots[0] and "x-destroy" not in roots[0]


def test_status_poll_only_reads_successful_answers(client):
    # A 401 carries HX-Redirect (P4.3); its JSON body must not be read as a
    # state and turn the card into WAIT/unknown (C-L6).
    page = card_html(client, enabled=True)
    polls = [attrs for tag, attrs in parse(page).tags if "hx-get" in attrs]
    assert polls[0]["hx-on::after-request"] == (
        "if (event.detail.successful) updateCardState("
        + polls[0]["hx-get"].split("/")[3] + ", event.detail.xhr.responseText)"
    )


def test_label_says_why_there_is_no_countdown(client):
    # No agent runs for rows created after startup, so next_run is empty here.
    idle = card_html(client, enabled=True, search_missing_enabled=False, search_upgrades_enabled=False)
    assert "No search skill enabled" in idle
    starting = card_html(client, name="Radarr", enabled=True)
    assert "Starting up..." in starting
    assert "Next run · " not in idle + starting


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_error_status_shows_an_error_badge(tmp_path):
    out = run_js(tmp_path, """
const badge = { className: '', textContent: '' };
const card = { querySelector: (sel) => sel === '[data-status-badge]' ? badge : null, querySelectorAll: () => [] };
document.getElementById = () => card;
updateCardState(1, JSON.stringify({ agent_state: { status: 'error' } }));
out.badge = [badge.className, badge.textContent];
""")
    assert out["badge"] == ["badge badge-offline", "ERROR"]
