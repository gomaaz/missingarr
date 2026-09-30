import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import database, db
from backend.api import history as history_api
from backend.api import searched as searched_api
from backend.config import settings
from backend.db import history


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "missingarr.db"
    monkeypatch.setattr(settings, "database_url", str(path))
    monkeypatch.setattr(database, "_cached_secret_key", None)
    database.init_db()
    return path


def make_instance(**fields):
    data = {"name": "Sonarr", "type": "sonarr", "url": "http://127.0.0.1:9", "api_key": "k" * 32}
    data.update(fields)
    return db.instances.create(data)


@pytest.fixture
def client(db_path):
    app = FastAPI()
    app.include_router(history_api.router, prefix="/api")
    app.include_router(searched_api.router, prefix="/api")
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize("url", [
    "/api/history?limit=-1", "/api/history?offset=-3", "/api/history?limit=201",
    "/api/history/items?limit=-1", "/api/history/items?limit=1001", "/api/history/items?offset=-1",
    "/api/history/items?item_type=upgrade", "/api/history/items?skill=health_check",
    "/api/searched?limit=-1", "/api/searched?limit=501", "/api/searched?offset=-1",
])
def test_out_of_range_parameters_are_rejected(client, url):
    assert client.get(url).status_code == 422


def test_items_endpoint_filters_pages_and_reports_the_total(client):
    inst = make_instance()
    missing = history.start_run(inst["id"], inst["name"], "search_missing")
    upgrades = history.start_run(inst["id"], inst["name"], "search_upgrades")
    for i in range(7):
        history.record_submission(missing, inst["id"], f"Show S01E0{i}", i, "episode", f"ep:{i}", i)
    history.record_submission(upgrades, inst["id"], "Show Season 2", 5, "season", "upg:sea:5:2", 99)

    resp = client.get("/api/history/items", params={"limit": 3, "offset": 3, "skill": "search_missing"})
    assert resp.status_code == 200
    assert resp.headers["X-Total-Count"] == "7"
    assert len(resp.json()) == 3

    resp = client.get("/api/history/items", params={"skill": "search_upgrades"})
    assert resp.headers["X-Total-Count"] == "1"
    assert resp.json()[0]["title"] == "Show Season 2"

    resp = client.get("/api/history/items", params={"q": "s01e03", "instance_id": inst["id"]})
    assert resp.headers["X-Total-Count"] == "1"


def test_clear_reports_deleted_and_kept_runs(client):
    inst = make_instance()
    history.start_run(inst["id"], inst["name"], "search_missing")  # running, kept
    done = history.start_run(inst["id"], inst["name"], "search_missing")
    history.finish_run(done, 0, 0, "success")
    assert client.delete("/api/history").json() == {"status": "cleared", "deleted": 1, "kept_open": 1}


def test_searched_endpoint_reports_the_total(client):
    inst = make_instance()
    for i in range(4):
        db.searched.add(inst["id"], f"ep:{i}", f"E{i}", "episode")
    resp = client.get("/api/searched", params={"instance_id": inst["id"], "limit": 2, "offset": 2})
    assert resp.headers["X-Total-Count"] == "4"
    assert len(resp.json()) == 2
