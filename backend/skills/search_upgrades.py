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

            candidates = self._collect_candidates(agent, source, per_run)
            wanted_count = len(candidates)

            if not candidates:
                agent.log("info", self.name, "No upgrade candidates found")
                db.history.finish_run(run_id, 0, 0, "success")
                return

            for movie in candidates:
                if not agent.check_rate_cap():
                    agent.log("warn", self.name, "Rate cap reached — stopping run")
                    break

                movie_id = movie["id"]
                label = movie["label"]
                try:
                    agent.http_post("/api/v3/command", {"name": "MoviesSearch", "movieIds": [movie_id]})
                    triggered_count += 1
                    agent.record_action()
                    db.history.insert_item(run_id, label, movie_id, "movie")
                    agent.log("debug", self.name, f"Upgrade search: {label}")
                except Exception as exc:
                    agent.log("warn", self.name, f"Failed to trigger upgrade for {label}: {exc}")

                if delay > 0 and movie != candidates[-1]:
                    time.sleep(delay)

            agent.log("info", self.name, f"Done — candidates: {wanted_count}, triggered: {triggered_count}")
            db.history.finish_run(run_id, wanted_count, triggered_count, "success")

        except Exception as exc:
            agent.log("error", self.name, f"Upgrade search failed: {exc}")
            db.history.finish_run(run_id, wanted_count, triggered_count, "error", str(exc))

    def _collect_candidates(self, agent, source: str, per_run: int) -> list[dict]:
        items = []

        if source in ("wanted_list_only", "both"):
            try:
                resp = agent.http_get(
                    "/api/v3/wanted/cutoff",
                    params={"pageSize": per_run, "page": 1, "monitored": "true"},
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

        # Deduplicate, respect per_run limit
        seen = set()
        result = []
        for item in items:
            if item["id"] not in seen:
                seen.add(item["id"])
                result.append(item)
                if len(result) >= per_run:
                    break

        return result
