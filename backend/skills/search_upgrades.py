import time
from backend.skills.base import BaseSkill
from backend import db


class SearchUpgradesSkill(BaseSkill):
    name = "search_upgrades"

    def execute(self, agent) -> None:
        cfg = agent.config
        if cfg["type"] != "radarr":
            return

        run_id = db.history.start_run(cfg["id"], cfg["name"], self.name)
        wanted_count = 0
        triggered_count = 0

        try:
            agent.log("info", self.name, "Searching for upgrade candidates...")
            source = cfg.get("upgrade_source", "monitored_items_only")
            per_run = cfg.get("upgrades_per_run", 1)
            delay = cfg.get("seconds_between_actions", 2)

            movie_ids = self._collect_candidates(agent, source, per_run)
            wanted_count = len(movie_ids)

            if not movie_ids:
                agent.log("info", self.name, "No upgrade candidates found")
                db.history.finish_run(run_id, 0, 0, "success")
                return

            for movie_id in movie_ids:
                if not agent.check_rate_cap():
                    agent.log("warn", self.name, "Rate cap reached — stopping run")
                    break

                try:
                    agent.http_post("/api/v3/command", {"name": "MoviesSearch", "movieIds": [movie_id]})
                    triggered_count += 1
                    agent.record_action()
                    agent.log("debug", self.name, f"Upgrade search triggered for movie {movie_id}")
                except Exception as exc:
                    agent.log("warn", self.name, f"Failed to trigger upgrade for movie {movie_id}: {exc}")

                if delay > 0 and movie_id != movie_ids[-1]:
                    time.sleep(delay)

            agent.log("info", self.name, f"Done — candidates: {wanted_count}, triggered: {triggered_count}")
            db.history.finish_run(run_id, wanted_count, triggered_count, "success")

        except Exception as exc:
            agent.log("error", self.name, f"Upgrade search failed: {exc}")
            db.history.finish_run(run_id, wanted_count, triggered_count, "error", str(exc))

    def _collect_candidates(self, agent, source: str, per_run: int) -> list[int]:
        ids = []

        if source in ("wanted_list_only", "both"):
            try:
                resp = agent.http_get(
                    "/api/v3/wanted/cutoff",
                    params={"pageSize": per_run, "page": 1, "monitored": "true"},
                )
                records = resp.get("records", [])
                ids += [r["id"] for r in records if "id" in r]
            except Exception as exc:
                agent.log("warn", self.name, f"Failed to fetch cutoff list: {exc}")

        if source in ("monitored_items_only", "both"):
            try:
                movies = agent.http_get(
                    "/api/v3/movie",
                    params={"monitored": "true"},
                )
                # Only movies that have a file but might need upgrade
                candidates = [
                    m["id"] for m in (movies if isinstance(movies, list) else [])
                    if m.get("hasFile") and not m.get("isAvailable") is False
                ]
                ids += candidates[:per_run]
            except Exception as exc:
                agent.log("warn", self.name, f"Failed to fetch monitored movies: {exc}")

        # Deduplicate, respect per_run limit
        seen = set()
        result = []
        for mid in ids:
            if mid not in seen:
                seen.add(mid)
                result.append(mid)
                if len(result) >= per_run:
                    break

        return result
