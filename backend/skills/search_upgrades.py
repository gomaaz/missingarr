import math
import random
import time

from backend import db
from backend.checked_search.runner import (
    CheckedTask, indexer_pause, pause_note, paused_before_collecting, queue_note, queued_before_collecting,
    run_checked,
)
from backend.checked_search.settings import CheckedSearchSettings
from backend.skills.profiles import PROFILE_TIMEOUT, ProfileState, trim_embedded_series
from backend.skills.profiles import refresh as refresh_profiles
from backend.skills.base import (
    BaseSkill, SearchResult, SubmitOutcome, finish_search_run, store_unsaved_submissions,
    submit_candidates, unsaved_cache_keys,
)

CUTOFF_PATH = "/api/v3/wanted/cutoff"
MOVIES_PATH = "/api/v3/movie"

# Random pages read per run and source. No ten-page ceiling: every page of the
# cutoff list is reachable, and a page whose entries are all cached is simply
# followed by the next unvisited one (A5).
UPGRADE_PAGE_BUDGET = 20


class SearchUpgradesSkill(BaseSkill):
    name = "search_upgrades"

    SOURCES = {
        "wanted_list_only": ("cutoff",),
        "monitored_items_only": ("monitored",),
        "both": ("cutoff", "monitored"),
    }
    SOURCE_LABELS = {"cutoff": "cutoff list", "monitored": "monitored movies"}

    def execute(self, agent, force: bool = False) -> None:
        cfg = agent.config
        # The checked search's time budget counts from here.
        started = time.monotonic()
        run_id = db.history.start_run(cfg["id"], cfg["name"], self.name)
        wanted_count = 0
        outcome = SubmitOutcome()

        try:
            store_unsaved_submissions(self.name, agent, run_id)
            per_run = int(cfg.get("upgrades_per_run", 1) or 0)
            if per_run <= 0:
                agent.log("info", self.name, "Upgrades per run is 0 — nothing to do")
                finish_search_run(self.name, agent, run_id, 0, outcome)
                return

            agent.log("info", self.name, "Searching for upgrade candidates...")
            checked = cfg.get("checked_search") or "off"
            if checked != "off":
                # Before collecting: a run without candidates pauses visibly too.
                pause = indexer_pause(agent)
                if pause:
                    agent.log("warn", self.name, pause)
                    finish_search_run(self.name, agent, run_id, 0, SubmitOutcome(paused=pause))
                    return
            # Fingerprints of the quality profiles: a cached title whose
            # profile changed may be searched again (spec addendum). After
            # the pause check: a paused run reads nothing more (0.10.1).
            profiles = refresh_profiles(self.name, agent)
            # Active checked search: titles with a download in the *arr queue
            # take no place of "per run" (the runner would skip them unsearched).
            queued, unread_queue = queued_before_collecting(self.name, agent, checked, cfg["type"])
            # Checked search: titles in their error pause take no place either.
            paused = paused_before_collecting(cfg, checked)
            # Dry run: once per round, a force run as well.
            round_keys = (
                db.checked_search_log.dry_run_keys(cfg["id"], int(cfg.get("dry_run_round") or 0))
                if checked == "dry_run" else None
            )
            candidates, failures, notes, requested = self._collect_candidates(
                agent, cfg, per_run, force, checked != "off", round_keys, profiles, queued, paused)
            for failure in failures:
                agent.log("warn", self.name, f"Could not load {failure}")
            if failures and len(failures) == requested:
                # Every source failed: an error, not "no candidates" (A7).
                # last_sync stays untouched.
                db.history.finish_run(run_id, 0, 0, "error", "; ".join(failures))
                return

            notes = failures + ([unread_queue] if unread_queue else []) + notes
            wanted_count = len(candidates)
            if not candidates:
                agent.log("info", self.name, "No upgrade candidates found")
                finish_search_run(self.name, agent, run_id, 0, outcome, notes)
                return

            if checked != "off":
                tasks = [self._checked_task(cfg["type"], item, profiles) for item in candidates]
                outcome = run_checked(self.name, agent, run_id, tasks, checked,
                                      profiles=profiles, started=started, config=cfg, check_indexers=False)
                finish_search_run(self.name, agent, run_id, wanted_count, outcome, notes + outcome.notes)
                return

            delay = cfg.get("seconds_between_actions", 2) or 0
            outcome = submit_candidates(
                self.name, agent, run_id, candidates,
                lambda item: self._fire_upgrade(agent, cfg["type"], item),
                delay,
                fingerprint_of=profiles.stored_fingerprint,
            )
            agent.log("info", self.name,
                      f"Done — candidates: {wanted_count}, triggered: {outcome.triggered}, "
                      f"failed: {len(outcome.errors)}")
            finish_search_run(self.name, agent, run_id, wanted_count, outcome, notes)

        except Exception as exc:
            agent.log("error", self.name, f"Upgrade search failed: {exc}")
            db.history.finish_run(run_id, wanted_count, outcome.triggered, "error", str(exc))

    def _cache_key(self, arr_type: str, item: dict, checked: bool = False) -> str:
        """checked: the checked search works per episode, so a Sonarr
        upgrade is keyed by its episode, not by the season."""
        if arr_type == "radarr" or checked:
            return f"upg:{item['id']}"
        # Sonarr: season level when known (SeasonSearch deduplication)
        series_id = item.get("series_id")
        season_number = item.get("season_number")
        if series_id is not None and season_number is not None:
            return f"upg:sea:{series_id}:{season_number}"
        return f"upg:{item['id']}"

    @staticmethod
    def _hold_key(arr_type: str, item: dict) -> str | None:
        """Sonarr: the key a checked grab of this episode also writes, so the
        command path waits with the season search while the grab blocks
        (owner decision 02.10.2026). Only the command path asks for it: in
        the checked search each episode stands alone."""
        series_id, season_number = item.get("series_id"), item.get("season_number")
        if arr_type != "sonarr" or series_id is None or season_number is None:
            return None
        return f"upg:sea-hold:{series_id}:{season_number}"

    def _checked_task(self, arr_type: str, item: dict, profiles: ProfileState | None = None) -> CheckedTask:
        profiles = profiles or ProfileState()
        return CheckedTask(
            arr_id=item["id"],
            title=item.get("label") or f"#{item['id']}",
            item_type="movie" if arr_type == "radarr" else "episode",
            cache_key=self._cache_key(arr_type, item, checked=True),
            series_id=item.get("series_id"),
            profile_fingerprint=profiles.fingerprint(profiles.profile_of(item)),
            hold_key=self._hold_key(arr_type, item),
        )

    def _trigger_upgrade(self, agent, arr_type: str, item: dict) -> SearchResult:
        """Fire the upgrade search and report what was actually addressed.
        Reuses _cache_key so the pre-filter and the stored key never disagree."""
        label = item.get("label") or item.get("title") or f"#{item['id']}"
        cache_key = self._cache_key(arr_type, item)

        if arr_type == "radarr":
            movie_id = item["id"]
            resp = agent.http_post("/api/v3/command", {"name": "MoviesSearch", "movieIds": [movie_id]})
            return SearchResult(True, label, "movie", cache_key, movie_id, resp.get("id"))

        series_id = item.get("series_id")
        season_number = item.get("season_number")
        if series_id is not None and season_number is not None:
            resp = agent.http_post(
                "/api/v3/command",
                {"name": "SeasonSearch", "seriesId": series_id, "seasonNumber": season_number},
            )
            return SearchResult(True, label, "season", cache_key, series_id, resp.get("id"))

        episode_id = item["id"]
        resp = agent.http_post("/api/v3/command", {"name": "EpisodeSearch", "episodeIds": [episode_id]})
        return SearchResult(True, label, "episode", cache_key, episode_id, resp.get("id"))

    def _fire_upgrade(self, agent, arr_type: str, item: dict) -> SearchResult:
        try:
            result = self._trigger_upgrade(agent, arr_type, item)
            agent.log("debug", self.name, f"Upgrade search: {result.title}")
            return result
        except Exception as exc:
            if arr_type == "radarr":
                item_type = "movie"
            elif item.get("series_id") is not None and item.get("season_number") is not None:
                item_type = "season"
            else:
                item_type = "episode"
            return SearchResult(False, item.get("label") or f"#{item['id']}", item_type, "",
                                item.get("id"), None, str(exc))

    # ── Candidates ───────────────────────────────────────────────────────────

    def _collect_candidates(self, agent, cfg, per_run, force, checked=False, round_keys=None, profiles=None,
                            queued=frozenset(), paused=frozenset()):
        """Returns (candidates, failed sources, notes, number of sources asked).
        queued, paused: see _keep_uncached; the notes say how many were left out."""
        if cfg["type"] == "radarr":
            sources = self.SOURCES.get(cfg.get("upgrade_source", "monitored_items_only"), ("monitored",))
        else:
            sources = ("cutoff",)

        found: list = []
        seen: set = set()
        failures: list = []
        notes: list = []
        left_out: set = set()
        left_paused: set = set()
        queue = {"queued": queued, "left_out": left_out, "paused": paused, "left_paused": left_paused}
        for source in sources:
            try:
                if source == "cutoff":
                    self._collect_cutoff(agent, cfg, per_run, force, found, seen, notes,
                                         checked, round_keys, profiles, **queue)
                else:
                    self._collect_monitored(agent, cfg, per_run, force, found, seen, checked, round_keys, profiles,
                                            **queue)
            except Exception as exc:
                failures.append(f"{self.SOURCE_LABELS[source]}: {exc}")

        random.shuffle(found)
        return (found[:per_run], failures, queue_note(len(left_out)) + pause_note(len(left_paused)) + notes,
                len(sources))

    def _keep_uncached(self, agent, cfg, items, force, found, seen, limit, checked=False, round_keys=None,
                       profiles=None, wanted_list=False, queued=frozenset(), left_out=None,
                       paused=frozenset(), left_paused=None) -> None:
        """Commands still waiting to be stored count as cached. A cache entry
        blocks only under the current fingerprint of the item's quality
        profile (spec addendum, unless switched off). wanted_list (the cutoff
        list): a grab of the checked search blocks only "Search again if
        still missing after (days)" — the movie list cannot tell whether a
        grab is still missing. An empty search of the checked search
        (no_results) is released after the same days from either list, also
        with retry_hours 0: nothing was grabbed, the title is still a
        candidate (owner decision 02.10.2026). In a checked-search dry run (round_keys, force
        run too) only this round's log counts, under the current profile
        fingerprint and rule settings.

        Sonarr keys an upgrade by season on the command path and by episode
        in the checked search: the other path's key blocks as well, so
        switching the mode releases nothing early. On the command path a
        season also waits while a checked grab of one of its episodes blocks
        (hold key, owner decision 02.10.2026): SeasonSearch would search
        the grabbed episode again.

        queued (active checked search, force run too): ids of the movies or
        episodes with a download in the *arr queue. They do not count toward
        the limit and are not taken; their ids go to left_out.

        paused (checked search, dry run and active, force run too): keys of
        titles in their error pause (db.checked_search_pause). Left out the
        same way; their keys go to left_paused."""
        profiles = profiles or ProfileState()
        arr_type = cfg["type"]
        keyed = [(self._cache_key(arr_type, item, checked), item) for item in items]
        other = [self._cache_key(arr_type, item, not checked) for item in items]
        held = [None if checked else self._hold_key(arr_type, item) for item in items]
        again_days = CheckedSearchSettings.from_stored(cfg.get("checked_search_settings")).search_again_after_days
        grab_days = again_days if wanted_list else 0
        if round_keys is not None:
            hits = {key: True for key, item in keyed if profiles.round_blocks(round_keys, key, item)}
            other = [None] * len(keyed)
            held = [None] * len(keyed)
        elif force:
            hits = {}
        else:
            asked = (keyed + [(key, item) for key, (own, item) in zip(other, keyed) if key != own]
                     + [(key, item) for key, (_, item) in zip(held, keyed) if key])
            hits = {
                **db.searched.lookup_many(
                    cfg["id"], [key for key, _ in asked], int(cfg.get("retry_hours", 0) or 0),
                    fingerprints=profiles.cache_filter(asked), grab_release_days=grab_days,
                    no_results_release_days=again_days,
                ),
                **unsaved_cache_keys(agent),
            }
        for (key, item), other_key, held_key in zip(keyed, other, held):
            if len(found) >= limit:
                return
            if key in hits or other_key in hits or held_key in hits or key in seen:
                continue
            if item["id"] in queued:
                if left_out is not None:
                    left_out.add(item["id"])
                continue
            if key in paused:
                if left_paused is not None:
                    left_paused.add(key)
                continue
            seen.add(key)
            found.append(item)

    def _collect_cutoff(self, agent, cfg, per_run, force, found, seen, notes, checked=False, round_keys=None,
                        profiles=None, **left_out) -> None:
        """left_out: queued, left_out, paused, left_paused (see _keep_uncached)."""
        arr_type = cfg["type"]
        limit = len(found) + per_run
        pool = max(per_run * 5, 50)
        probe = agent.http_get(CUTOFF_PATH, params={"pageSize": 1, "page": 1, "monitored": "true"})
        total = int(probe.get("totalRecords", 0) or 0)
        pages = list(range(1, max(1, math.ceil(total / pool)) + 1))
        random.shuffle(pages)
        budget = max(UPGRADE_PAGE_BUDGET, math.ceil(per_run * 4 / pool))

        loaded = 0
        for page in pages[:budget]:
            if len(found) >= limit or agent.stop_requested():
                return
            params = {"pageSize": pool, "page": page, "monitored": "true"}
            timeout = {}
            if arr_type == "sonarr":
                # The embedded series names the episode's quality profile; such
                # pages get the timeout of the series-list read they replace
                # and their series are trimmed at once (0.10.1).
                params["includeSeries"] = "true"
                timeout = {"timeout": PROFILE_TIMEOUT}
            try:
                resp = agent.http_get(CUTOFF_PATH, params=params, **timeout)
                trim_embedded_series(resp.get("records"))
            except Exception as exc:
                if loaded == 0:
                    raise
                notes.append(f"cutoff page {page} could not be loaded: {exc}")
                return
            loaded += 1
            items = [self._cutoff_item(arr_type, r) for r in resp.get("records") or []]
            items = [item for item in items if item is not None]
            random.shuffle(items)
            self._keep_uncached(agent, cfg, items, force, found, seen, limit, checked, round_keys, profiles,
                                wanted_list=True, **left_out)

    @staticmethod
    def _cutoff_item(arr_type: str, record: dict):
        if "id" not in record:
            return None
        if arr_type == "radarr":
            if not record.get("hasFile"):
                return None
            year = record.get("year", "")
            title = record.get("title") or f"Movie #{record['id']}"
            return {"id": record["id"], "label": f"{title} ({year})" if year else title,
                    "qualityProfileId": record.get("qualityProfileId")}
        series = record.get("series") or {}
        series_title = series.get("title") or record.get("seriesTitle", "") or f"Series #{record.get('seriesId', '?')}"
        season_number = record.get("seasonNumber")
        episode_title = record.get("title", "")
        if season_number is not None:
            label = f"{series_title} S{(season_number or 0):02d}E{(record.get('episodeNumber') or 0):02d}"
            if episode_title:
                label += f" – {episode_title}"
        else:
            label = episode_title or f"Episode #{record['id']}"
        return {"id": record["id"], "label": label,
                "series_id": record.get("seriesId"), "season_number": season_number,
                "qualityProfileId": series.get("qualityProfileId")}

    def _collect_monitored(self, agent, cfg, per_run, force, found, seen, checked=False, round_keys=None,
                           profiles=None, **left_out) -> None:
        """left_out: queued, left_out, paused, left_paused (see _keep_uncached)."""
        limit = len(found) + per_run
        movies = agent.http_get(MOVIES_PATH, params={"monitored": "true"})
        items = []
        for movie in movies if isinstance(movies, list) else []:
            # Checked here as well: the monitored filter of /movie is not
            # guaranteed on every Radarr version.
            if "id" not in movie or not movie.get("hasFile") or not movie.get("monitored", True):
                continue
            year = movie.get("year", "")
            title = movie.get("title") or f"Movie #{movie['id']}"
            items.append({"id": movie["id"], "label": f"{title} ({year})" if year else title,
                          "qualityProfileId": movie.get("qualityProfileId")})
        random.shuffle(items)
        self._keep_uncached(agent, cfg, items, force, found, seen, limit, checked, round_keys, profiles,
                            **left_out)
