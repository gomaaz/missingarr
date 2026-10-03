from datetime import datetime, timedelta, timezone

import pytest
import requests
from fastapi.testclient import TestClient

from backend import database, db
from backend.api.imports import queue_link
from backend.config import settings
from backend.imports import service
from tests.imports_fake_arr import (
    API_KEY, COMMAND, INDEXER_KEY, MANUAL_IMPORT, QUEUE, SONARR_BY_ID, FakeArr, config, episode, grab_record,
    http_error, queue_record, refused, series, sonarr_item, timed_out,
)

PASSWORD = "correct horse battery"
HOST = "missingarr.test"
SECRET = "SUPERSECRETKEY1234567890"
NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
STAMP = "2026-10-02T12:00:00Z"
TITLE = "Some.Show.S01E01.German.1080p.WEB.h264-GRP"
TITLE2 = "Some.Show.S01E02.German.1080p.WEB.h264-GRP"
PATH1 = "/downloads/complete/Some.Show.S01E01/Some.Show.S01E01.mkv"
PATH2 = "/downloads/complete/Some.Show.S01E02/Some.Show.S01E02.mkv"
SHOW = series(10, "Some Show", 2020)
EP1 = episode(3, 10, 1, 1, "2026-09-30T20:00:00Z")
EP2 = episode(4, 10, 1, 2, "2026-09-30T21:00:00Z")
KEY = "0123456789abcdef"


class Clock:
    """Stands in for service.clock and service.utcnow; advance() moves both."""

    def __init__(self):
        self.mono = 1000.0
        self.utc = NOW

    def monotonic(self):
        return self.mono

    def now(self):
        return self.utc

    def advance(self, seconds):
        self.mono += seconds
        self.utc += timedelta(seconds=seconds)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    fake_clock = Clock()
    service.reset_caches()
    monkeypatch.setattr(service, "clock", fake_clock.monotonic)
    monkeypatch.setattr(service, "utcnow", fake_clock.now)
    yield fake_clock
    service.reset_caches()


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
    """Hands each route the test's FakeArr of that instance, with the
    configuration the route read from the database (like detached_agent).
    The agent methods let the instances API edit, switch and delete."""

    def __init__(self, fakes):
        self.fakes = fakes
        self.detached = []

    def detached_agent(self, config):
        self.detached.append(config["id"])
        fake = self.fakes[config["id"]]
        fake.config = config
        return fake

    def get_agent_state(self, instance_id):
        return {}

    def reload_agent(self, instance_id):
        pass

    def start_agent(self, instance_id):
        pass

    def stop_agent(self, instance_id, abort_running=True, wait_seconds=0.0):
        pass

    def forget_instance(self, instance_id, wait_seconds=15.0):
        return True

    def stop_all(self):
        pass


@pytest.fixture
def orchestrator(client):
    client.app.state.orchestrator.stop_all()
    fake_orchestrator = FakeOrchestrator({})
    client.app.state.orchestrator = fake_orchestrator
    return fake_orchestrator


@pytest.fixture
def api(client, orchestrator):
    client.post("/login", data={"username": "admin", "password": PASSWORD, "next": "/"})
    return client


def sonarr_parse(title, number):
    return {"title": title, "series": {"id": 10, "title": "Some Show"},
            "parsedEpisodeInfo": {"seriesTitle": "Some Show", "seasonNumber": 1, "episodeNumbers": [number],
                                  "absoluteEpisodeNumbers": []}}


def add_instance(orchestrator, name="Sonarr", arr_type="sonarr", enabled=True, **fake_fields):
    """An instance in the database and its FakeArr. Sonarr holds back dl-1
    and dl-2 unless the test says otherwise."""
    inst = db.instances.create({"name": name, "type": arr_type, "url": "http://127.0.0.1:9", "api_key": SECRET,
                                "enabled": enabled})
    data = dict(
        queue=[queue_record(11, "dl-1", TITLE, series_id=10, episode_id=3, season=1),
               queue_record(21, "dl-2", TITLE2, series_id=10, episode_id=4, season=1, added="2026-10-02T11:00:00Z")],
        manual_imports={"dl-1": [sonarr_item(PATH1, SHOW, 1, [EP1])],
                        "dl-2": [sonarr_item(PATH2, SHOW, 1, [EP2], folder="Some.Show.S01E02.German.1080p")]},
        parses={TITLE: sonarr_parse(TITLE, 1), TITLE2: sonarr_parse(TITLE2, 2)},
        series_list=[SHOW], episodes=[EP1, EP2],
        history={"dl-1": [grab_record("dl-1")], "dl-2": [grab_record("dl-2")]},
    ) if arr_type == "sonarr" else {}
    data.update(fake_fields)
    fake = FakeArr(config(inst["id"], arr_type), **data)
    orchestrator.fakes[inst["id"]] = fake
    return inst["id"], fake


def assert_no_secret(text):
    for secret in (SECRET, API_KEY, INDEXER_KEY):
        assert secret not in text


# ── Login and cross-site check ───────────────────────────────────────────────

REQUESTS = [
    ("GET", "/api/imports", None),
    ("GET", "/api/imports/count", None),
    ("GET", "/api/imports/1/proposal?download_id=dl-1", None),
    ("POST", "/api/imports/1/import", {"download_id": "dl-1", "proposal_key": KEY}),
    ("GET", "/api/imports/1/commands/500", None),
    ("POST", "/api/imports/1/discard", {"download_id": "dl-1", "blocklist": True}),
]


@pytest.mark.parametrize("method,path,body", REQUESTS)
def test_every_endpoint_needs_a_session(client, orchestrator, method, path, body):
    instance_id, fake = add_instance(orchestrator)
    assert instance_id == 1
    resp = client.request(method, path, json=body, follow_redirects=False)
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Not authenticated"}
    assert orchestrator.detached == [] and fake.gets == []


def test_imports_page_redirects_without_a_session(client):
    resp = client.get("/imports", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/login?next=/imports"


def test_imports_page_renders_with_a_session(api, orchestrator):
    add_instance(orchestrator)
    resp = api.get("/imports")
    assert resp.status_code == 200
    assert "<title>Imports — " in resp.text
    assert_no_secret(resp.text)
    assert orchestrator.detached == []         # the page itself reads no *arr


@pytest.mark.parametrize("path,body", [REQUESTS[3][1:], REQUESTS[5][1:]])
def test_cross_site_actions_are_blocked(api, orchestrator, path, body):
    _, fake = add_instance(orchestrator)
    resp = api.post(path, json=body, headers={"Origin": "http://evil.example"})
    assert resp.status_code == 403
    assert resp.json() == {"detail": "Cross-site request blocked"}
    assert orchestrator.detached == [] and fake.posts == [] and fake.deletes == []


# ── List and count ───────────────────────────────────────────────────────────

def test_list_shows_every_instance_without_keys(api, orchestrator):
    instance_id, _ = add_instance(orchestrator)
    resp = api.get("/api/imports")
    assert resp.status_code == 200
    common = {"size": 2_000_000_000, "download_client": "SABnzbd", "state": "importBlocked",
              "messages": [SONARR_BY_ID], "movie_id": None, "series_id": 10}
    assert resp.json() == {
        "instances": [{
            "id": instance_id, "name": "Sonarr", "type": "sonarr", "enabled": True,
            "queue_link": {"scheme": "http", "port": 9, "path": "/activity/queue"},
            "error": "", "starting": False,
            "downloads": [
                {"download_id": "dl-1", "title": TITLE, "queue_ids": [11], "added": "2026-10-02T10:00:00Z",
                 "episode_ids": [3], **common},
                {"download_id": "dl-2", "title": TITLE2, "queue_ids": [21], "added": "2026-10-02T11:00:00Z",
                 "episode_ids": [4], **common},
            ],
        }],
        "checked_at": STAMP,
    }
    assert_no_secret(resp.text)


def test_one_app_down_keeps_the_other(api, orchestrator):
    radarr_id, _ = add_instance(orchestrator, "Radarr", "radarr", get_errors={"/api/v3/": refused()})
    sonarr_id, _ = add_instance(orchestrator)
    boxes = api.get("/api/imports").json()["instances"]
    assert [(box["id"], box["error"], len(box["downloads"])) for box in boxes] == [
        (radarr_id, "Cannot connect to instance", 0), (sonarr_id, "", 2)]
    count = api.get("/api/imports/count").json()
    assert (count["total"], count["complete"]) == (2, False)
    assert count["per_instance"] == [
        {"id": radarr_id, "name": "Radarr", "count": None, "error": "Cannot connect to instance", "starting": False},
        {"id": sonarr_id, "name": "Sonarr", "count": 2, "error": "", "starting": False}]


def test_unexpected_errors_stay_in_their_box(api, orchestrator):
    add_instance(orchestrator, get_errors={QUEUE + "$": RuntimeError("boom")})
    [box] = api.get("/api/imports").json()["instances"]
    assert (box["error"], box["downloads"], box["starting"]) == ("Unexpected error (RuntimeError)", [], False)


def test_list_says_when_an_app_has_just_started(api, orchestrator, clock):
    # Right after a start the app's queue is still empty: not "Nothing open." yet.
    _, fake = add_instance(orchestrator, queue=[], system_status={"startTime": "2026-10-02T11:59:30Z"})
    [box] = api.get("/api/imports").json()["instances"]
    assert (box["error"], box["downloads"], box["starting"]) == ("", [], True)
    clock.advance(90)
    [box] = api.get("/api/imports").json()["instances"]
    assert box["starting"] is False
    fake.queue = [queue_record(11, "dl-1", TITLE, series_id=10, episode_id=3, season=1)]
    fake.system_status = {"startTime": "2026-10-02T12:01:00Z"}
    [box] = api.get("/api/imports").json()["instances"]
    assert (len(box["downloads"]), box["starting"]) == (1, False)      # a download: no start note


def test_switched_off_instance_is_never_read(api, orchestrator):
    instance_id, fake = add_instance(orchestrator, enabled=False)
    [box] = api.get("/api/imports").json()["instances"]
    assert (box["enabled"], box["error"], box["downloads"]) == (False, "", [])
    assert api.get("/api/imports/count").json()["per_instance"] == []
    for method, path, body in REQUESTS[2:]:
        resp = api.request(method, path.replace("/1/", f"/{instance_id}/"), json=body)
        if "/commands/" in path:      # no import is followed there: unknown, checked before the switch
            assert (resp.status_code, resp.json()) == (404, {"detail": service.UNKNOWN_COMMAND}), path
        else:
            assert (resp.status_code, resp.json()) == (409, {"detail": service.INSTANCE_OFF}), path
    assert orchestrator.detached == [] and fake.gets == []


@pytest.mark.parametrize("method,path,body", REQUESTS[2:])
def test_unknown_instance_is_404(api, orchestrator, method, path, body):
    resp = api.request(method, path.replace("/1/", "/999/"), json=body)
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Instance not found"}


def test_count_sums_known_numbers_and_takes_the_oldest_time(api, orchestrator, clock):
    first_id, _ = add_instance(orchestrator)
    second_id, second = add_instance(orchestrator, "Sonarr 4K", queue=[],
                                     system_status={"startTime": "2026-10-02T11:59:30Z"})
    assert api.get("/api/imports/count").json() == {
        "total": 2, "complete": False,
        "per_instance": [{"id": first_id, "name": "Sonarr", "count": 2, "error": "", "starting": False},
                         {"id": second_id, "name": "Sonarr 4K", "count": None, "error": "", "starting": True}],
        "checked_at": STAMP}
    clock.advance(30)
    second.system_status = {"startTime": "2026-10-02T11:00:00Z"}
    service.invalidate(second_id)
    answer = api.get("/api/imports/count").json()
    assert (answer["total"], answer["complete"], answer["checked_at"]) == (2, True, STAMP)


def test_count_without_instances(api, orchestrator):
    assert api.get("/api/imports/count").json() == {"total": 0, "complete": True, "per_instance": [],
                                                    "checked_at": STAMP}


# ── Proposal, errors and input ───────────────────────────────────────────────

def test_proposal_has_no_keys_and_no_history_data(api, orchestrator):
    instance_id, _ = add_instance(orchestrator)
    resp = api.get(f"/api/imports/{instance_id}/proposal", params={"download_id": "dl-1"})
    assert resp.status_code == 200
    data = resp.json()
    assert (data["download_id"], data["importable"], data["verdict"]["state"]) == ("dl-1", True, "fits")
    assert_no_secret(resp.text)


def leaky_error():
    response = requests.Response()
    response.status_code = 500
    return requests.exceptions.HTTPError(f"500 for http://127.0.0.1:9/api/v3/manualimport?apikey={SECRET}",
                                         response=response)


@pytest.mark.parametrize("error,status,detail", [
    (timed_out(), 504, "Connection timed out"),
    (refused(), 503, "Cannot connect to instance"),
    (http_error(401), 502, "Invalid API key"),
    (http_error(403), 502, "Invalid API key"),
    (http_error(302), 502, "Instance answered with a redirect (HTTP 302) — check the URL"),
    (leaky_error(), 502, "HTTP 500 from instance"),
    (ValueError("manual import answer is not a list"), 502, "Unexpected answer from instance"),
])
def test_arr_errors_map_to_status_codes(api, orchestrator, error, status, detail):
    instance_id, _ = add_instance(orchestrator, get_errors={"/api/v3/manualimport": error})
    resp = api.get(f"/api/imports/{instance_id}/proposal", params={"download_id": "dl-1"})
    assert resp.status_code == status
    assert resp.json() == {"detail": detail}
    assert_no_secret(resp.text)


def test_a_proposal_without_a_free_slot_is_503(api, orchestrator, monkeypatch):
    # More proposal reads at once than the app gets (a reload in the browser, several tabs): the app is not asked.
    monkeypatch.setattr(service, "PROPOSAL_SLOTS", 0)
    monkeypatch.setattr(service, "MANUAL_IMPORT_TIMEOUT", 0.01)
    instance_id, fake = add_instance(orchestrator)
    resp = api.get(f"/api/imports/{instance_id}/proposal", params={"download_id": "dl-1"})
    assert (resp.status_code, resp.json()) == (503, {"detail": "Too many proposal requests, try again"})
    assert MANUAL_IMPORT not in [path for path, _ in fake.gets]


@pytest.mark.parametrize("method,path,body", [
    ("POST", "/api/imports/1/import", {"download_id": "dl-1", "proposal_key": "NOT-A-KEY"}),
    ("POST", "/api/imports/1/import", {"download_id": "", "proposal_key": KEY}),
    ("POST", "/api/imports/1/discard", {"download_id": "dl-1", "blocklist": "maybe"}),
    ("GET", "/api/imports/1/proposal", None),
])
def test_invalid_input_is_422(api, orchestrator, method, path, body):
    _, fake = add_instance(orchestrator)
    assert api.request(method, path, json=body).status_code == 422
    assert fake.gets == [] and fake.posts == [] and fake.deletes == []


# ── Actions ──────────────────────────────────────────────────────────────────

def test_import_status_and_discard_through_the_api(api, orchestrator):
    instance_id, fake = add_instance(orchestrator)
    base = f"/api/imports/{instance_id}"
    texts = []
    proposal = api.get(f"{base}/proposal", params={"download_id": "dl-1"})
    resp = api.post(f"{base}/import", json={"download_id": "dl-1", "proposal_key": proposal.json()["proposal_key"]})
    assert resp.status_code == 200
    assert resp.json() == {"state": "sent", "command_id": 500, "files": 1, "title": TITLE,
                           "target": "Some Show S01E01", "message": ""}
    assert [path for path, _ in fake.posts] == [COMMAND]
    texts += [proposal.text, resp.text]

    resp = api.get(f"{base}/commands/500")
    assert resp.json() == {"command_id": 500, "state": "running", "status": "queued", "message": ""}
    fake.finish_command(500)
    fake.imported("dl-1")
    resp = api.get(f"{base}/commands/500")
    assert resp.json() == {"command_id": 500, "state": "imported", "status": "completed", "message": "Imported"}
    texts.append(resp.text)

    resp = api.post(f"{base}/discard", json={"download_id": "dl-2", "blocklist": False})
    assert resp.status_code == 200
    assert resp.json() == {"download_id": "dl-2", "title": TITLE2, "blocklist": False, "queue_id": 21}
    assert fake.deletes == [(f"{QUEUE}/21", {"removeFromClient": "true", "blocklist": "false",
                                             "skipRedownload": "false", "changeCategory": "false"})]
    texts.append(resp.text)

    assert fake.logged == [
        ("info", "imports", f"Import sent — '{TITLE}' → Some Show S01E01 (command 500, 1 file(s))"),
        ("info", "imports", f"Import done — '{TITLE}' imported (command 500)"),
        ("info", "imports", f"Discarded — '{TITLE2}' (not blocklisted, no new search)"),
    ]
    assert api.get("/api/imports").json()["instances"][0]["downloads"] == []
    for text in texts:
        assert_no_secret(text)


def test_refused_imports_are_409(api, orchestrator):
    instance_id, fake = add_instance(orchestrator)
    base = f"/api/imports/{instance_id}"
    resp = api.post(f"{base}/import", json={"download_id": "dl-1", "proposal_key": KEY})
    assert (resp.status_code, resp.json()) == (409, {"detail": service.PROPOSAL_CHANGED})
    resp = api.post(f"{base}/import", json={"download_id": "dl-9", "proposal_key": KEY})
    assert (resp.status_code, resp.json()) == (409, {"detail": service.ALREADY_HANDLED})
    resp = api.post(f"{base}/discard", json={"download_id": "dl-9"})
    assert (resp.status_code, resp.json()) == (409, {"detail": service.ALREADY_HANDLED})
    assert fake.posts == [] and fake.deletes == [] and fake.logged == []


def send_import(api, base, download_id="dl-1"):
    key = api.get(f"{base}/proposal", params={"download_id": download_id}).json()["proposal_key"]
    return api.post(f"{base}/import", json={"download_id": download_id, "proposal_key": key})


def test_only_commands_sent_here_are_answered(api, orchestrator):
    instance_id, fake = add_instance(orchestrator)
    base = f"/api/imports/{instance_id}"
    fake.commands[12345] = {"id": 12345, "name": "ManualImport", "status": "failed",
                            "exception": f"GET http://127.0.0.1:9/api?apikey={SECRET} failed"}
    resp = api.get(f"{base}/commands/12345")
    assert (resp.status_code, resp.json()) == (404, {"detail": service.UNKNOWN_COMMAND})
    assert fake.gets == []                                  # the app is not even asked
    assert send_import(api, base).status_code == 200
    del fake.commands[500]                                   # the app restarted and forgot it
    resp = api.get(f"{base}/commands/500")
    assert resp.json() == {"command_id": 500, "state": "unknown", "status": "", "message": service.MSG_GONE}


def test_an_import_whose_answer_got_lost_says_it_may_still_run(api, orchestrator):
    instance_id, fake = add_instance(orchestrator, post_lost=timed_out())
    base = f"/api/imports/{instance_id}"
    resp = send_import(api, base)
    assert resp.status_code == 200
    assert resp.json() == {"state": "uncertain", "command_id": None, "files": 1, "title": TITLE,
                           "target": "Some Show S01E01", "message": service.MSG_UNCERTAIN}
    fake.post_lost = None
    assert (send_import(api, base).status_code, len(fake.posts)) == (409, 1)     # the kept command waits
    resp = api.post(f"{base}/discard", json={"download_id": "dl-1"})
    assert (resp.status_code, resp.json()) == (409, {"detail": service.IMPORT_RUNNING})
    assert fake.deletes == []


def test_switching_the_instance_off_while_the_page_follows_an_import_ends_in_unknown_command(api, orchestrator):
    # The page asks for the command status every 3 s. A switch-off (like an edit or a
    # delete) forgets the import: the status answers the 404 of an unknown command at
    # once, not 409 "switched off" until the page gives up, and the app is not asked.
    instance_id, fake = add_instance(orchestrator)
    base = f"/api/imports/{instance_id}"
    assert send_import(api, base).json()["command_id"] == 500
    assert api.get(f"{base}/commands/500").json()["state"] == "running"
    assert api.post(f"/api/instances/{instance_id}/toggle", params={"enabled": False}).status_code == 200
    resp = api.get(f"{base}/commands/500")
    assert (resp.status_code, resp.json()) == (404, {"detail": service.UNKNOWN_COMMAND})
    assert api.post(f"/api/instances/{instance_id}/toggle", params={"enabled": True}).status_code == 200
    resp = api.get(f"{base}/commands/500")
    assert (resp.status_code, resp.json()) == (404, {"detail": service.UNKNOWN_COMMAND})    # forgotten for good
    assert [path for path, _ in fake.gets].count(f"{COMMAND}/500") == 1


def test_a_post_that_reaches_the_app_late_guards_the_download(api, orchestrator):
    instance_id, fake = add_instance(orchestrator, post_delayed=timed_out())
    base = f"/api/imports/{instance_id}"
    assert send_import(api, base).json()["state"] == "uncertain"
    fake.post_delayed = None
    for resp in (send_import(api, base), api.post(f"{base}/discard", json={"download_id": "dl-1"})):
        assert (resp.status_code, resp.json()) == (409, {"detail": service.IMPORT_MAY_RUN})
    fake.arrive()                                     # the app shows the command now: the usual check
    resp = api.post(f"{base}/discard", json={"download_id": "dl-1"})
    assert (resp.status_code, resp.json()) == (409, {"detail": service.IMPORT_RUNNING})
    assert fake.deletes == [] and len(fake.posts) == 1


def test_an_edit_right_after_the_route_read_the_instance_sends_nothing(api, orchestrator, monkeypatch):
    instance_id, fake = add_instance(orchestrator)
    base = f"/api/imports/{instance_id}"
    key = api.get(f"{base}/proposal", params={"download_id": "dl-1"}).json()["proposal_key"]
    real = db.instances.get_by_id

    def read_then_edited(wanted):
        inst = real(wanted)
        service.forget_instance(wanted)              # what an edit, a switch or a delete does now
        return inst

    monkeypatch.setattr(db.instances, "get_by_id", read_then_edited)
    for resp in (api.post(f"{base}/import", json={"download_id": "dl-1", "proposal_key": key}),
                 api.post(f"{base}/discard", json={"download_id": "dl-2"})):
        assert (resp.status_code, resp.json()) == (409, {"detail": service.INSTANCE_CHANGED})
    assert fake.posts == [] and fake.deletes == [] and fake.logged == []


def test_a_queue_too_large_to_read_is_an_error_not_a_shorter_list(api, orchestrator, monkeypatch):
    monkeypatch.setattr(service, "QUEUE_PAGE_SIZE", 1)
    monkeypatch.setattr(service, "QUEUE_MAX_PAGES", 1)
    instance_id, _ = add_instance(orchestrator)          # two downloads: more than one page of one
    [box] = api.get("/api/imports").json()["instances"]
    assert (box["error"], box["downloads"]) == ("Queue too large to read completely", [])
    count = api.get("/api/imports/count").json()
    assert (count["total"], count["complete"], count["per_instance"][0]["error"]) == (
        0, False, "Queue too large to read completely")
    resp = api.get(f"/api/imports/{instance_id}/proposal", params={"download_id": "dl-1"})
    assert (resp.status_code, resp.json()) == (502, {"detail": "Queue too large to read completely"})


def test_a_failed_command_answers_a_fixed_text_only(api, orchestrator):
    instance_id, fake = add_instance(orchestrator)
    base = f"/api/imports/{instance_id}"
    assert send_import(api, base).status_code == 200
    leak = f"System.Net.WebException: GET http://127.0.0.1:9/api?apikey={SECRET} failed"
    fake.finish_command(500, "failed", exception=leak, message=leak)
    resp = api.get(f"{base}/commands/500")
    assert resp.json() == {"command_id": 500, "state": "failed", "status": "failed", "message": service.MSG_FAILED}
    assert_no_secret(resp.text)
    for _, _, line in fake.logged:
        assert_no_secret(line)


def test_editing_switching_or_deleting_an_instance_forgets_its_imports(api, orchestrator, monkeypatch):
    instance_id, fake = add_instance(orchestrator)
    base = f"/api/imports/{instance_id}"
    assert send_import(api, base).status_code == 200
    api.get(f"{base}/proposal", params={"download_id": "dl-2"})
    forgotten = []
    real = service.forget_instance
    monkeypatch.setattr(service, "forget_instance", lambda i: (forgotten.append(i), real(i)))
    # Same address, new name: only the hook makes missingarr forget what it sent and read.
    resp = api.put(f"/api/instances/{instance_id}", json={"name": "Sonarr 2", "type": "sonarr",
                                                         "url": "http://127.0.0.1:9", "api_key": "", "enabled": True})
    assert resp.status_code == 200
    assert api.get(f"{base}/commands/500").status_code == 404
    api.get(f"{base}/proposal", params={"download_id": "dl-2"})
    assert [params for path, params in fake.gets if path == MANUAL_IMPORT].count(
        {"downloadId": "dl-2", "filterExistingFiles": "true"}) == 2       # read again, not from the cache
    assert api.post(f"/api/instances/{instance_id}/toggle", params={"enabled": False}).status_code == 200
    assert api.delete(f"/api/instances/{instance_id}").status_code == 204
    assert forgotten == [instance_id] * 6              # right before and right after each database write


def test_an_action_that_began_before_an_edit_sends_nothing_while_the_agent_restarts(api, orchestrator):
    # An edit restarts the agent and a switch-off stops it; that can take seconds
    # (BaseAgent.stop waits up to 5 s). The database holds the new configuration by
    # then, so the revision has moved already: an import or discard that took the
    # revision before the edit and reaches its last check now sends nothing (409).
    instance_id, fake = add_instance(orchestrator)
    key = api.get(f"/api/imports/{instance_id}/proposal", params={"download_id": "dl-1"}).json()["proposal_key"]
    taken, outcome = {}, []

    def actions_meanwhile(*args, **kwargs):
        for action in (lambda: service.import_download(fake, "dl-1", key, taken["revision"]),
                       lambda: service.discard_download(fake, "dl-2", True, taken["revision"])):
            try:
                action()
                outcome.append("sent")
            except service.ImportConflict as exc:
                outcome.append(str(exc))

    orchestrator.reload_agent = orchestrator.stop_agent = actions_meanwhile
    taken["revision"] = service.instance_revision(instance_id)
    resp = api.put(f"/api/instances/{instance_id}", json={"name": "Sonarr 2", "type": "sonarr",
                                                         "url": "http://127.0.0.1:9", "api_key": "", "enabled": True})
    assert resp.status_code == 200
    taken["revision"] = service.instance_revision(instance_id)
    assert api.post(f"/api/instances/{instance_id}/toggle", params={"enabled": False}).status_code == 200
    assert outcome == [service.INSTANCE_CHANGED] * 4
    assert fake.posts == [] and fake.deletes == [] and fake.logged == []


def test_detached_agent_gets_the_stored_configuration(api, orchestrator):
    instance_id, fake = add_instance(orchestrator)
    api.get(f"/api/imports/{instance_id}/proposal", params={"download_id": "dl-1"})
    assert orchestrator.detached == [instance_id]
    assert fake.config == db.instances.get_by_id(instance_id)
    assert fake.config["api_key"] == SECRET


@pytest.mark.parametrize("url,expected", [
    ("http://127.0.0.1:8989", {"scheme": "http", "port": 8989, "path": "/activity/queue"}),
    ("https://127.0.0.1", {"scheme": "https", "port": None, "path": "/activity/queue"}),
    ("http://127.0.0.1:7878/radarr/", {"scheme": "http", "port": 7878, "path": "/radarr/activity/queue"}),
    ("http://127.0.0.1:99999", {"scheme": "http", "port": None, "path": "/activity/queue"}),
    ("javascript:alert(1)", {"scheme": "http", "port": None, "path": "/activity/queue"}),
])
def test_queue_link(url, expected):
    assert queue_link(url) == expected
