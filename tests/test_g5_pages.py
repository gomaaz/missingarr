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
NODE = shutil.which("node")
ROOT = Path(__file__).resolve().parent.parent


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
    data = {"name": "Radarr", "type": "radarr", "url": "http://127.0.0.1:9",
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


def run_node(tmp_path, script, body):
    case = tmp_path / "case.js"
    case.write_text(
        "const vm = require('vm');\n"
        "const calls = []; const toasts = [];\n"
        "globalThis.toast = (m, t) => toasts.push([m, t]);\n"
        "globalThis.isSessionExpired = () => false;\n"
        "globalThis.confirm = () => true;\n"
        "globalThis.apiFetch = async (url, options) => { calls.push([url, (options || {}).method || 'GET']);"
        " return { ok: true, headers: { get: () => '3' }, json: async () => [{ id: 1 }] }; };\n"
        f"vm.runInThisContext({json.dumps(script)});\n"
        "const out = {};\n(async () => {\n" + body +
        "\nconsole.log(JSON.stringify(out));\n})().catch(e => { console.error(e); process.exit(1); });\n")
    result = subprocess.run([NODE, str(case)], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_card_shows_the_mode(client):
    dry = make_instance(name="Dry", checked_search="dry_run")
    active = make_instance(name="Live", checked_search="active")
    off = make_instance(name="Off")
    assert ">dry run</span>" in client.get(f"/instances/{dry['id']}/card").text
    assert ">checked</span>" in client.get(f"/instances/{active['id']}/card").text
    assert "data-checked-badge" not in client.get(f"/instances/{off['id']}/card").text


def test_history_knows_the_new_item_statuses(client):
    page = client.get("/history").text
    assert "grabbed: 'geladen'" in page
    assert "no_hit: 'kein sauberer Treffer'" in page


def test_help_explains_checked_search(client):
    page = client.get("/help").text
    assert "Checked search (pre-filter)" in page
    assert "Checked Search Year Tolerance" in page


def test_prefilter_page_lists_counters_and_reset_buttons(client):
    inst = make_instance(name=HOSTILE, checked_search="dry_run")
    make_instance(name="Off instance")
    db.checked_search_log.insert({"instance_id": inst["id"], "mode": "dry_run", "skill": "search_missing",
                                  "title": "A", "outcome": "would_grab", "cache_key": "mov:1"})
    page = client.get("/checked-search").text
    assert 'href="/checked-search" class="nav-link active"' in page
    assert "would grab" in page
    assert "current round only" in page
    assert "profile changed" in page and "settings changed" in page
    assert '<option value="grab_uncertain">' in page
    for tag, name, value in script_attributes(page):
        assert "alert(" not in value, (tag, name, value)
    buttons = [a for t, a in tags(page) if t == "button" and "data-name" in a]
    assert [b["data-name"] for b in buttons] == [HOSTILE]
    assert buttons[0]["@click"] == "resetDryRun(Number($el.dataset.id), $el.dataset.name)"
    links = [a for t, a in tags(page) if t == "a" and a.get(":href") == "csvHref()"]
    assert links and links[0]["hx-boost"] == "false"


def test_counters_show_the_current_round(client):
    inst = make_instance(checked_search="dry_run")
    entry = {"instance_id": inst["id"], "mode": "dry_run", "skill": "search_missing", "cache_key": "mov:1"}
    db.checked_search_log.insert({**entry, "title": "A", "outcome": "no_clean_hit", "dry_run_round": 0})
    db.instances.reset_dry_run(inst["id"])
    db.checked_search_log.insert({**entry, "title": "A", "outcome": "would_grab", "dry_run_round": 1})
    page = client.get("/checked-search").text
    assert "would grab" in page
    assert "no clean hit" not in page.split("Download CSV", 1)[1].split("<template", 1)[0]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_prefilter_component_filters_pages_and_resets(client, tmp_path):
    script = component_script(client.get("/checked-search").text, "function checkedSearchPage()")
    out = run_node(tmp_path, script, """
const page = checkedSearchPage();
page.instance = '2'; page.mode = 'dry_run'; page.onlyDifferences = true; page.search = ' Thing ';
await page.load();
out.list = calls[0];
out.total = page.total;
out.csv = page.csvHref();
await page.resetDryRun(2, 'Radarr');
out.reset = calls[1];
""")
    assert out["list"] == ["/api/checked-search?instance_id=2&mode=dry_run&q=Thing&only_differences=true"
                           "&current_round=true&limit=50&offset=0", "GET"]
    assert out["total"] == 3
    assert out["csv"] == ("/api/checked-search.csv?instance_id=2&mode=dry_run&q=Thing&only_differences=true"
                          "&current_round=true")
    assert out["reset"] == ["/api/instances/2/checked-search/reset-dry-run", "POST"]
