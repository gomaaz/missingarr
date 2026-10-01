import csv
import io

import pytest
from fastapi.testclient import TestClient

from backend import database, db
from backend.checked_search.settings import CheckedSearchSettings
from backend.config import settings
from backend.models.instance import CHECKED_SEARCH_MODE_MESSAGE

PASSWORD = "correct horse battery"
HOST = "missingarr.test"
SECRET = "SUPERSECRETKEY1234567890"


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


class FakeOrchestrator:
    def __init__(self):
        self.calls = []
        self.stops_in_time = True

    def get_agent_state(self, instance_id):
        return {}

    def forget_instance(self, instance_id, wait_seconds=15.0):
        self.calls.append(("forget", instance_id))
        return self.stops_in_time

    def start_agent(self, instance_id):
        self.calls.append(("start", instance_id))

    def stop_agent(self, instance_id, abort_running=True, wait_seconds=0.0):
        self.calls.append(("stop", instance_id))

    def reload_agent(self, instance_id):
        self.calls.append(("reload", instance_id))

    def refresh_config(self, instance_id):
        self.calls.append(("refresh", instance_id))

    def stop_all(self):
        pass


@pytest.fixture
def api(client):
    client.app.state.orchestrator.stop_all()
    client.app.state.orchestrator = FakeOrchestrator()
    client.post("/login", data={"username": "admin", "password": PASSWORD, "next": "/"})
    return client


def body(**fields):
    data = {"name": "Sonarr", "type": "sonarr", "url": "http://sonarr:8989", "api_key": SECRET, "enabled": False}
    data.update(fields)
    return data


def log(instance_id, title, outcome="would_grab", mode="dry_run", **extra):
    entry = {"instance_id": instance_id, "run_id": None, "mode": mode, "skill": "search_missing", "arr_id": 1,
             "cache_key": f"mov:{title}", "title": title, "outcome": outcome}
    entry.update(extra)
    db.checked_search_log.insert(entry)


# ── Instances ────────────────────────────────────────────────────────────────

def test_create_with_checked_search_returns_every_setting(api):
    resp = api.post("/api/instances", json=body(type="radarr", checked_search="dry_run",
                                                checked_search_settings={"year_tolerance": 2}))
    assert resp.status_code == 201
    data = resp.json()
    assert data["checked_search"] == "dry_run"
    assert data["checked_search_settings"]["year_tolerance"] == 2
    assert data["checked_search_settings"]["release_timeout_seconds"] == 120
    assert data["dry_run_round_started_at"]
    assert data["search_again_after_profile_change"] is True


def test_search_again_after_profile_change_is_saved_and_kept(api):
    instance_id = api.post("/api/instances", json=body(search_again_after_profile_change=False)).json()["id"]
    assert api.get(f"/api/instances/{instance_id}").json()["search_again_after_profile_change"] is False
    assert api.put(f"/api/instances/{instance_id}", json=body(api_key="", interval_minutes=30)).status_code == 200
    assert db.instances.get_by_id(instance_id)["search_again_after_profile_change"] == 0


def test_delete_during_a_checked_search_explains_the_wait(api):
    checked = api.post("/api/instances", json=body(checked_search="dry_run")).json()["id"]
    plain = api.post("/api/instances", json=body(name="Plain")).json()["id"]
    api.app.state.orchestrator.stops_in_time = False
    resp = api.delete(f"/api/instances/{checked}")
    assert resp.status_code == 409
    assert "release search" in resp.json()["detail"]
    resp = api.delete(f"/api/instances/{plain}")
    assert resp.status_code == 409
    assert "release search" not in resp.json()["detail"]


def test_sonarr_pack_modes_are_refused_with_422(api):
    resp = api.post("/api/instances", json=body(missing_mode="season_packs", checked_search="dry_run"))
    assert resp.status_code == 422
    assert CHECKED_SEARCH_MODE_MESSAGE in resp.json()["detail"][0]["msg"]


def test_update_without_checked_fields_checks_the_stored_mode(api):
    instance_id = api.post("/api/instances", json=body(checked_search="active")).json()["id"]
    resp = api.put(f"/api/instances/{instance_id}", json=body(api_key="", missing_mode="show_batch"))
    assert resp.status_code == 422
    assert resp.json()["detail"] == CHECKED_SEARCH_MODE_MESSAGE
    assert db.instances.get_by_id(instance_id)["missing_mode"] == "episode"


def test_update_without_checked_fields_keeps_them(api):
    instance_id = api.post("/api/instances", json=body(
        checked_search="active", checked_search_settings={"check_suffix": False})).json()["id"]
    assert api.put(f"/api/instances/{instance_id}", json=body(api_key="", interval_minutes=30)).status_code == 200
    stored = db.instances.get_by_id(instance_id)
    assert stored["checked_search"] == "active"
    assert stored["checked_search_settings"]["check_suffix"] is False


def test_switching_off_frees_the_pack_modes(api):
    instance_id = api.post("/api/instances", json=body(checked_search="active")).json()["id"]
    resp = api.put(f"/api/instances/{instance_id}",
                   json=body(api_key="", checked_search="off", missing_mode="season_packs"))
    assert resp.status_code == 200


def test_reset_dry_run(api):
    instance_id = api.post("/api/instances", json=body(type="radarr", checked_search="dry_run")).json()["id"]
    database_value = "2026-01-01 00:00:00.000"
    with database.get_db() as conn:
        conn.execute("UPDATE instances SET dry_run_round_started_at=?", (database_value,))
    resp = api.post(f"/api/instances/{instance_id}/checked-search/reset-dry-run")
    assert resp.status_code == 200
    assert resp.json()["status"] == "reset"
    assert resp.json()["dry_run_round_started_at"] > database_value
    assert api.post("/api/instances/999/checked-search/reset-dry-run").status_code == 404


def test_reset_needs_a_session_and_the_same_origin(client):
    assert client.post("/api/instances/1/checked-search/reset-dry-run").status_code == 401
    client.post("/login", data={"username": "admin", "password": PASSWORD, "next": "/"})
    resp = client.post("/api/instances/1/checked-search/reset-dry-run", headers={"Origin": "http://evil.example"})
    assert resp.status_code == 403


# ── Log list and CSV ─────────────────────────────────────────────────────────

def test_list_filters_and_counts(api):
    instance_id = api.post("/api/instances", json=body(type="radarr")).json()["id"]
    log(instance_id, "Same", arr_pick="A", pick={"title": "A"})
    log(instance_id, "Different", arr_pick="A", pick={"title": "B"})
    log(instance_id, "Grabbed", outcome="grabbed", mode="active")
    resp = api.get("/api/checked-search", params={"limit": 2})
    assert resp.status_code == 200
    assert resp.headers["X-Total-Count"] == "3"
    assert len(resp.json()) == 2
    assert [r["title"] for r in api.get("/api/checked-search", params={"only_differences": "true"}).json()] == [
        "Different"]
    assert api.get("/api/checked-search", params={"mode": "active"}).headers["X-Total-Count"] == "1"
    assert api.get("/api/checked-search", params={"q": "same"}).headers["X-Total-Count"] == "1"


def test_list_and_csv_can_show_the_current_round_only(api):
    instance_id = api.post("/api/instances", json=body(type="radarr", checked_search="dry_run")).json()["id"]
    log(instance_id, "Old round", dry_run_round=0)
    assert api.post(f"/api/instances/{instance_id}/checked-search/reset-dry-run").status_code == 200
    log(instance_id, "New round", dry_run_round=1)
    assert api.get("/api/checked-search").headers["X-Total-Count"] == "2"
    resp = api.get("/api/checked-search", params={"current_round": "true"})
    assert [r["title"] for r in resp.json()] == ["New round"]
    csv_text = api.get("/api/checked-search.csv", params={"current_round": "true"}).text
    assert "New round" in csv_text and "Old round" not in csv_text


def test_list_marks_rows_checked_under_other_rule_settings(api):
    instance_id = api.post("/api/instances", json=body(type="radarr", checked_search="dry_run")).json()["id"]
    current = CheckedSearchSettings().rules_fingerprint("radarr")
    log(instance_id, "Same settings", settings_fingerprint=current, dry_run_round=1)
    log(instance_id, "Before the change", settings_fingerprint="0000000000000000", dry_run_round=1)
    log(instance_id, "Unknown", dry_run_round=1)
    rows = {r["title"]: r["settings_changed"] for r in api.get("/api/checked-search").json()}
    assert rows == {"Same settings": False, "Before the change": True, "Unknown": False}
    assert api.put(f"/api/instances/{instance_id}", json=body(
        type="radarr", api_key="", checked_search="dry_run",
        checked_search_settings={"year_tolerance": 2})).status_code == 200
    rows = {r["title"]: r["settings_changed"] for r in api.get("/api/checked-search").json()}
    assert rows == {"Same settings": True, "Before the change": True, "Unknown": False}
    assert api.get("/api/checked-search", params={"outcome": "grab_uncertain"}).status_code == 200


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"offset": -1}, {"mode": "maybe"},
                                    {"outcome": "lost"}, {"q": "x" * 201}])
def test_bad_parameters_are_refused(api, params):
    assert api.get("/api/checked-search", params=params).status_code == 422


def test_csv_download_uses_the_filter(api):
    instance_id = api.post("/api/instances", json=body(type="radarr")).json()["id"]
    log(instance_id, "Keep", outcome="no_clean_hit", candidates=[
        {"title": "Rel.2020", "indexer": "Idx", "score": 5, "size": 1, "quality": "WEBDL-1080p",
         "verdict": "reject", "reasons": ["year"], "notes": [], "chosen": False, "arr_choice": True}])
    log(instance_id, "Drop", outcome="would_grab")
    resp = api.get("/api/checked-search.csv", params={"outcome": "no_clean_hit"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert 'attachment; filename="checked-search.csv"' == resp.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(resp.text)))
    assert rows[0][:4] == ["time", "instance", "mode", "title"]
    assert [r[3] for r in rows[1:]] == ["Keep"]


def test_list_needs_a_session(client):
    assert client.get("/api/checked-search").status_code == 401
    assert client.get("/api/checked-search.csv").status_code == 401
