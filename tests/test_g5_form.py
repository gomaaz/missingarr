import html.parser
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import database, db
from backend.checked_search.settings import SETTING_BOUNDS, CheckedSearchSettings
from backend.config import settings
from backend.tooltips import TOOLTIPS

PASSWORD = "correct horse battery"
HOST = "missingarr.test"
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


def by_id(text):
    return {attrs["id"]: (tag, attrs) for tag, attrs in tags(text) if "id" in attrs}


def component_script(page, marker):
    at = page.index(marker)
    start = page.rindex("<script>", 0, at) + len("<script>")
    return page[start:page.index("</script>", at)]


def run_node(tmp_path, script, body):
    case = tmp_path / "case.js"
    case.write_text(
        "const vm = require('vm');\n"
        f"vm.runInThisContext({json.dumps(script)});\n"
        "const out = {};\n(async () => {\n" + body +
        "\nconsole.log(JSON.stringify(out));\n})().catch(e => { console.error(e); process.exit(1); });\n")
    result = subprocess.run([NODE, str(case)], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_every_setting_has_an_english_tooltip():
    for field in CheckedSearchSettings.model_fields:
        assert TOOLTIPS.get(f"cs_{field}"), field
    assert "Dry run" in TOOLTIPS["checked_search"] and "Active" in TOOLTIPS["checked_search"]
    assert "profile" in TOOLTIPS["search_again_after_profile_change"]


@pytest.mark.parametrize("stored", [True, False])
def test_search_again_after_profile_changes_is_in_the_form(client, stored):
    inst = make_instance(search_again_after_profile_change=stored)
    page = client.get(f"/instances/{inst['id']}/edit").text
    tag, attrs = by_id(page)["search_again_after_profile_change"]
    assert (tag, attrs["type"]) == ("input", "checkbox")
    assert ("checked" in attrs) is stored
    icons = [a["data-tooltip"] for t, a in tags(page) if t == "span" and "tooltip-icon" in a.get("class", "")]
    assert TOOLTIPS["search_again_after_profile_change"] in icons
    assert "search_again_after_profile_change: form.search_again_after_profile_change.checked" in page


@pytest.mark.parametrize("path_kind", ["new", "edit"])
def test_form_has_the_checked_search_section_with_info_icons(client, path_kind):
    path = "/instances/new" if path_kind == "new" else f"/instances/{make_instance()['id']}/edit"
    page = client.get(path).text
    assert "Checked search" in page
    elements = by_id(page)
    assert {a["value"] for t, a in tags(page) if t == "option" and a.get("value") in ("off", "dry_run", "active")} \
        == {"off", "dry_run", "active"}
    for field in CheckedSearchSettings.model_fields:
        tag, attrs = elements[f"cs_{field}"]
        assert attrs["data-cs-field"] == field
        if field in SETTING_BOUNDS:
            assert (attrs["min"], attrs["max"]) == tuple(str(v) for v in SETTING_BOUNDS[field])
    icons = [a["data-tooltip"] for t, a in tags(page) if t == "span" and "tooltip-icon" in a.get("class", "")]
    assert TOOLTIPS["checked_search"] in icons
    for field in CheckedSearchSettings.model_fields:
        assert TOOLTIPS[f"cs_{field}"] in icons, field
    assert page.count('id="cs_skip_existing_file"') == 1


def test_edit_form_shows_the_stored_settings(client):
    inst = make_instance(checked_search="dry_run", checked_search_settings={"year_tolerance": 3,
                                                                              "country_codes": ["US", "DE"]})
    elements = by_id(client.get(f"/instances/{inst['id']}/edit").text)
    assert elements["cs_year_tolerance"][1]["value"] == "3"
    assert elements["cs_country_codes"][1]["value"] == "US, DE"
    assert elements["cs_release_timeout_seconds"][1]["value"] == "120"


def test_pack_modes_are_locked_while_checked_search_is_on(client):
    page = client.get("/instances/new").text
    locked = {a["value"] for t, a in tags(page) if t == "option" and a.get(":disabled") == "packsLocked()"}
    assert locked == {"smart", "season_packs", "show_batch"}
    assert "Checked search works with single episodes only." in page


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_form_sends_every_setting_with_its_type(client, tmp_path):
    script = component_script(client.get("/instances/new").text, "function checkedSearchSettings(form)")
    script = script[:script.index("document.getElementById('instance-form')")]
    out = run_node(tmp_path, script, """
const fields = [
  { dataset: { csField: 'year_tolerance', csKind: 'int' }, value: '2' },
  { dataset: { csField: 'prefix_match', csKind: 'bool' }, checked: false },
  { dataset: { csField: 'country_codes', csKind: 'list' }, value: 'US, de;  CO' },
];
out.settings = checkedSearchSettings({ querySelectorAll: () => fields });
""")
    assert out["settings"] == {"year_tolerance": 2, "prefix_match": False, "country_codes": ["US", "de", "CO"]}
