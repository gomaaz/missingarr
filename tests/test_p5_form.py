import html.parser

import pytest
from fastapi.testclient import TestClient

from backend import database, db
from backend.config import settings
from backend.models.instance import FIELD_BOUNDS
from backend.tooltips import TOOLTIPS

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


def inputs(page):
    return {attrs["id"]: attrs for tag, attrs in parse(page).tags if tag == "input" and "id" in attrs}


@pytest.mark.parametrize("path_kind", ["new", "edit"])
def test_number_fields_carry_the_model_bounds(client, path_kind):
    path = "/instances/new" if path_kind == "new" else f"/instances/{make_instance()['id']}/edit"
    fields = inputs(client.get(path).text)
    for name, (low, high) in FIELD_BOUNDS.items():
        assert fields[name]["min"] == str(low), name
        assert fields[name]["max"] == str(high), name


def test_edit_form_explains_the_stored_key_and_keeps_it_secret(client):
    inst = make_instance()
    page = client.get(f"/instances/{inst['id']}/edit").text
    assert "SUPERSECRETKEY" not in page
    assert "A key is stored" in page
    forms = [attrs for tag, attrs in parse(page).tags if tag == "form" and attrs.get("id") == "instance-form"]
    assert forms[0]["data-original-url"] == "http://127.0.0.1:9"


@pytest.mark.parametrize("path_kind", ["new", "edit"])
def test_instance_form_is_not_boosted(client, path_kind):
    # <body hx-boost="true"> also boosts forms, and htmx does not look at
    # defaultPrevented: every save sent a second request, a GET to the page
    # with all fields in the query string, the API key included. It ended up
    # in the address bar, the browser history and the access log.
    path = "/instances/new" if path_kind == "new" else f"/instances/{make_instance()['id']}/edit"
    forms = [attrs for tag, attrs in parse(client.get(path).text).tags
             if tag == "form" and attrs.get("id") == "instance-form"]
    assert forms[0]["hx-boost"] == "false"


def test_tooltips_name_the_limits():
    assert "10080" in TOOLTIPS["interval_minutes"]
    assert "at least 1" in TOOLTIPS["missing_per_run"]
    assert "Radarr only" not in TOOLTIPS["search_upgrades_enabled"]
