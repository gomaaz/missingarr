import random
import time
from datetime import datetime
from backend.skills.base import BaseSkill, SearchResult
from backend import db


class SearchUpgradesSkill(BaseSkill):
    name = "search_upgrades"

    def execute(self, agent, force: bool = False) -> None:
        cfg = agent.config
        arr_type = cfg["type"]

        run_id = db.history.start_run(cfg["id"], cfg["name"], self.name)
        wanted_count = 0
        triggered_count = 0

        try:
            agent.log("info", self.name, "Searching for upgrade candidates...")
            source = cfg.get("upgrade_source", "monitored_items_only")
            per_run = cfg.get("upgrades_per_run", 1)
            delay = cfg.get("seconds_between_actions", 2)

            raw = self._collect_candidates(agent, arr_type, source, per_run)

            # Pre-filter: skip already-searched items and deduplicate within this run
            candidates = []
            seen_keys: set = set()
            for item in raw:
                cache_key = self._cache_key(arr_type, item)
                if not force and db.searched.exists(cfg["id"], cache_key, cfg.get("retry_hours", 0)):
                    continue
                if cache_key in seen_keys:
                    continue
                candidates.append(item)
                seen_keys.add(cache_key)
                if len(candidates) >= per_run:
                    break

            wanted_count = len(candidates)

            if not candidates:
                agent.log("info", self.name, "No upgrade candidates found")
                db.history.finish_run(run_id, 0, 0, "success")
                agent.state["last_wanted"] = 0
                agent.state["last_triggered"] = 0
                agent.state["last_sync"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                return

            for item in candidates:
                if not agent.check_rate_cap():
                    agent.log("warn", self.name, "Rate cap reached — stopping run")
                    break

                label = item["label"]

                try:
                    result = self._trigger_upgrade(agent, arr_type, item)
                    triggered_count += 1
                    agent.record_action()
                    db.history.insert_item(
                        run_id,
                        result.title,
                        result.arr_id,
                        result.item_type,
                        cache_key=result.cache_key,
                        command_id=result.command_id,
                    )
                    db.searched.add(cfg["id"], result.cache_key, result.title, result.item_type)
                    agent.log("debug", self.name, f"Upgrade search: {result.title}")
                except Exception as exc:
                    agent.log("warn", self.name, f"Failed to trigger upgrade for {label}: {exc}")

                if delay > 0 and item != candidates[-1]:
                    time.sleep(delay)

            agent.log("info", self.name, f"Done — candidates: {wanted_count}, triggered: {triggered_count}")
            db.history.finish_run(run_id, wanted_count, triggered_count, "success")
            agent.state["last_wanted"] = wanted_count
            agent.state["last_triggered"] = triggered_count
            agent.state["last_sync"] = datetime.now().strftime("%Y-%m-%d %H:%M")

        except Exception as exc:
            agent.log("error", self.name, f"Upgrade search failed: {exc}")
            db.history.finish_run(run_id, wanted_count, triggered_count, "error", str(exc))

    def _cache_key(self, arr_type: str, item: dict) -> str:
        if arr_type == "radarr":
            return f"upg:{item['id']}"
        # Sonarr: key at season level if available (SeasonSearch deduplication)
        series_id = item.get("series_id")
        season_number = item.get("season_number")
        if series_id is not None and season_number is not None:
            return f"upg:sea:{series_id}:{season_number}"
        return f"upg:{item['id']}"

    def _trigger_upgrade(self, agent, arr_type: str, item: dict) -> SearchResult:
        """Fire the upgrade search and report what was actually addressed.

        Reuses _cache_key rather than spelling the keys out again: the pre-filter
        in execute() decides with that same function, and two copies of the rule
        would eventually disagree — filtering an item under one key while storing
        it under another.
        """
        label = item.get("label") or item.get("title") or f"#{item['id']}"
        cache_key = self._cache_key(arr_type, item)

        if arr_type == "radarr":
            movie_id = item["id"]
            resp = agent.http_post("/api/v3/command", {"name": "MoviesSearch", "movieIds": [movie_id]})
            return SearchResult(True, label, "movie", cache_key, movie_id, resp.get("id"))

        # Sonarr: prefer SeasonSearch if season info available, else EpisodeSearch
        series_id = item.get("series_id")
        season_number = item.get("season_number")
        episode_id = item["id"]
        if series_id is not None and season_number is not None:
            resp = agent.http_post(
                "/api/v3/command",
                {"name": "SeasonSearch", "seriesId": series_id, "seasonNumber": season_number},
            )
            return SearchResult(True, label, "season", cache_key, series_id, resp.get("id"))

        resp = agent.http_post("/api/v3/command", {"name": "EpisodeSearch", "episodeIds": [episode_id]})
        return SearchResult(True, label, "episode", cache_key, episode_id, resp.get("id"))

    def _collect_candidates(self, agent, arr_type: str, source: str, per_run: int) -> list[dict]:
        items = []

        if arr_type == "radarr":
            items = self._collect_radarr(agent, source, per_run)
        else:
            items = self._collect_sonarr(agent, per_run)

        # Shuffle for rotation before dedup so different items surface each run
        random.shuffle(items)

        # Deduplicate by episode/movie ID — season-level dedup happens in execute()
        seen = set()
        result = []
        for item in items:
            if item["id"] not in seen:
                seen.add(item["id"])
                result.append(item)

        return result

    def _collect_radarr(self, agent, source: str, per_run: int) -> list[dict]:
        items = []

        if source in ("wanted_list_only", "both"):
            try:
                pool_size = max(per_run * 5, 50)
                page = 1
                probe = agent.http_get("/api/v3/wanted/cutoff", params={"pageSize": 1, "page": 1, "monitored": "true"})
                total = probe.get("totalRecords", 0)
                if total > pool_size:
                    max_page = min(10, -(-total // pool_size))  # ceil division
                    if max_page >= 2:
                        page = random.randint(1, max_page)
                resp = agent.http_get(
                    "/api/v3/wanted/cutoff",
                    params={"pageSize": pool_size, "page": page, "monitored": "true"},
                )
                for r in resp.get("records", []):
                    if "id" in r and r.get("hasFile"):
                        year = r.get("year", "")
                        title = r.get("title") or f"Movie #{r['id']}"
                        label = f"{title} ({year})" if year else title
                        items.append({"id": r["id"], "label": label})
            except Exception as exc:
                agent.log("warn", self.name, f"Failed to fetch cutoff list: {exc}")

        if source in ("monitored_items_only", "both"):
            try:
                movies = agent.http_get("/api/v3/movie", params={"monitored": "true"})
                for m in (movies if isinstance(movies, list) else []):
                    if m.get("hasFile"):
                        year = m.get("year", "")
                        title = m.get("title") or f"Movie #{m['id']}"
                        label = f"{title} ({year})" if year else title
                        items.append({"id": m["id"], "label": label})
            except Exception as exc:
                agent.log("warn", self.name, f"Failed to fetch monitored movies: {exc}")

        return items

    def _collect_sonarr(self, agent, per_run: int) -> list[dict]:
        """Sonarr upgrades always use the cutoff (quality unmet) list."""
        items = []
        try:
            pool_size = max(per_run * 5, 50)
            page = 1
            probe = agent.http_get("/api/v3/wanted/cutoff", params={"pageSize": 1, "page": 1, "monitored": "true"})
            total = probe.get("totalRecords", 0)
            if total > pool_size:
                max_page = min(10, -(-total // pool_size))  # ceil division
                if max_page >= 2:
                    page = random.randint(1, max_page)
            resp = agent.http_get(
                "/api/v3/wanted/cutoff",
                params={"pageSize": pool_size, "page": page, "monitored": "true"},
            )
            for r in resp.get("records", []):
                if "id" not in r:
                    continue
                series = r.get("series") or {}
                series_title = series.get("title") or r.get("seriesTitle", "") or f"Series #{r.get('seriesId', '?')}"
                season_number = r.get("seasonNumber")
                ep_number = r.get("episodeNumber", 0)
                ep_title = r.get("title", "")
                if season_number is not None:
                    label = f"{series_title} S{(season_number or 0):02d}E{ep_number:02d}"
                    if ep_title:
                        label += f" – {ep_title}"
                else:
                    label = ep_title or f"Episode #{r['id']}"
                items.append({
                    "id": r["id"],
                    "label": label,
                    "series_id": r.get("seriesId"),
                    "season_number": season_number,
                })
        except Exception as exc:
            agent.log("warn", self.name, f"Failed to fetch Sonarr cutoff list: {exc}")
        return items
