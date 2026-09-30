import math
import random

from backend import db
from backend.skills.base import BaseSkill, SearchResult, SubmitOutcome, finish_search_run, submit_candidates

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
        run_id = db.history.start_run(cfg["id"], cfg["name"], self.name)
        wanted_count = 0
        outcome = SubmitOutcome()

        try:
            per_run = int(cfg.get("upgrades_per_run", 1) or 0)
            if per_run <= 0:
                agent.log("info", self.name, "Upgrades per run is 0 — nothing to do")
                finish_search_run(self.name, agent, run_id, 0, outcome)
                return

            agent.log("info", self.name, "Searching for upgrade candidates...")
            candidates, failures, notes, requested = self._collect_candidates(agent, cfg, per_run, force)
            for failure in failures:
                agent.log("warn", self.name, f"Could not load {failure}")
            if failures and len(failures) == requested:
                # Every source failed: an error, not "no candidates" (A7).
                # last_sync stays untouched.
                db.history.finish_run(run_id, 0, 0, "error", "; ".join(failures))
                return

            notes = failures + notes
            wanted_count = len(candidates)
            if not candidates:
                agent.log("info", self.name, "No upgrade candidates found")
                finish_search_run(self.name, agent, run_id, 0, outcome, notes)
                return

            delay = cfg.get("seconds_between_actions", 2) or 0
            outcome = submit_candidates(
                self.name, agent, run_id, candidates,
                lambda item: self._fire_upgrade(agent, cfg["type"], item),
                delay,
            )
            agent.log("info", self.name,
                      f"Done — candidates: {wanted_count}, triggered: {outcome.triggered}, "
                      f"failed: {len(outcome.errors)}")
            finish_search_run(self.name, agent, run_id, wanted_count, outcome, notes)

        except Exception as exc:
            agent.log("error", self.name, f"Upgrade search failed: {exc}")
            db.history.finish_run(run_id, wanted_count, outcome.triggered, "error", str(exc))

    def _cache_key(self, arr_type: str, item: dict) -> str:
        if arr_type == "radarr":
            return f"upg:{item['id']}"
        # Sonarr: season level when known (SeasonSearch deduplication)
        series_id = item.get("series_id")
        season_number = item.get("season_number")
        if series_id is not None and season_number is not None:
            return f"upg:sea:{series_id}:{season_number}"
        return f"upg:{item['id']}"

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

    def _collect_candidates(self, agent, cfg, per_run, force):
        """Returns (candidates, failed sources, notes, number of sources asked)."""
        if cfg["type"] == "radarr":
            sources = self.SOURCES.get(cfg.get("upgrade_source", "monitored_items_only"), ("monitored",))
        else:
            sources = ("cutoff",)

        found: list = []
        seen: set = set()
        failures: list = []
        notes: list = []
        for source in sources:
            try:
                if source == "cutoff":
                    self._collect_cutoff(agent, cfg, per_run, force, found, seen, notes)
                else:
                    self._collect_monitored(agent, cfg, per_run, force, found, seen)
            except Exception as exc:
                failures.append(f"{self.SOURCE_LABELS[source]}: {exc}")

        random.shuffle(found)
        return found[:per_run], failures, notes, len(sources)

    def _keep_uncached(self, cfg, items, force, found, seen, limit) -> None:
        keyed = [(self._cache_key(cfg["type"], item), item) for item in items]
        hits = {} if force else db.searched.lookup_many(
            cfg["id"], [key for key, _ in keyed], int(cfg.get("retry_hours", 0) or 0)
        )
        for key, item in keyed:
            if len(found) >= limit:
                return
            if key in hits or key in seen:
                continue
            seen.add(key)
            found.append(item)

    def _collect_cutoff(self, agent, cfg, per_run, force, found, seen, notes) -> None:
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
            try:
                resp = agent.http_get(CUTOFF_PATH, params={"pageSize": pool, "page": page, "monitored": "true"})
            except Exception as exc:
                if loaded == 0:
                    raise
                notes.append(f"cutoff page {page} could not be loaded: {exc}")
                return
            loaded += 1
            items = [self._cutoff_item(arr_type, r) for r in resp.get("records") or []]
            items = [item for item in items if item is not None]
            random.shuffle(items)
            self._keep_uncached(cfg, items, force, found, seen, limit)

    @staticmethod
    def _cutoff_item(arr_type: str, record: dict):
        if "id" not in record:
            return None
        if arr_type == "radarr":
            if not record.get("hasFile"):
                return None
            year = record.get("year", "")
            title = record.get("title") or f"Movie #{record['id']}"
            return {"id": record["id"], "label": f"{title} ({year})" if year else title}
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
                "series_id": record.get("seriesId"), "season_number": season_number}

    def _collect_monitored(self, agent, cfg, per_run, force, found, seen) -> None:
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
            items.append({"id": movie["id"], "label": f"{title} ({year})" if year else title})
        random.shuffle(items)
        self._keep_uncached(cfg, items, force, found, seen, limit)
