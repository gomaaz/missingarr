import math
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from backend import db
from backend.checked_search.runner import (
    CheckedTask, indexer_pause, pause_note, paused_before_collecting, queue_note, queued_before_collecting,
    run_checked,
)
from backend.checked_search.settings import CheckedSearchSettings
from backend.database import ANCESTOR_RULE_SINCE_SETTING
from backend.skills.profiles import ProfileState
from backend.skills.profiles import refresh as refresh_profiles
from backend.skills.base import (
    BaseSkill, SearchResult, SubmitOutcome, finish_search_run, parse_arr_date,
    release_date, store_unsaved_submissions, submit_candidates, unsaved_cache_keys,
)

WANTED_PATH = "/api/v3/wanted/missing"

# newest_first / oldest_first / smart read the whole list and sort it here.
# *arr's server-side sorting (the sort key parameter) is deliberately not used:
# it drops records without the sort field (specials, films without a physical
# date — see 8a1a912, c68178e).
# They page to the end of the list; the page ceiling is only an emergency
# brake far above any real backlog, and a run that hits it fails instead of
# sorting a slice.
ORDERED_PAGE_SIZE = 1000
ORDERED_MAX_PAGES = 1000

# random: at most this many pages per run, so a mostly searched backlog does
# not mean reading the whole list every interval (A-L3).
RANDOM_PAGE_BUDGET = 10

RATIO_THRESHOLD = 0.5
CHECK_CHUNK = 1000


@dataclass
class _Stats:
    total: int = 0
    pages: int = 0
    examined: int = 0
    skipped_file: int = 0
    skipped_window: int = 0
    skipped_cache: int = 0
    skipped_queue: int = 0
    skipped_paused: int = 0
    notes: list = field(default_factory=list)

    def describe(self) -> str:
        return (
            f"checked {self.examined} of {self.total} missing item(s) on {self.pages} page(s): "
            f"{self.skipped_cache} already searched, {self.skipped_window} inside the release "
            f"window, {self.skipped_file} with a file"
            + (f", {self.skipped_queue} in the *arr queue" if self.skipped_queue else "")
            + (f", {self.skipped_paused} paused after an error" if self.skipped_paused else "")
        )


class SearchMissingSkill(BaseSkill):
    name = "search_missing"

    # 50 % recent, 30 % random, 20 % oldest — repeated over the whole list.
    SMART_PATTERN = (0, 0, 0, 0, 0, 1, 1, 1, 2, 2)

    def execute(self, agent, force: bool = False) -> None:
        cfg = agent.config
        # The checked search's time budget counts from here (profiles and
        # candidate collection included).
        started = time.monotonic()
        run_id = db.history.start_run(cfg["id"], cfg["name"], self.name)
        wanted_count = 0
        outcome = SubmitOutcome()

        try:
            store_unsaved_submissions(self.name, agent, run_id)
            per_run = int(cfg.get("missing_per_run", 5) or 0)
            if per_run <= 0:
                agent.log("info", self.name, "Missing per run is 0 — nothing to do")
                finish_search_run(self.name, agent, run_id, 0, outcome)
                return

            order = cfg.get("search_order", "random")
            checked = cfg.get("checked_search") or "off"
            # The checked search handles single episodes only; the form and
            # the API refuse other modes while it is on (spec: Sonarr).
            mode = "episode" if checked != "off" else cfg.get("missing_mode", "episode")
            # Fingerprints of the quality profiles: a cached title whose
            # profile changed may be searched again (spec addendum).
            profiles = refresh_profiles(self.name, agent)
            if checked != "off":
                # Every checked run reads the indexer list before it collects,
                # also one that will find nothing to search: a lasting pause
                # must show in the History and keep last_sync (spec).
                pause = indexer_pause(agent)
                if pause:
                    agent.log("warn", self.name, pause)
                    finish_search_run(self.name, agent, run_id, 0, SubmitOutcome(paused=pause))
                    return
            # Active checked search: titles with a download in the *arr queue
            # take no place of "per run" (the runner would skip them
            # unsearched, and a fixed order would never get past them).
            queued, unread_queue = queued_before_collecting(self.name, agent, checked, cfg["type"])
            # Checked search: titles that just ended in an error sit out
            # their pause and take no place of "per run" either.
            paused = paused_before_collecting(cfg, checked)
            # A dry run ignores the search cache: titles searched long ago
            # are checked too. Instead each title is checked once per round
            # (the round this run begins in) — a force run as well.
            round_keys = (
                db.checked_search_log.dry_run_keys(cfg["id"], int(cfg.get("dry_run_round") or 0))
                if checked == "dry_run" else None
            )
            hours = int(cfg.get("hours_after_release", 9) or 0)
            cutoff = (
                datetime.now(timezone.utc) - timedelta(hours=hours)
                if (not force and hours > 0) else None
            )

            agent.log("info", self.name,
                      f"Searching for missing content (order={order}, per_run={per_run})...")
            if order == "random":
                candidates, stats = self._collect_random(agent, cfg, per_run, mode, cutoff, force,
                                                         round_keys, profiles, queued, paused)
            else:
                candidates, stats = self._collect_ordered(agent, cfg, per_run, mode, order, cutoff, force,
                                                          round_keys, profiles, queued, paused)
            stats.notes[:0] = (([unread_queue] if unread_queue else []) + queue_note(stats.skipped_queue)
                               + pause_note(stats.skipped_paused))
            wanted_count = len(candidates)
            agent.log("debug", self.name, stats.describe())

            if not candidates:
                agent.log("info", self.name, f"Nothing to search — {stats.describe()}")
                finish_search_run(self.name, agent, run_id, 0, outcome, stats.notes)
                return

            series_lookup = self._series_lookup(agent, cfg, candidates)
            if checked != "off":
                tasks = [self._checked_task(cfg["type"], record, series_lookup, profiles) for record in candidates]
                outcome = run_checked(self.name, agent, run_id, tasks, checked,
                                      profiles=profiles, started=started, config=cfg, check_indexers=False)
                finish_search_run(self.name, agent, run_id, wanted_count, outcome, stats.notes + outcome.notes)
                return

            delay = cfg.get("seconds_between_actions", 2) or 0
            outcome = submit_candidates(
                self.name, agent, run_id, candidates,
                lambda record: self._trigger_search(agent, cfg, record, mode, series_lookup),
                delay,
                fingerprint_of=profiles.stored_fingerprint,
            )
            agent.log(
                "info", self.name,
                f"Done — candidates: {wanted_count}, triggered: {outcome.triggered}, "
                f"failed: {len(outcome.errors)} (total missing: {stats.total})",
            )
            finish_search_run(self.name, agent, run_id, wanted_count, outcome, stats.notes)

        except Exception as exc:
            agent.log("error", self.name, f"Search failed: {exc}")
            db.history.finish_run(run_id, wanted_count, outcome.triggered, "error", str(exc))

    # ── Collecting candidates ────────────────────────────────────────────────

    def _collect_random(self, agent, cfg, per_run, mode, cutoff, force, round_keys=None, profiles=None,
                        queued=frozenset(), paused=frozenset()):
        stats = _Stats()
        page_size = min(max(per_run * 10, 50), 250)
        probe = agent.http_get(WANTED_PATH, params={"page": 1, "pageSize": 1, "monitored": "true"})
        stats.total = int(probe.get("totalRecords", 0) or 0)

        pages = list(range(1, max(1, math.ceil(stats.total / page_size)) + 1))
        random.shuffle(pages)
        budget = max(RANDOM_PAGE_BUDGET, math.ceil(per_run * 4 / page_size))

        candidates: list = []
        seen: set = set()
        for page in pages[:budget]:
            if len(candidates) >= per_run or agent.stop_requested():
                break
            try:
                resp = agent.http_get(
                    WANTED_PATH, params={"page": page, "pageSize": page_size, "monitored": "true"}
                )
            except Exception as exc:
                if stats.pages == 0:
                    raise
                stats.notes.append(f"page {page} could not be loaded: {exc}")
                break
            stats.pages += 1
            records = list(resp.get("records") or [])
            random.shuffle(records)
            self._take_eligible(agent, cfg, records, mode, cutoff, force, per_run, candidates, seen, stats,
                                round_keys, profiles, queued, paused)
        return candidates, stats

    def _collect_ordered(self, agent, cfg, per_run, mode, order, cutoff, force, round_keys=None, profiles=None,
                         queued=frozenset(), paused=frozenset()):
        """Read the whole wanted list, sort it here, then walk it in order (A3/A4)."""
        stats = _Stats()
        records: list = []
        seen_ids: set = set()
        page = 1
        while True:
            if page > ORDERED_MAX_PAGES:
                raise RuntimeError(
                    f"wanted list has more than {ORDERED_MAX_PAGES * ORDERED_PAGE_SIZE} missing "
                    f"items — stopped instead of ordering only part of it"
                )
            if agent.stop_requested():
                break
            resp = agent.http_get(
                WANTED_PATH, params={"page": page, "pageSize": ORDERED_PAGE_SIZE, "monitored": "true"}
            )
            batch = resp.get("records") or []
            stats.total = int(resp.get("totalRecords", 0) or 0)
            stats.pages += 1
            for record in batch:
                record_id = record.get("id")
                if record_id in seen_ids:
                    continue
                seen_ids.add(record_id)
                records.append(record)
            if not batch or page * ORDERED_PAGE_SIZE >= stats.total:
                break
            page += 1

        ordered = self._apply_order(records, order, cfg["type"])
        candidates: list = []
        self._take_eligible(agent, cfg, ordered, mode, cutoff, force, per_run, candidates, set(), stats,
                            round_keys, profiles, queued, paused)
        return candidates, stats

    def _take_eligible(self, agent, cfg, records, mode, cutoff, force, per_run, candidates, seen, stats,
                       round_keys=None, profiles=None, queued=frozenset(), paused=frozenset()):
        """Append records, in the given order, that are missing, released and
        not in the cache, until per_run candidates exist. The cache is asked
        once per chunk, not once per record (A-L3). Commands still waiting to
        be stored count as cached.

        profiles: a cache entry blocks only under the current fingerprint of
        the record's quality profile (spec addendum), unless the instance
        switched that off. A grab of the checked search blocks only "Search
        again if still missing after (days)": the records come from the
        wanted list, so a listed grab is still missing. An empty search of
        the checked search (no_results) is released after the same days,
        also with retry_hours 0 (owner decision 02.10.2026). round_keys
        (checked-search dry run, force run too): the cache is not asked at
        all; a record is skipped only when its own key was checked in this
        round under its current fingerprint and the current rule settings.

        queued (active checked search, force run too): ids of the movies or
        episodes with a download in the *arr queue. They count toward
        neither per_run nor the candidates, so a stuck one does not hold back
        the next title in order.

        paused (checked search, dry run and active, force run too): own
        keys of titles in their error pause (db.checked_search_pause). Left
        out the same way, so a title that keeps failing does not hold back
        the next one either."""
        profiles = profiles or ProfileState()
        grab_days = CheckedSearchSettings.from_stored(cfg.get("checked_search_settings")).search_again_after_days
        arr_type = cfg["type"]
        retry_hours = int(cfg.get("retry_hours", 0) or 0)
        # For season/series keys: a search inside the release window proves
        # nothing about the episode (A9/B6). Force runs skip the cache anyway.
        window = timedelta(hours=max(0, int(cfg.get("hours_after_release", 9) or 0)))
        since = None if force else self._ancestor_rule_since()
        for start in range(0, len(records), CHECK_CHUNK):
            if len(candidates) >= per_run:
                return
            pool = []
            for record in records[start:start + CHECK_CHUNK]:
                stats.examined += 1
                if record.get("hasFile"):
                    stats.skipped_file += 1
                    continue
                if cutoff is not None:
                    released = release_date(record, arr_type)
                    if released is not None and released > cutoff:
                        stats.skipped_window += 1
                        continue
                pool.append(record)

            if round_keys is not None:
                now = datetime.now(timezone.utc)
                hits = {
                    self._own_key(arr_type, r): now for r in pool
                    if profiles.round_blocks(round_keys, self._own_key(arr_type, r), r)
                }
            elif force:
                hits = {}
            else:
                keyed = [(key, r) for r in pool for key in self._check_keys(arr_type, r)]
                hits = {
                    **db.searched.lookup_many(
                        cfg["id"], [key for key, _ in keyed], retry_hours,
                        fingerprints=profiles.cache_filter(keyed), grab_release_days=grab_days,
                        no_results_release_days=grab_days,
                    ),
                    **unsaved_cache_keys(agent),
                }
            for record in pool:
                if len(candidates) >= per_run:
                    return
                if (not force or round_keys is not None) and self._blocked(arr_type, record, hits, window, since):
                    stats.skipped_cache += 1
                    continue
                dedup = self._cache_key(arr_type, record, mode)
                if dedup in seen:
                    continue
                seen.add(dedup)
                if record.get("id") in queued:
                    stats.skipped_queue += 1
                    continue
                if self._own_key(arr_type, record) in paused:
                    stats.skipped_paused += 1
                    continue
                candidates.append(record)

    # ── Cache keys ───────────────────────────────────────────────────────────

    def _cache_key(self, arr_type: str, record: dict, mode: str) -> str:
        """Broad intent key for within-run deduplication, so one run does not
        send the same season or series search twice."""
        if arr_type == "radarr":
            return f"mov:{record.get('id')}"
        if mode in ("season_packs", "smart"):
            return f"sea:{record.get('seriesId')}:{record.get('seasonNumber')}"
        if mode == "show_batch":
            return f"ser:{record.get('seriesId')}"
        return f"ep:{record.get('id')}"

    def _check_keys(self, arr_type: str, record: dict) -> list:
        """Every cache key that can cover this record — independent of the
        current mode, so a mode switch does not bypass earlier season or
        series searches (A9)."""
        if arr_type == "radarr":
            return [f"mov:{record.get('id')}"]
        keys = [f"ep:{record.get('id')}"]
        series_id = record.get("seriesId")
        if series_id is not None:
            keys.append(f"ser:{series_id}")
            if record.get("seasonNumber") is not None:
                keys.append(f"sea:{series_id}:{record.get('seasonNumber')}")
        return keys

    @staticmethod
    def _own_key(arr_type: str, record: dict) -> str:
        return f"mov:{record.get('id')}" if arr_type == "radarr" else f"ep:{record.get('id')}"

    @staticmethod
    def _ancestor_rule_since() -> datetime | None:
        """First start of 0.8.0 (aware UTC), written once by init_db()."""
        value = db.app_settings.get_value(ANCESTOR_RULE_SINCE_SETTING)
        if not value:
            return None
        try:
            return db.searched.local_to_utc(value)
        except ValueError:
            return None

    def _blocked(self, arr_type: str, record: dict, hits: dict,
                 window: timedelta = timedelta(0), since: datetime | None = None) -> bool:
        """The record's own key always blocks.

        A season or series key blocks only when that search ran after the
        episode aired *and* after its release window (hours_after_release):
        a season search from last month, or one a few hours after the air
        date while indexers do not have it yet, cannot have found it (A8/B6).
        Keys cached before `since` (first start of 0.8.0) never block an
        episode: they come from show_batch runs under the old rules and would
        lock most of the live backlog again (A9). Without an air date a
        broader key from after `since` blocks, as before."""
        own = self._own_key(arr_type, record)
        if own in hits:
            return True
        if arr_type == "radarr":
            return False
        aired = release_date(record, arr_type)
        for key in self._check_keys(arr_type, record):
            if key == own or key not in hits:
                continue
            searched = hits[key]
            if since is not None and searched < since:
                continue
            if aired is None or searched >= aired + window:
                return True
        return False

    # ── Ordering ─────────────────────────────────────────────────────────────

    def _apply_order(self, records: list, order: str, arr_type: str) -> list:
        undated = datetime.min.replace(tzinfo=timezone.utc)

        def when(record):
            return release_date(record, arr_type) or undated

        if order == "newest_first":
            return sorted(records, key=when, reverse=True)
        if order == "oldest_first":
            return sorted(records, key=when)
        if order == "smart":
            return self._smart_order(records, when)
        shuffled = list(records)
        random.shuffle(shuffled)
        return shuffled

    def _smart_order(self, records: list, when) -> list:
        """Order the whole list so that, from the front, half the picks are
        recent (last 30 days), 30 % random and 20 % the oldest — without
        dropping anything (A4)."""
        recent_cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        recent = [r for r in records if when(r) >= recent_cutoff]
        rest = [r for r in records if when(r) < recent_cutoff]
        random.shuffle(recent)
        shuffled_rest = list(rest)
        random.shuffle(shuffled_rest)
        queues = [recent, shuffled_rest, sorted(rest, key=when)]
        positions = [0, 0, 0]
        used: set = set()

        def draw(q):
            queue = queues[q]
            while positions[q] < len(queue):
                record = queue[positions[q]]
                positions[q] += 1
                if id(record) not in used:
                    used.add(id(record))
                    return record
            return None

        ordered: list = []
        while len(ordered) < len(records):
            for q in self.SMART_PATTERN:
                # An empty queue hands its turn to the next one that has records left.
                record = draw(q)
                for fallback in (0, 1, 2):
                    if record is not None:
                        break
                    record = draw(fallback)
                if record is None:
                    return ordered
                ordered.append(record)
        return ordered

    # ── Triggering ───────────────────────────────────────────────────────────

    def _series_lookup(self, agent, cfg, candidates) -> dict[int, str]:
        """Fetch titles only for series the wanted list did not name."""
        lookup: dict[int, str] = {}
        if cfg["type"] != "sonarr":
            return lookup
        needed = {
            r.get("seriesId") for r in candidates
            if r.get("seriesId")
            and not (r.get("series") or {}).get("title", "")
            and not r.get("seriesTitle", "")
        }
        for series_id in needed:
            try:
                lookup[series_id] = agent.http_get(f"/api/v3/series/{series_id}").get("title", "")
            except Exception:
                pass
        return lookup

    def _series_title(self, record: dict, series_lookup: dict) -> str:
        series_id = record.get("seriesId")
        title = (record.get("series") or {}).get("title", "") or record.get("seriesTitle", "")
        if not title and series_id and series_lookup:
            title = series_lookup.get(series_id, f"Series #{series_id}")
        return title

    def _label(self, arr_type: str, record: dict, series_lookup: dict) -> str:
        if arr_type == "radarr":
            title = record.get("title") or f"Movie #{record.get('id')}"
            year = record.get("year", "")
            return f"{title} ({year})" if year else title
        series_title = self._series_title(record, series_lookup)
        episode_title = record.get("title", "")
        if series_title:
            return (f"{series_title} S{(record.get('seasonNumber') or 0):02d}"
                    f"E{(record.get('episodeNumber') or 0):02d} – {episode_title}")
        return episode_title or f"Episode #{record.get('id')}"

    def _checked_task(self, arr_type: str, record: dict, series_lookup: dict,
                      profiles: ProfileState | None = None) -> CheckedTask:
        """The title as the checked search sees it: always one movie or one
        episode, keyed like the commands it replaces. profile_fingerprint is
        the fallback; the runner takes the one of the movie or series it loads."""
        profiles = profiles or ProfileState()
        return CheckedTask(
            arr_id=record.get("id"),
            title=self._label(arr_type, record, series_lookup or {}),
            item_type="movie" if arr_type == "radarr" else "episode",
            cache_key=self._own_key(arr_type, record),
            series_id=record.get("seriesId") if arr_type == "sonarr" else None,
            profile_fingerprint=profiles.fingerprint(profiles.profile_of(record)),
        )

    def _trigger_search(self, agent, cfg, record, mode, series_lookup) -> SearchResult:
        arr_type = cfg["type"]
        try:
            if arr_type == "sonarr":
                return self._sonarr_search(agent, record, mode, series_lookup or {})
            return self._radarr_search(agent, record)
        except Exception as exc:
            return SearchResult(
                False, self._label(arr_type, record, series_lookup or {}),
                "movie" if arr_type == "radarr" else "episode", "", record.get("id"), None, str(exc),
            )

    def _episodes(self, agent, series_id, season_number=None):
        params = {"seriesId": series_id, "includeImages": "false"}
        if season_number is not None:
            params["seasonNumber"] = season_number
        try:
            episodes = agent.http_get("/api/v3/episode", params=params)
        except Exception as exc:
            agent.log("debug", self.name, f"Episode list for series {series_id} failed, using EpisodeSearch: {exc}")
            return None
        return episodes if isinstance(episodes, list) else None

    @staticmethod
    def _density(episodes, season=None, exclude_specials=False) -> tuple[int, int]:
        """(missing, relevant) over monitored episodes that have already aired.
        Unmonitored and future episodes say nothing about whether a pack is
        worth searching (A8)."""
        now = datetime.now(timezone.utc)
        relevant = []
        for e in episodes or []:
            if not e.get("monitored"):
                continue
            if season is not None and e.get("seasonNumber") != season:
                continue
            if exclude_specials and e.get("seasonNumber") == 0:
                continue
            aired = parse_arr_date(e.get("airDateUtc"))
            if aired is None or aired > now:
                continue
            relevant.append(e)
        missing = sum(1 for e in relevant if not e.get("hasFile"))
        return missing, len(relevant)

    @staticmethod
    def _dense(missing: int, total: int) -> bool:
        return total > 0 and missing / total >= RATIO_THRESHOLD

    def _sonarr_search(self, agent, record: dict, mode: str, series_lookup: dict) -> SearchResult:
        """cache_key and arr_id describe the command actually sent, not the
        mode's intent, so later runs check at the right level."""
        episode_id = record.get("id")
        series_id = record.get("seriesId")
        season_number = record.get("seasonNumber")
        series_title = self._series_title(record, series_lookup)
        label = self._label("sonarr", record, series_lookup)

        if not episode_id:
            return SearchResult(False, label, "episode", "", None, None, "record has no episode id")

        def fire_episode() -> SearchResult:
            resp = agent.http_post("/api/v3/command", {"name": "EpisodeSearch", "episodeIds": [episode_id]})
            agent.log("debug", self.name, f"EpisodeSearch: {label}")
            return SearchResult(True, label, "episode", f"ep:{episode_id}", episode_id, resp.get("id"))

        def fire_season() -> SearchResult:
            resp = agent.http_post(
                "/api/v3/command",
                {"name": "SeasonSearch", "seriesId": series_id, "seasonNumber": season_number},
            )
            title = f"{series_title} Season {season_number}"
            agent.log("debug", self.name, f"SeasonSearch: {title}")
            return SearchResult(True, title, "season", f"sea:{series_id}:{season_number}", series_id, resp.get("id"))

        def fire_series() -> SearchResult:
            resp = agent.http_post("/api/v3/command", {"name": "SeriesSearch", "seriesId": series_id})
            agent.log("debug", self.name, f"SeriesSearch: {series_title}")
            return SearchResult(True, series_title, "series", f"ser:{series_id}", series_id, resp.get("id"))

        if mode in ("season_packs", "smart") and series_id is not None and season_number is not None:
            missing, total = self._density(self._episodes(agent, series_id, season_number), season=season_number)
            agent.log("debug", self.name,
                      f"{mode}: {missing}/{total} aired monitored episodes of season {season_number} missing")
            return fire_season() if self._dense(missing, total) else fire_episode()

        if mode == "show_batch" and series_id is not None:
            episodes = self._episodes(agent, series_id)
            series_missing, series_total = self._density(episodes, exclude_specials=True)
            if self._dense(series_missing, series_total):
                return fire_series()
            if season_number is not None:
                season_missing, season_total = self._density(episodes, season=season_number)
                if self._dense(season_missing, season_total):
                    return fire_season()
            return fire_episode()

        return fire_episode()

    def _radarr_search(self, agent, record: dict) -> SearchResult:
        movie_id = record.get("id")
        label = self._label("radarr", record, {})
        if not movie_id:
            return SearchResult(False, label, "movie", "", None, None, "record has no movie id")
        resp = agent.http_post("/api/v3/command", {"name": "MoviesSearch", "movieIds": [movie_id]})
        agent.log("debug", self.name, f"MoviesSearch: {label}")
        return SearchResult(True, label, "movie", f"mov:{movie_id}", movie_id, resp.get("id"))
