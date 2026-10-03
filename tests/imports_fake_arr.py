"""Radarr/Sonarr stand-in for the Imports feature, shared by the h2-h4 tests.

No test_ prefix: pytest does not collect this file, the test files import
it (tests is a package). The double answers the way the apps do where the
feature depends on it: the queue comes in pages and leaves out downloads
without a movie or series unless asked, GET /queue/details is the whole
queue in one answer (unpaged, unknown items included), GET /manualimport
refuses movieId and seriesId next to downloadId, a DELETE of one queue
record removes the whole download (all episode records), and POST /command
keeps the command so GET /command and GET /command/{id} can report it.
GET /history does not filter by eventType: the feature has to look at each
record's event type itself.

Builders leave keys out whose value is None, as *arr does with null. Paths
in test data start with /downloads/. The indexer key exists only inside
grab history data: no answer of the feature may ever carry it.
"""

import copy
import zlib

import requests
from urllib3.exceptions import MaxRetryError, NewConnectionError

from backend.agents.base import BaseAgent

API_KEY = "ARRSECRETKEY1234567890"
INDEXER_KEY = "INDEXERSECRET123"      # appears only in history data; must never leak
QUEUE = "/api/v3/queue"
QUEUE_STATUS = "/api/v3/queue/status"
QUEUE_DETAILS = "/api/v3/queue/details"
SYSTEM_STATUS = "/api/v3/system/status"
MANUAL_IMPORT = "/api/v3/manualimport"
COMMAND = "/api/v3/command"
PARSE = "/api/v3/parse"
EPISODE = "/api/v3/episode"
HISTORY = "/api/v3/history"
SONARR_BY_ID = ("Found matching series via grab history, but release was matched to series by ID. "
                "Automatic import is not possible. See the FAQ for details.")
RADARR_BY_ID = ("Found matching movie via grab history, but release was matched to movie by ID. "
                "Manual Import required.")

DEFAULT = object()   # sentinel: "use the default value"; None means "left out"


def http_error(status: int) -> requests.exceptions.HTTPError:
    response = requests.Response()
    response.status_code = status
    return requests.exceptions.HTTPError(f"{status} answer", response=response)


def refused() -> requests.exceptions.ConnectionError:
    """What requests raises when nothing listens: the request never left."""
    return requests.exceptions.ConnectionError(
        MaxRetryError(None, QUEUE, NewConnectionError(None, "Connection refused")))


def timed_out() -> requests.exceptions.ReadTimeout:
    return requests.exceptions.ReadTimeout("Read timed out. (read timeout=10)")


def _without_none(data: dict) -> dict:
    return {key: value for key, value in data.items() if value is not None}


def config(instance_id: int = 1, arr_type: str = "sonarr", **extra) -> dict:
    data = {"id": instance_id, "name": "Radarr" if arr_type == "radarr" else "Sonarr", "type": arr_type,
            "url": "http://127.0.0.1:9", "api_key": API_KEY, "enabled": 1, "checked_search_settings": {}}
    data.update(extra)
    return data


def queue_record(record_id: int, download_id: str | None, title: str = "Some.Show.S01E01.German.1080p.WEB.h264-GRP",
                 *, state: str | None = "importBlocked", status: str | None = "warning",
                 messages: tuple[str, ...] = (SONARR_BY_ID,), error_message: str | None = None,
                 size: int = 2_000_000_000, added: str | None = "2026-10-02T10:00:00Z",
                 client: str = "SABnzbd", movie_id: int | None = None, series_id: int | None = None,
                 episode_id: int | None = None, season: int | None = None) -> dict:
    """One record of GET /queue. Sonarr: one record per episode, all with
    the same downloadId and the full size repeated."""
    return _without_none({
        "id": record_id, "title": title, "size": size, "sizeleft": 0, "status": "completed",
        "trackedDownloadStatus": status, "trackedDownloadState": state,
        "statusMessages": [{"title": title, "messages": list(messages)}] if messages else None,
        "errorMessage": error_message, "downloadId": download_id, "protocol": "usenet",
        "downloadClient": client, "downloadClientHasPostImportCategory": False,
        "outputPath": f"/downloads/complete/{title}", "added": added,
        "movieId": movie_id, "seriesId": series_id, "episodeId": episode_id, "seasonNumber": season,
    })


def delay_record(record_id: int, title: str, **ids) -> dict:
    """A release a delay profile holds back: status "delay", no downloadId,
    no trackedDownloadState. ids: movie_id, series_id, episode_id, season."""
    names = {"movie_id": "movieId", "series_id": "seriesId", "episode_id": "episodeId", "season": "seasonNumber"}
    data = {"id": record_id, "title": title, "status": "delay", "size": 2_000_000_000, "sizeleft": 2_000_000_000,
            "protocol": "usenet", "added": "2026-10-02T09:00:00Z"}
    for key, value in ids.items():
        data[names[key]] = value
    return data


def quality(quality_id: int = 7, name: str = "WEBDL-1080p", version: int = 1) -> dict:
    return {"quality": {"id": quality_id, "name": name, "source": "web", "resolution": 1080},
            "revision": {"version": version, "real": 0, "isRepack": False}}


def movie(movie_id: int, title: str, year: int, **extra) -> dict:
    """GET /api/v3/movie/{id}: no movieFile unless given."""
    data = {"id": movie_id, "title": title, "originalTitle": title, "alternateTitles": [], "year": year,
            "monitored": True}
    data.update(extra)
    data.setdefault("hasFile", isinstance(data.get("movieFile"), dict))
    return data


def series(series_id: int, title: str, year: int, alternate_titles=(), **extra) -> dict:
    """GET /api/v3/series/{id}, with alternateTitles."""
    data = {"id": series_id, "title": title, "year": year, "monitored": True,
            "alternateTitles": [{"title": t, "seasonNumber": -1} for t in alternate_titles]}
    data.update(extra)
    return data


def episode(episode_id: int, series_id: int, season: int, number: int, air_date_utc: str | None, *,
            absolute: int | None = None, scene_name: str | None = None, relative_path: str | None = None) -> dict:
    """GET /api/v3/episode?episodeIds=…&includeEpisodeFile=true, one entry.
    episodeFile only when scene_name or relative_path is given."""
    episode_file = None
    if scene_name is not None or relative_path is not None:
        episode_file = _without_none({"id": 1000 + episode_id, "sceneName": scene_name,
                                      "relativePath": relative_path})
    return _without_none({
        "id": episode_id, "seriesId": series_id, "seasonNumber": season, "episodeNumber": number,
        "title": f"Episode {number}", "airDateUtc": air_date_utc, "absoluteEpisodeNumber": absolute,
        "hasFile": episode_file is not None, "monitored": True, "episodeFile": episode_file,
    })


def _item_id(path: str) -> int:
    return zlib.crc32(path.encode("utf-8")) & 0x7FFFFFFF


def _common_item(path, size, quality_res, languages, rejections, release_group, folder, indexer_flags) -> dict:
    return {
        "id": _item_id(path), "path": path, "relativePath": path.rsplit("/", 1)[-1], "folderName": folder,
        "size": size, "releaseGroup": release_group,
        "quality": quality() if quality_res is DEFAULT else copy.deepcopy(quality_res),
        "languages": [{"id": 4, "name": "German"}] if languages is DEFAULT else copy.deepcopy(languages),
        "qualityWeight": 0, "customFormats": [], "customFormatScore": 0, "indexerFlags": indexer_flags,
        "rejections": [{"reason": reason, "type": "permanent"} for reason in rejections],
    }


def radarr_item(path: str, movie_res: dict | None, *, size: int = 4_000_000_000, quality_res=DEFAULT,
                languages=DEFAULT, rejections: tuple[str, ...] = (), release_group: str | None = "GRP",
                folder: str | None = "Some.Movie.2020.German.1080p", indexer_flags: int = 0) -> dict:
    """One entry of GET /manualimport (Radarr)."""
    data = _common_item(path, size, quality_res, languages, rejections, release_group, folder, indexer_flags)
    data["movie"] = copy.deepcopy(movie_res)
    return _without_none(data)


def sonarr_item(path: str, series_res: dict | None, season: int | None, episodes: list[dict] | None, *,
                size: int = 2_000_000_000, quality_res=DEFAULT, languages=DEFAULT, rejections: tuple[str, ...] = (),
                release_group: str | None = "GRP", folder: str | None = "Some.Show.S01E01.German.1080p",
                indexer_flags: int = 0, release_type: str | None = "singleEpisode") -> dict:
    """One entry of GET /manualimport (Sonarr). The fallback entry of an
    unreadable file: sonarr_item(path, None, None, None, quality_res=None, languages=None)."""
    data = _common_item(path, size, quality_res, languages, rejections, release_group, folder, indexer_flags)
    data.update({"series": copy.deepcopy(series_res), "seasonNumber": season,
                 "episodes": copy.deepcopy(episodes), "releaseType": release_type})
    return _without_none(data)


def grab_record(download_id: str, published: str | None = "2026-10-01T03:00:00Z") -> dict:
    """One "grabbed" record of GET /history. data carries links with the
    indexer key, like the real thing."""
    data = _without_none({
        "publishedDate": published,
        "downloadUrl": f"http://127.0.0.1:9/download?apikey={INDEXER_KEY}",
        "guid": "http://127.0.0.1:9/details/abc",
        "nzbInfoUrl": f"http://127.0.0.1:9/details/abc?apikey={INDEXER_KEY}",
        "seriesMatchType": "Id", "releaseSource": "Rss",
    })
    return {"id": 1, "eventType": "grabbed", "downloadId": download_id, "date": "2026-10-01T03:05:00Z",
            "sourceTitle": "Some.Show.S01E01.German.1080p.WEB.h264-GRP", "data": data}


def import_record(download_id: str) -> dict:
    """One "downloadFolderImported" record of GET /history (eventType 3), as
    the app writes it when a file of the download was imported."""
    return {"id": 2, "eventType": "downloadFolderImported", "downloadId": download_id,
            "date": "2026-10-02T12:00:05Z", "sourceTitle": "Some.Show.S01E01.German.1080p.WEB.h264-GRP",
            "data": {"downloadClient": "SABnzbd", "droppedPath": "/downloads/complete/Some.Show.S01E01/x.mkv",
                     "importedPath": "/downloads/library/Some Show/Season 01/x.mkv"}}


class FakeArr(BaseAgent):
    """Radarr/Sonarr stand-in for the Imports feature.

    Canned state (tests may change it between calls): queue, queue_status
    (None: computed from queue), system_status (None: started long ago),
    manual_imports (download id -> items or exception), parses (release
    title -> answer or exception; missing: nothing parsed), movies, series,
    episodes (id -> resource), history (download id -> records or exception;
    not filtered by eventType), commands (id -> resource or exception).
    get_errors: path prefix, or exact path with a trailing "$", -> exception
    (prefix: QUEUE also matches QUEUE_STATUS and QUEUE_DETAILS, use QUEUE +
    "$" for the paged queue alone). on_get: path -> hook(fake), run before
    that GET is answered. post_error: raised before the command is kept (the
    app never got it); post_lost: raised after the command is kept (the app
    took it, the answer got lost); post_delayed: raised at once, the body
    waits in `delayed` until arrive() (a proxy forwards the POST late).
    on_post: path -> hook(fake), run after the command is kept and before
    the answer goes back.

    Recorded: gets (path, params), timeouts (path -> timeout of the last GET,
    POST or DELETE on it), posts (path, body), deletes (path, params) and
    logged (level, skill, message). log() writes no database row and
    broadcasts nothing."""

    def __init__(self, cfg: dict | None = None, *, queue=(), queue_status: dict | None = None,
                 system_status: dict | None = None, manual_imports: dict | None = None,
                 parses: dict | None = None, movies=(), series_list=(), episodes=(),
                 history: dict | None = None, commands=(), get_errors: dict | None = None,
                 post_error: Exception | None = None, post_lost: Exception | None = None,
                 post_delayed: Exception | None = None, delete_errors: dict | None = None,
                 on_get: dict | None = None, on_post: dict | None = None):
        super().__init__(cfg if cfg is not None else config())
        self.queue = [copy.deepcopy(record) for record in queue]
        self.queue_status = queue_status
        self.system_status = system_status
        self.manual_imports = dict(manual_imports or {})
        self.parses = dict(parses or {})
        self.movies = {m["id"]: m for m in movies}
        self.series = {s["id"]: s for s in series_list}
        self.episodes = {e["id"]: e for e in episodes}
        self.history = dict(history or {})
        self.commands = dict(commands) if isinstance(commands, dict) else {c["id"]: c for c in commands}
        self.next_command_id = 500
        self.get_errors = dict(get_errors or {})
        self.post_error = post_error
        self.post_lost = post_lost
        self.post_delayed = post_delayed
        self.delayed: list[dict] = []
        self.delete_errors = dict(delete_errors or {})
        self.on_get = dict(on_get or {})
        self.on_post = dict(on_post or {})
        self.gets: list[tuple[str, dict]] = []
        self.timeouts: dict[str, float] = {}
        self.posts: list[tuple[str, dict]] = []
        self.deletes: list[tuple[str, dict]] = []
        self.logged: list[tuple[str, str, str]] = []

    def build_skills(self):
        return []

    @staticmethod
    def _matches(path: str, pattern: str) -> bool:
        return path == pattern[:-1] if pattern.endswith("$") else path.startswith(pattern)

    def _queue_page(self, params: dict) -> dict:
        assert "page" in params and "pageSize" in params, f"queue read without paging: {params}"
        if self.config.get("type") == "radarr":
            flag, key = "includeUnknownMovieItems", "movieId"
        else:
            flag, key = "includeUnknownSeriesItems", "seriesId"
        records = list(self.queue)
        if params.get(flag) != "true":
            # Like the apps: without the switch a download without a movie or
            # series ("Unknown Series") is left out.
            records = [r for r in records if r.get(key)]
        page, size = int(params["page"]), int(params["pageSize"])
        return {"page": page, "pageSize": size, "sortKey": "timeleft", "sortDirection": "descending",
                "totalRecords": len(records), "records": records[(page - 1) * size: page * size]}

    def _computed_status(self) -> dict:
        count = len(self.queue)
        statuses = [r.get("trackedDownloadStatus") for r in self.queue]
        return {"totalCount": count, "count": count, "unknownCount": 0,
                "errors": "error" in statuses, "warnings": "warning" in statuses,
                "unknownErrors": False, "unknownWarnings": False}

    def _resource(self, store: dict, path: str):
        key = int(path.rsplit("/", 1)[1])
        if key not in store:
            raise http_error(404)
        value = store[key]
        if isinstance(value, Exception):
            raise value
        return value

    def _answer(self, path: str, params: dict):
        if path == QUEUE:
            return self._queue_page(params)
        if path == QUEUE_STATUS:
            return self.queue_status if self.queue_status is not None else self._computed_status()
        if path == QUEUE_DETAILS:
            # Like the apps: the whole queue in one list, no paging, no filter for unknown items.
            assert not params, f"queue details takes no paging or filter here: {params}"
            return list(self.queue)
        if path == SYSTEM_STATUS:
            if self.system_status is not None:
                return self.system_status
            app = "Radarr" if self.config.get("type") == "radarr" else "Sonarr"
            return {"appName": app, "version": "4.0.0.0", "startTime": "2026-09-30T12:00:00Z"}
        if path == MANUAL_IMPORT:
            assert "downloadId" in params, params
            assert params.get("filterExistingFiles") == "true", params
            assert "movieId" not in params and "seriesId" not in params, "Sonarr ignores downloadId then"
            answer = self.manual_imports.get(params["downloadId"], [])
            if isinstance(answer, Exception):
                raise answer
            return answer
        if path == PARSE:
            answer = self.parses.get(params["title"], {"title": params["title"]})
            if isinstance(answer, Exception):
                raise answer
            return answer
        if path.startswith("/api/v3/movie/"):
            return self._resource(self.movies, path)
        if path.startswith("/api/v3/series/"):
            return self._resource(self.series, path)
        if path == EPISODE:
            assert params.get("includeEpisodeFile") == "true", params
            ids = params.get("episodeIds")
            ids = ids if isinstance(ids, (list, tuple)) else [ids]
            return [self.episodes[i] for i in ids if i in self.episodes]
        if path == HISTORY:
            # 1 = grabbed (the verdict), 3 = downloadFolderImported (proof of an import).
            assert params.get("eventType") in (1, 3), params
            records = self.history.get(params.get("downloadId"), [])
            if isinstance(records, Exception):
                raise records
            return {"page": 1, "pageSize": 10, "sortKey": "date", "sortDirection": "descending",
                    "totalRecords": len(records), "records": records}
        if path == COMMAND:
            return [c for c in self.commands.values() if not isinstance(c, Exception)]
        if path.startswith(COMMAND + "/"):
            return self._resource(self.commands, path)
        raise AssertionError(f"unexpected GET {path}")

    def http_get(self, path, params=None, timeout=10):
        params = dict(params or {})
        self.gets.append((path, copy.deepcopy(params)))
        self.timeouts[path] = timeout
        if path in self.on_get:
            self.on_get[path](self)
        for pattern, error in self.get_errors.items():
            if self._matches(path, pattern):
                raise error
        return copy.deepcopy(self._answer(path, params))

    def http_post(self, path, body, timeout=10):
        self.posts.append((path, copy.deepcopy(body)))
        self.timeouts[path] = timeout
        if self.post_error is not None:
            raise self.post_error
        if path != COMMAND:
            raise AssertionError(f"unexpected POST {path}")
        if self.post_delayed is not None:
            self.delayed.append(copy.deepcopy(body))
            raise self.post_delayed     # the answer is lost before the app even got the command
        resource = self._keep(body)
        if path in self.on_post:
            self.on_post[path](self)
        if self.post_lost is not None:
            raise self.post_lost        # the app took the command, the answer never arrived
        return copy.deepcopy(resource)

    def _keep(self, body: dict) -> dict:
        command_id = self.next_command_id
        self.next_command_id += 1
        resource = {"id": command_id, "name": body["name"], "status": "queued", "queued": "2026-10-02T12:00:00Z",
                    "trigger": "manual", "body": copy.deepcopy(body)}
        self.commands[command_id] = resource
        return resource

    def arrive(self) -> list[int]:
        """The delayed POSTs reach the app now: each becomes a queued command."""
        ids = [self._keep(body)["id"] for body in self.delayed]
        self.delayed.clear()
        return ids

    def http_delete(self, path, params=None, timeout=10):
        params = dict(params or {})
        self.deletes.append((path, copy.deepcopy(params)))
        self.timeouts[path] = timeout
        if not path.startswith(QUEUE + "/"):
            raise AssertionError(f"unexpected DELETE {path}")
        queue_id = int(path.rsplit("/", 1)[1])
        if queue_id in self.delete_errors:
            raise self.delete_errors[queue_id]
        record = next((r for r in self.queue if r.get("id") == queue_id), None)
        if record is None:
            raise http_error(404)
        download_id = record.get("downloadId")
        # The app removes the whole download: every episode record with its downloadId.
        self.queue = [r for r in self.queue
                      if r.get("id") != queue_id and (download_id is None or r.get("downloadId") != download_id)]
        return None

    def log(self, level, skill, message):
        self.logged.append((level, skill, message))

    def finish_command(self, command_id: int, status: str = "completed", *, ended: str | None = "2026-10-02T12:00:00Z",
                       exception: str | None = None, message: str | None = None) -> None:
        """Moves a stored command on, like the app does when it runs it.
        ended=None leaves "ended" out (a command still running)."""
        resource = self.commands[command_id]
        resource["status"] = status
        resource["started"] = resource.get("queued", "2026-10-02T12:00:00Z")
        resource.pop("ended", None)
        if ended is not None:
            resource["ended"] = ended
        if exception is not None:
            resource["exception"] = exception
        if message is not None:
            resource["message"] = message

    def imported(self, download_id: str) -> None:
        """What the app does after it imported the download: its queue records
        go, and the history gets a downloadFolderImported record."""
        self.queue = [r for r in self.queue if r.get("downloadId") != download_id]
        records = self.history.get(download_id, [])
        self.history[download_id] = [*(records if isinstance(records, list) else []), import_record(download_id)]
