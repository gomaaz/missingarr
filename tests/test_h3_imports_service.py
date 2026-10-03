import json
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
import requests

from backend.checked_search import import_check
from backend.checked_search.settings import CheckedSearchSettings
from backend.imports import entries, service
from tests.imports_fake_arr import (
    API_KEY, COMMAND, EPISODE, HISTORY, INDEXER_KEY, MANUAL_IMPORT, PARSE, QUEUE, QUEUE_DETAILS, QUEUE_STATUS,
    RADARR_BY_ID, SYSTEM_STATUS, FakeArr, config, delay_record, episode, grab_record, http_error, import_record,
    movie, quality, queue_record, radarr_item, refused, series, sonarr_item, timed_out,
)

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
STAMP = "2026-10-02T12:00:00Z"
TITLE = "Some.Show.S01E01.German.1080p.WEB.h264-GRP"
TITLE2 = "Some.Show.S01E02.German.1080p.WEB.h264-GRP"
HELD_BACK = "Some.Show.S01E05.German.1080p.WEB.h264-GRP"
PATH1 = "/downloads/complete/Some.Show.S01E01/Some.Show.S01E01.mkv"
PATH2 = "/downloads/complete/Some.Show.S01E02/Some.Show.S01E02.mkv"
NFO = "/downloads/complete/Some.Show.S01E01/Some.Show.S01E01.nfo"
SHOW = series(10, "Some Show", 2020)
EP1 = episode(3, 10, 1, 1, "2026-09-30T20:00:00Z")
EP2 = episode(4, 10, 1, 2, "2026-09-30T21:00:00Z")
SERIES_10 = "/api/v3/series/10"
RTITLE = "Some.Movie.2020.German.1080p.BluRay.x264-GRP"
RPATH = "/downloads/complete/Some.Movie.2020/Some.Movie.2020.mkv"
FILM = movie(1, "Some Movie", 2020)


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


def sonarr_parse(title, number):
    return {"title": title, "series": {"id": 10, "title": "Some Show"},
            "parsedEpisodeInfo": {"seriesTitle": "Some Show", "seasonNumber": 1, "episodeNumbers": [number],
                                  "absoluteEpisodeNumbers": []}}


def sonarr_fake(cfg=None, cls=FakeArr, **overrides):
    """Sonarr holding back dl-1 (one episode, plus a nfo file in the
    download) next to a release the delay profile holds back."""
    data = dict(
        queue=[queue_record(11, "dl-1", TITLE, series_id=10, episode_id=3, season=1), delay_record(90, HELD_BACK)],
        manual_imports={"dl-1": [sonarr_item(PATH1, SHOW, 1, [EP1]), sonarr_item(NFO, None, None, None, size=2_000)]},
        parses={TITLE: sonarr_parse(TITLE, 1), TITLE2: sonarr_parse(TITLE2, 2)},
        series_list=[SHOW], episodes=[EP1, EP2],
        history={"dl-1": [grab_record("dl-1")], "dl-2": [grab_record("dl-2")]},
    )
    data.update(overrides)
    return cls(cfg or config(1, "sonarr"), **data)


def with_second_download(fake):
    fake.queue.append(queue_record(21, "dl-2", TITLE2, series_id=10, episode_id=4, season=1,
                                   added="2026-10-02T11:00:00Z"))
    fake.manual_imports["dl-2"] = [sonarr_item(PATH2, SHOW, 1, [EP2], folder="Some.Show.S01E02.German.1080p")]
    return fake


def radarr_fake(**overrides):
    data = dict(
        queue=[queue_record(31, "dl-r", RTITLE, messages=(RADARR_BY_ID,), movie_id=1)],
        manual_imports={"dl-r": [radarr_item(RPATH, FILM)]},
        parses={RTITLE: {"title": RTITLE, "movie": {"id": 1},
                         "parsedMovieInfo": {"movieTitles": ["Some Movie"], "year": 2020}}},
        movies=[FILM],
    )
    data.update(overrides)
    return FakeArr(config(2, "radarr"), **data)


class OddArr:
    """An instance that answers every GET with the same value."""

    def __init__(self, answer):
        self.config = config(1, "sonarr")
        self.answer = answer

    def http_get(self, path, params=None, timeout=10):
        return self.answer


def reads(fake, path):
    return sum(1 for done, _ in fake.gets if done == path)


def paths(fake):
    return [done for done, _ in fake.gets]


# ── Queue ────────────────────────────────────────────────────────────────────

def test_queue_is_read_page_by_page(monkeypatch):
    monkeypatch.setattr(service, "QUEUE_PAGE_SIZE", 2)
    fake = sonarr_fake(queue=[queue_record(n, f"dl-{n}", series_id=10, episode_id=n, season=1) for n in (1, 2, 3)])
    assert [record["id"] for record in service.read_queue(fake)] == [1, 2, 3]
    assert fake.gets == [(QUEUE, {"page": 1, "pageSize": 2, "includeUnknownSeriesItems": "true"}),
                         (QUEUE, {"page": 2, "pageSize": 2, "includeUnknownSeriesItems": "true"})]


def test_reading_stops_when_every_record_is_there(monkeypatch):
    monkeypatch.setattr(service, "QUEUE_PAGE_SIZE", 2)
    fake = sonarr_fake(queue=[queue_record(n, f"dl-{n}", series_id=10, episode_id=n, season=1) for n in (1, 2, 3, 4)])
    assert len(service.read_queue(fake)) == 4
    assert reads(fake, QUEUE) == 2      # no third, empty page


def test_a_queue_longer_than_the_page_limit_is_an_error_not_a_shorter_list(monkeypatch):
    monkeypatch.setattr(service, "QUEUE_PAGE_SIZE", 2)
    monkeypatch.setattr(service, "QUEUE_MAX_PAGES", 2)
    fake = sonarr_fake(queue=[queue_record(n, f"dl-{n}", series_id=10, episode_id=n, season=1) for n in range(1, 6)])
    with pytest.raises(service.QueueTooLarge) as caught:
        service.read_queue(fake)
    assert str(caught.value) == service.QUEUE_TOO_LARGE == "Queue too large to read completely"
    assert reads(fake, QUEUE) == 2
    with pytest.raises(service.QueueTooLarge):
        service.blocked_downloads(fake)
    assert service.count_open(fake).error == service.QUEUE_TOO_LARGE
    fake.queue.pop()                           # exactly two full pages: complete
    assert len(service.read_queue(fake)) == 4


def test_queue_details_is_one_answer_with_every_record():
    # No paging, so no record can slip past; unknown items are in it without a switch.
    fake = radarr_fake(queue=[queue_record(31, "dl-r", RTITLE, messages=(RADARR_BY_ID,)),
                              delay_record(90, "Some.Movie.2020.German.1080p", movie_id=1)])
    assert [record["id"] for record in service.read_queue_details(fake)] == [31, 90]
    assert fake.gets == [(QUEUE_DETAILS, {})]
    with pytest.raises(ValueError):
        service.read_queue_details(OddArr({"records": []}))


def test_a_fresh_find_reads_the_whole_queue_in_one_answer_and_keeps_it(monkeypatch):
    monkeypatch.setattr(service, "QUEUE_PAGE_SIZE", 1)
    fake = with_second_download(sonarr_fake())
    assert service.find_download(fake, "dl-2", fresh=True).queue_ids == (21,)
    assert paths(fake) == [QUEUE_DETAILS]
    assert service.find_download(fake, "dl-1", fresh=False).queue_ids == (11,)    # served from that snapshot
    assert paths(fake) == [QUEUE_DETAILS]


def test_radarr_queue_asks_for_unknown_movie_items():
    # No movieId: the app could not map the download ("Unknown Movie").
    fake = radarr_fake(queue=[queue_record(31, "dl-r", RTITLE, messages=(RADARR_BY_ID,))])
    assert [record["id"] for record in service.read_queue(fake)] == [31]
    assert fake.gets == [(QUEUE, {"page": 1, "pageSize": 1000, "includeUnknownMovieItems": "true"})]


@pytest.mark.parametrize("answer", [[], {"records": None}])
def test_queue_answer_without_records_is_refused(answer):
    with pytest.raises(ValueError):
        service.read_queue(OddArr(answer))


def test_blocked_downloads_group_episode_records_and_skip_held_back_releases():
    fake = sonarr_fake(queue=[queue_record(11, "dl-1", TITLE, series_id=10, episode_id=3, season=1),
                              queue_record(12, "dl-1", TITLE, series_id=10, episode_id=4, season=1),
                              delay_record(90, HELD_BACK)])
    [download] = service.blocked_downloads(fake)
    assert (download.download_id, download.queue_ids, download.episode_ids) == ("dl-1", (11, 12), (3, 4))
    assert download.size == 2_000_000_000


def test_snapshot_is_served_for_60_seconds(clock):
    fake = sonarr_fake()
    first = service.blocked_downloads(fake, fresh=False)
    clock.advance(59)
    assert service.blocked_downloads(fake, fresh=False) == first
    assert reads(fake, QUEUE) == 1
    clock.advance(1)
    service.blocked_downloads(fake, fresh=False)
    assert reads(fake, QUEUE) == 2
    service.blocked_downloads(fake)          # fresh=True: always reads, and stores
    service.blocked_downloads(fake, fresh=False)
    assert reads(fake, QUEUE) == 3


def test_list_open_drops_the_cached_count():
    fake = sonarr_fake()
    assert service.cached_count(fake).count == 1
    fake.queue.append(queue_record(21, "dl-2", TITLE2, series_id=10, episode_id=4, season=1,
                                   added="2026-10-02T11:00:00Z"))
    assert service.cached_count(fake).count == 1        # still the cached value
    assert [d.download_id for d in service.list_open(fake)] == ["dl-1", "dl-2"]
    assert service.cached_count(fake).count == 2


def test_find_download_reports_a_vanished_download_and_drops_its_proposal():
    fake = sonarr_fake()
    service.load_proposal(fake, "dl-1")
    fake.queue.clear()
    with pytest.raises(service.ImportConflict) as caught:
        service.find_download(fake, "dl-1", fresh=True)
    assert str(caught.value) == service.ALREADY_HANDLED
    service.load_proposal(fake, "dl-1")
    assert reads(fake, MANUAL_IMPORT) == 2


# ── Proposal ─────────────────────────────────────────────────────────────────

def test_proposal_is_read_with_the_download_id_only():
    fake = sonarr_fake()
    proposal = service.load_proposal(fake, "dl-1")
    assert fake.gets == [(MANUAL_IMPORT, {"downloadId": "dl-1", "filterExistingFiles": "true"})]
    assert fake.timeouts[MANUAL_IMPORT] == 120
    assert proposal.download_id == "dl-1"
    assert [item["path"] for item in proposal.items] == [PATH1, NFO]
    assert proposal.assessment.importable is True
    assert proposal.key == entries.proposal_key(proposal.items)
    assert PATH1 not in repr(proposal)       # items stay out of logs


def test_proposal_answer_must_be_a_list():
    with pytest.raises(ValueError):
        service.load_proposal(OddArr({"records": []}), "dl-1")


def test_proposal_is_cached_for_60_seconds(clock):
    fake = sonarr_fake()
    first = service.load_proposal(fake, "dl-1")
    clock.advance(59)
    assert service.load_proposal(fake, "dl-1") is first
    clock.advance(1)
    assert service.load_proposal(fake, "dl-1") is not first
    assert reads(fake, MANUAL_IMPORT) == 2


def test_fresh_proposal_reads_again_and_is_stored():
    fake = sonarr_fake()
    service.load_proposal(fake, "dl-1")
    fresh = service.load_proposal(fake, "dl-1", fresh=True)
    assert service.load_proposal(fake, "dl-1") is fresh
    assert reads(fake, MANUAL_IMPORT) == 2


def test_failed_proposal_reads_are_not_cached():
    fake = sonarr_fake(manual_imports={"dl-1": timed_out()})
    with pytest.raises(requests.exceptions.Timeout):
        service.load_proposal(fake, "dl-1")
    fake.manual_imports["dl-1"] = [sonarr_item(PATH1, SHOW, 1, [EP1])]
    assert service.load_proposal(fake, "dl-1").assessment.importable is True
    assert reads(fake, MANUAL_IMPORT) == 2


def test_proposals_are_cached_per_instance():
    first, other = sonarr_fake(), sonarr_fake(cfg=config(3, "sonarr"))
    service.load_proposal(first, "dl-1")
    service.load_proposal(other, "dl-1")
    assert (reads(first, MANUAL_IMPORT), reads(other, MANUAL_IMPORT)) == (1, 1)


def test_a_proposal_read_that_overlaps_an_invalidate_is_not_stored():
    # A discard while a card loads its proposal: the old proposal must not come back for a minute.
    def discard_meanwhile(f):
        f.on_get.pop(MANUAL_IMPORT)
        service.invalidate(1, "dl-1")

    fake = sonarr_fake(on_get={MANUAL_IMPORT: discard_meanwhile})
    service.load_proposal(fake, "dl-1")
    service.load_proposal(fake, "dl-1")
    service.load_proposal(fake, "dl-1")
    assert reads(fake, MANUAL_IMPORT) == 2       # the first answer was not stored, the second was


def test_an_older_proposal_read_never_replaces_a_newer_one():
    old_items = [sonarr_item(PATH1, SHOW, 1, [EP1])]
    new_items = [sonarr_item(PATH1, SHOW, 1, [EP2])]
    newer = []

    def newer_read_meanwhile(f):
        # A second fresh read starts and ends while the first one waits for the app.
        f.on_get.pop(MANUAL_IMPORT)
        f.manual_imports["dl-1"] = new_items
        newer.append(service.load_proposal(f, "dl-1", fresh=True))
        f.manual_imports["dl-1"] = old_items           # the first read still gets the old answer

    fake = sonarr_fake(manual_imports={"dl-1": old_items}, on_get={MANUAL_IMPORT: newer_read_meanwhile})
    older = service.load_proposal(fake, "dl-1", fresh=True)
    assert older.key == entries.proposal_key(old_items) != newer[0].key
    assert service.load_proposal(fake, "dl-1") is newer[0]
    assert reads(fake, MANUAL_IMPORT) == 2


def test_loading_the_list_while_a_proposal_is_read_still_stores_the_proposal():
    # Reload, a second tab or the new list after an action: the app is not asked for the same proposal twice.
    def list_meanwhile(f):
        f.on_get.pop(MANUAL_IMPORT)
        assert [d.download_id for d in service.list_open(f)] == ["dl-1"]

    fake = sonarr_fake(on_get={MANUAL_IMPORT: list_meanwhile})
    first = service.load_proposal(fake, "dl-1")
    assert service.load_proposal(fake, "dl-1") is first
    assert reads(fake, MANUAL_IMPORT) == 1


def test_a_third_proposal_read_waits_for_a_free_slot():
    # A reload in the browser or several tabs: the app never runs more than two ffprobe reads for missingarr.
    entered = {name: threading.Event() for name in ("dl-1", "dl-2")}
    release = {name: threading.Event() for name in ("dl-1", "dl-2")}

    def slow(_fake):
        name = threading.current_thread().name
        if name in entered:
            entered[name].set()
            release[name].wait(5)

    fake = with_second_download(sonarr_fake(on_get={MANUAL_IMPORT: slow}))
    results = {}

    def read(name, download_id):
        results[name] = service.load_proposal(fake, download_id)

    readers = {name: threading.Thread(target=read, args=(name, download_id), name=name)
               for name, download_id in (("dl-1", "dl-1"), ("dl-2", "dl-2"), ("reload", "dl-1"))}
    readers["dl-1"].start()
    readers["dl-2"].start()
    assert entered["dl-1"].wait(5) and entered["dl-2"].wait(5)
    readers["reload"].start()                  # the card of dl-1 once more, after a reload in the browser
    time.sleep(0.05)
    assert reads(fake, MANUAL_IMPORT) == 2 and "reload" not in results       # it waits for a slot
    other = sonarr_fake(cfg=config(3, "sonarr"))
    service.load_proposal(other, "dl-1")       # another app has slots of its own
    assert reads(other, MANUAL_IMPORT) == 1
    release["dl-1"].set()
    readers["dl-1"].join(5)
    readers["reload"].join(5)
    assert results["reload"] is results["dl-1"]          # served from the read it waited for
    release["dl-2"].set()
    readers["dl-2"].join(5)
    assert reads(fake, MANUAL_IMPORT) == 2


def test_a_read_without_a_free_slot_gives_up_and_a_failed_read_frees_its_slot(monkeypatch):
    monkeypatch.setattr(service, "PROPOSAL_SLOTS", 1)
    monkeypatch.setattr(service, "MANUAL_IMPORT_TIMEOUT", 0.05)
    entered, release = threading.Event(), threading.Event()

    def slow(_fake):
        entered.set()
        release.wait(5)

    fake = with_second_download(sonarr_fake(on_get={MANUAL_IMPORT: slow}))
    reader = threading.Thread(target=service.load_proposal, args=(fake, "dl-1"))
    reader.start()
    assert entered.wait(5)
    with pytest.raises(service.ProposalsBusy) as caught:
        service.load_proposal(fake, "dl-2")
    assert str(caught.value) == service.PROPOSALS_BUSY
    assert reads(fake, MANUAL_IMPORT) == 1                 # the app was not asked
    release.set()
    reader.join(5)
    fake.on_get.clear()
    fake.manual_imports["dl-2"] = timed_out()
    with pytest.raises(requests.exceptions.Timeout):
        service.load_proposal(fake, "dl-2")
    assert service.load_proposal(fake, "dl-3").items == ()     # the failed read gave its slot back
    assert reads(fake, MANUAL_IMPORT) == 3


def test_import_target():
    assert service.import_target(entries.Assessment(importable=True, why_not="", movie_id=1)) \
        == import_check.ImportTarget(movie_id=1)
    assert service.import_target(entries.Assessment(importable=True, why_not="", series_id=10, episode_ids=(3, 4))) \
        == import_check.ImportTarget(series_id=10, episode_ids=(3, 4))
    assert service.import_target(entries.Assessment(importable=False, why_not=entries.WHY_NO_VIDEO)) is None
    assert service.import_target(
        entries.Assessment(importable=False, why_not=entries.WHY_NO_EPISODES, series_id=10)) is None


def test_proposal_view_shows_candidates_and_the_verdict():
    fake = sonarr_fake()
    view = service.proposal_view(fake, "dl-1")
    assert paths(fake) == [QUEUE, MANUAL_IMPORT, PARSE, SERIES_10, EPISODE, HISTORY]
    assert set(view) == {"download_id", "title", "target", "candidates", "importable", "why_not",
                         "uncovered_episodes", "proposal_key", "verdict", "cached"}
    assert (view["download_id"], view["title"], view["target"]) == ("dl-1", TITLE, "Some Show S01E01")
    assert [c["path"] for c in view["candidates"]] == [PATH1, NFO]
    assert [c["video"] for c in view["candidates"]] == [True, False]
    assert (view["importable"], view["why_not"], view["uncovered_episodes"]) == (True, "", 0)
    assert view["proposal_key"] == entries.proposal_key(fake.manual_imports["dl-1"])
    assert view["verdict"] == {"state": "fits", "reasons": [], "notes": [], "details": [], "error": ""}
    assert view["cached"] is False
    assert INDEXER_KEY not in json.dumps(view)    # grab history never comes back


def test_second_view_uses_the_cached_proposal_and_series():
    fake = sonarr_fake()
    service.proposal_view(fake, "dl-1")
    fake.gets.clear()
    view = service.proposal_view(fake, "dl-1")
    assert view["cached"] is True
    assert paths(fake) == [PARSE, EPISODE, HISTORY]


def test_series_is_read_once_for_two_downloads(clock):
    fake = with_second_download(sonarr_fake())
    service.proposal_view(fake, "dl-1")
    service.proposal_view(fake, "dl-2")
    assert reads(fake, SERIES_10) == 1
    service.invalidate(1)                     # an action leaves the series alone
    service.proposal_view(fake, "dl-1")
    assert reads(fake, SERIES_10) == 1
    clock.advance(60)
    service.proposal_view(fake, "dl-1")
    assert reads(fake, SERIES_10) == 2


def test_verdict_gets_the_instance_settings_and_the_download(monkeypatch):
    calls = []

    def spy(get, arr_type, release_title, target, settings, *, download_id=None, cache=None):
        calls.append((get, arr_type, release_title, target, settings, download_id, cache))
        return import_check.ImportVerdict(import_check.VERDICT_UNKNOWN, error="spy")

    monkeypatch.setattr(import_check, "check_import", spy)
    fake = sonarr_fake(cfg=config(1, "sonarr", checked_search_settings={"reject_days_before_air": 30}))
    view = service.proposal_view(fake, "dl-1")
    [(get, arr_type, release_title, target, settings, download_id, cache)] = calls
    assert get == fake.http_get
    assert (arr_type, release_title, download_id) == ("sonarr", TITLE, "dl-1")
    assert target == import_check.ImportTarget(series_id=10, episode_ids=(3,))
    assert settings == CheckedSearchSettings.from_stored({"reject_days_before_air": 30})
    assert isinstance(cache, dict)
    assert view["verdict"]["error"] == "spy"


def test_proposal_view_of_a_vanished_download_is_a_conflict():
    fake = sonarr_fake()
    with pytest.raises(service.ImportConflict) as caught:
        service.proposal_view(fake, "dl-unknown")
    assert str(caught.value) == service.ALREADY_HANDLED
    assert reads(fake, MANUAL_IMPORT) == 0


def test_empty_proposal_of_a_download_that_just_left_the_queue_is_already_handled():
    fake = sonarr_fake()
    service.blocked_downloads(fake)            # the snapshot still holds dl-1
    fake.queue.clear()
    del fake.manual_imports["dl-1"]            # the app answers [] for a download it no longer knows
    fake.gets.clear()
    with pytest.raises(service.ImportConflict) as caught:
        service.proposal_view(fake, "dl-1")
    assert str(caught.value) == service.ALREADY_HANDLED
    assert paths(fake) == [MANUAL_IMPORT, QUEUE_DETAILS]


def test_empty_proposal_of_a_queued_download_has_no_video():
    fake = sonarr_fake(manual_imports={"dl-1": []})
    view = service.proposal_view(fake, "dl-1")
    assert (view["importable"], view["why_not"]) == (False, entries.WHY_NO_VIDEO)
    assert view["verdict"]["state"] == import_check.VERDICT_UNKNOWN
    assert paths(fake) == [QUEUE, MANUAL_IMPORT, QUEUE_DETAILS]      # the whole queue once more, nothing else


def test_proposal_view_counts_the_episodes_the_proposal_leaves_out():
    fake = sonarr_fake(queue=[queue_record(11, "dl-1", TITLE, series_id=10, episode_id=3, season=1),
                              queue_record(12, "dl-1", TITLE, series_id=10, episode_id=4, season=1)])
    assert service.proposal_view(fake, "dl-1")["uncovered_episodes"] == 1      # the proposal names EP1 only
    fake.manual_imports["dl-1"] = [sonarr_item(PATH1, SHOW, 1, [EP1, EP2])]
    service.invalidate(1, "dl-1")
    assert service.proposal_view(fake, "dl-1")["uncovered_episodes"] == 0
    assert service.proposal_view(radarr_fake(), "dl-r")["uncovered_episodes"] == 0


# ── Errors ───────────────────────────────────────────────────────────────────

def _no_response_error():
    return requests.exceptions.HTTPError("no response")


@pytest.mark.parametrize("exc,expected", [
    (requests.exceptions.ReadTimeout(), (504, "Connection timed out")),
    (requests.exceptions.ConnectTimeout(), (504, "Connection timed out")),
    (refused(), (503, "Cannot connect to instance")),
    (http_error(302), (502, "Instance answered with a redirect (HTTP 302) — check the URL")),
    (http_error(401), (502, "Invalid API key")),
    (http_error(403), (502, "Invalid API key")),
    (http_error(404), (502, "HTTP 404 from instance")),
    (http_error(500), (502, "HTTP 500 from instance")),
    (_no_response_error(), (502, "HTTP 0 from instance")),
    (json.JSONDecodeError("Expecting value", "", 0), (502, "Instance did not answer with JSON — is the URL correct?")),
    (requests.exceptions.JSONDecodeError("Expecting value", "", 0),
     (502, "Instance did not answer with JSON — is the URL correct?")),
    (ValueError("queue answer without a record list"), (502, "Unexpected answer from instance")),
    (service.QueueTooLarge(service.QUEUE_TOO_LARGE), (502, "Queue too large to read completely")),
    (requests.exceptions.ChunkedEncodingError("cut off"), (502, "Request to instance failed")),
    (requests.exceptions.InvalidURL("no host in the URL"), (502, "Request to instance failed")),
    (KeyError("id"), (502, "Unexpected error (KeyError)")),
])
def test_arr_error(exc, expected):
    assert service.arr_error(exc) == expected


def test_arr_error_never_repeats_the_message():
    response = requests.Response()
    response.status_code = 500
    leaky = requests.exceptions.HTTPError(f"500 for http://127.0.0.1:9/api/v3/queue?apikey={API_KEY}",
                                          response=response)
    assert API_KEY not in service.arr_error(leaky)[1]


# ── Open-imports count ───────────────────────────────────────────────────────

def test_count_is_zero_without_reading_the_queue_when_no_flag_is_set():
    fake = sonarr_fake(queue=[delay_record(90, HELD_BACK)])
    assert service.cached_count(fake) == service.InstanceCount(count=0, error="", starting=False, checked_at=STAMP)
    assert paths(fake) == [QUEUE_STATUS, SYSTEM_STATUS]


def test_count_reads_the_queue_when_a_flag_is_set():
    fake = with_second_download(sonarr_fake())
    fake.queue.append(queue_record(12, "dl-1", TITLE, series_id=10, episode_id=4, season=1))
    assert service.cached_count(fake).count == 2      # downloads, not records
    assert paths(fake) == [QUEUE_STATUS, QUEUE]


def test_count_reads_the_queue_when_flags_are_missing():
    fake = sonarr_fake(queue_status={"totalCount": 2})
    assert service.cached_count(fake).count == 1
    assert paths(fake) == [QUEUE_STATUS, QUEUE]


def test_count_is_unknown_right_after_an_app_start():
    fake = sonarr_fake(queue=[], system_status={"appName": "Sonarr", "startTime": "2026-10-02T11:58:01Z"})
    assert service.count_open(fake) == service.InstanceCount(count=None, error="", starting=True, checked_at=STAMP)
    fake.system_status = {"appName": "Sonarr", "startTime": "2026-10-02T11:58:00Z"}    # 120 s ago
    assert service.count_open(fake).count == 0


def test_app_starting_reads_the_start_time_and_never_raises():
    fake = sonarr_fake(system_status={"appName": "Sonarr", "startTime": "2026-10-02T11:58:01Z"})
    assert service.app_starting(fake) is True
    fake.system_status = {"appName": "Sonarr", "startTime": "2026-10-02T11:58:00Z"}    # 120 s ago
    assert service.app_starting(fake) is False
    fake.system_status = {"appName": "Sonarr"}
    assert service.app_starting(fake) is False
    assert service.app_starting(OddArr([])) is False
    broken = sonarr_fake(queue=[], get_errors={SYSTEM_STATUS: refused()})
    assert service.app_starting(broken) is False
    assert service.count_open(broken) == service.InstanceCount(count=0, error="", starting=False, checked_at=STAMP)


@pytest.mark.parametrize("errors,text", [
    ({QUEUE_STATUS: refused()}, "Cannot connect to instance"),
    ({QUEUE + "$": http_error(500)}, "HTTP 500 from instance"),
])
def test_count_error_is_reported_not_raised(errors, text):
    fake = sonarr_fake(get_errors=errors)
    assert service.cached_count(fake) == service.InstanceCount(count=None, error=text, starting=False,
                                                                checked_at=STAMP)


def test_count_of_an_odd_answer_is_unknown():
    assert service.count_open(OddArr([])).error == "Unexpected answer from instance"


def test_count_is_cached_for_60_seconds(clock):
    fake = sonarr_fake()
    service.cached_count(fake)
    clock.advance(59)
    service.cached_count(fake)
    assert reads(fake, QUEUE_STATUS) == 1
    clock.advance(1)
    assert service.cached_count(fake).checked_at == "2026-10-02T12:01:00Z"
    assert reads(fake, QUEUE_STATUS) == 2


def test_invalidate_forces_a_new_count():
    fake = sonarr_fake()
    service.cached_count(fake)
    service.invalidate(1)
    service.cached_count(fake)
    assert reads(fake, QUEUE_STATUS) == 2


def test_a_read_that_overlaps_an_invalidate_is_not_stored():
    # A discard while the menu counts: the old answer must not come back for a minute.
    fake = sonarr_fake(on_get={QUEUE_STATUS: lambda f: service.invalidate(1), QUEUE: lambda f: service.invalidate(1)})
    service.cached_count(fake)
    service.cached_count(fake)
    assert reads(fake, QUEUE_STATUS) == 2
    service.blocked_downloads(fake, fresh=False)
    service.blocked_downloads(fake, fresh=False)
    assert reads(fake, QUEUE) == 4      # one per count, one per snapshot read: nothing was stored


def test_two_callers_count_only_once():
    entered, release = threading.Event(), threading.Event()

    def slow(_fake):
        entered.set()
        release.wait(5)

    fake = sonarr_fake(on_get={QUEUE_STATUS: slow})
    results = []
    callers = [threading.Thread(target=lambda: results.append(service.cached_count(fake))) for _ in range(2)]
    callers[0].start()
    assert entered.wait(5)
    callers[1].start()
    time.sleep(0.05)                    # the second caller now waits for the first
    release.set()
    for caller in callers:
        caller.join(5)
    assert reads(fake, QUEUE_STATUS) == 1
    assert results[0] == results[1] and results[0].count == 1


# ── Instance changes ─────────────────────────────────────────────────────────

def test_caches_belong_to_the_app_the_instance_points_to():
    fake = sonarr_fake()
    service.proposal_view(fake, "dl-1")
    service.cached_count(fake)
    fake.config = config(1, "sonarr", url="http://127.0.0.1:10")     # the instance now points to another app
    fake.gets.clear()
    service.proposal_view(fake, "dl-1")
    service.cached_count(fake)
    assert paths(fake) == [QUEUE, MANUAL_IMPORT, PARSE, SERIES_10, EPISODE, HISTORY, QUEUE_STATUS, QUEUE]


def test_only_forget_instance_raises_the_configuration_revision():
    assert (service.instance_revision(1), service.instance_revision(3)) == (0, 0)
    service.invalidate(1, "dl-1")              # an action or a list load changes no configuration
    service.list_open(sonarr_fake())
    service.forget_instance(1)
    assert (service.instance_revision(1), service.instance_revision(3)) == (1, 0)


def test_forget_instance_drops_everything_of_that_instance_only():
    first, other = sonarr_fake(), sonarr_fake(cfg=config(3, "sonarr"))
    for fake in (first, other):
        service.proposal_view(fake, "dl-1")
        service.cached_count(fake)
        fake.gets.clear()
    service.forget_instance(1)
    for fake in (first, other):
        service.proposal_view(fake, "dl-1")
        service.cached_count(fake)
    assert paths(first) == [QUEUE, MANUAL_IMPORT, PARSE, SERIES_10, EPISODE, HISTORY, QUEUE_STATUS, QUEUE]
    assert paths(other) == [PARSE, EPISODE, HISTORY]        # its proposal, series and count stay cached
    # A read that runs while the instance is edited does not store its answer.
    service.forget_instance(1)
    first.on_get[QUEUE] = lambda f: service.forget_instance(1)
    first.gets.clear()
    service.blocked_downloads(first, fresh=False)
    service.blocked_downloads(first, fresh=False)
    assert reads(first, QUEUE) == 2
