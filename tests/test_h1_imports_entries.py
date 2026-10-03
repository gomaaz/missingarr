import ast
import copy
import dataclasses
import json
import re
import sys
from pathlib import Path

import pytest

from backend.imports import entries

SONARR_BY_ID = ("Found matching series via grab history, but release was matched to series by ID. "
                "Automatic import is not possible. See the FAQ for details.")
RADARR_BY_ID = ("Found matching movie via grab history, but release was matched to movie by ID. "
                "Manual Import required.")
NOT_AN_UPGRADE = ("Not an upgrade for existing episode file(s). Existing quality: WEBDL-1080p. "
                  "New Quality WEBDL-1080p.")
SHOW_RELEASE = "Some.Show.S01E01.German.1080p.WEB.h264-GRP"
MOVIE_RELEASE = "Some.Movie.2020.German.1080p.BluRay.x264-GRP"
SEASON_FOLDER = "/downloads/complete/Some.Show.S01.German.1080p.WEB.h264-GRP"
MOVIE_PATH = f"/downloads/complete/{MOVIE_RELEASE}/{MOVIE_RELEASE}.mkv"
OTHER_MOVIE_PATH = f"/downloads/complete/{MOVIE_RELEASE}/Some.Movie.2020.German.1080p.Part2.mkv"
EP1 = f"{SEASON_FOLDER}/Some.Show.S01E01.German.1080p.WEB.h264-GRP.mkv"
EP2 = f"{SEASON_FOLDER}/Some.Show.S01E02.German.1080p.WEB.h264-GRP.mkv"
EP3 = f"{SEASON_FOLDER}/Some.Show.S01E03.German.1080p.WEB.h264-GRP.mkv"
NFO = f"{SEASON_FOLDER}/Some.Show.S01.German.1080p.WEB.h264-GRP.nfo"
SAMPLE = f"{SEASON_FOLDER}/Sample/Some.Show.S01E01.sample.mkv"
QUALITY = {"quality": {"id": 7, "name": "WEBDL-1080p", "source": "web", "resolution": 1080},
           "revision": {"version": 1, "real": 0, "isRepack": False}}
UNKNOWN_QUALITY = {"quality": {"id": 0, "name": "Unknown", "source": "unknown", "resolution": 0},
                   "revision": {"version": 1, "real": 0, "isRepack": False}}
GERMAN = [{"id": 4, "name": "German"}]
DROP = object()   # leave the key out, like *arr leaves out null fields


def record(record_id, download_id="dl-1", title=SHOW_RELEASE, *, state="importBlocked", status="warning",
           messages=(SONARR_BY_ID,), error=None, size=2_000_000_000, added="2026-10-02T10:00:00Z",
           client="SABnzbd", **ids):
    """A GET /api/v3/queue record: keys whose value is None are left out, like *arr does."""
    data = {"id": record_id, "downloadId": download_id, "title": title, "status": "completed",
            "trackedDownloadState": state, "trackedDownloadStatus": status,
            "statusMessages": [{"title": title, "messages": list(messages)}] if messages else None,
            "errorMessage": error, "size": size, "sizeleft": 0, "added": added, "downloadClient": client,
            "protocol": "usenet", **ids}
    return {key: value for key, value in data.items() if value is not None}


def delay(record_id):
    """A release a delay profile holds back: no downloadId, no tracked state."""
    return {"id": record_id, "title": "Other.Show.S01E01.German.1080p.WEB.h264-GRP", "status": "delay",
            "size": 1_500_000_000, "sizeleft": 1_500_000_000, "added": "2026-10-02T09:00:00Z",
            "seriesId": 11, "episodeId": 9, "seasonNumber": 1, "protocol": "usenet"}


def item(path, **fields):
    """A GET /api/v3/manualimport item; DROP leaves a key out."""
    data = {"id": 123456, "path": path,
            "relativePath": path.rsplit("/", 1)[-1] if isinstance(path, str) else DROP,
            "folderName": "Some.Release.German.1080p", "name": "Some.Release", "size": 2_000_000_000,
            "quality": copy.deepcopy(QUALITY), "languages": copy.deepcopy(GERMAN), "releaseGroup": "GRP",
            "qualityWeight": 101, "customFormats": [{"id": 1, "name": "German"}], "customFormatScore": 100,
            "indexerFlags": 0, "rejections": []}
    data.update(fields)
    return {key: value for key, value in data.items() if value is not DROP}


def movie_item(path=MOVIE_PATH, **fields):
    fields.setdefault("movie", {"id": 1, "title": "Some Movie", "year": 2020})
    fields.setdefault("movieFileId", 0)
    fields.setdefault("size", 4_000_000_000)
    return item(path, **fields)


def episode(episode_id, number, season=1):
    return {"id": episode_id, "seriesId": 10, "seasonNumber": season, "episodeNumber": number,
            "title": f"Episode {number}", "hasFile": False, "monitored": True}


def episode_item(path=EP1, numbers=((3, 1),), **fields):
    fields.setdefault("series", {"id": 10, "title": "Some Show", "year": 2024})
    fields.setdefault("seasonNumber", 1)
    fields.setdefault("episodes", [episode(episode_id, number) for episode_id, number in numbers])
    fields.setdefault("episodeFileId", 0)
    fields.setdefault("releaseType", "singleEpisode")
    return item(path, **fields)


# --- which queue records count, and how they are grouped ---------------------

def test_sonarr_episode_records_of_one_download_become_one_entry():
    records = [record(12, episodeId=4, seriesId=10, seasonNumber=1),
               record(11, episodeId=3, seriesId=10, seasonNumber=1),
               record(13, episodeId=5, seriesId=10, seasonNumber=1)]
    assert entries.group_blocked(records) == [entries.BlockedDownload(
        download_id="dl-1", title=SHOW_RELEASE, queue_ids=(12, 11, 13), size=2_000_000_000,
        added="2026-10-02T10:00:00Z", download_client="SABnzbd", state="importBlocked",
        messages=(SONARR_BY_ID,), movie_id=None, series_id=10, episode_ids=(3, 4, 5))]


def test_a_radarr_download_is_one_record():
    rec = record(21, "dl-2", MOVIE_RELEASE, messages=(RADARR_BY_ID,), size=4_000_000_000, movieId=1)
    assert entries.group_blocked([rec]) == [entries.BlockedDownload(
        download_id="dl-2", title=MOVIE_RELEASE, queue_ids=(21,), size=4_000_000_000,
        added="2026-10-02T10:00:00Z", download_client="SABnzbd", state="importBlocked",
        messages=(RADARR_BY_ID,), movie_id=1)]


def test_releases_held_back_by_a_delay_profile_never_count():
    assert not entries.is_open_record(delay(1))
    assert entries.group_blocked([delay(1), delay(2)]) == []
    assert [d.download_id for d in entries.group_blocked([delay(1), record(2), delay(3)])] == ["dl-1"]


@pytest.mark.parametrize(("state", "status", "counted"), [
    ("importBlocked", "warning", True),
    ("importBlocked", "error", True),
    ("importBlocked", None, True),
    ("importPending", "warning", True),     # one rejected file: stays importPending for ever
    ("importPending", "ok", False),
    ("importPending", None, False),
    ("downloading", "warning", False),
    ("importing", "warning", False),
    ("failedPending", "error", False),
    (None, "warning", False),
])
def test_which_states_count(state, status, counted):
    rec = record(1, state=state, status=status)
    assert entries.is_open_record(rec) is counted
    assert len(entries.group_blocked([rec])) == (1 if counted else 0)


@pytest.mark.parametrize("download_id", [None, "", 12345, ["dl-1"]])
def test_a_record_needs_a_download_id_text(download_id):
    rec = dict(record(1), downloadId=download_id)
    assert not entries.is_open_record(rec)
    assert entries.group_blocked([rec]) == []
    assert entries.group_blocked([record(1, None)]) == []   # key left out


def test_things_that_are_no_records_are_skipped():
    assert not entries.is_open_record(None)
    assert entries.group_blocked([]) == []
    assert len(entries.group_blocked([None, "dl-1", 7, ["dl-1"], record(1)])) == 1


@pytest.mark.parametrize("record_id", [DROP, None, "11", 1.5, True])
def test_a_record_without_an_integer_queue_id_is_left_out(record_id):
    rec = record(11)
    if record_id is DROP:
        del rec["id"]
    else:
        rec["id"] = record_id
    assert entries.group_blocked([rec]) == []


def test_missing_fields_get_empty_values():
    bare = {"id": 5, "downloadId": "dl-bare", "trackedDownloadState": "importBlocked"}
    assert entries.group_blocked([bare]) == [entries.BlockedDownload(
        download_id="dl-bare", title="", queue_ids=(5,), size=0, added=None, download_client="",
        state="importBlocked", messages=())]


def test_the_size_counts_once_per_download():
    records = [record(1, episodeId=3), record(2, size=2_000_000_000.0, episodeId=4),
               record(3, size="oops", episodeId=5)]
    assert entries.group_blocked(records)[0].size == 2_000_000_000


@pytest.mark.parametrize("size", [None, True, -5, "oops", float("nan")])
def test_an_unusable_size_counts_as_zero(size):
    assert entries.group_blocked([dict(record(1), size=size)])[0].size == 0


def test_messages_use_the_title_of_an_empty_list_and_end_with_the_error_message():
    first = record(1, episodeId=3, error="Unable to parse download, automatic import is not possible.")
    first["statusMessages"] = [{"title": f"{SHOW_RELEASE}.mkv", "messages": []},
                               {"title": SHOW_RELEASE, "messages": ["Unknown Series", SONARR_BY_ID]}]
    second = dict(first, id=2, episodeId=4)   # Sonarr repeats the messages on every episode record
    second["statusMessages"] = [{"title": SHOW_RELEASE, "messages": [SONARR_BY_ID]}, "junk",
                                {"title": "", "messages": []}, {"title": "x", "messages": [None, ""]}]
    assert entries.group_blocked([first, second])[0].messages == (
        f"{SHOW_RELEASE}.mkv", "Unknown Series", SONARR_BY_ID, "x",
        "Unable to parse download, automatic import is not possible.")


def test_added_client_and_ids_come_from_the_first_record_that_has_them():
    records = [record(1, added=None, client=None, episodeId=3),    # an unknown-series record has no seriesId
               record(2, added="2026-10-02T08:00:00Z", client="SABnzbd", episodeId=4, seriesId=10),
               record(3, added="2026-10-02T09:00:00Z", client="Other", episodeId=0, seriesId=12)]
    [download] = entries.group_blocked(records)
    assert (download.added, download.download_client) == ("2026-10-02T08:00:00Z", "SABnzbd")
    assert (download.series_id, download.episode_ids) == (10, (3, 4))


def test_an_unknown_series_download_has_no_target_ids():
    [download] = entries.group_blocked([record(1), record(2)])
    assert (download.movie_id, download.series_id, download.episode_ids) == (None, None, ())


def test_the_state_is_the_one_of_the_first_record():
    records = [record(1, state="importPending", episodeId=3), record(2, state="importBlocked", episodeId=4)]
    assert entries.group_blocked(records)[0].state == "importPending"


def test_a_record_seen_twice_counts_once():
    rec = record(1, episodeId=3)
    assert entries.group_blocked([rec, dict(rec)])[0].queue_ids == (1,)


def test_oldest_download_comes_first_and_downloads_without_added_come_last():
    records = [record(1, "dl-c", "C", added=None),
               record(2, "dl-new", "B", added="2026-10-02T12:00:00Z"),
               record(3, "dl-old", "Z", added="2026-10-01T23:00:00Z"),
               record(4, "dl-b", "A", added=None),
               record(5, "dl-a", "A", added=None),
               record(6, "dl-odd", "0", added="yesterday"),
               record(7, "dl-frac", "Y", added="2026-10-02T11:59:59.5Z"),
               record(8, "dl-offset", "X", added="2026-10-02T13:30:00+02:00")]
    assert [d.download_id for d in entries.group_blocked(records)] == [
        "dl-old", "dl-offset", "dl-frac", "dl-new", "dl-odd", "dl-a", "dl-b", "dl-c"]
    odd = [d for d in entries.group_blocked(records) if d.download_id == "dl-odd"][0]
    assert odd.added == "yesterday"   # passed on as *arr sent it


def test_as_dict_is_the_json_shape():
    [download] = entries.group_blocked([record(11, "abc", episodeId=3, seriesId=10),
                                        record(12, "abc", episodeId=4, seriesId=10)])
    assert download.as_dict() == {
        "download_id": "abc", "title": SHOW_RELEASE, "queue_ids": [11, 12], "size": 2_000_000_000,
        "added": "2026-10-02T10:00:00Z", "download_client": "SABnzbd", "state": "importBlocked",
        "messages": [SONARR_BY_ID], "movie_id": None, "series_id": 10, "episode_ids": [3, 4]}
    assert json.loads(json.dumps(download.as_dict())) == download.as_dict()


def test_results_are_frozen():
    [download] = entries.group_blocked([record(1)])
    with pytest.raises(dataclasses.FrozenInstanceError):
        download.title = "x"
    with pytest.raises(dataclasses.FrozenInstanceError):
        entries.assess([movie_item()], "radarr").importable = False


# --- video candidates ----------------------------------------------------------

def test_constants():
    assert entries.VIDEO_EXTENSIONS == frozenset({".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts", ".wmv"})
    assert entries.MIN_VIDEO_BYTES == 50 * 1024 * 1024
    assert entries.ARR_TYPES == ("radarr", "sonarr")
    assert (entries.STATE_BLOCKED, entries.STATE_PENDING, entries.STATUS_WARNING) == (
        "importBlocked", "importPending", "warning")


@pytest.mark.parametrize("extension", sorted(entries.VIDEO_EXTENSIONS))
def test_every_listed_extension_is_a_video(extension):
    assert entries.is_video_candidate(movie_item(f"/downloads/complete/x/Some.Movie.2020{extension}"))
    assert entries.is_video_candidate(movie_item(f"/downloads/complete/x/Some.Movie.2020{extension.upper()}"))


@pytest.mark.parametrize("name", ["Some.Movie.2020.nfo", "Some.Movie.2020.srt", "Some.Movie.2020.rar",
                                  "Some.Movie.2020.mkv.part", "Some.Movie.2020", "Some.Movie.2020.exe"])
def test_other_extensions_are_no_videos(name):
    assert not entries.is_video_candidate(movie_item(f"/downloads/complete/x/{name}"))


@pytest.mark.parametrize("relative_path", ["Some.Movie.2020.sample.mkv", "Sample/Some.Movie.2020.mkv",
                                           "Some.Movie.2020-SAMPLE.mkv", "samples/Some.Movie.2020.mkv"])
def test_sample_anywhere_in_the_relative_path_excludes_the_file(relative_path):
    assert not entries.is_video_candidate(movie_item(relativePath=relative_path))


def test_the_path_stands_in_for_a_missing_relative_path():
    assert entries.is_video_candidate(movie_item(relativePath=DROP))
    assert not entries.is_video_candidate(movie_item(SAMPLE, relativePath=DROP))
    assert not entries.is_video_candidate(movie_item(path=DROP))
    assert not entries.is_video_candidate(movie_item(relativePath=7, path=DROP))


@pytest.mark.parametrize(("size", "video"), [
    (entries.MIN_VIDEO_BYTES, True),
    (entries.MIN_VIDEO_BYTES - 1, False),
    (DROP, False),
    (None, False),
    (True, False),
    ("4000000000", False),
    (4_000_000_000.0, False),
])
def test_a_video_has_at_least_50_mib(size, video):
    assert entries.is_video_candidate(movie_item(size=size)) is video


def test_video_candidates_keep_the_answer_order():
    second, first = episode_item(EP2, numbers=((4, 2),)), episode_item(EP1)
    extras = [item(NFO, size=2_000), episode_item(SAMPLE), None, "x.mkv"]
    assert entries.video_candidates([second, *extras, first]) == [second, first]
    assert entries.video_candidates(None) == []


# --- target and quality ---------------------------------------------------------

@pytest.mark.parametrize(("movie", "expected"), [
    ({"id": 1, "title": "Some Movie"}, True),
    ({"id": 0, "title": "Some Movie"}, False),
    ({"id": "1"}, False),
    ({"id": True}, False),
    ({"title": "Some Movie"}, False),
    (None, False),
    (DROP, False),
    ("Some Movie", False),
])
def test_a_radarr_target_is_a_movie_with_an_id(movie, expected):
    assert entries.has_target(movie_item(movie=movie), "radarr") is expected


@pytest.mark.parametrize(("fields", "expected"), [
    ({}, True),
    ({"seasonNumber": 0}, True),                 # specials
    ({"seasonNumber": DROP}, False),             # episodes from several seasons: Sonarr leaves both out
    ({"seasonNumber": None}, False),
    ({"seasonNumber": True}, False),
    ({"seasonNumber": "1"}, False),
    ({"episodes": []}, False),
    ({"episodes": DROP}, False),
    ({"episodes": [{"id": 0, "episodeNumber": 1}]}, False),
    ({"episodes": [episode(3, 1), "E02"]}, False),
    ({"series": DROP}, False),
    ({"series": {"id": 0, "title": "Some Show"}}, False),
    ({"series": {"title": "Some Show"}}, False),
])
def test_a_sonarr_target_needs_series_season_and_episodes(fields, expected):
    assert entries.has_target(episode_item(**fields), "sonarr") is expected


def test_a_target_needs_an_item():
    assert not entries.has_target(None, "radarr")
    assert not entries.has_target("x", "sonarr")


@pytest.mark.parametrize(("quality", "expected"), [
    (QUALITY, True),
    (UNKNOWN_QUALITY, False),                    # "Unknown" is id 0
    ({"quality": {"name": "WEBDL-1080p"}}, False),
    ({"quality": {"id": "7"}}, False),
    ({"revision": {"version": 1}}, False),
    (None, False),
    (DROP, False),
])
def test_a_quality_needs_a_known_quality_id(quality, expected):
    assert entries.has_quality(movie_item(quality=quality)) is expected


# --- may the proposal be imported? ----------------------------------------------

def test_a_clean_radarr_proposal_is_importable():
    video = movie_item()
    nfo = item(f"/downloads/complete/{MOVIE_RELEASE}/{MOVIE_RELEASE}.nfo", size=2_000, quality=DROP, movie=DROP)
    assert entries.assess([video, nfo], "radarr") == entries.Assessment(
        importable=True, why_not="", videos=(video,), movie_id=1)


def test_a_clean_sonarr_proposal_with_two_files_is_importable():
    first = episode_item(EP1)
    second = episode_item(EP2, numbers=((5, 3), (4, 2)))
    result = entries.assess([second, item(NFO, size=2_000), first], "sonarr")
    assert (result.importable, result.why_not) == (True, "")
    assert result.videos == (second, first)
    assert (result.movie_id, result.series_id, result.episode_ids) == (None, 10, (3, 4, 5))


@pytest.mark.parametrize("items", [[], [item(NFO, size=2_000)],
                                   [item(NFO, size=2_000), episode_item(SAMPLE),
                                    episode_item(EP1, size=entries.MIN_VIDEO_BYTES - 1)]])
def test_no_video_file_locks_the_import(items):
    result = entries.assess(items, "sonarr")
    assert (result.importable, result.why_not, result.videos) == (False, "No video file in this download", ())
    assert (result.series_id, result.episode_ids) == (None, ())


def test_any_objection_of_the_app_locks_the_import():
    first = episode_item(EP1, rejections=[{"reason": NOT_AN_UPGRADE, "type": "permanent"}])
    clean = episode_item(EP3, numbers=((5, 3),))
    second = episode_item(EP2, numbers=((4, 2),), rejections=[{"reason": "Sample", "type": "permanent"},
                                                               {"reason": NOT_AN_UPGRADE, "type": "permanent"}])
    result = entries.assess([first, clean, second], "sonarr")
    assert not result.importable
    assert result.why_not == f"The app objects: {NOT_AN_UPGRADE}; Sample"
    assert result.why_not == entries.WHY_REJECTED.format(reasons=f"{NOT_AN_UPGRADE}; Sample")
    # the target is still known: the verdict needs it
    assert (result.series_id, result.episode_ids) == (10, (3, 4, 5))


def test_objections_to_files_that_are_no_videos_are_ignored():
    sample = movie_item(SAMPLE, rejections=[{"reason": "Sample", "type": "permanent"}])
    nfo = item(NFO, size=2_000, rejections=[{"reason": "Invalid video file, unsupported extension: '.nfo'",
                                             "type": "permanent"}])
    assert entries.assess([movie_item(), sample, nfo], "radarr").importable


@pytest.mark.parametrize("rejections", [[{"type": "permanent"}], [{"reason": "", "type": "permanent"}],
                                        [{"reason": "   "}], ["odd"], {"reason": "Sample"}])
def test_an_objection_without_a_reason_still_locks(rejections):
    result = entries.assess([movie_item(rejections=rejections)], "radarr")
    assert (result.importable, result.why_not) == (False, "The app objects: no reason given")


def test_a_video_without_a_movie_locks_a_radarr_import():
    lost = movie_item(OTHER_MOVIE_PATH, movie=DROP)
    result = entries.assess([movie_item(), lost], "radarr")
    assert (result.importable, result.why_not, result.movie_id) == (False, "A video file has no movie", None)


@pytest.mark.parametrize("fields", [{"episodes": DROP}, {"episodes": []}, {"seasonNumber": DROP}, {"series": DROP}])
def test_a_video_without_series_season_or_episodes_locks_a_sonarr_import(fields):
    result = entries.assess([episode_item(**fields)], "sonarr")
    assert (result.importable, result.why_not) == (False, "A video file has no series, season or episodes")
    assert (result.series_id, result.episode_ids) == (None, ())


def test_the_fallback_answer_of_the_app_is_not_a_clean_proposal():
    # What *arr answers for a file it could not evaluate: no target, no
    # quality, no languages and an EMPTY objection list.
    radarr = {"path": MOVIE_PATH, "relativePath": f"{MOVIE_RELEASE}.mkv", "size": 4_000_000_000, "rejections": []}
    sonarr = {"path": EP1, "relativePath": EP1.rsplit("/", 1)[-1], "size": 2_000_000_000, "rejections": []}
    assert entries.assess([radarr], "radarr").why_not == entries.WHY_NO_MOVIE
    assert entries.assess([sonarr], "sonarr").why_not == entries.WHY_NO_EPISODES


@pytest.mark.parametrize("quality", [DROP, None, UNKNOWN_QUALITY])
def test_a_video_without_a_quality_locks_the_import(quality):
    result = entries.assess([movie_item(quality=quality)], "radarr")
    assert (result.importable, result.why_not, result.movie_id) == (False, "A video file has no quality", 1)


def test_two_files_for_the_same_movie_lock_the_import():
    result = entries.assess([movie_item(), movie_item(OTHER_MOVIE_PATH)], "radarr")
    assert (result.importable, result.why_not) == (False, "Two video files for the same movie or episode")
    assert result.movie_id == 1


def test_an_episode_in_two_files_locks_the_import():
    first = episode_item(EP1, numbers=((3, 1), (4, 2)))
    second = episode_item(EP2, numbers=((4, 2),))
    result = entries.assess([first, second], "sonarr")
    assert (result.importable, result.why_not) == (False, entries.WHY_DUPLICATE)
    assert (result.series_id, result.episode_ids) == (10, (3, 4))


def test_files_for_different_movies_are_importable_without_a_single_target():
    other = movie_item(OTHER_MOVIE_PATH, movie={"id": 2, "title": "Other Movie", "year": 2021})
    result = entries.assess([movie_item(), other], "radarr")
    assert (result.importable, result.movie_id) == (True, None)


def test_files_for_different_series_are_importable_without_a_single_target():
    other = episode_item(EP2, series={"id": 11, "title": "Other Show"}, numbers=((9, 1),))
    result = entries.assess([episode_item(), other], "sonarr")
    assert (result.importable, result.series_id, result.episode_ids) == (True, None, ())


def test_locks_are_checked_in_a_fixed_order():
    objection = [{"reason": "Unknown Movie", "type": "permanent"}]
    # an objection says more than a missing target or quality
    assert entries.assess([movie_item(movie=DROP, quality=DROP, rejections=objection)],
                          "radarr").why_not == "The app objects: Unknown Movie"
    # a missing target comes before a missing quality
    assert entries.assess([movie_item(movie=DROP, quality=DROP)], "radarr").why_not == entries.WHY_NO_MOVIE
    # a missing quality comes before a duplicate
    assert entries.assess([movie_item(quality=DROP), movie_item(OTHER_MOVIE_PATH)],
                          "radarr").why_not == entries.WHY_NO_QUALITY
    # no video comes first: objections to files that are no videos do not count
    assert entries.assess([item(NFO, size=2_000, rejections=objection)], "radarr").why_not == entries.WHY_NO_VIDEO


def test_the_raw_videos_stay_out_of_repr():
    result = entries.assess([movie_item()], "radarr")
    assert "/downloads/" not in repr(result)
    assert "movie_id=1" in repr(result)


def test_an_unknown_instance_type_is_a_programming_error():
    video = movie_item()
    calls = [lambda: entries.assess([video], "lidarr"),
             lambda: entries.has_target(video, "lidarr"),
             lambda: entries.item_target_text(video, "lidarr"),
             lambda: entries.target_text([video], "lidarr"),
             lambda: entries.candidate_view(video, "lidarr"),
             lambda: entries.file_payload(video, "dl-1", "lidarr"),
             lambda: entries.command_body([video], "dl-1", "lidarr")]
    for call in calls:
        with pytest.raises(ValueError, match="Unknown instance type"):
            call()


# --- proposal key -----------------------------------------------------------------

def proposal():
    return [episode_item(EP1), episode_item(EP2, numbers=((5, 3), (4, 2))), item(NFO, size=2_000)]


def test_proposal_key_is_16_hex_characters():
    assert re.fullmatch(r"[0-9a-f]{16}", entries.proposal_key(proposal()))
    assert re.fullmatch(r"[0-9a-f]{16}", entries.proposal_key([]))


def test_proposal_key_ignores_order_and_files_that_are_no_videos():
    items = proposal()
    key = entries.proposal_key(items)
    assert entries.proposal_key(list(reversed(items))) == key
    assert entries.proposal_key(items[:2]) == key
    srt = item(f"{SEASON_FOLDER}/Some.Show.S01E01.srt", size=50_000, rejections=[{"reason": "x"}])
    assert entries.proposal_key(items[:2] + [srt, episode_item(SAMPLE)]) == key
    items[1]["episodes"].reverse()
    assert entries.proposal_key(items) == key


def test_proposal_key_ignores_languages_group_and_the_items_own_id():
    items = proposal()
    key = entries.proposal_key(items)
    items[0]["languages"] = [{"id": 1, "name": "English"}]
    items[0]["releaseGroup"] = "OTHER"
    items[0]["id"] = 654321
    assert entries.proposal_key(items) == key


def change_path(items):
    items[0]["path"] = items[0]["path"].replace("S01E01", "S01E01.REPACK")


def change_series(items):
    items[0]["series"] = {"id": 11, "title": "Other Show"}


def change_season(items):
    items[0]["seasonNumber"] = 2


def change_episodes(items):
    items[0]["episodes"] = [episode(9, 1)]


def change_quality(items):
    items[0]["quality"]["quality"] = {"id": 3, "name": "WEBDL-720p"}


def change_version(items):
    items[0]["quality"]["revision"]["version"] = 2


def change_real(items):
    items[0]["quality"]["revision"]["real"] = 1


def change_repack(items):
    items[0]["quality"]["revision"]["isRepack"] = True


def drop_a_video(items):
    del items[1]


@pytest.mark.parametrize("change", [change_path, change_series, change_season, change_episodes, change_quality,
                                    change_version, change_real, change_repack, drop_a_video])
def test_proposal_key_changes_with_path_target_quality_or_revision(change):
    items = proposal()
    key = entries.proposal_key(items)
    change(items)
    assert entries.proposal_key(items) != key


def test_proposal_key_changes_with_the_movie():
    key = entries.proposal_key([movie_item()])
    assert entries.proposal_key([movie_item(movie={"id": 2, "title": "Other Movie", "year": 2021})]) != key


# --- texts and views for the page -------------------------------------------------

def test_target_text_of_a_movie():
    assert entries.item_target_text(movie_item(), "radarr") == "Some Movie (2020)"
    assert entries.item_target_text(movie_item(movie={"id": 1, "title": "Some Movie", "year": 0}),
                                    "radarr") == "Some Movie"
    assert entries.item_target_text(movie_item(movie={"id": 1}), "radarr") == "movie 1"
    assert entries.item_target_text(movie_item(movie=DROP), "radarr") == "no target"


def test_target_text_of_episodes_is_sorted_by_episode_number():
    assert entries.item_target_text(episode_item(numbers=((5, 3), (3, 1), (4, 2))),
                                    "sonarr") == "Some Show S01E01E02E03"
    assert entries.item_target_text(episode_item(seasonNumber=0, numbers=((7, 4),)), "sonarr") == "Some Show S00E04"
    assert entries.item_target_text(episode_item(series={"id": 10}), "sonarr") == "series 10 S01E01"
    assert entries.item_target_text(episode_item(episodes=DROP), "sonarr") == "no target"


def test_target_text_of_several_files_names_each_target_once():
    items = [episode_item(EP1), episode_item(EP2, numbers=((4, 2),)), episode_item(EP3)]
    assert entries.target_text(items, "sonarr") == "Some Show S01E01, Some Show S01E02"
    assert entries.target_text([], "sonarr") == "no target"
    assert entries.target_text([movie_item(movie=DROP), movie_item()], "radarr") == "no target, Some Movie (2020)"


def test_candidate_view_of_an_episode_file():
    view = entries.candidate_view(episode_item(EP1, numbers=((4, 2), (3, 1))), "sonarr")
    assert view == {
        "path": EP1, "relative_path": "Some.Show.S01E01.German.1080p.WEB.h264-GRP.mkv", "size": 2_000_000_000,
        "video": True, "target": "Some Show S01E01E02", "movie_id": None, "series_id": 10, "season_number": 1,
        "episode_ids": [3, 4], "episode_numbers": [1, 2], "quality": "WEBDL-1080p", "languages": ["German"],
        "release_group": "GRP", "rejections": []}
    assert json.loads(json.dumps(view)) == view


def test_candidate_view_of_a_movie_file_with_an_objection():
    video = movie_item(rejections=[{"reason": "Not an upgrade for existing movie file. Existing quality: "
                                              "Bluray-1080p. New Quality WEBDL-1080p.", "type": "permanent"}])
    assert entries.candidate_view(video, "radarr") == {
        "path": MOVIE_PATH, "relative_path": f"{MOVIE_RELEASE}.mkv", "size": 4_000_000_000, "video": True,
        "target": "Some Movie (2020)", "movie_id": 1, "series_id": None, "season_number": None,
        "episode_ids": [], "episode_numbers": [], "quality": "WEBDL-1080p", "languages": ["German"],
        "release_group": "GRP",
        "rejections": ["Not an upgrade for existing movie file. Existing quality: Bluray-1080p. "
                       "New Quality WEBDL-1080p."]}


def test_candidate_view_shows_a_partial_target_and_files_that_are_no_videos():
    partial = entries.candidate_view(episode_item(seasonNumber=DROP), "sonarr")
    assert (partial["target"], partial["series_id"], partial["season_number"], partial["episode_ids"]) == (
        "no target", 10, None, [3])
    nfo = entries.candidate_view(item(NFO, size=2_000, quality=DROP, languages=DROP, releaseGroup=None), "sonarr")
    assert (nfo["video"], nfo["target"], nfo["quality"], nfo["languages"], nfo["release_group"]) == (
        False, "no target", "", [], "")


@pytest.mark.parametrize("odd", [{}, None, "x.mkv"])
def test_candidate_view_of_an_odd_item_is_empty(odd):
    assert entries.candidate_view(odd, "radarr") == {
        "path": "", "relative_path": "", "size": 0, "video": False, "target": "no target", "movie_id": None,
        "series_id": None, "season_number": None, "episode_ids": [], "episode_numbers": [], "quality": "",
        "languages": [], "release_group": "", "rejections": []}


# --- the ManualImport command -------------------------------------------------------

def test_radarr_file_payload_has_exactly_the_fields_the_app_sends():
    video = movie_item(indexerFlags=8)
    assert entries.file_payload(video, "dl-1", "radarr") == {
        "path": MOVIE_PATH, "folderName": "Some.Release.German.1080p", "quality": QUALITY, "languages": GERMAN,
        "releaseGroup": "GRP", "indexerFlags": 8, "downloadId": "dl-1", "movieId": 1}


def test_sonarr_file_payload_has_exactly_the_fields_the_app_sends():
    video = episode_item(EP1, numbers=((4, 2), (3, 1)), releaseType="multiEpisode")
    assert entries.file_payload(video, "dl-1", "sonarr") == {
        "path": EP1, "folderName": "Some.Release.German.1080p", "quality": QUALITY, "languages": GERMAN,
        "releaseGroup": "GRP", "indexerFlags": 0, "downloadId": "dl-1", "seriesId": 10, "episodeIds": [4, 3],
        "releaseType": "multiEpisode"}


def test_missing_optional_fields_get_the_app_defaults():
    video = episode_item(folderName=DROP, releaseGroup=DROP, languages=DROP, indexerFlags=DROP, releaseType=DROP)
    payload = entries.file_payload(video, "dl-1", "sonarr")
    assert (payload["folderName"], payload["releaseGroup"], payload["languages"], payload["indexerFlags"],
            payload["releaseType"]) == (None, None, [], 0, "unknown")
    assert entries.file_payload(movie_item(indexerFlags=True), "dl-1", "radarr")["indexerFlags"] == 0


def test_file_payload_copies_quality_and_languages():
    video = movie_item()
    payload = entries.file_payload(video, "dl-1", "radarr")
    payload["quality"]["quality"]["id"] = 99
    payload["languages"].append({"id": 1, "name": "English"})
    assert (video["quality"], video["languages"]) == (QUALITY, GERMAN)


@pytest.mark.parametrize(("arr_type", "fields"), [
    ("radarr", {"movie": DROP}),
    ("radarr", {"quality": DROP}),
    ("radarr", {"quality": UNKNOWN_QUALITY}),
    ("radarr", {"path": DROP}),
    ("sonarr", {"episodes": DROP}),
    ("sonarr", {"seasonNumber": DROP}),
    ("sonarr", {"quality": None}),
])
def test_a_half_file_is_never_built(arr_type, fields):
    video = movie_item(**fields) if arr_type == "radarr" else episode_item(**fields)
    with pytest.raises(ValueError):
        entries.file_payload(video, "dl-1", arr_type)


def test_command_body_lists_one_file_per_video_in_answer_order():
    second, first = episode_item(EP2, numbers=((4, 2),)), episode_item(EP1)
    body = entries.command_body([second, first], "dl-1", "sonarr")
    assert body == {"name": "ManualImport", "importMode": "auto",
                    "files": [entries.file_payload(second, "dl-1", "sonarr"),
                              entries.file_payload(first, "dl-1", "sonarr")]}
    assert json.loads(json.dumps(body)) == body
    assert "changeCategory" not in json.dumps(body)


def test_command_body_needs_at_least_one_file():
    with pytest.raises(ValueError):
        entries.command_body([], "dl-1", "radarr")


def test_command_body_refuses_a_proposal_with_a_half_file():
    with pytest.raises(ValueError):
        entries.command_body([movie_item(), movie_item(OTHER_MOVIE_PATH, quality=DROP)], "dl-1", "radarr")


# --- purity --------------------------------------------------------------------------

def test_entries_uses_the_standard_library_only():
    tree = ast.parse(Path(entries.__file__).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= set(sys.stdlib_module_names)
