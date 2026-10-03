import html.parser
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from markupsafe import escape

from backend import database, db
from backend.config import settings
from backend.tooltips import TOOLTIPS

PASSWORD = "correct horse battery"
HOST = "missingarr.test"
HOSTILE = "x'); alert(1);//\"><img src=x onerror=alert(2)>"
NODE = shutil.which("node")
ROOT = Path(__file__).resolve().parent.parent
TITLE = "Some.Show.S01E01.German.1080p.WEB.h264-GRP"
SONARR_NOTE = "Rules S1–S4, not measured; the title is only compared when Sonarr maps the name to a series."
COUNT_START = "startImportsCount()"
UNKNOWN_SUMMARY = "Imports: unknown — an app could not be read or has just restarted"
STARTING = "The app started less than 2 minutes ago — its queue may not be filled yet."
UNCLEAR = "No clear answer — the import may still run in the app; check the queue"
MAY_RUN = "The app may still run the import — check the queue"
LOST = "missingarr no longer follows this import — check the queue"
STOPPED = "Tracking stopped — the instance was switched off or changed. Check the app's Activity page."
STILL_RUNNING = "Still running in the app — check the queue later"
INSTANCE_OFF = "This instance is switched off — switch it on to import or discard"
INSTANCE_CHANGED = "The instance was changed — reload the page"
COVERAGE = "The proposal covers fewer episodes than the download (1 missing); the app keeps the download afterwards."
UNCONFIRMED_RADARR = "The app ran the import; the queue has not confirmed it yet — check it in the app"
UNCONFIRMED_SONARR = ("The app ran the import; the queue has not confirmed it yet — it may have imported only some "
                      "episodes; check it in the app")
DISCARD_ON = ("The download and its files are removed from the download client. If the app grabbed this release "
              "itself, it marks it as failed, puts it on the blocklist and may search again (Radarr only for a "
              "monitored, available movie; Sonarr as its Redownload Failed settings allow). "
              "A download added by hand is only removed.")


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


class Collector(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, {name: value or "" for name, value in attrs}))


def tags(text):
    collector = Collector()
    collector.feed(text)
    return collector.tags


def script_attributes(text):
    """Attributes whose value the browser or Alpine runs as JavaScript."""
    return [(tag, name, value) for tag, attrs in tags(text) for name, value in attrs.items()
            if name.startswith(("on", "@", "x-", ":"))]


def component_script(page, marker):
    at = page.index(marker)
    start = page.rindex("<script>", 0, at) + len("<script>")
    return page[start:page.index("</script>", at)]


def nav_links(text):
    return [a for t, a in tags(text) if t == "a" and "nav-link" in a.get("class", "").split()]


# ── Page, menu, dashboard, help ──────────────────────────────────────────────

def test_imports_page_renders_the_component(client):
    resp = client.get("/imports")
    assert resp.status_code == 200
    page = resp.text
    assert "<title>Imports — " in page
    assert 'href="/imports" class="nav-link active"' in page
    roots = [a for t, a in tags(page) if a.get("x-data") == "importsPage()"]
    assert roots and roots[0]["x-init"] == "load()"
    script = component_script(page, "function importsPage()")
    for value in ("fits: ['badge-online', 'fits']", "foreign: ['badge-error', 'foreign?']",
                  "unknown: ['badge-unknown', 'cannot judge']", "var IMPORTS_POLL_MS = 3000;",
                  "var IMPORTS_POLL_BUDGET_MS = 120000;", "var IMPORTS_PARALLEL_PROPOSALS = 2;"):
        assert value in script
    for text in ("Nothing open.", "Switched off — not read.", STARTING, "Blocklist and search again", SONARR_NOTE):
        assert text in page
    notes = [a for t, a in tags(page) if a.get("x-text") == "coverageNote(inst, d)"]
    assert notes and notes[0]["x-show"] == "coverageNote(inst, d)"
    links = [a for t, a in tags(page) if t == "a" and a.get(":href") == "queueHref(inst)"]
    assert links and links[0]["hx-boost"] == "false" and links[0]["target"] == "_blank"
    tips = [a["data-tooltip"] for t, a in tags(page) if "data-tooltip" in a]
    assert TOOLTIPS["imports_verdict"] in tips
    assert TOOLTIPS["imports_discard_blocklist"] in tips


def test_import_button_is_locked_and_names_the_reason(client):
    page = client.get("/imports").text
    buttons = [a for t, a in tags(page) if t == "button" and a.get("@click") == "importDownload(inst, d)"]
    assert buttons and buttons[0][":disabled"] == "!canImport(inst, d)"
    assert buttons[0][":title"] == "importTitle(inst, d)"
    discards = [a for t, a in tags(page) if t == "button" and a.get("@click") == "discard(inst, d)"]
    assert discards and discards[0][":class"] == "discardClass(inst, d)"
    assert discards[0][":disabled"] == "!!busy[key(inst, d)]"


def test_page_holds_no_instance_data(client):
    # Everything about an instance comes from /api/imports as JSON and is
    # shown with x-text; nothing of it is written into the page or a script.
    inst = make_instance(name=HOSTILE)
    page = client.get("/imports").text
    assert HOSTILE not in page and str(escape(HOSTILE)) not in page
    for tag, name, value in script_attributes(page):
        assert "alert(" not in value, (tag, name, value)
    clicks = {value for tag, name, value in script_attributes(page) if name == "@click"}
    assert clicks == {"load()", "importDownload(inst, d)", "discard(inst, d)", "$store.toasts.remove(toast.id)"}
    assert f"/api/imports/{inst['id']}/" not in page


def test_menu_has_the_imports_link_and_counter_on_every_page(client):
    for path in ("/", "/history", "/checked-search", "/help", "/imports"):
        page = client.get(path).text
        links = nav_links(page)
        hrefs = [a["href"] for a in links]
        assert hrefs.index("/imports") == hrefs.index("/checked-search") + 1, path
        imports = [a for a in links if a["href"] == "/imports"][0]
        assert ("active" in imports["class"].split()) == (path == "/imports"), path
        counters = [a for t, a in tags(page) if "data-imports-count" in a]
        assert len(counters) == 1, path
        counter = counters[0]
        assert counter["class"] == "badge badge-error"
        assert "hidden" in counter
        # One loader in app.js polls and refreshes the count; htmx does not poll it.
        assert counter["x-data"] == "" and counter["x-init"] == COUNT_START
        assert not [name for name in counter if name.startswith("hx-")]


def test_dashboard_has_the_imports_line(client):
    page = client.get("/").text
    lines = [a for t, a in tags(page) if t == "a" and "data-imports-dashboard" in a]
    assert len(lines) == 1
    assert lines[0]["href"] == "/imports" and "hidden" in lines[0]


def test_hidden_counters_stay_hidden(client):
    # .badge sets display:inline-flex, which would beat the browser's own
    # [hidden] rule and show an empty badge.
    css = client.get("/static/css/app.css").text
    assert "[hidden] { display: none !important; }" in css


def test_help_explains_the_imports_page(client):
    page = client.get("/help").text
    assert "<strong>Imports</strong>" in page
    assert page.index("Checked search (pre-filter)") < page.index("<strong>Imports</strong>") < page.index("Field Reference")
    for text in ("Not an upgrade", "ignores every objection", "reads the queue and the proposal again",
                 "Blocklist and search again", "grabbed this release itself", "added by hand", "no new search",
                 "Discard is refused and deletes nothing", "Only one Import or Discard of a download",
                 "history records the import", "may still run it", "the queue has not confirmed it yet",
                 "until the app shows that import", "edited, switched off or deleted meanwhile",
                 "the page stops following it",
                 "reachable on that host"):
        assert text in page, text
    assert "Imports Verdict" in page and "Imports Discard Blocklist" in page
    assert str(escape(TOOLTIPS["imports_verdict"])) in page
    assert str(escape(TOOLTIPS["imports_discard_blocklist"])) in page


def test_tooltips_explain_the_verdict_and_the_blocklist():
    verdict = TOOLTIPS["imports_verdict"]
    for text in ("fits", "foreign?", "cannot judge", "S1–S4", "not measured", "never locks Import"):
        assert text in verdict, text
    blocklist = TOOLTIPS["imports_discard_blocklist"]
    for text in ("grabbed this release itself", "blocklist", "may search again", "monitored, available movie",
                 "Redownload Failed", "added by hand is only removed", "no new search", "download client"):
        assert text in blocklist, text


# ── The page component in node ───────────────────────────────────────────────

PAGE_PRELUDE = r"""
const vm = require('vm');
const calls = []; const toasts = []; const confirms = []; const slept = []; const pending = [];
let counted = 0; let confirmAnswer = true; let inflight = 0; let maxInflight = 0;
let proposalsOpen = 0; let maxProposals = 0;
let now = Date.parse('2026-10-02T12:00:00Z');
Date.now = () => now;
globalThis.location = { hostname: 'missingarr.test' };
globalThis.toast = (m, t) => toasts.push([m, t]);
globalThis.isSessionExpired = (err) => !!err && err.name === 'SessionExpiredError';
globalThis.confirm = (text) => { confirms.push(text); return confirmAnswer; };
globalThis.errorDetail = async (resp, fallback) => {
  const data = await resp.json().catch(() => ({}));
  return typeof data.detail === 'string' ? data.detail : fallback;
};
globalThis.refreshImportsCount = async () => { counted += 1; };
const answer = (status, data) => ({ ok: status >= 200 && status < 300, status,
  json: async () => JSON.parse(JSON.stringify(data)) });
const broken = (status) => ({ ok: false, status, json: async () => { throw new SyntaxError('not json'); } });
const expired = () => { const err = new Error('Session expired'); err.name = 'SessionExpiredError'; throw err; };
const tick = () => new Promise(resolve => setImmediate(resolve));
// The page's own timers (the abort timer of a command poll) run on the test clock.
const timers = [];
globalThis.setTimeout = (fn, ms) => { const t = { fn, at: now + ms, done: false }; timers.push(t); return t; };
globalThis.clearTimeout = (t) => { if (t) t.done = true; };
const openTimers = () => timers.filter(t => !t.done).length;
// Moves the test clock on and fires the timers that fall due on the way; stops at
// the timer after which stop() holds (the page aborted the request).
const advance = (ms, stop = () => false) => {
  const end = now + ms;
  for (;;) {
    const due = timers.filter(t => !t.done && t.at <= end).sort((a, b) => a.at - b.at)[0];
    if (!due) break;
    now = Math.max(now, due.at); due.done = true; due.fn();
    if (stop()) return;
  }
  now = end;
};
// An answer that takes `ms` on the test clock, then a body that takes `bodyMs` to read.
// An abort of the page ends either wait when it fires, with an AbortError like fetch.
const timed = (options, ms, response, bodyMs = 0) => {
  const signal = options.signal;
  const wait = (span) => {
    advance(span, () => !!signal && signal.aborted);
    if (signal && signal.aborted) { const err = new Error('aborted'); err.name = 'AbortError'; throw err; }
  };
  wait(ms);
  return Object.assign({}, response, { json: async () => { wait(bodyMs); return response.json(); } });
};
const until = async (done) => {
  for (let i = 0; i < 1000 && !done(); i++) await tick();
  if (!done()) throw new Error('gave up waiting');
};
// A handler that answers only when the test calls pending[i].resolve(answer(...)).
const held = (url) => new Promise(resolve => pending.push({ url, resolve }));
const routes = [];
const route = (method, path, handler) => routes.unshift([method, path, handler]);
globalThis.apiFetch = async (url, options) => {
  const method = (options || {}).method || 'GET';
  const body = options && options.body ? JSON.parse(options.body) : null;
  const isProposal = url.includes('/proposal?');
  calls.push([method, url, body]);
  inflight += 1; maxInflight = Math.max(maxInflight, inflight);
  if (isProposal) { proposalsOpen += 1; maxProposals = Math.max(maxProposals, proposalsOpen); }
  try {
    await tick();
    for (const [m, path, handler] of routes) {
      if (m === method && url.split('?')[0] === path) return await handler(url, body, options || {});
    }
    throw new Error('unexpected ' + method + ' ' + url);
  } finally {
    inflight -= 1;
    if (isProposal) proposalsOpen -= 1;
  }
};
"""


def run_page(tmp_path, script, body):
    case = tmp_path / "case.js"
    case.write_text(
        PAGE_PRELUDE + f"vm.runInThisContext({json.dumps(script)});\n"
        "const page = importsPage();\n"
        "page.sleep = async (ms) => { slept.push(ms); advance(ms); };\n"
        "const out = {};\n(async () => {\n" + body +
        "\nconsole.log(JSON.stringify(out));\n})().catch(e => { console.error(e); process.exit(1); });\n")
    result = subprocess.run([NODE, str(case)], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def download(download_id, title=TITLE):
    return {"download_id": download_id, "title": title, "queue_ids": [11, 12], "size": 2_000_000_000,
            "added": "2026-10-02T10:00:00Z", "download_client": "SABnzbd", "state": "importBlocked",
            "messages": ["Found matching series via grab history, but release was matched to series by ID."],
            "movie_id": None, "series_id": 10, "episode_ids": [3]}


def listing(*download_ids):
    return {"instances": [
        {"id": 1, "name": "Sonarr", "type": "sonarr", "enabled": True,
         "queue_link": {"scheme": "http", "port": 8989, "path": "/activity/queue"},
         "error": "", "starting": False, "downloads": [download(i) for i in download_ids]},
        {"id": 2, "name": "Radarr", "type": "radarr", "enabled": False,
         "queue_link": {"scheme": "http", "port": 7878, "path": "/activity/queue"},
         "error": "", "starting": False, "downloads": []},
    ], "checked_at": "2026-10-02T17:00:00Z"}


def proposal(download_id="a/1", *, importable=True, why_not="", state="fits", reasons=(), details=(), notes=(),
             uncovered=0):
    return {"download_id": download_id, "title": TITLE, "target": "Some Show S01E01",
            "candidates": [{"path": "/downloads/complete/Some.Show.S01E01/Some.Show.S01E01.mkv",
                            "relative_path": "Some.Show.S01E01.mkv", "size": 2_000_000_000, "video": True,
                            "target": "Some Show S01E01", "movie_id": None, "series_id": 10, "season_number": 1,
                            "episode_ids": [3], "episode_numbers": [1], "quality": "WEBDL-1080p",
                            "languages": ["German"], "release_group": "GRP", "rejections": []}],
            "importable": importable, "why_not": why_not, "uncovered_episodes": uncovered,
            "proposal_key": "0123456789abcdef",
            "verdict": {"state": state, "reasons": list(reasons), "notes": list(notes),
                        "details": list(details), "error": ""},
            "cached": False}


def page_script(client):
    return component_script(client.get("/imports").text, "function importsPage()")


def setup_one(answer_proposal):
    """JS lines: the list holds one Sonarr download "a/1" with the given proposal."""
    return (f"route('GET', '/api/imports', () => answer(200, {json.dumps(listing('a/1'))}));\n"
            f"route('GET', '/api/imports/1/proposal', () => answer(200, {json.dumps(answer_proposal)}));\n"
            "await page.load();\n"
            "const inst = page.instances[0]; const d = inst.downloads[0];\n"
            "calls.length = 0;\n")


needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


@needs_node
def test_load_reads_the_list_then_the_proposals_two_at_a_time(client, tmp_path):
    out = run_page(tmp_path, page_script(client), f"""
route('GET', '/api/imports', () => answer(200, {json.dumps(listing('a/1', 'b 2', 'c&3'))}));
route('GET', '/api/imports/1/proposal', (url) => answer(200, {json.dumps(proposal())}));
await page.load();
out.calls = calls.map(c => [c[0], c[1]]);
out.max = maxInflight;
out.loading = page.loading;
out.checkedAt = page.checkedAt;
out.keys = Object.keys(page.proposals).sort();
out.ready = Object.values(page.proposals).every(p => !p.loading && p.error === '' && p.data.proposal_key === '0123456789abcdef');
out.canImport = page.canImport(page.instances[0], page.instances[0].downloads[0]);
out.queue = page.queueHref(page.instances[0]);
out.noPort = page.queueHref({{ queue_link: {{ scheme: 'https', port: null, path: '/sonarr/activity/queue' }} }});
""")
    assert out["calls"] == [["GET", "/api/imports"],
                            ["GET", "/api/imports/1/proposal?download_id=a%2F1"],
                            ["GET", "/api/imports/1/proposal?download_id=b%202"],
                            ["GET", "/api/imports/1/proposal?download_id=c%263"]]
    assert out["max"] == 2
    assert out["loading"] is False and out["checkedAt"] == "2026-10-02T17:00:00Z"
    assert out["keys"] == ["1:a/1", "1:b 2", "1:c&3"] and out["ready"] is True
    assert out["canImport"] is True
    assert out["queue"] == "http://missingarr.test:8989/activity/queue"
    assert out["noPort"] == "https://missingarr.test/sonarr/activity/queue"


@needs_node
def test_a_handled_download_disappears_and_a_failed_proposal_shows_its_error(client, tmp_path):
    out = run_page(tmp_path, page_script(client), f"""
route('GET', '/api/imports', () => answer(200, {json.dumps(listing('a/1', 'b 2', 'c&3'))}));
route('GET', '/api/imports/1/proposal', (url) => {{
  if (url.endsWith('b%202')) return answer(409, {{ detail: 'Already handled — this download no longer waits in the queue' }});
  if (url.endsWith('c%263')) return answer(503, {{ detail: 'Cannot connect to instance' }});
  return answer(200, {json.dumps(proposal())});
}});
await page.load();
const inst = page.instances[0];
out.ids = inst.downloads.map(d => d.download_id);
out.keys = Object.keys(page.proposals).sort();
out.failed = page.proposals['1:c&3'];
out.canImport = page.canImport(inst, inst.downloads[1]);
out.title = page.importTitle(inst, inst.downloads[1]);
out.toasts = toasts;
""")
    assert out["ids"] == ["a/1", "c&3"]
    assert out["keys"] == ["1:a/1", "1:c&3"]
    assert out["failed"] == {"loading": False, "error": "Cannot connect to instance", "data": None}
    assert out["canImport"] is False
    assert out["title"] == "No proposal: Cannot connect to instance"
    assert out["toasts"] == []


@needs_node
def test_import_confirms_with_the_reasons_sends_the_key_and_follows_the_command(client, tmp_path):
    foreign = proposal(state="foreign", reasons=["other series", "published too early"],
                       details=["Sonarr maps the name to another series", "published 400 days before air date"],
                       uncovered=1)
    out = run_page(tmp_path, page_script(client), setup_one(foreign) + f"""
route('POST', '/api/imports/1/import', () => answer(200, {{ state: 'sent', command_id: 500, files: 1, title: '{TITLE}', target: 'Some Show S01E01', message: '' }}));
let polls = 0;
route('GET', '/api/imports/1/commands/500', () => {{
  polls += 1;
  return polls < 3 ? answer(200, {{ command_id: 500, state: 'running', status: 'started', message: '' }})
                   : answer(200, {{ command_id: 500, state: 'imported', status: 'completed', message: 'Imported' }});
}});
out.discardClass = page.discardClass(inst, d);
out.note = page.coverageNote(inst, d);
await page.importDownload(inst, d);
out.confirms = confirms; out.calls = calls; out.toasts = toasts; out.slept = slept;
out.counted = counted; out.busy = page.busy;
""")
    assert out["discardClass"] == "btn-danger"
    assert out["note"] == COVERAGE
    assert out["confirms"] == [
        f'Import "{TITLE}" as Some Show S01E01?\n\n'
        "The pre-filter rules object: other series, published too early "
        "(Sonarr maps the name to another series; published 400 days before air date).\n\n"
        f"{COVERAGE}\n\n"
        "The app moves the files into the library."]
    assert out["calls"] == [
        ["POST", "/api/imports/1/import", {"download_id": "a/1", "proposal_key": "0123456789abcdef"}],
        ["GET", "/api/imports/1/commands/500", None],
        ["GET", "/api/imports/1/commands/500", None],
        ["GET", "/api/imports/1/commands/500", None],
        ["GET", "/api/imports", None],
        ["GET", "/api/imports/1/proposal?download_id=a%2F1", None]]
    assert out["slept"] == [3000, 3000, 3000]
    assert out["toasts"] == [[f"Imported: {TITLE}", "success"]]
    assert out["counted"] == 1 and out["busy"] == {}


@needs_node
def test_import_of_a_fitting_or_locked_proposal(client, tmp_path):
    locked = proposal(importable=False, why_not="The app objects: Not an upgrade for existing episode file(s)")
    out = run_page(tmp_path, page_script(client), setup_one(proposal()) + f"""
out.discardClass = page.discardClass(inst, d);
out.note = page.coverageNote(inst, d);
confirmAnswer = false;
await page.importDownload(inst, d);
out.confirm = confirms[0];
out.cancelledCalls = calls.length;
page.proposals[page.key(inst, d)].data = {json.dumps(locked)};
out.canImport = page.canImport(inst, d);
out.title = page.importTitle(inst, d);
await page.importDownload(inst, d);
out.confirmsAfterLocked = confirms.length;
out.lockedCalls = calls.length;
""")
    assert out["discardClass"] == "btn-secondary" and out["note"] == ""
    assert out["confirm"] == f'Import "{TITLE}" as Some Show S01E01?\n\nThe app moves the files into the library.'
    assert out["cancelledCalls"] == 0
    assert out["canImport"] is False
    assert out["title"] == "The app objects: Not an upgrade for existing episode file(s)"
    assert out["confirmsAfterLocked"] == 1 and out["lockedCalls"] == 0


@needs_node
@pytest.mark.parametrize("code, state, message, expected", [
    (200, "unconfirmed", UNCONFIRMED_SONARR, [UNCONFIRMED_SONARR, "info"]),
    (200, "unconfirmed", UNCONFIRMED_RADARR, [UNCONFIRMED_RADARR, "info"]),
    (200, "failed", "The import failed in the app — see the app's log",
     ["Import failed: The import failed in the app — see the app's log", "error"]),
    (200, "unknown", "The app may have restarted since the import was sent — check it in the app",
     ["The app may have restarted since the import was sent — check it in the app", "info"]),
    # missingarr restarted or the instance was edited: it no longer follows the command
    (404, "", "", [LOST, "info"]),
])
def test_import_reports_every_final_state(client, tmp_path, code, state, message, expected):
    final = json.dumps({"command_id": 501, "state": state, "status": "", "message": message} if code == 200
                       else {"detail": "Unknown command — missingarr did not send it to this instance"})
    out = run_page(tmp_path, page_script(client), setup_one(proposal()) + f"""
route('POST', '/api/imports/1/import', () => answer(200, {{ state: 'sent', command_id: 501, files: 1, title: 't', target: 'x', message: '' }}));
route('GET', '/api/imports/1/commands/501', () => answer({code}, {final}));
await page.importDownload(inst, d);
out.toasts = toasts; out.counted = counted;
""")
    assert out["toasts"] == [expected]
    assert out["counted"] == 1


@needs_node
def test_import_stops_asking_after_two_minutes(client, tmp_path):
    out = run_page(tmp_path, page_script(client), setup_one(proposal()) + """
route('POST', '/api/imports/1/import', () => answer(200, { state: 'sent', command_id: 502, files: 1, title: 't', target: 'x', message: '' }));
let polls = 0;
route('GET', '/api/imports/1/commands/502', () => {
  polls += 1;
  return polls === 1 ? broken(502) : answer(200, { command_id: 502, state: 'running', status: 'queued', message: '' });
});
await page.importDownload(inst, d);
out.polls = polls; out.slept = slept.length; out.toasts = toasts; out.counted = counted;
""")
    # The 40th sleep ends right at the deadline: no request is started then.
    assert out["polls"] == 39 and out["slept"] == 40
    assert out["toasts"] == [[STILL_RUNNING, "info"]]
    assert out["counted"] == 1


@needs_node
def test_import_keeps_asking_while_the_queue_has_not_confirmed_it(client, tmp_path):
    # "confirming" is not final: the page asks on, and when its budget ends it shows the hint.
    out = run_page(tmp_path, page_script(client), setup_one(proposal()) + f"""
route('POST', '/api/imports/1/import', () => answer(200, {{ state: 'sent', command_id: 503, files: 1, title: 't', target: 'x', message: '' }}));
let polls = 0; let confirmed = true;
route('GET', '/api/imports/1/commands/503', () => {{
  polls += 1;
  if (confirmed && polls === 3) return answer(200, {{ command_id: 503, state: 'imported', status: 'completed', message: 'Imported' }});
  return answer(200, {{ command_id: 503, state: 'confirming', status: 'completed', message: '{UNCONFIRMED_SONARR}' }});
}});
await page.importDownload(inst, d);
out.first = {{ polls, toasts: toasts.slice() }};
polls = 0; confirmed = false; toasts.length = 0; slept.length = 0;
await page.importDownload(inst, d);
out.second = {{ polls, slept: slept.length, toasts: toasts.slice() }};
""")
    assert out["first"] == {"polls": 3, "toasts": [[f"Imported: {TITLE}", "success"]]}
    assert out["second"] == {"polls": 39, "slept": 40, "toasts": [[UNCONFIRMED_SONARR, "info"]]}


@needs_node
@pytest.mark.parametrize("slowness, state, polls, expected", [
    ("answer", "running", 3, [STILL_RUNNING, "info"]),            # every answer takes 40 s
    ("body", "confirming", 3, [UNCONFIRMED_SONARR, "info"]),      # every body takes 40 s to read
    ("stalled", "running", 1, [STILL_RUNNING, "info"]),           # the request never ends
])
def test_slow_answers_never_keep_the_card_busy_beyond_two_minutes(client, tmp_path, slowness, state, polls,
                                                                  expected):
    # The clock also runs while a request and its body are under way: the third
    # request (or the stalled first one) is aborted when the two minutes are up.
    out = run_page(tmp_path, page_script(client), setup_one(proposal()) + f"""
route('POST', '/api/imports/1/import', () => answer(200, {{ state: 'sent', command_id: 504, files: 1, title: 't', target: 'x', message: '' }}));
const status = answer(200, {{ command_id: 504, state: '{state}', status: 'completed', message: '{UNCONFIRMED_SONARR}' }});
let polls = 0;
route('GET', '/api/imports/1/commands/504', (url, body, options) => {{
  polls += 1;
  if ('{slowness}' === 'answer') return timed(options, 40000, status);
  if ('{slowness}' === 'body') return timed(options, 0, status, 40000);
  return timed(options, Infinity, status);
}});
const started = now;
await page.importDownload(inst, d);
out.elapsed = now - started; out.polls = polls; out.toasts = toasts; out.timers = openTimers(); out.busy = page.busy;
""")
    assert out["elapsed"] == 120000
    assert out["polls"] == polls
    assert out["toasts"] == [expected]
    assert out["timers"] == 0 and out["busy"] == {}


@needs_node
def test_a_switched_off_or_changed_instance_ends_the_tracking(client, tmp_path):
    # A 409 of the command status (switched off or changed between two polls) does not
    # change by asking again: the page stops at once with a fixed text.
    out = run_page(tmp_path, page_script(client), setup_one(proposal()) + f"""
route('POST', '/api/imports/1/import', () => answer(200, {{ state: 'sent', command_id: 505, files: 1, title: 't', target: 'x', message: '' }}));
out.runs = [];
for (const detail of {json.dumps([INSTANCE_OFF, INSTANCE_CHANGED])}) {{
  let polls = 0;
  route('GET', '/api/imports/1/commands/505', () => {{
    polls += 1;
    return polls === 1 ? answer(200, {{ command_id: 505, state: 'running', status: 'started', message: '' }})
                       : answer(409, {{ detail }});
  }});
  toasts.length = 0; slept.length = 0;
  await page.importDownload(inst, d);
  out.runs.push({{ polls, slept: slept.length, toasts: toasts.slice() }});
}}
""")
    assert out["runs"] == [{"polls": 2, "slept": 2, "toasts": [[STOPPED, "info"]]}] * 2


@needs_node
def test_import_refused_or_session_lost(client, tmp_path):
    out = run_page(tmp_path, page_script(client), setup_one(proposal()) + """
route('POST', '/api/imports/1/import', () => answer(409, { detail: "The app's proposal has changed — reload the page" }));
await page.importDownload(inst, d);
out.refused = { toasts: toasts.slice(), calls: calls.map(c => c[1]), counted };
toasts.length = 0; calls.length = 0;
route('POST', '/api/imports/1/import', () => broken(500));
await page.importDownload(inst, d);
out.broken = toasts.slice();
toasts.length = 0; calls.length = 0;
route('POST', '/api/imports/1/import', expired);
await page.importDownload(inst, d);
out.expired = { toasts: toasts.slice(), calls: calls.map(c => c[1]), busy: page.busy };
""")
    assert out["refused"] == {"toasts": [["The app's proposal has changed — reload the page", "error"]],
                              "calls": ["/api/imports/1/import", "/api/imports",
                                        "/api/imports/1/proposal?download_id=a%2F1"],
                              "counted": 1}
    assert out["broken"] == [[UNCLEAR, "info"]]
    assert out["expired"] == {"toasts": [], "calls": ["/api/imports/1/import"], "busy": {}}


@needs_node
def test_a_gateway_error_does_not_claim_the_import_failed(client, tmp_path):
    # Behind a reverse proxy a long import can end in an HTML 504 while
    # missingarr still sends the command: the page must not say "failed".
    out = run_page(tmp_path, page_script(client), setup_one(proposal()) + """
route('POST', '/api/imports/1/import', () => broken(504));
await page.importDownload(inst, d);
out.gateway = { toasts: toasts.slice(), calls: calls.map(c => c[1]), counted, busy: page.busy };
toasts.length = 0; calls.length = 0;
route('POST', '/api/imports/1/import', () => { throw new TypeError('network down'); });
await page.importDownload(inst, d);
out.network = { toasts: toasts.slice(), calls: calls.map(c => c[1]) };
""")
    assert out["gateway"] == {"toasts": [[UNCLEAR, "info"]],
                              "calls": ["/api/imports/1/import", "/api/imports",
                                        "/api/imports/1/proposal?download_id=a%2F1"],
                              "counted": 1, "busy": {}}
    assert out["network"] == {"toasts": [[UNCLEAR, "info"]],
                              "calls": ["/api/imports/1/import", "/api/imports",
                                        "/api/imports/1/proposal?download_id=a%2F1"]}


@needs_node
def test_discard_with_and_without_blocklist(client, tmp_path):
    out = run_page(tmp_path, page_script(client), setup_one(proposal()) + f"""
route('POST', '/api/imports/1/discard', (url, body) => answer(200, {{ download_id: body.download_id, title: '{TITLE}', blocklist: body.blocklist, queue_id: 11 }}));
await page.discard(inst, d);
out.first = {{ confirm: confirms[0], calls: calls.map(c => [c[0], c[1], c[2]]), toasts: toasts.slice() }};
page.blocklist[page.key(inst, d)] = false;
calls.length = 0; toasts.length = 0;
await page.discard(inst, d);
out.second = {{ confirm: confirms[1], call: calls[0], toasts: toasts.slice() }};
out.counted = counted;
confirmAnswer = false; calls.length = 0;
await page.discard(inst, d);
out.cancelled = calls.length;
confirmAnswer = true; toasts.length = 0;
route('POST', '/api/imports/1/discard', () => answer(409, {{ detail: 'Already handled — this download no longer waits in the queue' }}));
await page.discard(inst, d);
route('POST', '/api/imports/1/discard', () => broken(500));
await page.discard(inst, d);
out.errors = toasts.slice();
""")
    assert out["first"]["confirm"] == f'Discard "{TITLE}"?\n\n{DISCARD_ON}'
    assert out["first"]["calls"] == [
        ["POST", "/api/imports/1/discard", {"download_id": "a/1", "blocklist": True}],
        ["GET", "/api/imports", None],
        ["GET", "/api/imports/1/proposal?download_id=a%2F1", None]]
    assert out["first"]["toasts"] == [[f"Discarded: {TITLE}", "success"]]
    assert out["second"]["confirm"] == (
        f'Discard "{TITLE}"?\n\nThe download and its files are removed from the download client. '
        "It is not blocklisted, and there is no new search.")
    assert out["second"]["call"] == ["POST", "/api/imports/1/discard", {"download_id": "a/1", "blocklist": False}]
    assert out["second"]["toasts"] == [[f"Discarded: {TITLE}", "success"]]
    assert out["counted"] == 2
    assert out["cancelled"] == 0
    assert out["errors"] == [["Already handled — this download no longer waits in the queue", "error"],
                             ["Discard failed", "error"]]


@needs_node
def test_an_import_whose_answer_got_lost_is_not_followed(client, tmp_path):
    # missingarr says the app may have taken the command: no polling, an info toast, a fresh list.
    out = run_page(tmp_path, page_script(client), setup_one(proposal()) + f"""
route('POST', '/api/imports/1/import', () => answer(200, {{ state: 'uncertain', command_id: null, files: 1, title: '{TITLE}', target: 'Some Show S01E01', message: '{MAY_RUN}' }}));
await page.importDownload(inst, d);
out.calls = calls.map(c => c[1]); out.toasts = toasts; out.counted = counted; out.slept = slept.length;
out.busy = page.busy;
""")
    assert out["calls"] == ["/api/imports/1/import", "/api/imports", "/api/imports/1/proposal?download_id=a%2F1"]
    assert out["toasts"] == [[MAY_RUN, "info"]]
    assert out["counted"] == 1 and out["slept"] == 0 and out["busy"] == {}


@needs_node
def test_a_reload_drops_the_older_answer_of_a_proposal(client, tmp_path):
    out = run_page(tmp_path, page_script(client), f"""
route('GET', '/api/imports', () => answer(200, {json.dumps(listing('a/1'))}));
route('GET', '/api/imports/1/proposal', (url) => held(url));
const first = page.load();
await until(() => pending.length === 1);
const second = page.load();                      // Reload while the first answer is still out
await until(() => pending.length === 2);
pending[1].resolve(answer(200, {json.dumps(proposal())}));            // the newer answer arrives first
await second;
pending[0].resolve(answer(200, {json.dumps(proposal(state="foreign", reasons=["other series"]))}));   // the older one last
await first;
out.entry = page.proposals['1:a/1'];
out.urls = pending.map(p => p.url);
""")
    assert out["urls"] == ["/api/imports/1/proposal?download_id=a%2F1"] * 2
    assert out["entry"]["loading"] is False and out["entry"]["data"]["verdict"]["state"] == "fits"


@needs_node
def test_reloads_never_run_more_than_two_proposal_requests(client, tmp_path):
    handled = "Already handled — this download no longer waits in the queue"
    out = run_page(tmp_path, page_script(client), f"""
route('GET', '/api/imports', () => answer(200, {json.dumps(listing('a/1', 'b 2', 'c&3'))}));
route('GET', '/api/imports/1/proposal', (url) => held(url));
const first = page.load();
await until(() => pending.length === 2);         // a/1 and b 2 of the first load
const second = page.load();
for (let i = 0; i < 20; i++) await tick();
out.waiting = pending.length;                    // the reload waits for a free slot
pending[1].resolve(answer(409, {{ detail: '{handled}' }}));     // b 2 of the first load: a 409 of an old load
await until(() => pending.length === 3);
pending[0].resolve(answer(200, {json.dumps(proposal(state="foreign"))}));
await until(() => pending.length === 4);
pending[2].resolve(answer(200, {json.dumps(proposal())}));
pending[3].resolve(answer(200, {json.dumps(proposal())}));
await until(() => pending.length === 5);
pending[4].resolve(answer(200, {json.dumps(proposal())}));
await second; await first;
out.max = maxProposals;
out.urls = pending.map(p => p.url.split('=')[1]);
out.ids = page.instances[0].downloads.map(d => d.download_id);
out.states = Object.keys(page.proposals).sort().map(k => page.proposals[k].data.verdict.state);
""")
    assert out["waiting"] == 2 and out["max"] == 2
    assert out["urls"] == ["a%2F1", "b%202", "a%2F1", "b%202", "c%263"]
    assert out["ids"] == ["a/1", "b 2", "c&3"]          # the old 409 removed no card
    assert out["states"] == ["fits", "fits", "fits"]


@needs_node
def test_a_failed_reload_keeps_the_cards_and_their_running_requests(client, tmp_path):
    handled = "Already handled — this download no longer waits in the queue"
    out = run_page(tmp_path, page_script(client), f"""
let mode = 'ok';
route('GET', '/api/imports', () => {{
  if (mode === 'down') throw new TypeError('Failed to fetch');          // no connection
  return mode === 'proxy' ? broken(502) : answer(200, {json.dumps(listing('a/1', 'b 2'))});
}});
route('GET', '/api/imports/1/proposal', (url) => held(url));
const first = page.load();
await until(() => pending.length === 2);         // two cards: both proposals are asked at once
mode = 'proxy';
await page.load();                               // Reload fails: a reverse proxy's 502 without JSON
mode = 'down';
await page.load();                               // and once more: no connection
out.loading = page.loading;
pending[0].resolve(answer(200, {json.dumps(proposal())}));        // the shown cards' requests still count
pending[1].resolve(answer(409, {{ detail: '{handled}' }}));
await first;
const inst = page.instances[0];
out.ids = inst.downloads.map(d => d.download_id);
out.entry = page.proposals['1:a/1'];
out.canImport = page.canImport(inst, inst.downloads[0]);
out.lists = calls.filter(c => c[1] === '/api/imports').length;
out.proposals = pending.length;
out.max = maxProposals;
out.toasts = toasts;
""")
    assert out["loading"] is False
    assert out["ids"] == ["a/1"]                     # the 409 of a shown card still removes it
    assert out["entry"]["loading"] is False and out["entry"]["data"]["verdict"]["state"] == "fits"
    assert out["canImport"] is True
    assert out["lists"] == 3 and out["proposals"] == 2 and out["max"] == 2
    assert out["toasts"] == [["Could not load the imports", "error"], ["Could not load the imports", "error"]]


@needs_node
def test_an_older_list_answer_never_replaces_a_newer_one(client, tmp_path):
    # The body of a list answer can arrive after the body of a newer one: only the newest load counts,
    # for the list and for an error toast alike.
    out = run_page(tmp_path, page_script(client), f"""
const bodies = [];
const slow = (status, data) => ({{ ok: status >= 200 && status < 300, status,
  json: () => new Promise(resolve => bodies.push(() => resolve(JSON.parse(JSON.stringify(data))))) }});
let lists = 0;
route('GET', '/api/imports', () => {{
  lists += 1;
  if (lists === 1) return slow(200, {json.dumps(listing('a/1'))});
  if (lists === 2) return slow(200, {json.dumps(listing('b 2'))});
  if (lists === 3) return slow(502, {{ detail: 'Cannot connect to instance' }});
  return slow(200, {json.dumps(listing('c&3'))});
}});
route('GET', '/api/imports/1/proposal', () => answer(200, {json.dumps(proposal())}));
const first = page.load();
await until(() => bodies.length === 1);
const second = page.load();
await until(() => bodies.length === 2);
bodies[1]();                                     // the newer body arrives first
await second;
bodies[0]();                                     // the older one last
await first;
out.first = {{ ids: page.instances[0].downloads.map(d => d.download_id), keys: Object.keys(page.proposals),
              asked: calls.filter(c => c[1].includes('/proposal?')).map(c => c[1]) }};
const third = page.load();                        // fails, and its error body comes late
await until(() => bodies.length === 3);
const fourth = page.load();
await until(() => bodies.length === 4);
bodies[3]();
await fourth;
bodies[2]();
await third;
out.second = {{ ids: page.instances[0].downloads.map(d => d.download_id), toasts: toasts.slice(),
               loading: page.loading }};
""")
    assert out["first"] == {"ids": ["b 2"], "keys": ["1:b 2"], "asked": ["/api/imports/1/proposal?download_id=b%202"]}
    assert out["second"] == {"ids": ["c&3"], "toasts": [], "loading": False}


@needs_node
def test_leaving_the_page_stops_the_proposal_workers(client, tmp_path):
    # hx-boost keeps the JS context: Alpine calls destroy() when the page is left. The two
    # running proposal requests end, but write nothing, and no further request starts.
    out = run_page(tmp_path, page_script(client), f"""
route('GET', '/api/imports', () => answer(200, {json.dumps(listing('a/1', 'b 2', 'c&3'))}));
route('GET', '/api/imports/1/proposal', (url) => held(url));
const loading = page.load();
await until(() => pending.length === 2);
const before = JSON.parse(JSON.stringify(page.proposals));
page.destroy();
pending[0].resolve(answer(200, {json.dumps(proposal())}));
pending[1].resolve(answer(409, {{ detail: 'Already handled — this download no longer waits in the queue' }}));
await loading;
for (let i = 0; i < 20; i++) await tick();
out.urls = pending.map(p => p.url.split('=')[1]);
out.unchanged = JSON.stringify(page.proposals) === JSON.stringify(before);
out.ids = page.instances[0].downloads.map(d => d.download_id);
out.calls = calls.length;
""")
    assert out["urls"] == ["a%2F1", "b%202"]
    assert out["unchanged"] is True
    assert out["ids"] == ["a/1", "b 2", "c&3"]          # the 409 of the left page removed no card
    assert out["calls"] == 3


@needs_node
def test_leaving_the_page_during_an_import_skips_the_reload_but_keeps_toast_and_counter(client, tmp_path):
    out = run_page(tmp_path, page_script(client), setup_one(proposal()) + f"""
route('POST', '/api/imports/1/import', () => answer(200, {{ state: 'sent', command_id: 506, files: 1, title: '{TITLE}', target: 'Some Show S01E01', message: '' }}));
route('GET', '/api/imports/1/commands/506', (url) => held(url));
const importing = page.importDownload(inst, d);
await until(() => pending.length === 1);
page.destroy();
pending[0].resolve(answer(200, {{ command_id: 506, state: 'imported', status: 'completed', message: 'Imported' }}));
await importing;
for (let i = 0; i < 20; i++) await tick();
out.calls = calls.map(c => [c[0], c[1]]); out.toasts = toasts; out.counted = counted; out.busy = page.busy;
""")
    assert out["calls"] == [["POST", "/api/imports/1/import"], ["GET", "/api/imports/1/commands/506"]]
    assert out["toasts"] == [[f"Imported: {TITLE}", "success"]]
    assert out["counted"] == 1 and out["busy"] == {}


@needs_node
def test_the_counter_is_refreshed_before_the_cards_reload(client, tmp_path):
    # After a discard and after the end of an import the menu counter is asked at once,
    # not only after every proposal of the reloaded list has come back.
    out = run_page(tmp_path, page_script(client), setup_one(proposal()) + f"""
globalThis.refreshImportsCount = async () => {{ counted += 1; calls.push(['COUNT', '', null]); }};
route('GET', '/api/imports/1/proposal', (url) => held(url));
route('POST', '/api/imports/1/discard', (url, body) => answer(200, {{ download_id: body.download_id, title: '{TITLE}', blocklist: body.blocklist, queue_id: 11 }}));
const discarding = page.discard(inst, d);
await until(() => pending.length === 1);
out.discard = {{ counted, calls: calls.map(c => [c[0], c[1]]) }};
pending[0].resolve(answer(200, {json.dumps(proposal())}));
await discarding;
calls.length = 0;
route('POST', '/api/imports/1/import', () => answer(200, {{ state: 'sent', command_id: 507, files: 1, title: '{TITLE}', target: 'Some Show S01E01', message: '' }}));
route('GET', '/api/imports/1/commands/507', () => answer(200, {{ command_id: 507, state: 'imported', status: 'completed', message: 'Imported' }}));
const importing = page.importDownload(inst, d);
await until(() => pending.length === 2);
out.import = {{ counted, calls: calls.map(c => [c[0], c[1]]) }};
pending[1].resolve(answer(200, {json.dumps(proposal())}));
await importing;
out.counted = counted;
""")
    assert out["discard"] == {"counted": 1, "calls": [["POST", "/api/imports/1/discard"], ["COUNT", ""],
                                                      ["GET", "/api/imports"],
                                                      ["GET", "/api/imports/1/proposal?download_id=a%2F1"]]}
    assert out["import"] == {"counted": 2, "calls": [["POST", "/api/imports/1/import"],
                                                     ["GET", "/api/imports/1/commands/507"], ["COUNT", ""],
                                                     ["GET", "/api/imports"],
                                                     ["GET", "/api/imports/1/proposal?download_id=a%2F1"]]}
    assert out["counted"] == 2


@needs_node
def test_leaving_the_page_during_the_list_load_starts_no_proposal_request(client, tmp_path):
    out = run_page(tmp_path, page_script(client), f"""
route('GET', '/api/imports', (url) => held(url));
route('GET', '/api/imports/1/proposal', (url) => held(url));
const loading = page.load();
await until(() => pending.length === 1);
page.destroy();
pending[0].resolve(answer(200, {json.dumps(listing('a/1', 'b 2'))}));
await loading;
for (let i = 0; i < 20; i++) await tick();
out.proposals = calls.filter(c => c[1].includes('/proposal?')).length;
out.instances = page.instances.length;
out.toasts = toasts;
""")
    assert out["proposals"] == 0
    assert out["instances"] == 0
    assert out["toasts"] == []


@needs_node
def test_leaving_the_page_during_a_discard_keeps_the_counter_but_loads_nothing(client, tmp_path):
    out = run_page(tmp_path, page_script(client), setup_one(proposal()) + f"""
globalThis.refreshImportsCount = async () => {{ counted += 1; calls.push(['COUNT', '', null]); }};
route('POST', '/api/imports/1/discard', (url) => held(url));
const discarding = page.discard(inst, d);
await until(() => pending.length === 1);
page.destroy();
pending[0].resolve(answer(200, {{ download_id: 'a/1', title: '{TITLE}', blocklist: true, queue_id: 11 }}));
await discarding;
for (let i = 0; i < 20; i++) await tick();
out.counted = counted;
out.lists = calls.filter(c => c[1] === '/api/imports').length;
out.proposals = calls.filter(c => c[1].includes('/proposal?')).length;
out.busy = page.busy;
""")
    assert out["counted"] == 1
    assert out["lists"] == 0 and out["proposals"] == 0
    assert out["busy"] == {}


# ── The counter functions of app.js in node ──────────────────────────────────

APP_PRELUDE = r"""
const fs = require('fs');
const vm = require('vm');
globalThis.setTimeout = () => 0;
globalThis.clearTimeout = () => {};
const intervals = [];
globalThis.setInterval = (fn, ms) => { intervals.push({ fn, ms, cleared: false }); return intervals.length; };
globalThis.clearInterval = (id) => { if (intervals[id - 1]) intervals[id - 1].cleared = true; };
globalThis.EventSource = function () {};
globalThis.window = globalThis;
globalThis.location = { pathname: '/imports', search: '', href: 'http://t/imports' };
const listeners = {};
const elements = {};
const element = () => ({ textContent: 'old', hidden: true });
globalThis.document = {
  addEventListener: (name, fn) => { listeners[name] = fn; },
  getElementById: () => null,
  querySelectorAll: (selector) => elements[selector] || [],
};
const stores = {};
globalThis.Alpine = { store: (name, value) => { if (value !== undefined) stores[name] = value; return stores[name]; },
                      $data: () => ({}) };
const responses = [];
const fetched = [];
globalThis.fetch = async (url, options) => { fetched.push(url); return responses.shift(); };
const source = fs.readFileSync('static/js/app.js', 'utf8');
vm.runInThisContext(source);
vm.runInThisContext(source);   // hx-boost runs the file again after every navigation
const out = {};
"""


def run_app_js(tmp_path, body):
    case = tmp_path / "case.js"
    case.write_text(APP_PRELUDE + "(async () => {\n" + body +
                    "\nconsole.log(JSON.stringify(out));\n})().catch(e => { console.error(e); process.exit(1); });\n")
    result = subprocess.run([NODE, str(case)], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


@needs_node
def test_count_texts(tmp_path):
    out = run_app_js(tmp_path, """
out.badge = [importsCountText({ total: 0, complete: true }), importsCountText({ total: 0, complete: false }),
             importsCountText({ total: 3, complete: true }), importsCountText({ total: 2, complete: false })];
out.dashboard = [importsDashboardText({ total: 0, complete: true }), importsDashboardText({ total: 0, complete: false }),
                 importsDashboardText({ total: 1, complete: true }), importsDashboardText({ total: 3, complete: true }),
                 importsDashboardText({ total: 2, complete: false })];
""")
    # "?" only stands for 0: with a number the badge shows it, the dashboard line names the missing app.
    assert out["badge"] == ["", "?", "3", "2"]
    assert out["dashboard"] == ["No imports waiting", UNKNOWN_SUMMARY, "1 import waiting", "3 imports waiting",
                                "2 imports waiting — an app could not be read or has just restarted"]


@needs_node
def test_update_fills_the_menu_badge_and_the_dashboard_line(tmp_path):
    out = run_app_js(tmp_path, """
const badges = [element(), element()]; const lines = [element()];
elements['[data-imports-count]'] = badges; elements['[data-imports-dashboard]'] = lines;
const state = () => [badges.map(b => [b.textContent, b.hidden]), lines.map(l => [l.textContent, l.hidden])];
updateImportsCount(JSON.stringify({ total: 0, complete: true, per_instance: [], checked_at: '2026-10-02T17:00:00Z' }));
out.zero = state();
updateImportsCount(JSON.stringify({ total: 0, complete: false }));
out.unknown = state();
updateImportsCount(JSON.stringify({ total: 3, complete: true }));
out.three = state();
updateImportsCount('<html>not json</html>');
out.afterJunk = state();
""")
    assert out["zero"] == [[["", True], ["", True]], [["No imports waiting", False]]]
    assert out["unknown"] == [[["?", False], ["?", False]], [[UNKNOWN_SUMMARY, False]]]
    assert out["three"] == [[["3", False], ["3", False]], [["3 imports waiting", False]]]
    assert out["afterJunk"] == out["three"]


@needs_node
def test_refresh_reads_the_count_and_swallows_errors(tmp_path):
    out = run_app_js(tmp_path, """
const badge = element(); elements['[data-imports-count]'] = [badge];
responses.push({ status: 200, ok: true, redirected: false, url: '/api/imports/count',
                 text: async () => JSON.stringify({ total: 2, complete: true }) });
await refreshImportsCount();
out.first = [fetched.slice(), badge.textContent, badge.hidden];
responses.push({ status: 502, ok: false, redirected: false, url: '/api/imports/count', text: async () => 'bad' });
await refreshImportsCount();
out.afterError = [badge.textContent, badge.hidden];
globalThis.fetch = async () => { throw new TypeError('network down'); };
await refreshImportsCount();
out.afterNetwork = badge.textContent;
globalThis.fetch = async (url) => ({ status: 401, ok: false, redirected: false, url });
await refreshImportsCount();
out.login = location.href;
""")
    assert out["first"] == [["/api/imports/count"], "2", False]
    assert out["afterError"] == ["2", False]
    assert out["afterNetwork"] == "2"
    assert out["login"] == "/login?next=%2Fimports"


@needs_node
def test_the_menu_reads_the_count_at_once_and_every_minute_with_one_timer(tmp_path):
    out = run_app_js(tmp_path, """
const badge = element(); elements['[data-imports-count]'] = [badge];
const reply = (total) => ({ status: 200, ok: true, redirected: false, url: '/api/imports/count',
                            text: async () => JSON.stringify({ total, complete: true }) });
const timers = () => intervals.map(t => [t.ms, t.cleared]);
responses.push(reply(1));
await startImportsCount();
out.first = [fetched.length, badge.textContent, timers()];
vm.runInThisContext(source);           // the next page view (hx-boost) runs the file again
responses.push(reply(2));
await startImportsCount();
out.second = [fetched.length, badge.textContent, timers()];
responses.push(reply(3));
await intervals[1].fn();               // a minute later
out.later = [fetched.length, badge.textContent];
""")
    assert out["first"] == [1, "1", [[60000, False]]]
    assert out["second"] == [2, "2", [[60000, True], [60000, False]]]
    assert out["later"] == [3, "3"]


@needs_node
def test_only_the_newest_count_request_fills_the_counter(tmp_path):
    # The menu poll and the refresh after an action share one sequence: an answer is
    # used only if no later request started before its body was read.
    out = run_app_js(tmp_path, """
const badge = element(); const line = element();
elements['[data-imports-count]'] = [badge]; elements['[data-imports-dashboard]'] = [line];
const held = [];
globalThis.fetch = (url) => new Promise(resolve => held.push(resolve));
const tick = () => new Promise(resolve => setImmediate(resolve));
const reply = (total, text) => ({ status: 200, ok: true, redirected: false, url: '/api/imports/count',
                                  text: text || (async () => JSON.stringify({ total, complete: true })) });
const shown = () => [badge.textContent, line.textContent];
// Two polls overlap; the newer one answers first, the older one last.
const older = refreshImportsCount(); const newer = refreshImportsCount();
await tick();
held[1](reply(2)); await newer;
held[0](reply(5)); await older;
out.reversed = shown();
// The poll has its answer (one import waits), its body is still on the way. Discard
// ends that import and the page refreshes the count; the poll's body comes after that.
let body;
const poll = refreshImportsCount();
await tick();
held[2](reply(1, () => new Promise(resolve => { body = () => resolve(JSON.stringify({ total: 1, complete: true })); })));
await tick();
const refresh = refreshImportsCount();
await tick();
held[3](reply(0)); await refresh;
out.refreshed = shown();
body(); await poll;
out.afterOldBody = shown();
""")
    assert out["reversed"] == ["2", "2 imports waiting"]
    assert out["refreshed"] == ["", "No imports waiting"]
    assert out["afterOldBody"] == ["", "No imports waiting"]
