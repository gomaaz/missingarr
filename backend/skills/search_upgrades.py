import random
import time
from backend.skills.base import BaseSkill
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
                return

            for item in candidates:
                if not agent.check_rate_cap():
                    agent.log("warn", self.name, "Rate cap reached — stopping run")
                    break

                item_id = item["id"]
                label = item["label"]
                cache_key = self._cache_key(arr_type, item)

                try:
                    item_type = self._trigger_upgrade(agent, arr_type, item)
                    triggered_count += 1
                    agent.record_action()
                    db.history.insert_item(run_id, label, item_id, item_type)
                    db.searched.add(cfg["id"], cache_key, label, item_type)
                    agent.log("debug", self.name, f"Upgrade search: {label}")
                except Exception as exc:
                    agent.log("warn", self.name, f"Failed to trigger upgrade for {label}: {exc}")

                if delay > 0 and item != candidates[-1]:
                    time.sleep(delay)

            agent.log("info", self.name, f"Done — candidates: {wanted_count}, triggered: {triggered_count}")
            db.history.finish_run(run_id, wanted_count, triggered_count, "success")

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

    def _trigger_upgrade(self, agent, arr_type: str, item: dict) -> str:
        if arr_type == "radarr":
            agent.http_post("/api/v3/command", {"name": "MoviesSearch", "movieIds": [item["id"]]})
            return "movie"
        else:
            # Sonarr: prefer SeasonSearch if season info available, else EpisodeSearch
            series_id = item.get("series_id")
            season_number = item.get("season_number")
            episode_id = item.get("id")
            if series_id is not None and season_number is not None:
                agent.http_post("/api/v3/command", {"name": "SeasonSearch", "seriesId": series_id, "seasonNumber": season_number})
                return "season"
            else:
                agent.http_post("/api/v3/command", {"name": "EpisodeSearch", "episodeIds": [episode_id]})
                return "episode"

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
                resp = agent.http_get(
                    "/api/v3/wanted/cutoff",
                    params={"pageSize": max(per_run * 5, 50), "page": 1, "monitored": "true"},
                )
                for r in resp.get("records", []):
                    if "id" in r:
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
            resp = agent.http_get(
                "/api/v3/wanted/cutoff",
                params={"pageSize": max(per_run * 5, 50), "page": 1, "monitored": "true"},
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
