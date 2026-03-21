import random
import time
from datetime import datetime, timezone, timedelta
from backend.skills.base import BaseSkill
from backend import db


class SearchMissingSkill(BaseSkill):
    name = "search_missing"

    def execute(self, agent) -> None:
        cfg = agent.config
        run_id = db.history.start_run(cfg["id"], cfg["name"], self.name)
        wanted_count = 0
        triggered_count = 0

        try:
            agent.log("info", self.name, "Searching for missing content...")

            params = {
                "pageSize": cfg.get("missing_per_run", 5),
                "page": 1,
                "monitored": "true",
                "sortKey": "airDateUtc" if cfg["type"] == "sonarr" else "physicalRelease",
                "sortDirection": "descending",
            }

            resp = agent.http_get("/api/v3/wanted/missing", params=params)
            records = resp.get("records", [])
            total = resp.get("totalRecords", 0)
            wanted_count = min(len(records), cfg.get("missing_per_run", 5))

            if not records:
                agent.log("info", self.name, "No missing content found")
                db.history.finish_run(run_id, 0, 0, "success")
                agent.state["last_wanted"] = 0
                agent.state["last_triggered"] = 0
                return

            # Filter: hours_after_release
            hours = cfg.get("hours_after_release", 9)
            if hours > 0:
                cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
                filtered = []
                for r in records:
                    date_str = r.get("airDateUtc") or r.get("physicalRelease") or r.get("inCinemas")
                    if date_str:
                        try:
                            released = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                            if released <= cutoff:
                                filtered.append(r)
                        except ValueError:
                            filtered.append(r)
                    else:
                        filtered.append(r)
                records = filtered

            if not records:
                agent.log("info", self.name, f"All {wanted_count} results are too recent (hours_after_release={hours}h)")
                db.history.finish_run(run_id, wanted_count, 0, "success")
                return

            # Apply search_order
            records = self._apply_order(records, cfg.get("search_order", "random"), cfg["type"])

            # Execute searches respecting rate cap
            delay = cfg.get("seconds_between_actions", 2)
            missing_mode = cfg.get("missing_mode", "episode")

            for record in records:
                if not agent.check_rate_cap():
                    agent.log("warn", self.name, "Rate cap reached — stopping run")
                    break

                cache_key = self._cache_key(cfg["type"], record, missing_mode)
                if cache_key and db.searched.exists(cfg["id"], cache_key):
                    agent.log("debug", self.name, f"Already searched, skipping: {record.get('title') or record.get('id')}")
                    continue

                success, title, item_type = self._trigger_search(agent, cfg, record, missing_mode)
                if success:
                    triggered_count += 1
                    agent.record_action()
                    db.history.insert_item(run_id, title, record.get("id"), item_type)
                    if cache_key:
                        db.searched.add(cfg["id"], cache_key, title, item_type)

                if delay > 0 and record is not records[-1]:
                    time.sleep(delay)

            agent.log(
                "info", self.name,
                f"Done — wanted: {wanted_count}, triggered: {triggered_count} (total missing: {total})",
            )
            db.history.finish_run(run_id, wanted_count, triggered_count, "success")
            agent.state["last_wanted"] = wanted_count
            agent.state["last_triggered"] = triggered_count
            agent.state["last_sync"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

        except Exception as exc:
            agent.log("error", self.name, f"Search failed: {exc}")
            db.history.finish_run(run_id, wanted_count, triggered_count, "error", str(exc))

    def _cache_key(self, arr_type: str, record: dict, mode: str) -> str:
        """Build a stable cache key for the searched item."""
        if arr_type == "radarr":
            return f"mov:{record.get('id')}"
        # Sonarr — key depends on mode
        if mode == "episode":
            return f"ep:{record.get('id')}"
        elif mode == "season_packs":
            return f"sea:{record.get('seriesId')}:{record.get('seasonNumber')}"
        elif mode == "show_batch":
            return f"ser:{record.get('seriesId')}"
        elif mode == "smart":
            # Key on season level (smart may pick episode or season per run)
            return f"sea:{record.get('seriesId')}:{record.get('seasonNumber')}"
        return f"ep:{record.get('id')}"

    def _apply_order(self, records: list, order: str, arr_type: str) -> list:
        date_key = "airDateUtc" if arr_type == "sonarr" else "physicalRelease"

        if order == "random":
            random.shuffle(records)

        elif order == "newest_first":
            records.sort(
                key=lambda r: r.get(date_key) or "",
                reverse=True,
            )

        elif order == "oldest_first":
            records.sort(
                key=lambda r: r.get(date_key) or "",
                reverse=False,
            )

        elif order == "smart":
            # 50% recent (last 30d), 30% random, 20% oldest
            now = datetime.now(timezone.utc)
            cutoff_30d = now - timedelta(days=30)
            recent, rest = [], []
            for r in records:
                date_str = r.get(date_key) or ""
                try:
                    d = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                    if d >= cutoff_30d:
                        recent.append(r)
                    else:
                        rest.append(r)
                except ValueError:
                    rest.append(r)

            n = len(records)
            n_recent = max(1, int(n * 0.5))
            n_oldest = max(1, int(n * 0.2))

            random.shuffle(recent)
            rest_random = rest[:]
            random.shuffle(rest_random)
            rest_oldest = sorted(rest, key=lambda r: r.get(date_key) or "")

            result = recent[:n_recent] + rest_random[:int(n * 0.3)] + rest_oldest[:n_oldest]
            seen = set()
            records = []
            for r in result:
                rid = id(r)
                if rid not in seen:
                    seen.add(rid)
                    records.append(r)

        return records

    def _trigger_search(self, agent, cfg: dict, record: dict, missing_mode: str) -> tuple[bool, str, str]:
        try:
            if cfg["type"] == "sonarr":
                return self._sonarr_search(agent, record, missing_mode)
            else:
                return self._radarr_search(agent, record)
        except Exception as exc:
            agent.log("warn", self.name, f"Failed to trigger search: {exc}")
            return False, "", ""

    def _sonarr_search(self, agent, record: dict, mode: str) -> tuple[bool, str, str]:
        episode_id = record.get("id")
        series_id = record.get("seriesId")
        season_number = record.get("seasonNumber")
        series_title = record.get("series", {}).get("title", "") or record.get("seriesTitle", "")
        ep_title = record.get("title", "")

        if mode == "episode" and episode_id:
            agent.http_post("/api/v3/command", {"name": "EpisodeSearch", "episodeIds": [episode_id]})
            label = f"{series_title} S{season_number:02d}E{record.get('episodeNumber', 0):02d} – {ep_title}" if series_title else ep_title
            agent.log("debug", self.name, f"EpisodeSearch: {label}")
            return True, label, "episode"

        elif mode == "season_packs" and series_id is not None and season_number is not None:
            agent.http_post("/api/v3/command", {"name": "SeasonSearch", "seriesId": series_id, "seasonNumber": season_number})
            label = f"{series_title} Season {season_number}"
            agent.log("debug", self.name, f"SeasonSearch: {label}")
            return True, label, "season"

        elif mode == "show_batch" and series_id:
            agent.http_post("/api/v3/command", {"name": "SeriesSearch", "seriesId": series_id})
            agent.log("debug", self.name, f"SeriesSearch: {series_title}")
            return True, series_title, "series"

        elif mode == "smart" and series_id is not None and season_number is not None:
            try:
                eps = agent.http_get(f"/api/v3/episode?seriesId={series_id}&seasonNumber={season_number}&includeImages=false")
                total_eps = len(eps) if isinstance(eps, list) else 0
                missing_eps = sum(1 for e in (eps if isinstance(eps, list) else []) if not e.get("hasFile"))
                ratio = missing_eps / total_eps if total_eps > 0 else 1.0

                if ratio >= 0.5:
                    agent.http_post("/api/v3/command", {"name": "SeasonSearch", "seriesId": series_id, "seasonNumber": season_number})
                    label = f"{series_title} Season {season_number}"
                    agent.log("debug", self.name, f"Smart: SeasonSearch (missing {missing_eps}/{total_eps} eps)")
                    return True, label, "season"
                else:
                    agent.http_post("/api/v3/command", {"name": "EpisodeSearch", "episodeIds": [episode_id]})
                    label = f"{series_title} S{season_number:02d}E{record.get('episodeNumber', 0):02d} – {ep_title}" if series_title else ep_title
                    agent.log("debug", self.name, f"Smart: EpisodeSearch (missing {missing_eps}/{total_eps} eps)")
                    return True, label, "episode"
            except Exception:
                agent.http_post("/api/v3/command", {"name": "EpisodeSearch", "episodeIds": [episode_id]})
                label = f"{series_title} S{season_number:02d}E{record.get('episodeNumber', 0):02d}" if series_title else ep_title
                return True, label, "episode"

        elif episode_id:
            agent.http_post("/api/v3/command", {"name": "EpisodeSearch", "episodeIds": [episode_id]})
            label = f"{series_title} S{season_number:02d}E{record.get('episodeNumber', 0):02d} – {ep_title}" if series_title else ep_title
            return True, label, "episode"

        return False, "", ""

    def _radarr_search(self, agent, record: dict) -> tuple[bool, str, str]:
        movie_id = record.get("id")
        title = record.get("title", f"Movie #{movie_id}")
        year = record.get("year", "")
        label = f"{title} ({year})" if year else title
        if movie_id:
            agent.http_post("/api/v3/command", {"name": "MoviesSearch", "movieIds": [movie_id]})
            agent.log("debug", self.name, f"MoviesSearch: {label}")
            return True, label, "movie"
        return False, "", ""
