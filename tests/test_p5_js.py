import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

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


def test_sse_retries_only_for_a_closed_current_source(tmp_path):
    out = run_js(tmp_path, """
const store = stores.logs;
store.init();
const first = sources[0];
first.readyState = 0; first.onerror();
out.afterConnecting = activeTimers();
first.readyState = 2; first.onerror(); first.onerror();
out.afterClosedTwice = activeTimers();
store.connect();
out.afterReconnect = activeTimers();
first.onerror();
out.afterStaleError = activeTimers();
out.sources = sources.length;
""")
    assert out == {"afterConnecting": 0, "afterClosedTwice": 1, "afterReconnect": 0,
                   "afterStaleError": 0, "sources": 2}


def test_seed_merges_without_duplicates(tmp_path):
    out = run_js(tmp_path, """
const store = stores.logs;
store.entries = [{ created_at: '2026-09-30 12:00:02', instance_name: 'S', level: 'info', message: 'live', _id: 99 }];
store.seed([
  { created_at: '2026-09-30 12:00:02', instance_name: 'S', level: 'info', message: 'live' },
  { created_at: '2026-09-30 12:00:01', instance_name: 'S', level: 'info', message: 'older' },
]);
out.messages = store.entries.map(e => e.message);
""")
    assert out["messages"] == ["live", "older"]


def test_seed_tolerates_a_second_between_live_and_stored_time(tmp_path):
    out = run_js(tmp_path, """
const store = stores.logs;
store.entries = [{ created_at: '2026-09-30 12:00:03', instance_name: 'S', level: 'info', message: 'tick', _id: 99 }];
store.seed([
  { created_at: '2026-09-30 12:00:02', instance_name: 'S', level: 'info', message: 'tick' },
  { created_at: '2026-09-30 11:59:00', instance_name: 'S', level: 'info', message: 'tick' },
]);
out.times = store.entries.map(e => e.created_at);
""")
    assert out["times"] == ["2026-09-30 12:00:03", "2026-09-30 11:59:00"]


def test_api_fetch_sends_a_lost_session_to_the_login_page(tmp_path):
    out = run_js(tmp_path, """
responses.push({ status: 401, ok: false, redirected: false, url: '/api/x' });
try { await apiFetch('/api/x'); out.thrown = false; } catch (err) { out.thrown = isSessionExpired(err); }
out.href = location.href;
location.href = 'http://t/logs?x=1';
responses.push({ status: 200, ok: true, redirected: true, url: 'http://t/login?next=/x' });
try { await apiFetch('/api/y'); out.redirectThrown = false; } catch (err) { out.redirectThrown = isSessionExpired(err); }
""")
    assert out == {"thrown": True, "href": "/login?next=%2Flogs%3Fx%3D1", "redirectThrown": True}


def test_force_run_explains_a_running_search(tmp_path):
    out = run_js(tmp_path, """
responses.push({ status: 409, ok: false, redirected: false, url: '/api', json: async () => ({ detail: 'busy' }) });
await forceRun(1);
out.toasts = toasts;
""")
    assert out["toasts"] == [["Already running — wait for the current run to finish", "info"]]
