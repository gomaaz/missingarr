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


# ── Import ───────────────────────────────────────────────────────────────────

def send(fake, download_id="dl-1"):
    """Imports a download the way the page does: with the key it was shown."""
    key = service.proposal_view(fake, download_id)["proposal_key"]
    return service.import_download(fake, download_id, key)


def leaky_error(status=500):
    """An HTTP error whose message names the key, as a URL in a log might."""
    response = requests.Response()
    response.status_code = status
    return requests.exceptions.HTTPError(f"{status} for http://127.0.0.1:9/api/v3/command?apikey={API_KEY}",
                                         response=response)


class TimedArr(FakeArr):
    def http_post(self, path, body, timeout=10):
        self.post_timeout = timeout
        return super().http_post(path, body, timeout)


class NoIdArr(FakeArr):
    def http_post(self, path, body, timeout=10):
        super().http_post(path, body, timeout)
        return {"name": "ManualImport"}


def test_import_reads_queue_and_proposal_again():
    fake = sonarr_fake()
    key = service.proposal_view(fake, "dl-1")["proposal_key"]
    fake.gets.clear()
    started = service.import_download(fake, "dl-1", key)
    assert paths(fake) == [QUEUE_DETAILS, MANUAL_IMPORT, COMMAND]     # cached snapshot and proposal not used
    assert started == service.ImportStarted(state="sent", command_id=500, files=1, title=TITLE,
                                            target="Some Show S01E01", message="")


def test_import_of_a_vanished_download_is_refused():
    fake = sonarr_fake()
    key = service.proposal_view(fake, "dl-1")["proposal_key"]
    fake.queue.clear()          # imported elsewhere meanwhile
    with pytest.raises(service.ImportConflict) as caught:
        service.import_download(fake, "dl-1", key)
    assert str(caught.value) == service.ALREADY_HANDLED
    assert (fake.posts, fake.logged) == ([], [])


def test_import_with_a_changed_proposal_is_refused():
    fake = sonarr_fake()
    key = service.proposal_view(fake, "dl-1")["proposal_key"]
    fake.manual_imports["dl-1"] = [sonarr_item(PATH1, SHOW, 1, [EP2])]    # now another episode
    with pytest.raises(service.ImportConflict) as caught:
        service.import_download(fake, "dl-1", key)
    assert str(caught.value) == service.PROPOSAL_CHANGED
    assert fake.posts == []


def test_locked_import_is_refused_with_the_reason():
    reason = "Not an upgrade for existing episode file(s). Existing quality: WEBDL-1080p. New Quality WEBDL-1080p."
    fake = sonarr_fake(manual_imports={"dl-1": [sonarr_item(PATH1, SHOW, 1, [EP1], rejections=(reason,))]})
    key = service.proposal_view(fake, "dl-1")["proposal_key"]
    with pytest.raises(service.ImportConflict) as caught:
        service.import_download(fake, "dl-1", key)
    assert str(caught.value) == "Import is locked: " + entries.WHY_REJECTED.format(reasons=reason)
    assert (fake.posts, fake.logged) == ([], [])


OTHER = "/downloads/complete/Other/Other.mkv"


@pytest.mark.parametrize("name,status,file,refused_import", [
    ("ManualImport", "started", {"path": PATH1}, True),
    ("manualimport", "queued", {"path": PATH1}, True),
    ("ManualImport", "queued", {"path": OTHER, "downloadId": "dl-1"}, True),
    ("ManualImport", "started", {"path": PATH2}, False),
    ("ManualImport", "completed", {"path": PATH1}, False),
    ("RefreshMonitoredDownloads", "started", {"path": PATH1}, False),
])
def test_running_manual_import_of_the_files_or_the_download_blocks(name, status, file, refused_import):
    fake = sonarr_fake()
    fake.commands[77] = {"id": 77, "name": name, "status": status, "body": {"files": [file]}}
    key = service.proposal_view(fake, "dl-1")["proposal_key"]
    if refused_import:
        with pytest.raises(service.ImportConflict) as caught:
            service.import_download(fake, "dl-1", key)
        assert str(caught.value) == service.IMPORT_RUNNING
        assert fake.posts == []
    else:
        assert service.import_download(fake, "dl-1", key).command_id == 500


def test_command_list_must_be_a_list():
    with pytest.raises(ValueError):
        service.running_import(OddArr({"records": []}), {PATH1})


def test_sonarr_command_body_names_series_and_episodes():
    fake = sonarr_fake()
    send(fake)
    assert fake.posts == [(COMMAND, {"name": "ManualImport", "importMode": service.IMPORT_MODE, "files": [{
        "path": PATH1, "folderName": "Some.Show.S01E01.German.1080p", "quality": quality(),
        "languages": [{"id": 4, "name": "German"}], "releaseGroup": "GRP", "indexerFlags": 0,
        "downloadId": "dl-1", "seriesId": 10, "episodeIds": [3], "releaseType": "singleEpisode"}]})]
    assert "changeCategory" not in json.dumps(fake.posts)


def test_radarr_command_body_names_the_movie():
    fake = radarr_fake()
    started = send(fake, "dl-r")
    assert fake.posts == [(COMMAND, {"name": "ManualImport", "importMode": "auto", "files": [{
        "path": RPATH, "folderName": "Some.Movie.2020.German.1080p", "quality": quality(),
        "languages": [{"id": 4, "name": "German"}], "releaseGroup": "GRP", "indexerFlags": 0,
        "downloadId": "dl-r", "movieId": 1}]})]
    assert (started.target, started.files) == ("Some Movie (2020)", 1)


def test_command_name_and_mode_are_the_ones_entries_sends():
    body = entries.command_body([sonarr_item(PATH1, SHOW, 1, [EP1])], "dl-1", "sonarr")
    assert (body["name"], body["importMode"]) == (service.COMMAND_NAME, service.IMPORT_MODE)


def test_import_is_sent_with_its_own_timeout_and_logged():
    fake = sonarr_fake(cls=TimedArr)
    send(fake)
    assert fake.post_timeout == 30
    assert fake.logged == [("info", "imports", f"Import sent — '{TITLE}' → Some Show S01E01 (command 500, 1 file(s))")]


def test_import_drops_the_caches_of_the_download():
    fake = sonarr_fake()
    send(fake)
    fake.gets.clear()
    service.blocked_downloads(fake, fresh=False)
    service.load_proposal(fake, "dl-1")
    assert paths(fake) == [QUEUE, MANUAL_IMPORT]


@pytest.mark.parametrize("error,text", [
    (refused(), "Cannot connect to instance"),
    (requests.exceptions.ConnectTimeout(), "Connection timed out"),
    (leaky_error(404), "HTTP 404 from instance"),
    (http_error(401), "Invalid API key"),
])
def test_a_post_the_app_did_not_take_is_a_failure(error, text):
    # No connection was made, or the app answered 3xx/4xx: the command does not run.
    fake = sonarr_fake(post_error=error)
    key = service.proposal_view(fake, "dl-1")["proposal_key"]
    with pytest.raises(type(error)):
        service.import_download(fake, "dl-1", key)
    assert fake.logged == [("error", "imports",
                            f"Import of '{TITLE}' failed: {text} — the app did not take the command")]
    fake.gets.clear()
    service.blocked_downloads(fake, fresh=False)      # dropped all the same
    assert paths(fake) == [QUEUE]


@pytest.mark.parametrize("fields,text", [
    ({"post_lost": timed_out()}, "Connection timed out"),                                   # taken, answer lost
    ({"post_lost": requests.exceptions.ConnectionError("Connection aborted.")}, "Cannot connect to instance"),
    ({"post_lost": http_error(502)}, "HTTP 502 from instance"),
    ({"post_error": leaky_error(500)}, "HTTP 500 from instance"),                           # maybe taken
    ({"cls": NoIdArr}, "Unexpected answer from instance"),                                  # 2xx without an id
])
def test_a_post_whose_answer_got_lost_is_uncertain(fields, text):
    fake = sonarr_fake(**fields)
    key = service.proposal_view(fake, "dl-1")["proposal_key"]
    started = service.import_download(fake, "dl-1", key)
    assert started == service.ImportStarted(state="uncertain", command_id=None, files=1, title=TITLE,
                                            target="Some Show S01E01", message=service.MSG_UNCERTAIN)
    assert fake.logged == [("warn", "imports",
                            f"Import sent, answer lost — '{TITLE}' → Some Show S01E01 (1 file(s)): {text}; "
                            "the app may still run it, check the queue")]
    assert API_KEY not in json.dumps(fake.logged)
    fake.gets.clear()
    service.blocked_downloads(fake, fresh=False)
    assert paths(fake) == [QUEUE]


def test_after_a_lost_answer_the_next_action_is_refused_while_the_import_runs():
    # The app took the command, the answer got lost: a second import or a
    # discard of the download is refused (409) while that command is queued or running.
    fake = sonarr_fake(post_lost=timed_out())
    assert send(fake).state == "uncertain"
    fake.post_lost = None
    key = service.proposal_view(fake, "dl-1")["proposal_key"]
    for action in (lambda: service.import_download(fake, "dl-1", key),
                   lambda: service.discard_download(fake, "dl-1", True)):
        with pytest.raises(service.ImportConflict) as caught:
            action()
        assert str(caught.value) == service.IMPORT_RUNNING
    assert len(fake.posts) == 1 and fake.deletes == []
    fake.finish_command(500)
    fake.imported("dl-1")
    with pytest.raises(service.ImportConflict) as caught:
        service.discard_download(fake, "dl-1", True)
    assert str(caught.value) == service.ALREADY_HANDLED


def test_a_post_that_reaches_the_app_late_keeps_the_download_guarded(clock):
    # A proxy forwards the POST after missingarr gave up on the answer: until the app shows
    # the command, neither a second import nor a discard (it would delete the files) runs.
    fake = sonarr_fake(post_delayed=timed_out())
    assert send(fake).state == "uncertain"
    fake.post_delayed = None
    key = service.proposal_view(fake, "dl-1")["proposal_key"]
    actions = (lambda: service.import_download(fake, "dl-1", key),
               lambda: service.discard_download(fake, "dl-1", True))
    clock.advance(service.UNCERTAIN_GUARD_SECONDS - 1)
    for action in actions:
        with pytest.raises(service.ImportConflict) as caught:
            action()
        assert str(caught.value) == service.IMPORT_MAY_RUN == (
            "An import may still be running in the app — try again in a few minutes")
    assert len(fake.posts) == 1 and fake.deletes == [] and len(fake.logged) == 1
    assert fake.arrive() == [500]                      # the POST reaches the app now
    for action in actions:
        with pytest.raises(service.ImportConflict) as caught:
            action()
        assert str(caught.value) == service.IMPORT_RUNNING     # the guard ended, the usual check applies
    fake.finish_command(500, "failed")
    assert service.discard_download(fake, "dl-1", True).queue_id == 11


def guarded_actions(fake, download_id="dl-1"):
    """Import (with the key the page was shown) and discard of a download."""
    key = service.proposal_view(fake, download_id)["proposal_key"]
    return (lambda: service.import_download(fake, download_id, key),
            lambda: service.discard_download(fake, download_id, True))


def refused_with(actions, text):
    for action in actions:
        with pytest.raises(service.ImportConflict) as caught:
            action()
        assert str(caught.value) == text


def test_a_download_that_left_the_queue_and_came_back_is_still_guarded():
    # A fresh /queue/details shows only that the download is out of sight now, not that the
    # lost POST will never come: a queue without the download ends no guard.
    fake = with_second_download(sonarr_fake(post_delayed=timed_out()))
    assert send(fake, "dl-1").state == "uncertain" and send(fake, "dl-2").state == "uncertain"
    fake.post_delayed = None
    record = next(r for r in fake.queue if r.get("downloadId") == "dl-2")
    fake.queue.remove(record)                          # dl-2 is not in the queue for a while
    with pytest.raises(service.ImportConflict) as caught:
        service.discard_download(fake, "dl-2", True)
    assert str(caught.value) == service.ALREADY_HANDLED
    fake.queue.append(record)                          # back again: its guard is still there
    for download_id in ("dl-2", "dl-1"):
        with pytest.raises(service.ImportConflict) as caught:
            service.discard_download(fake, download_id, True)
        assert str(caught.value) == service.IMPORT_MAY_RUN
    assert fake.deletes == [] and len(fake.posts) == 2


def test_a_download_client_outage_keeps_the_guard_until_the_app_shows_the_command(clock):
    # The app cannot read its download client: its queue is empty for a while (it keeps
    # only what the clients returned), then the download is back. The lost POST may still
    # arrive; only the command itself (or ten minutes) ends the guard.
    fake = sonarr_fake(post_delayed=timed_out())
    assert send(fake).state == "uncertain"
    fake.post_delayed = None
    actions = guarded_actions(fake)
    held, fake.queue = fake.queue, []                  # the client read failed
    refused_with(actions, service.ALREADY_HANDLED)     # nothing to act on, nothing deleted
    fake.queue = held                                  # the client answers again
    clock.advance(service.UNCERTAIN_GUARD_SECONDS - 1)
    refused_with(actions, service.IMPORT_MAY_RUN)
    assert len(fake.posts) == 1 and fake.deletes == []
    assert fake.arrive() == [500]                      # the new command: the usual check
    refused_with(actions, service.IMPORT_RUNNING)
    assert len(fake.posts) == 1 and fake.deletes == []


def test_an_app_restart_keeps_the_guard_until_the_app_shows_the_command(clock):
    # After a restart the app's queue is empty until it read its download clients again,
    # and its start time is new. The POST may still reach the restarted app.
    fake = sonarr_fake(post_delayed=timed_out())
    assert send(fake).state == "uncertain"
    fake.post_delayed = None
    actions = guarded_actions(fake)
    held, fake.queue = fake.queue, []
    fake.system_status = {"appName": "Sonarr", "startTime": "2026-10-02T12:00:30Z"}
    clock.advance(60)
    refused_with(actions, service.ALREADY_HANDLED)
    fake.queue = held                                  # filled again after the start
    refused_with(actions, service.IMPORT_MAY_RUN)
    assert len(fake.posts) == 1 and fake.deletes == []
    assert fake.arrive() == [500]
    refused_with(actions, service.IMPORT_RUNNING)
    assert len(fake.posts) == 1 and fake.deletes == []


def test_the_guard_ends_after_ten_minutes(clock):
    fake = sonarr_fake(post_delayed=timed_out())
    assert send(fake).state == "uncertain"
    fake.post_delayed = None
    clock.advance(service.UNCERTAIN_GUARD_SECONDS - 1)
    with pytest.raises(service.ImportConflict) as caught:
        service.discard_download(fake, "dl-1", False)
    assert str(caught.value) == service.IMPORT_MAY_RUN
    clock.advance(1)                                   # ten minutes: the POST never arrived
    assert service.discard_download(fake, "dl-1", False).queue_id == 11
    assert fake.delayed and fake.commands == {} and service._uncertain == {}


def test_an_older_import_of_the_download_does_not_end_the_guard():
    # The app keeps ended commands in GET /command for some minutes: an earlier
    # ManualImport of the download that failed is not the POST whose answer got lost.
    older = {"id": 77, "name": "ManualImport", "status": "failed",
             "body": {"files": [{"path": PATH1, "downloadId": "dl-1"}]}}
    fake = sonarr_fake(post_delayed=timed_out(), commands=[older])
    assert send(fake).state == "uncertain"
    fake.post_delayed = None
    key = service.proposal_view(fake, "dl-1")["proposal_key"]
    actions = (lambda: service.import_download(fake, "dl-1", key),
               lambda: service.discard_download(fake, "dl-1", True))
    for action in (*actions, *actions):                # seen again and again, it ends nothing
        with pytest.raises(service.ImportConflict) as caught:
            action()
        assert str(caught.value) == service.IMPORT_MAY_RUN
    assert len(fake.posts) == 1 and fake.deletes == []
    assert fake.arrive() == [500]                      # the new command: the usual check
    for action in actions:
        with pytest.raises(service.ImportConflict) as caught:
            action()
        assert str(caught.value) == service.IMPORT_RUNNING
    assert len(fake.posts) == 1 and fake.deletes == []


def test_a_download_only_no_longer_held_back_keeps_its_guard():
    # importPending/ok is not held back ("Already handled"), but the download is still in
    # the queue: the lost POST may still reach the app, so the guard stays.
    fake = sonarr_fake(post_delayed=timed_out())
    assert send(fake).state == "uncertain"
    fake.post_delayed = None
    record = fake.queue[0]
    record.update(trackedDownloadState="importPending", trackedDownloadStatus="ok")
    with pytest.raises(service.ImportConflict) as caught:
        service.discard_download(fake, "dl-1", True)
    assert str(caught.value) == service.ALREADY_HANDLED
    record.update(trackedDownloadState="importBlocked", trackedDownloadStatus="warning")   # held back again
    with pytest.raises(service.ImportConflict) as caught:
        service.discard_download(fake, "dl-1", True)
    assert str(caught.value) == service.IMPORT_MAY_RUN
    assert len(fake.posts) == 1 and fake.deletes == []


def test_a_stale_paged_snapshot_that_misses_the_download_keeps_its_guard(monkeypatch):
    # Only a fresh answer of the whole queue shows that a download left it. Read page by
    # page, dl-1 slips past when a record before it leaves between two pages; the
    # snapshot of that read is served for up to 60 s.
    fake = sonarr_fake(post_delayed=timed_out())
    assert send(fake).state == "uncertain"
    fake.post_delayed = None
    fake.queue.insert(0, queue_record(5, "dl-0", TITLE2, series_id=10, episode_id=4, season=1))
    fake.on_get[QUEUE] = lambda f: reads(f, QUEUE) == 2 and f.queue.pop(0)     # dl-0 leaves after page 1
    monkeypatch.setattr(service, "QUEUE_PAGE_SIZE", 1)
    fake.gets.clear()
    assert [d.download_id for d in service.list_open(fake)] == ["dl-0"]        # dl-1 slipped past
    with pytest.raises(service.ImportConflict) as caught:
        service.proposal_view(fake, "dl-1")             # from that snapshot
    assert str(caught.value) == service.ALREADY_HANDLED
    with pytest.raises(service.ImportConflict) as caught:
        service.discard_download(fake, "dl-1", True)    # /queue/details still has it
    assert str(caught.value) == service.IMPORT_MAY_RUN
    assert len(fake.posts) == 1 and fake.deletes == []


# ── One action per download ──────────────────────────────────────────────────

def during_the_command_check(fake, action, outcome):
    """Runs action() while the first action is between its checks and its
    POST or DELETE (its GET /command), and keeps what happened."""
    def hook(f):
        f.on_get.pop(COMMAND)
        try:
            outcome.append(action())
        except service.ImportConflict as exc:
            outcome.append(str(exc))
    fake.on_get[COMMAND] = hook


def test_discard_is_refused_while_an_import_of_the_download_runs():
    fake = sonarr_fake()
    key = service.proposal_view(fake, "dl-1")["proposal_key"]
    outcome = []
    during_the_command_check(fake, lambda: service.discard_download(fake, "dl-1", True), outcome)
    assert service.import_download(fake, "dl-1", key).state == "sent"
    assert outcome == [service.ACTION_BUSY]
    assert fake.deletes == [] and len(fake.posts) == 1


@pytest.mark.parametrize("second", ["import", "discard"])
def test_a_second_action_is_refused_while_a_discard_of_the_download_runs(second):
    fake = sonarr_fake()
    key = service.proposal_view(fake, "dl-1")["proposal_key"]
    outcome = []
    action = ((lambda: service.import_download(fake, "dl-1", key)) if second == "import"
              else (lambda: service.discard_download(fake, "dl-1", False)))
    during_the_command_check(fake, action, outcome)
    assert service.discard_download(fake, "dl-1", True).queue_id == 11
    assert outcome == [service.ACTION_BUSY]
    assert fake.posts == [] and len(fake.deletes) == 1


def test_actions_on_other_downloads_go_on_and_the_lock_is_released():
    fake = with_second_download(sonarr_fake())
    key = service.proposal_view(fake, "dl-1")["proposal_key"]
    outcome = []
    during_the_command_check(fake, lambda: service.discard_download(fake, "dl-2", True), outcome)
    service.import_download(fake, "dl-1", key)
    assert [result.download_id for result in outcome] == ["dl-2"]
    with pytest.raises(service.ImportConflict) as caught:       # a refusal releases the lock as well
        service.import_download(fake, "dl-1", key)
    assert str(caught.value) == service.IMPORT_RUNNING
    fake.finish_command(500)
    fake.imported("dl-1")
    with pytest.raises(service.ImportConflict) as caught:
        service.discard_download(fake, "dl-1", True)
    assert str(caught.value) == service.ALREADY_HANDLED


# ── The instance changes while an action runs ────────────────────────────────
# backend/api/instances.py calls forget_instance() right before and right after
# it stores an edit, a switch on or off or a delete; the hooks below call it
# while a read is held.

def forget_meanwhile(path):
    def hook(f):
        f.on_get.pop(path)
        service.forget_instance(1)
    return hook


@pytest.mark.parametrize("held", [MANUAL_IMPORT, COMMAND])
def test_an_instance_changed_during_an_import_sends_nothing(held):
    fake = sonarr_fake()
    key = service.proposal_view(fake, "dl-1")["proposal_key"]
    fake.on_get[held] = forget_meanwhile(held)       # while the proposal or the command list is read
    with pytest.raises(service.ImportConflict) as caught:
        service.import_download(fake, "dl-1", key)
    assert str(caught.value) == service.INSTANCE_CHANGED == "The instance was changed — reload the page"
    assert fake.posts == [] and fake.logged == []
    assert service.import_download(fake, "dl-1", key).state == "sent"     # a new action, a new revision


def test_an_instance_changed_during_a_discard_deletes_nothing():
    fake = sonarr_fake(on_get={COMMAND: forget_meanwhile(COMMAND)})
    with pytest.raises(service.ImportConflict) as caught:
        service.discard_download(fake, "dl-1", True)
    assert str(caught.value) == service.INSTANCE_CHANGED
    assert fake.deletes == [] and fake.logged == []


def test_a_revision_taken_before_the_instance_was_read_fences_the_action():
    # The API takes the revision before it reads the instance: an edit right after that read counts.
    fake = sonarr_fake()
    key = service.proposal_view(fake, "dl-1")["proposal_key"]
    revision = service.instance_revision(1)
    service.forget_instance(1)
    for action in (lambda: service.import_download(fake, "dl-1", key, revision),
                   lambda: service.discard_download(fake, "dl-1", True, revision)):
        with pytest.raises(service.ImportConflict) as caught:
            action()
        assert str(caught.value) == service.INSTANCE_CHANGED
    assert fake.posts == [] and fake.deletes == []


def test_a_command_answered_after_an_instance_change_is_not_followed():
    # The instance is edited or switched off while the app answers the POST: the command
    # runs, but missingarr no longer follows a configuration it forgot.
    fake = sonarr_fake(on_post={COMMAND: lambda f: service.forget_instance(1)})
    assert send(fake) == service.ImportStarted(state="uncertain", command_id=None, files=1, title=TITLE,
                                               target="Some Show S01E01", message=service.MSG_UNCERTAIN)
    assert fake.logged == [("warn", "imports",
                            f"Import sent — '{TITLE}' → Some Show S01E01 (command 500, 1 file(s)), but the instance "
                            "was changed meanwhile: missingarr does not follow it, check the queue")]
    with pytest.raises(service.UnknownCommand):
        service.command_status(fake, 500)
    fake.on_post.clear()
    key = service.proposal_view(fake, "dl-1")["proposal_key"]
    with pytest.raises(service.ImportConflict) as caught:      # the command waits in the app: the usual check
        service.import_download(fake, "dl-1", key)
    assert str(caught.value) == service.IMPORT_RUNNING
    assert len(fake.posts) == 1


# ── Command status ───────────────────────────────────────────────────────────

def test_queued_and_started_commands_are_running():
    fake = sonarr_fake()
    command_id = send(fake).command_id
    assert service.command_status(fake, command_id) == service.CommandState(
        command_id=500, state="running", status="queued", message="")
    fake.commands[command_id]["status"] = "started"
    assert service.command_status(fake, command_id).state == "running"
    assert len(fake.logged) == 1               # only "Import sent"


def test_completed_gone_and_recorded_is_imported_and_logged_once():
    fake = sonarr_fake()
    command_id = send(fake).command_id
    fake.finish_command(command_id)
    fake.imported("dl-1")
    fake.gets.clear()
    expected = service.CommandState(command_id=500, state="imported", status="completed", message="Imported")
    assert service.command_status(fake, command_id) == expected
    # The start time before and after queue and history: one lifetime of the app.
    assert fake.gets == [(f"{COMMAND}/500", {}), (SYSTEM_STATUS, {}), (QUEUE_DETAILS, {}),
                         (HISTORY, {"downloadId": "dl-1", "eventType": 3}), (SYSTEM_STATUS, {})]
    assert service.command_status(fake, command_id) == expected
    assert fake.logged[1:] == [("info", "imports", f"Import done — '{TITLE}' imported (command 500)")]


def test_gone_without_an_import_record_is_unknown():
    fake = sonarr_fake()
    command_id = send(fake).command_id
    fake.finish_command(command_id)
    fake.queue = [record for record in fake.queue if record.get("downloadId") != "dl-1"]   # only a grab record left
    assert service.command_status(fake, command_id) == service.CommandState(
        command_id=500, state="unknown", status="completed", message=service.MSG_NO_RECORD)
    assert fake.logged[1:] == [("warn", "imports",
                                f"Import result unknown — '{TITLE}' (command 500): {service.MSG_NO_RECORD}")]


@pytest.mark.parametrize("arr", ["sonarr", "radarr"])
@pytest.mark.parametrize("state,status", [("importing", "ok"), ("importPending", "ok"), ("importBlocked", "warning")])
def test_a_download_still_queued_in_any_state_is_never_imported_nor_failed(clock, arr, state, status):
    # The app drops an imported download only at its next queue refresh: still queued proves nothing.
    fake, download_id = (sonarr_fake(), "dl-1") if arr == "sonarr" else (radarr_fake(), "dl-r")
    command_id = send(fake, download_id).command_id
    fake.finish_command(command_id)
    for record in fake.queue:
        record["trackedDownloadState"], record["trackedDownloadStatus"] = state, status
    fake.history[download_id] = [import_record(download_id)]      # a record is no proof while the download waits
    hint = service.UNCONFIRMED_MESSAGES[arr]
    assert service.command_status(fake, command_id) == service.CommandState(500, "confirming", "completed", hint)
    clock.advance(service.CONFIRM_SECONDS)
    assert service.command_status(fake, command_id) == service.CommandState(500, "unconfirmed", "completed", hint)


def test_a_sonarr_import_the_queue_does_not_confirm_is_unconfirmed_after_confirm_seconds(clock):
    fake = sonarr_fake()
    command_id = send(fake).command_id
    fake.finish_command(command_id)
    assert service.command_status(fake, command_id).state == "confirming"
    clock.advance(service.CONFIRM_SECONDS - 1)
    assert service.command_status(fake, command_id).state == "confirming"
    assert fake.logged[1:] == []                      # not final yet
    clock.advance(1)
    assert service.command_status(fake, command_id) == service.CommandState(
        command_id=500, state="unconfirmed", status="completed", message=service.MSG_UNCONFIRMED_SONARR)
    assert service.command_status(fake, command_id).state == "unconfirmed"
    assert service.MSG_UNCONFIRMED_SONARR == ("The app ran the import; the queue has not confirmed it yet — it may "
                                              "have imported only some episodes; check it in the app")
    assert fake.logged[1:] == [("warn", "imports",
                                f"Import not confirmed — '{TITLE}' (command 500): {service.MSG_UNCONFIRMED_SONARR}")]


def test_a_radarr_import_the_queue_does_not_confirm_is_unconfirmed_never_failed(clock):
    fake = radarr_fake()
    command_id = send(fake, "dl-r").command_id
    fake.finish_command(command_id)
    assert service.command_status(fake, command_id).state == "confirming"
    clock.advance(service.CONFIRM_SECONDS)
    assert service.command_status(fake, command_id) == service.CommandState(
        command_id=500, state="unconfirmed", status="completed", message=service.MSG_UNCONFIRMED_RADARR)
    assert service.MSG_UNCONFIRMED_RADARR == ("The app ran the import; the queue has not confirmed it yet — check it "
                                              "in the app")
    assert fake.logged[1:] == [("warn", "imports",
                                f"Import not confirmed — '{RTITLE}' (command 500): {service.MSG_UNCONFIRMED_RADARR}")]


def test_confirming_turns_into_imported_when_the_queue_catches_up(clock):
    fake = sonarr_fake()
    command_id = send(fake).command_id
    fake.finish_command(command_id)
    assert service.command_status(fake, command_id).state == "confirming"
    clock.advance(60)
    fake.imported("dl-1")                             # the app's next queue refresh
    assert service.command_status(fake, command_id).state == "imported"


def test_confirming_counts_from_the_post_not_from_the_end_of_the_command(clock):
    # CONFIRM_SECONDS count from the POST; a command that waited in the app has less of
    # that time left. "ended" is not needed.
    fake = sonarr_fake()
    command_id = send(fake).command_id
    clock.advance(90)
    fake.finish_command(command_id, ended=None)
    assert service.command_status(fake, command_id).state == "confirming"
    clock.advance(10)
    assert service.command_status(fake, command_id).state == "unconfirmed"


PAGE_POLL_SECONDS = 3          # IMPORTS_POLL_MS of templates/imports.html (Task 7; h5 pins it)
PAGE_BUDGET_SECONDS = 120      # IMPORTS_POLL_BUDGET_MS: a hard limit, counted from the import answer


def test_a_page_polling_for_its_whole_budget_gets_unconfirmed_and_one_log_line(clock):
    # CONFIRM_SECONDS count from the POST and end inside the page's budget, which starts only
    # with the import answer: with prompt answers one of the page's polls gets the final
    # "unconfirmed", and the service writes "Import not confirmed" exactly once.
    fake = sonarr_fake()
    command_id = send(fake).command_id          # the page's budget starts now at the earliest
    fake.finish_command(command_id)             # done, but the queue still holds the download
    deadline, states = clock.mono + PAGE_BUDGET_SECONDS, []
    while clock.mono < deadline:                # pollCommand, with answers that take no time
        clock.advance(min(PAGE_POLL_SECONDS, deadline - clock.mono))
        if clock.mono >= deadline:
            break
        states.append(service.command_status(fake, command_id).state)
        if states[-1] not in ("running", "confirming"):
            break
    assert states[-1] == "unconfirmed" and set(states[:-1]) == {"confirming"}
    service.command_status(fake, command_id)    # asked once more (another tab): still one line
    assert [line for line in fake.logged if line[2].startswith("Import not confirmed")] == [
        ("warn", "imports", f"Import not confirmed — '{TITLE}' (command 500): {service.MSG_UNCONFIRMED_SONARR}")]


@pytest.mark.parametrize("system", [{"appName": "Sonarr", "startTime": "2026-10-02T12:00:30Z"},   # after "queued"
                                    {"appName": "Sonarr", "startTime": "2026-10-02T12:00:00Z"},   # same second
                                    {"appName": "Sonarr"}])                                       # no start time
def test_a_restart_after_the_command_was_queued_makes_the_result_unknown(system):
    # Right after a start the queue is empty: "gone" proves nothing then.
    fake = sonarr_fake()
    command_id = send(fake).command_id
    fake.finish_command(command_id)
    fake.imported("dl-1")
    fake.system_status = system
    fake.gets.clear()
    assert service.command_status(fake, command_id) == service.CommandState(
        command_id=500, state="unknown", status="completed", message=service.MSG_RESTARTED)
    assert paths(fake) == [f"{COMMAND}/500", SYSTEM_STATUS]
    assert fake.logged[1:] == [("warn", "imports",
                                f"Import result unknown — '{TITLE}' (command 500): {service.MSG_RESTARTED}")]


@pytest.mark.parametrize("held", [QUEUE_DETAILS, HISTORY])
def test_a_restart_while_the_evidence_is_read_makes_the_result_unknown(held):
    # The start time was read, then the app restarted: an empty queue right after the start
    # and an older import record must not add up to "imported".
    fake = sonarr_fake()
    command_id = send(fake).command_id
    fake.finish_command(command_id)
    fake.history["dl-1"] = [import_record("dl-1")]
    if held == HISTORY:
        fake.queue.clear()

    def restart(f):
        f.on_get.pop(held)
        f.queue.clear()
        f.system_status = {"appName": "Sonarr", "startTime": "2026-10-02T12:00:10Z"}

    fake.on_get[held] = restart
    fake.gets.clear()
    assert service.command_status(fake, command_id) == service.CommandState(
        command_id=500, state="unknown", status="completed", message=service.MSG_RESTARTED)
    assert paths(fake) == [f"{COMMAND}/500", SYSTEM_STATUS, QUEUE_DETAILS, HISTORY, SYSTEM_STATUS]


SECRET_TEXT = f"System.Net.WebException: GET http://127.0.0.1:9/api?apikey={API_KEY} failed\n   at Some.Method()"


@pytest.mark.parametrize("status,message", [
    ("failed", "The import failed in the app — see the app's log"),
    ("aborted", "The import was aborted in the app — check the queue"),
    ("cancelled", "The import was cancelled in the app — check the queue"),
])
def test_failed_commands_get_a_fixed_text_never_the_apps(status, message):
    fake = sonarr_fake()
    command_id = send(fake).command_id
    fake.finish_command(command_id, status, exception=SECRET_TEXT, message=SECRET_TEXT)
    state = service.command_status(fake, command_id)
    assert state == service.CommandState(command_id=500, state="failed", status=status, message=message)
    assert fake.logged[1:] == [("error", "imports", f"Import failed — '{TITLE}' (command 500): {message}")]
    assert API_KEY not in repr(state) and API_KEY not in json.dumps(fake.logged)


@pytest.mark.parametrize("status,reported,message", [
    ("orphaned", "orphaned", "The app restarted during the import — check the queue"),
    ("paused", "other", "The app reports an unexpected command status — check the queue"),
    (f"odd {API_KEY}", "other", "The app reports an unexpected command status — check the queue"),
])
def test_orphaned_and_odd_statuses_are_unknown(status, reported, message):
    fake = sonarr_fake()
    command_id = send(fake).command_id
    fake.finish_command(command_id, status)
    assert service.command_status(fake, command_id) == service.CommandState(
        command_id=500, state="unknown", status=reported, message=message)
    assert fake.logged[1:] == [("warn", "imports", f"Import result unknown — '{TITLE}' (command 500): {message}")]


def test_command_the_app_no_longer_knows_is_unknown():
    fake = sonarr_fake()
    command_id = send(fake).command_id
    del fake.commands[command_id]
    assert service.command_status(fake, command_id) == service.CommandState(
        command_id=500, state="unknown", status="", message=service.MSG_GONE)
    assert fake.logged[1:] == [("warn", "imports",
                                f"Import result unknown — '{TITLE}' (command 500): {service.MSG_GONE}")]


def test_only_imports_sent_here_are_followed():
    # Another command's exception could carry anything: it is never read.
    fake = sonarr_fake()
    fake.commands[900] = {"id": 900, "name": "ManualImport", "status": "failed", "exception": SECRET_TEXT}
    for command_id in (900, 12345):
        with pytest.raises(service.UnknownCommand) as caught:
            service.command_status(fake, command_id)
        assert str(caught.value) == service.UNKNOWN_COMMAND
    assert fake.gets == [] and fake.logged == []


def test_a_command_sent_to_the_old_app_is_unknown_after_an_instance_change():
    fake = sonarr_fake()
    command_id = send(fake).command_id
    original = fake.config
    fake.config = config(1, "sonarr", url="http://127.0.0.1:10")     # the instance now points to another app
    with pytest.raises(service.UnknownCommand):
        service.command_status(fake, command_id)
    fake.config = original
    assert service.command_status(fake, command_id).state == "running"
    service.forget_instance(1)                                      # the instance was edited
    with pytest.raises(service.UnknownCommand):
        service.command_status(fake, command_id)
    assert [path for path, _ in fake.gets].count(f"{COMMAND}/500") == 1


def test_command_known_names_only_imports_followed_at_this_instance():
    # The API asks this before it checks whether the instance is switched on: after an
    # edit, a switch-off or a delete the page gets the 404 of an unknown command.
    fake = sonarr_fake()
    command_id = send(fake).command_id
    assert service.command_known(1, command_id) is True
    assert service.command_known(2, command_id) is False and service.command_known(1, 12345) is False
    service.forget_instance(2)                                      # another instance changed
    assert service.command_known(1, command_id) is True
    service.forget_instance(1)
    assert service.command_known(1, command_id) is False
    assert f"{COMMAND}/500" not in [path for path, _ in fake.gets]    # the app is never asked


def test_registry_forgets_imports_after_a_day(clock):
    fake = with_second_download(sonarr_fake())
    first = send(fake, "dl-1").command_id
    clock.advance(24 * 3600)
    send(fake, "dl-2")                         # the insert drops the day-old entry
    fake.finish_command(first)
    with pytest.raises(service.UnknownCommand):
        service.command_status(fake, first)
    assert [message for _, _, message in fake.logged if message.startswith("Import done")] == []


@pytest.mark.parametrize("error", [http_error(500), refused()])
def test_other_command_errors_propagate(error):
    fake = sonarr_fake()
    command_id = send(fake).command_id
    fake.commands[command_id] = error
    with pytest.raises(type(error)):
        service.command_status(fake, command_id)
    assert len(fake.logged) == 1


# ── Discard ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("blocklist,flag,note", [
    (True, "true", "blocklist on: if the app grabbed this release itself, it marks it as failed, puts it on the "
                   "blocklist and may search again; a download added by hand is only removed"),
    (False, "false", "not blocklisted, no new search"),
])
def test_discard_removes_the_whole_download_with_one_delete(blocklist, flag, note):
    fake = sonarr_fake(queue=[queue_record(11, "dl-1", TITLE, series_id=10, episode_id=3, season=1),
                              queue_record(12, "dl-1", TITLE, series_id=10, episode_id=4, season=1)])
    result = service.discard_download(fake, "dl-1", blocklist)
    assert result == service.DiscardResult(download_id="dl-1", title=TITLE, blocklist=blocklist, queue_id=11)
    assert fake.deletes == [(f"{QUEUE}/11", {"removeFromClient": "true", "blocklist": flag,
                                             "skipRedownload": "false", "changeCategory": "false"})]
    assert fake.queue == []
    assert fake.logged == [("info", "imports", f"Discarded — '{TITLE}' ({note})")]


def test_discard_reads_the_queue_again():
    fake = sonarr_fake()
    service.blocked_downloads(fake)            # the snapshot still holds dl-1
    fake.queue.clear()
    with pytest.raises(service.ImportConflict) as caught:
        service.discard_download(fake, "dl-1", True)
    assert str(caught.value) == service.ALREADY_HANDLED
    assert (fake.deletes, fake.logged) == ([], [])


@pytest.mark.parametrize("status,download_id,refused_discard", [
    ("queued", "dl-1", True),
    ("started", "dl-1", True),
    ("queued", "dl-2", False),
    ("completed", "dl-1", False),
])
def test_discard_is_refused_while_an_import_of_the_download_is_queued(status, download_id, refused_discard):
    # Disk commands run one after another in the app: a sent import can wait for minutes,
    # and removeFromClient would delete its files.
    fake = sonarr_fake()
    fake.commands[77] = {"id": 77, "name": "ManualImport", "status": status,
                         "body": {"files": [{"path": OTHER, "downloadId": download_id}]}}
    if refused_discard:
        with pytest.raises(service.ImportConflict) as caught:
            service.discard_download(fake, "dl-1", True)
        assert str(caught.value) == service.IMPORT_RUNNING
        assert (fake.deletes, fake.logged) == ([], [])
        assert paths(fake) == [QUEUE_DETAILS, COMMAND]
    else:
        assert service.discard_download(fake, "dl-1", True).queue_id == 11
        assert len(fake.deletes) == 1


def test_delete_404_means_already_handled():
    fake = sonarr_fake(delete_errors={11: http_error(404)})
    with pytest.raises(service.ImportConflict) as caught:
        service.discard_download(fake, "dl-1", True)
    assert str(caught.value) == service.ALREADY_HANDLED
    assert len(fake.deletes) == 1 and fake.logged == []


@pytest.mark.parametrize("error,text", [(leaky_error(), "HTTP 500 from instance"),
                                        (refused(), "Cannot connect to instance")])
def test_failing_delete_is_logged_and_raised(error, text):
    fake = sonarr_fake(delete_errors={11: error})
    with pytest.raises(type(error)):
        service.discard_download(fake, "dl-1", False)
    assert fake.logged == [("error", "imports", f"Discard of '{TITLE}' failed: {text}")]
    fake.gets.clear()
    service.blocked_downloads(fake, fresh=False)
    assert paths(fake) == [QUEUE]


def test_discard_drops_the_caches():
    fake = sonarr_fake()
    service.cached_count(fake)
    service.load_proposal(fake, "dl-1")
    service.discard_download(fake, "dl-1", True)
    fake.gets.clear()
    assert service.cached_count(fake).count == 0
    service.load_proposal(fake, "dl-1")
    assert paths(fake) == [QUEUE_STATUS, SYSTEM_STATUS, MANUAL_IMPORT]


def test_no_key_in_log_lines_or_texts(clock):
    fake = sonarr_fake(post_error=leaky_error(), delete_errors={11: leaky_error()})
    assert send(fake).state == "uncertain"
    texts = []
    for refusal in (lambda: service.discard_download(fake, "dl-1", True),      # guarded after the lost answer
                    lambda: service.import_download(fake, "dl-1", "0" * 16),
                    lambda: service.discard_download(fake, "dl-gone", True)):
        with pytest.raises(service.ImportConflict) as caught:
            refusal()
        texts.append(str(caught.value))
    clock.advance(service.UNCERTAIN_GUARD_SECONDS)
    with pytest.raises(requests.exceptions.HTTPError):
        service.discard_download(fake, "dl-1", True)
    texts += [message for _, _, message in fake.logged]
    assert len(texts) == 5
    for text in texts:
        assert API_KEY not in text and INDEXER_KEY not in text
