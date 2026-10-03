"""Downloads *arr holds back for a manual import, and whether the app's
proposal may be imported as it is. Pure: no network, no database, standard
library only.

A queue record counts when trackedDownloadState is importBlocked, or
importPending with trackedDownloadStatus warning (a single rejected file
keeps a download importPending for ever). Releases a delay profile holds
back carry neither a state nor a downloadId and never count. Sonarr sends
one record per episode, all with the same downloadId; group_blocked() makes
one download of them.

The ManualImport command of *arr checks nothing: it imports a file the app
objects to (also "Not an upgrade for existing ... file", which replaces a file
as good or better), and two files for the same movie or episode both land,
the second replacing the first. assess() therefore locks "Import" unless
every video file has a target, a quality and no objection, and no target
appears twice.
"""

import copy
import dataclasses
import hashlib
import json
import posixpath
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

# Video candidates like the external auto-import: extension, no "sample" in
# the relative path (folders included), at least 50 MiB.
VIDEO_EXTENSIONS: frozenset[str] = frozenset({".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts", ".wmv"})
MIN_VIDEO_BYTES = 50 * 1024 * 1024
STATE_BLOCKED = "importBlocked"
STATE_PENDING = "importPending"
STATUS_WARNING = "warning"
ARR_TYPES = ("radarr", "sonarr")

# Why "Import" is locked (shown on the button and in the 409 detail)
WHY_NO_VIDEO = "No video file in this download"
WHY_REJECTED = "The app objects: {reasons}"          # reasons: rejections[].reason of all videos, de-duplicated, "; "-joined
WHY_NO_MOVIE = "A video file has no movie"
WHY_NO_EPISODES = "A video file has no series, season or episodes"
WHY_NO_QUALITY = "A video file has no quality"
WHY_DUPLICATE = "Two video files for the same movie or episode"

_NO_TARGET = "no target"
_NO_REASON = "no reason given"   # an objection without a text still locks


@dataclass(frozen=True)
class BlockedDownload:
    """One download *arr holds back, grouped from its queue records
    (Sonarr: one record per episode, all with the same downloadId)."""

    download_id: str
    title: str                       # "title" of the first record ("" if missing)
    queue_ids: tuple[int, ...]       # ids of all counted records, queue order, unique
    size: int                        # bytes, once per download: max of the records' "size"
    added: str | None                # "added" of the first record that has one, as *arr sends it
    download_client: str             # "downloadClient" of the first record that has one ("" if none)
    state: str                       # STATE_BLOCKED or STATE_PENDING (first counted record)
    messages: tuple[str, ...]        # statusMessages[].messages[] (the title when a list is empty),
                                     # then errorMessage; de-duplicated, order kept
    movie_id: int | None = None      # Radarr "movieId" of the first record that has one
    series_id: int | None = None     # Sonarr "seriesId" of the first record that has one
    episode_ids: tuple[int, ...] = ()  # Sonarr "episodeId" of the records, sorted, unique

    def as_dict(self) -> dict:
        """The JSON shape of GET /api/imports downloads[] (lists for tuples)."""
        return {key: list(value) if isinstance(value, tuple) else value
                for key, value in dataclasses.asdict(self).items()}


@dataclass(frozen=True)
class Assessment:
    """May the app's proposal be imported as it is (spec conditions 1-5)?"""

    importable: bool
    why_not: str                                      # "" when importable, else one WHY_* text
    videos: tuple[dict, ...] = field(default=(), repr=False)   # the raw video candidates, answer order (memory only)
    movie_id: int | None = None      # Radarr: every video has a movie and all name the same one
    series_id: int | None = None     # Sonarr: every video has a full target and all name the same series
    episode_ids: tuple[int, ...] = ()  # Sonarr: sorted union of the videos' episode ids (only with series_id)


def _int(value) -> int | None:
    """value when it is an int and not a bool, else None."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _positive_id(value) -> int | None:
    number = _int(value)
    return number if number is not None and number > 0 else None


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _text(value) -> str:
    return value if isinstance(value, str) else ""


def _size(value) -> int:
    """Bytes as *arr sends them (the queue sends a decimal); 0 when unusable."""
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _unique(values) -> tuple:
    return tuple(dict.fromkeys(values))


def _check_type(arr_type: str) -> None:
    if arr_type not in ARR_TYPES:
        raise ValueError(f"Unknown instance type: {arr_type}")


def is_open_record(record: dict) -> bool:
    """trackedDownloadState == importBlocked, or importPending with
    trackedDownloadStatus == warning; and a non-empty str downloadId.
    Held-back releases (no state, no downloadId) never count."""
    if not isinstance(record, dict):
        return False
    download_id = record.get("downloadId")
    if not isinstance(download_id, str) or not download_id:
        return False
    state = record.get("trackedDownloadState")
    if state == STATE_BLOCKED:
        return True
    return state == STATE_PENDING and record.get("trackedDownloadStatus") == STATUS_WARNING


def _first_text(records: list[dict], key: str) -> str | None:
    for record in records:
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _first_id(records: list[dict], key: str) -> int | None:
    for record in records:
        value = _positive_id(record.get(key))
        if value is not None:
            return value
    return None


def _messages(records: list[dict]) -> tuple[str, ...]:
    found = []
    for record in records:
        for status in _list(record.get("statusMessages")):
            status = _dict(status)
            texts = [text for text in _list(status.get("messages")) if isinstance(text, str) and text]
            if texts:
                found.extend(texts)
            elif _text(status.get("title")):
                found.append(status["title"])
    for record in records:
        if _text(record.get("errorMessage")):
            found.append(record["errorMessage"])
    return _unique(found)


def _download(download_id: str, records: list[dict]) -> BlockedDownload:
    first = records[0]
    episode_ids = {_positive_id(record.get("episodeId")) for record in records} - {None}
    return BlockedDownload(
        download_id=download_id,
        title=_text(first.get("title")),
        queue_ids=_unique(record["id"] for record in records),
        size=max(_size(record.get("size")) for record in records),
        added=_first_text(records, "added"),
        download_client=_first_text(records, "downloadClient") or "",
        state=first["trackedDownloadState"],
        messages=_messages(records),
        movie_id=_first_id(records, "movieId"),
        series_id=_first_id(records, "seriesId"),
        episode_ids=tuple(sorted(episode_ids)),
    )


def _timestamp(value: str | None) -> float | None:
    """*arr's "added" as seconds since the epoch; None when missing or unreadable."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _sort_key(download: BlockedDownload) -> tuple:
    stamp = _timestamp(download.added)
    return (stamp is None, stamp if stamp is not None else 0.0, download.title, download.download_id)


def group_blocked(records: Iterable) -> list[BlockedDownload]:
    """is_open_record() records grouped by downloadId (non-dicts skipped).
    A record without an integer id is left out too: *arr always sends one,
    and without it the download could not be discarded.
    Order: oldest "added" first, downloads without "added" last, then title, then download_id."""
    groups: dict[str, list[dict]] = {}
    for record in records or ():
        if is_open_record(record) and _int(record.get("id")) is not None:
            groups.setdefault(record["downloadId"], []).append(record)
    downloads = [_download(download_id, group) for download_id, group in groups.items()]
    downloads.sort(key=_sort_key)
    return downloads


def is_video_candidate(item: dict) -> bool:
    """Like the external auto-import: name = relativePath or path; extension
    (lower case, last component) in VIDEO_EXTENSIONS; "sample" not in name.lower();
    int size >= MIN_VIDEO_BYTES."""
    if not isinstance(item, dict):
        return False
    name = item.get("relativePath") or item.get("path")
    if not isinstance(name, str) or not name:
        return False
    lowered = name.lower()
    extension = posixpath.splitext(re.split(r"[\\/]", lowered)[-1])[1]
    if extension not in VIDEO_EXTENSIONS or "sample" in lowered:
        return False
    size = _int(item.get("size"))
    return size is not None and size >= MIN_VIDEO_BYTES


def video_candidates(items: Iterable) -> list[dict]:
    """The dict items passing is_video_candidate(), in answer order."""
    return [item for item in items or () if is_video_candidate(item)]


def has_target(item: dict, arr_type: str) -> bool:
    """radarr: movie is a dict with int id > 0.
    sonarr: series dict with int id > 0, seasonNumber an int (not bool),
    episodes a non-empty list of dicts, each with int id > 0."""
    _check_type(arr_type)
    if not isinstance(item, dict):
        return False
    if arr_type == "radarr":
        return _positive_id(_dict(item.get("movie")).get("id")) is not None
    if _positive_id(_dict(item.get("series")).get("id")) is None:
        return False
    if _int(item.get("seasonNumber")) is None:
        return False
    episodes = item.get("episodes")
    return (isinstance(episodes, list) and bool(episodes)
            and all(isinstance(e, dict) and _positive_id(e.get("id")) is not None for e in episodes))


def has_quality(item: dict) -> bool:
    """quality.quality.id is an int > 0 ("Unknown" = 0 does not count)."""
    if not isinstance(item, dict):
        return False
    return _positive_id(_dict(_dict(item.get("quality")).get("quality")).get("id")) is not None


def _rejections(item: dict) -> list[str]:
    """rejections[].reason; an objection without a text still counts."""
    raw = item.get("rejections")
    if not raw:
        return []
    if not isinstance(raw, list):
        return [_NO_REASON]
    return [_text(_dict(rejection).get("reason")).strip() or _NO_REASON for rejection in raw]


def _agreed_target(videos: tuple[dict, ...], arr_type: str) -> dict:
    """movie_id, or series_id and episode_ids, when every video has a full
    target and they all name the same movie or series; else nothing."""
    if not videos or not all(has_target(video, arr_type) for video in videos):
        return {}
    if arr_type == "radarr":
        movies = {video["movie"]["id"] for video in videos}
        return {"movie_id": movies.pop()} if len(movies) == 1 else {}
    series = {video["series"]["id"] for video in videos}
    if len(series) != 1:
        return {}
    episodes = {episode["id"] for video in videos for episode in video["episodes"]}
    return {"series_id": series.pop(), "episode_ids": tuple(sorted(episodes))}


def _duplicate_target(videos: tuple[dict, ...], arr_type: str) -> bool:
    if arr_type == "radarr":
        movies = [video["movie"]["id"] for video in videos]
        return len(movies) != len(set(movies))
    seen: set[int] = set()
    for video in videos:
        episodes = {episode["id"] for episode in video["episodes"]}
        if episodes & seen:
            return True
        seen |= episodes
    return False


def assess(items: Iterable, arr_type: str) -> Assessment:
    """Checks in this order, the first failing one is why_not:
    no video -> WHY_NO_VIDEO; any video with rejections -> WHY_REJECTED;
    a video without target -> WHY_NO_MOVIE / WHY_NO_EPISODES; a video without
    quality -> WHY_NO_QUALITY; Radarr two videos with the same movie id, or
    Sonarr an episode id in two videos -> WHY_DUPLICATE.
    Non-video candidates are ignored entirely (also their rejections).
    movie_id / series_id / episode_ids are filled whenever every video has
    a target and they agree, also when the import is locked (the verdict
    still needs them). arr_type not in ARR_TYPES -> ValueError.

    The app's fallback answer for a file it could not evaluate (no target,
    no quality, an empty objection list) is locked by the target check."""
    _check_type(arr_type)
    videos = tuple(video_candidates(items))
    target = _agreed_target(videos, arr_type)

    def locked(why_not: str) -> Assessment:
        return Assessment(importable=False, why_not=why_not, videos=videos, **target)

    if not videos:
        return locked(WHY_NO_VIDEO)
    reasons = _unique(reason for video in videos for reason in _rejections(video))
    if reasons:
        return locked(WHY_REJECTED.format(reasons="; ".join(reasons)))
    if not all(has_target(video, arr_type) for video in videos):
        return locked(WHY_NO_MOVIE if arr_type == "radarr" else WHY_NO_EPISODES)
    if not all(has_quality(video) for video in videos):
        return locked(WHY_NO_QUALITY)
    if _duplicate_target(videos, arr_type):
        return locked(WHY_DUPLICATE)
    return Assessment(importable=True, why_not="", videos=videos, **target)


def proposal_key(items: Iterable) -> str:
    """16 hex characters over path, target, quality and revision of every
    video candidate. Independent of item order and of non-video items; the
    page sends it back with "Import", and a changed proposal is refused."""
    rows = []
    for item in video_candidates(items):
        movie = item["movie"] if isinstance(item.get("movie"), dict) else {}
        series = item["series"] if isinstance(item.get("series"), dict) else {}
        episodes = item["episodes"] if isinstance(item.get("episodes"), list) else []
        quality = item["quality"] if isinstance(item.get("quality"), dict) else {}
        inner = quality["quality"] if isinstance(quality.get("quality"), dict) else {}
        revision = quality["revision"] if isinstance(quality.get("revision"), dict) else {}
        season = _int(item.get("seasonNumber"))
        rows.append([
            str(item.get("path") or ""),
            _int(movie.get("id")) or 0,
            _int(series.get("id")) or 0,
            season if season is not None else -1,
            sorted(_int(e.get("id")) or 0 for e in episodes if isinstance(e, dict)),
            _int(inner.get("id")) or 0,
            _int(revision.get("version")) or 0,
            _int(revision.get("real")) or 0,
            revision.get("isRepack") is True,
        ])
    rows.sort()
    text = json.dumps(rows, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def item_target_text(item: dict, arr_type: str) -> str:
    """radarr: "<movie.title> (<movie.year>)" ("(year)" left out when 0);
    sonarr: "<series.title> S01E02E03" (episodes sorted by episodeNumber);
    without target: "no target"."""
    if not has_target(item, arr_type):
        return _NO_TARGET
    if arr_type == "radarr":
        movie = item["movie"]
        title = _text(movie.get("title")) or f"movie {movie['id']}"
        year = _int(movie.get("year")) or 0
        return f"{title} ({year})" if year > 0 else title
    series = item["series"]
    title = _text(series.get("title")) or f"series {series['id']}"
    numbers = sorted(n for n in (_int(e.get("episodeNumber")) for e in item["episodes"]) if n is not None)
    return f"{title} S{item['seasonNumber']:02d}" + "".join(f"E{n:02d}" for n in numbers)


def target_text(items: Iterable[dict], arr_type: str) -> str:
    """The distinct item_target_text() of the items, ", "-joined; "no target" if there is none."""
    _check_type(arr_type)
    return ", ".join(_unique(item_target_text(item, arr_type) for item in items or ())) or _NO_TARGET


def candidate_view(item: dict, arr_type: str) -> dict:
    """One entry of the proposal's "candidates" (GET /api/imports/{id}/proposal),
    built from a /manualimport item; non-videos too (video: false)."""
    _check_type(arr_type)
    item = item if isinstance(item, dict) else {}
    sonarr = arr_type == "sonarr"
    episodes = [episode for episode in _list(item.get("episodes")) if isinstance(episode, dict)]
    episode_ids = sorted(i for i in (_positive_id(e.get("id")) for e in episodes) if i is not None)
    episode_numbers = sorted(n for n in (_int(e.get("episodeNumber")) for e in episodes) if n is not None)
    languages = [_text(_dict(language).get("name")) for language in _list(item.get("languages"))]
    return {
        "path": _text(item.get("path")),
        "relative_path": _text(item.get("relativePath")),
        "size": _size(item.get("size")),
        "video": is_video_candidate(item),
        "target": item_target_text(item, arr_type),
        "movie_id": None if sonarr else _positive_id(_dict(item.get("movie")).get("id")),
        "series_id": _positive_id(_dict(item.get("series")).get("id")) if sonarr else None,
        "season_number": _int(item.get("seasonNumber")) if sonarr else None,
        "episode_ids": episode_ids if sonarr else [],
        "episode_numbers": episode_numbers if sonarr else [],
        "quality": _text(_dict(_dict(item.get("quality")).get("quality")).get("name")),
        "languages": [name for name in languages if name],
        "release_group": _text(item.get("releaseGroup")),
        "rejections": _rejections(item),
    }


def file_payload(item: dict, download_id: str, arr_type: str) -> dict:
    """One file of the ManualImport command: the fields the apps' own pages
    send. ValueError when the item has no target, no quality or no path
    (never send a half file)."""
    if not has_target(item, arr_type):
        raise ValueError("a file without a target is never sent")
    if not has_quality(item):
        raise ValueError("a file without a quality is never sent")
    path = item.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError("a file without a path is never sent")
    payload = {
        "path": path,
        "folderName": item.get("folderName"),
        "quality": copy.deepcopy(item["quality"]),
        "languages": copy.deepcopy(item.get("languages") or []),
        "releaseGroup": item.get("releaseGroup"),
        "indexerFlags": _int(item.get("indexerFlags")) or 0,
        "downloadId": download_id,
    }
    if arr_type == "radarr":
        payload["movieId"] = item["movie"]["id"]
    else:
        payload["seriesId"] = item["series"]["id"]
        payload["episodeIds"] = [episode["id"] for episode in item["episodes"]]
        payload["releaseType"] = item.get("releaseType") or "unknown"
    return payload


def command_body(videos: Iterable[dict], download_id: str, arr_type: str) -> dict:
    """{"name": "ManualImport", "importMode": "auto", "files": [file_payload(...) for each video, answer order]}.
    importMode auto: *arr moves the files when the download client allows it
    (SABnzbd does), else it copies. No files -> ValueError."""
    _check_type(arr_type)
    files = [file_payload(video, download_id, arr_type) for video in videos]
    if not files:
        raise ValueError("a ManualImport without files is never sent")
    return {"name": "ManualImport", "importMode": "auto", "files": files}
