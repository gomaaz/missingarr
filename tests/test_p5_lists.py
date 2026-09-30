import html.parser
import json

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


def test_hostile_names_never_reach_script_attributes(client):
    make_instance(name=HOSTILE)
    for path in ("/", "/instances", "/history", "/logs"):
        page = client.get(path).text
        for tag, name, value in script_attributes(page):
            assert "alert(" not in value, (path, tag, name, value)


def test_delete_button_reads_the_name_from_data_attributes(client):
    make_instance(name=HOSTILE)
    buttons = [attrs for tag, attrs in parse(client.get("/instances").text).tags
               if tag == "button" and "data-name" in attrs]
    assert buttons[0]["data-name"] == HOSTILE
    assert buttons[0]["onclick"] == "deleteInstance(this.dataset.id, this.dataset.name)"


def test_logs_page_is_seeded_with_stored_entries(client):
    inst = make_instance(name="Radarr")
    db.activity.insert(inst["id"], "Radarr", "info", "stored before the page opened", "system")
    page = client.get("/logs").text
    seeds = [attrs for tag, attrs in parse(page).tags if tag == "script" and attrs.get("id") == "logs-seed"]
    assert seeds and seeds[0].get("type") == "application/json"
    start = page.index('id="logs-seed">') + len('id="logs-seed">')
    rows = json.loads(page[start:page.index("</script>", start)])
    assert any(r["message"] == "stored before the page opened" for r in rows)
    assert '<option value="Radarr">Radarr</option>' in page
    # Alpine calls init() of an x-data component by itself; x-init would run it twice.
    roots = [attrs for tag, attrs in parse(page).tags if attrs.get("x-data") == "logsTable()"]
    assert roots and "x-init" not in roots[0]


def test_history_filters_are_server_side(client):
    inst = make_instance()
    page = client.get("/history").text
    assert 'value="upgrade"' not in page
    assert '<option value="search_upgrades">Upgrade</option>' in page
    assert f'<option value="{inst["id"]}">Sonarr</option>' in page
    assert "/api/history/items?" in page and "X-Total-Count" in page
