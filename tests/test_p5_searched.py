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


def component_script(page, marker):
    """The inline <script> that defines `marker`, as the browser gets it."""
    at = page.index(marker)
    start = page.rindex("<script>", 0, at) + len("<script>")
    return page[start:page.index("</script>", at)]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_reset_and_heading_behave_in_the_component(client, tmp_path):
    # C13 is about behaviour: after a reset the open table must be empty and
    # the heading must name the instance. Run the real component in Node.
    make_instance(name="Alice's Sonarr")
    script = component_script(client.get("/searched").text, "function searchedPage()")
    out = run_js(tmp_path, f"""
vm.runInThisContext({json.dumps(script)});
globalThis.confirm = () => true;
document.querySelectorAll = () => [];
const listed = (rows, total) => ({{ status: 200, ok: true, redirected: false, url: '/api/searched',
  headers: {{ get: () => String(total) }}, json: async () => rows }});
const deleted = (n) => ({{ status: 200, ok: true, redirected: false, url: '/api/searched',
  json: async () => ({{ deleted: n }}) }});
const page = searchedPage();
responses.push(listed([{{ id: 1, title: 'A' }}, {{ id: 2, title: 'B' }}], 2));
await page.loadInstance(1, "Alice's Sonarr");
out.heading = page.activeInstanceName;
out.loaded = page.items.length;
responses.push(deleted(5));
await page.reset(2, 'Other');
out.afterOtherReset = page.items.length;
responses.push(deleted(2));
await page.reset(1, "Alice's Sonarr");
out.afterOwnReset = [page.items.length, page.total];
responses.push(listed([{{ id: 3, title: 'C' }}], 1));
await page.fetchItems();
responses.push(deleted(1));
await page.resetAll();
out.afterResetAll = [page.items.length, page.total];
""")
    assert out == {"heading": "Alice's Sonarr", "loaded": 2, "afterOtherReset": 2,
                   "afterOwnReset": [0, 0], "afterResetAll": [0, 0]}


def test_searched_page_uses_component_methods_and_data_attributes(client):
    make_instance(name=HOSTILE)
    page = client.get("/searched").text
    for tag, name, value in script_attributes(page):
        assert "alert(" not in value, (tag, name, value)
    assert "__x" not in page and "data-iname" not in page
    assert '@click="reset(Number($el.dataset.id), $el.dataset.name)"' in page
    assert '@click="loadInstance(Number($el.dataset.id), $el.dataset.name)"' in page
    assert '@click="resetAll()"' in page
    assert "X-Total-Count" in page
