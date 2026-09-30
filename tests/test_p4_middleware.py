import pytest
from fastapi.testclient import TestClient

from backend import database, db
from backend.auth import is_same_origin_request
from backend.config import settings

PASSWORD = "correct horse battery"
HOST = "missingarr.test"


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
        yield test_client
    crypto._reset_cache()
    main.app.middleware_stack = None


def login(client, remember=False, next_path="/"):
    data = {"username": "admin", "password": PASSWORD, "next": next_path}
    if remember:
        data["remember"] = "true"
    return client.post("/login", data=data, follow_redirects=False)


def make_instance(**fields):
    data = {"name": "Sonarr", "type": "sonarr", "url": "http://127.0.0.1:9",
            "api_key": "SUPERSECRETKEY1234567890", "enabled": False}
    data.update(fields)
    return db.instances.create(data)


def test_api_without_session_gets_401_json(client):
    resp = client.get("/api/instances", follow_redirects=False)
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Not authenticated"}


def test_htmx_without_session_gets_401_with_hx_redirect(client):
    resp = client.get("/history", headers={"HX-Request": "true"}, follow_redirects=False)
    assert resp.status_code == 401
    assert resp.headers["HX-Redirect"] == "/login?next=/history"


def test_card_polling_without_session_is_sent_to_the_login_page(client):
    # The dashboard card polls this through htmx every 5 s.
    resp = client.get("/api/instances/1/status", follow_redirects=False,
                      headers={"HX-Request": "true", "HX-Current-URL": f"http://{HOST}/?view=all"})
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Not authenticated"}
    assert resp.headers["HX-Redirect"] == "/login?next=/%3Fview%3Dall"


def test_odd_hx_current_url_falls_back_to_root(client):
    resp = client.get("/api/instances/1/status", follow_redirects=False,
                      headers={"HX-Request": "true", "HX-Current-URL": "javascript:alert(1)"})
    assert resp.headers["HX-Redirect"] == "/login?next=/"
    # Only the path is used; a foreign host in the header never becomes the target.
    resp = client.get("/api/instances/1/status", follow_redirects=False,
                      headers={"HX-Request": "true", "HX-Current-URL": "http://evil.example/x"})
    assert resp.headers["HX-Redirect"] == "/login?next=/x"


def test_page_without_session_is_redirected(client):
    resp = client.get("/history", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/login?next=/history"


def test_public_paths_stay_public(client):
    assert client.get("/api/health").status_code == 200
    assert client.get("/login").status_code == 200


@pytest.mark.parametrize("headers", [
    {"Origin": "http://evil.example"},
    {"Origin": "null"},
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},
    {"Sec-Fetch-Site": "same-site", "Origin": f"http://{HOST}"},
    {"Origin": f"https://{HOST}"},
])
def test_cross_site_writes_are_blocked(client, headers):
    login(client)
    resp = client.post("/api/instances/1/trigger?skill=search_missing", headers=headers)
    assert resp.status_code == 403
    assert resp.json() == {"detail": "Cross-site request blocked"}


@pytest.mark.parametrize("headers", [
    {},
    {"Sec-Fetch-Site": "same-origin"},
    {"Sec-Fetch-Site": "none"},
    {"Origin": f"http://{HOST}"},
])
def test_same_origin_and_non_browser_writes_pass(client, headers):
    login(client)
    resp = client.post("/api/instances/999/trigger?skill=search_missing", headers=headers)
    assert resp.status_code == 404  # reached the route: no such instance


def test_reads_are_not_checked(client):
    login(client)
    assert client.get("/api/instances", headers={"Origin": "http://evil.example"}).status_code == 200


def test_login_from_another_site_is_blocked(client):
    resp = client.post("/login", data={"username": "admin", "password": PASSWORD},
                       headers={"Origin": "http://evil.example"}, follow_redirects=False)
    assert resp.status_code == 403


def test_origin_check_unit():
    assert is_same_origin_request({"host": "a:8000", "origin": "http://a:8000"}, scheme="http")
    assert not is_same_origin_request({"host": "a:8000", "origin": "http://a:9000"}, scheme="http")
    assert is_same_origin_request({"host": "a:8000"}, scheme="http")


def test_origin_check_compares_the_scheme():
    # Same host and port, other scheme: a different origin (same-origin policy).
    assert not is_same_origin_request({"host": "a:8000", "origin": "https://a:8000"}, scheme="http")
    assert not is_same_origin_request({"host": "a:8000", "origin": "http://a:8000"}, scheme="https")
    assert is_same_origin_request({"host": "a", "origin": "HTTPS://A"}, scheme="https")
