import random
import time
from datetime import datetime, timezone, timedelta
from backend.skills.base import BaseSkill
from backend import db


class SearchMissingSkill(BaseSkill):
    name = "search_missing"

    def execute(self, agent, force: bool = False) -> None:
        cfg = agent.config
        run_id = db.history.start_run(cfg["id"], cfg["name"], self.name)
        wanted_count = 0
        triggered_count = 0

        try:
            per_run = cfg.get("missing_per_run", 5)
            search_order = cfg.get("search_order", "random")
            missing_mode = cfg.get("missing_mode", "episode")
            delay = cfg.get("seconds_between_actions", 2)

            # Fetch a larger pool so random order picks from a broad set,
            # not just the same top-N items every run.
            fetch_size = min(per_run * 10, 100) if search_order == "random" else per_run * 2

            params = {
                "pageSize": fetch_size,
                "page": 1,
                "monitored": "true",
                "sortKey": "airDateUtc" if cfg["type"] == "sonarr" else "physicalRelease",
                "sortDirection": "descending",
            }

            # For random order: probe for total so we can pick a random page and
            # reach items beyond the first page — true rotation across the full backlog.
            max_page = 1
            if search_order == "random":
                try:
                    probe = agent.http_get("/api/v3/wanted/missing", params={**params, "pageSize": 1})
                    total_available = probe.get("totalRecords", 0)
                    if total_available > fetch_size:
                        max_page = min(10, -(-total_available // fetch_size))  # ceil division
                        if max_page >= 2:
                            params["page"] = random.randint(1, max_page)
                except Exception:
                    pass

            agent.log("info", self.name, f"Searching for missing content (pool={fetch_size}, page={params['page']}, per_run={per_run})...")

            resp = agent.http_get("/api/v3/wanted/missing", params=params)
            records = resp.get("records", [])
            total = resp.get("totalRecords", 0)

            if not records:
                agent.log("info", self.name, "No missing content found")
                db.history.finish_run(run_id, 0, 0, "success")
                agent.state["last_wanted"] = 0
                agent.state["last_triggered"] = 0
                agent.state["last_sync"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                return

            # Filter: hours_after_release (skipped for force runs)
            hours = cfg.get("hours_after_release", 9)
            cutoff = datetime.now(timezone.utc) - timedelta(hours=hours) if (not force and hours > 0) else None
            if cutoff is not None:
                filtered = []
                for r in records:
                    date_str = r.get("airDateUtc") or r.get("physicalRelease") or r.get("inCinemas")
                    if date_str:
                        try:
                            released = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                            if released <= cutoff:
                                filtered.append(r)
                        except (ValueError, TypeError):
                            filtered.append(r)
                    else:
                        filtered.append(r)
                records = filtered

            if not records:
                agent.log("info", self.name, f"All results within hours_after_release={hours}h window")
                db.history.finish_run(run_id, 0, 0, "success")
                agent.state["last_wanted"] = 0
                agent.state["last_triggered"] = 0
                agent.state["last_sync"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                return

            # Apply search_order to the full pool
            records = self._apply_order(records, search_order, cfg["type"])

            # Filter already-searched items from cache, then take per_run.
            # Force runs bypass the DB cache but still deduplicate within this run.
            skipped_file = 0
            skipped_cache = 0
            candidates = []
            seen_keys: set = set()  # dedup within this run (same season/series key)
            for record in records:
                if record.get("hasFile"):
                    skipped_file += 1
                    continue
                # Use hierarchical keys for DB check: covers episode, season, and series level.
                check_keys = self._check_keys(cfg["type"], record, missing_mode)
                if not force and check_keys and db.searched.exists_any(cfg["id"], check_keys, cfg.get("retry_hours", 0)):
                    skipped_cache += 1
                    continue
                # Use the broad intent key for within-run dedup
                # (prevents duplicate season/series searches within the same run).
                dedup_key = self._cache_key(cfg["type"], record, missing_mode)
                if dedup_key and dedup_key in seen_keys:
                    continue
                candidates.append(record)
                if dedup_key:
                    seen_keys.add(dedup_key)
                if len(candidates) >= per_run:
                    break

            # Top-up: if not enough candidates from the first page, try more random pages
            if search_order == "random" and len(candidates) < per_run and max_page > 1:
                tried_pages = {params["page"]}
                for _ in range(max_page - 1):
                    if len(candidates) >= per_run:
                        break
                    remaining = [p for p in range(1, max_page + 1) if p not in tried_pages]
                    if not remaining:
                        break
                    params["page"] = random.choice(remaining)
                    tried_pages.add(params["page"])
                    try:
                        extra_resp = agent.http_get("/api/v3/wanted/missing", params=params)
                        extra_records = self._apply_order(extra_resp.get("records", []), search_order, cfg["type"])
                        for record in extra_records:
                            if len(candidates) >= per_run:
                                break
                            if record.get("hasFile"):
                                continue
                            if cutoff is not None:
                                date_str = record.get("airDateUtc") or record.get("physicalRelease") or record.get("inCinemas")
                                if date_str:
                                    try:
                                        released = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                                        if released > cutoff:
                                            continue
                                    except (ValueError, TypeError):
                                        pass
                            check_keys = self._check_keys(cfg["type"], record, missing_mode)
                            if not force and check_keys and db.searched.exists_any(cfg["id"], check_keys, cfg.get("retry_hours", 0)):
                                continue
                            dedup_key = self._cache_key(cfg["type"], record, missing_mode)
                            if dedup_key and dedup_key in seen_keys:
                                continue
                            candidates.append(record)
                            if dedup_key:
                                seen_keys.add(dedup_key)
                    except Exception:
                        break

            wanted_count = len(candidates)

            if skipped_file:
                agent.log("debug", self.name, f"Skipped {skipped_file} items that already have a file")
            if skipped_cache:
                agent.log("debug", self.name, f"Skipped {skipped_cache} already-searched items from pool of {len(records)}")

            if not candidates:
                skipped = skipped_file + skipped_cache
                agent.log("info", self.name, f"All {skipped} items in pool already searched or exist — nothing to do (total missing: {total})")
                db.history.finish_run(run_id, 0, 0, "success")
                agent.state["last_wanted"] = 0
                agent.state["last_triggered"] = 0
                agent.state["last_sync"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                return

            # Lazy series title lookup for Sonarr — only fetch the specific series
            # whose titles are missing from the wanted/missing response (typically 0–5),
            # instead of pre-fetching the entire library which is slow for large instances.
            series_lookup: dict[int, str] = {}
            if cfg["type"] == "sonarr":
                needed = {
                    r.get("seriesId") for r in candidates
                    if r.get("seriesId")
                    and not (r.get("series") or {}).get("title", "")
                    and not r.get("seriesTitle", "")
                }
                for sid in needed:
                    try:
                        s = agent.http_get(f"/api/v3/series/{sid}")
                        series_lookup[sid] = s.get("title", "")
                    except Exception:
                        pass

            # Execute searches respecting rate cap
            for record in candidates:
                if not agent.check_rate_cap():
                    agent.log("warn", self.name, "Rate cap reached — stopping run")
                    break

                success, title, item_type, stored_key = self._trigger_search(agent, cfg, record, missing_mode, series_lookup)
                if success:
                    triggered_count += 1
                    agent.record_action()
                    db.history.insert_item(run_id, title, record.get("id"), item_type)
                    if stored_key:
                        db.searched.add(cfg["id"], stored_key, title, item_type)

                if delay > 0 and record is not candidates[-1]:
                    time.sleep(delay)

            agent.log(
                "info", self.name,
                f"Done — candidates: {wanted_count}, triggered: {triggered_count} (total missing: {total})",
            )
            db.history.finish_run(run_id, wanted_count, triggered_count, "success")
            agent.state["last_wanted"] = wanted_count
            agent.state["last_triggered"] = triggered_count
            agent.state["last_sync"] = datetime.now().strftime("%Y-%m-%d %H:%M")

        except Exception as exc:
            agent.log("error", self.name, f"Search failed: {exc}")
            db.history.finish_run(run_id, wanted_count, triggered_count, "error", str(exc))

    def _cache_key(self, arr_type: str, record: dict, mode: str) -> str:
        """Broad intent key used for within-run deduplication (seen_keys set).
        Prevents triggering duplicate season/series searches in the same run."""
        if arr_type == "radarr":
            return f"mov:{record.get('id')}"
        if mode == "episode":
            return f"ep:{record.get('id')}"
        elif mode == "season_packs":
            return f"sea:{record.get('seriesId')}:{record.get('seasonNumber')}"
        elif mode == "show_batch":
            return f"ser:{record.get('seriesId')}"
        elif mode == "smart":
            # Use season-level key so multiple episodes from the same season don't each
            # trigger a separate SeasonSearch within the same run.
            return f"sea:{record.get('seriesId')}:{record.get('seasonNumber')}"
        return f"ep:{record.get('id')}"

    def _check_keys(self, arr_type: str, record: dict, mode: str) -> list:
        """Hierarchical cache keys for cross-run DB lookup.
        Checks narrow (episode) → broad (season/series) so that a past SeasonSearch
        blocks individual episode candidates and a past EpisodeSearch blocks that
        specific episode — without incorrectly blocking the whole season."""
        if arr_type == "radarr":
            return [f"mov:{record.get('id')}"]
        series_id = record.get("seriesId")
        season = record.get("seasonNumber")
        ep_id = record.get("id")
        if mode == "episode":
            return [f"ep:{ep_id}"]
        elif mode == "season_packs":
            return [f"sea:{series_id}:{season}", f"ep:{ep_id}"]
        elif mode == "show_batch":
            return [f"ser:{series_id}", f"sea:{series_id}:{season}", f"ep:{ep_id}"]
        elif mode == "smart":
            return [f"sea:{series_id}:{season}", f"ep:{ep_id}"]
        return [f"ep:{ep_id}"]

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

            random.shuffle(recent)
            random.shuffle(rest)

            # Build result: recent first, then random rest, then oldest rest
            rest_oldest = sorted(rest, key=lambda r: r.get(date_key) or "")

            n = len(records)
            n_recent = max(1, int(n * 0.5))
            n_random = max(1, int(n * 0.3))
            n_oldest = max(1, int(n * 0.2))

            result = recent[:n_recent] + rest[:n_random] + rest_oldest[:n_oldest]

            # Deduplicate by record ID (same record can appear in rest and rest_oldest)
            seen: set = set()
            records = []
            for r in result:
                rec_id = r.get("id")
                if rec_id not in seen:
                    seen.add(rec_id)
                    records.append(r)

        return records

    def _trigger_search(self, agent, cfg: dict, record: dict, missing_mode: str, series_lookup: dict | None = None) -> tuple[bool, str, str, str]:
        """Returns (success, title, item_type, stored_cache_key)."""
        try:
            if cfg["type"] == "sonarr":
                return self._sonarr_search(agent, record, missing_mode, series_lookup or {})
            else:
                return self._radarr_search(agent, record)
        except Exception as exc:
            agent.log("warn", self.name, f"Failed to trigger search: {exc}")
            return False, "", "", ""

    def _sonarr_search(self, agent, record: dict, mode: str, series_lookup: dict | None = None) -> tuple[bool, str, str, str]:
        """Returns (success, title, item_type, stored_cache_key).
        The stored_cache_key reflects the actual command issued, not the intent mode,
        so subsequent runs check at the correct granularity."""
        episode_id = record.get("id")
        series_id = record.get("seriesId")
        season_number = record.get("seasonNumber")
        series_title = (record.get("series") or {}).get("title", "") or record.get("seriesTitle", "")
        # Fall back to pre-fetched series lookup if API didn't include nested series data
        if not series_title and series_id and series_lookup:
            series_title = series_lookup.get(series_id, f"Series #{series_id}")
        ep_title = record.get("title", "")

        if mode == "episode" and episode_id:
            agent.http_post("/api/v3/command", {"name": "EpisodeSearch", "episodeIds": [episode_id]})
            label = f"{series_title} S{(season_number or 0):02d}E{record.get('episodeNumber', 0):02d} – {ep_title}" if series_title else ep_title
            agent.log("debug", self.name, f"EpisodeSearch: {label}")
            return True, label, "episode", f"ep:{episode_id}"

        elif mode == "season_packs" and series_id is not None and season_number is not None:
            try:
                eps = agent.http_get(f"/api/v3/episode?seriesId={series_id}&seasonNumber={season_number}&includeImages=false")
                total_eps = len(eps) if isinstance(eps, list) else 0
                missing_eps = sum(1 for e in (eps if isinstance(eps, list) else []) if not e.get("hasFile"))
                ratio = missing_eps / total_eps if total_eps > 0 else 1.0
                if ratio >= 0.5:
                    agent.http_post("/api/v3/command", {"name": "SeasonSearch", "seriesId": series_id, "seasonNumber": season_number})
                    label = f"{series_title} Season {season_number}"
                    agent.log("debug", self.name, f"SeasonSearch: {label} (missing {missing_eps}/{total_eps} eps)")
                    return True, label, "season", f"sea:{series_id}:{season_number}"
                else:
                    agent.http_post("/api/v3/command", {"name": "EpisodeSearch", "episodeIds": [episode_id]})
                    label = f"{series_title} S{(season_number or 0):02d}E{record.get('episodeNumber', 0):02d} – {ep_title}" if series_title else ep_title
                    agent.log("debug", self.name, f"season_packs → EpisodeSearch (only {missing_eps}/{total_eps} eps missing)")
                    return True, label, "episode", f"ep:{episode_id}"
            except Exception as exc:
                agent.log("debug", self.name, f"season_packs ratio check failed, falling back to EpisodeSearch: {exc}")
                agent.http_post("/api/v3/command", {"name": "EpisodeSearch", "episodeIds": [episode_id]})
                label = f"{series_title} S{(season_number or 0):02d}E{record.get('episodeNumber', 0):02d} – {ep_title}" if series_title else ep_title
                return True, label, "episode", f"ep:{episode_id}"

        elif mode == "show_batch" and series_id is not None:
            try:
                eps = agent.http_get(f"/api/v3/episode?seriesId={series_id}&includeImages=false")
                eps_list = eps if isinstance(eps, list) else []
                total_eps = len(eps_list)
                missing_eps = sum(1 for e in eps_list if not e.get("hasFile"))
                series_ratio = missing_eps / total_eps if total_eps > 0 else 1.0
                if series_ratio >= 0.5:
                    agent.http_post("/api/v3/command", {"name": "SeriesSearch", "seriesId": series_id})
                    agent.log("debug", self.name, f"SeriesSearch: {series_title} (missing {missing_eps}/{total_eps} eps)")
                    return True, series_title, "series", f"ser:{series_id}"
                else:
                    # Check season ratio using same data to avoid extra API call
                    sea_eps = [e for e in eps_list if e.get("seasonNumber") == season_number]
                    total_sea = len(sea_eps)
                    missing_sea = sum(1 for e in sea_eps if not e.get("hasFile"))
                    sea_ratio = missing_sea / total_sea if total_sea > 0 else 1.0
                    if sea_ratio >= 0.5 and season_number is not None:
                        agent.http_post("/api/v3/command", {"name": "SeasonSearch", "seriesId": series_id, "seasonNumber": season_number})
                        label = f"{series_title} Season {season_number}"
                        agent.log("debug", self.name, f"show_batch → SeasonSearch (series {missing_eps}/{total_eps}, season {missing_sea}/{total_sea})")
                        return True, label, "season", f"sea:{series_id}:{season_number}"
                    else:
                        agent.http_post("/api/v3/command", {"name": "EpisodeSearch", "episodeIds": [episode_id]})
                        label = f"{series_title} S{(season_number or 0):02d}E{record.get('episodeNumber', 0):02d} – {ep_title}" if series_title else ep_title
                        agent.log("debug", self.name, f"show_batch → EpisodeSearch (series {missing_eps}/{total_eps}, season {missing_sea}/{total_sea})")
                        return True, label, "episode", f"ep:{episode_id}"
            except Exception as exc:
                agent.log("debug", self.name, f"show_batch ratio check failed, falling back to EpisodeSearch: {exc}")
                agent.http_post("/api/v3/command", {"name": "EpisodeSearch", "episodeIds": [episode_id]})
                label = f"{series_title} S{(season_number or 0):02d}E{record.get('episodeNumber', 0):02d} – {ep_title}" if series_title else ep_title
                return True, label, "episode", f"ep:{episode_id}"

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
                    return True, label, "season", f"sea:{series_id}:{season_number}"
                else:
                    agent.http_post("/api/v3/command", {"name": "EpisodeSearch", "episodeIds": [episode_id]})
                    label = f"{series_title} S{(season_number or 0):02d}E{record.get('episodeNumber', 0):02d} – {ep_title}" if series_title else ep_title
                    agent.log("debug", self.name, f"Smart: EpisodeSearch (missing {missing_eps}/{total_eps} eps)")
                    return True, label, "episode", f"ep:{episode_id}"
            except Exception as exc:
                agent.log("debug", self.name, f"Smart mode episode fetch failed, falling back to EpisodeSearch: {exc}")
                agent.http_post("/api/v3/command", {"name": "EpisodeSearch", "episodeIds": [episode_id]})
                label = f"{series_title} S{(season_number or 0):02d}E{record.get('episodeNumber', 0):02d}" if series_title else ep_title
                return True, label, "episode", f"ep:{episode_id}"

        elif episode_id:
            agent.http_post("/api/v3/command", {"name": "EpisodeSearch", "episodeIds": [episode_id]})
            label = f"{series_title} S{(season_number or 0):02d}E{record.get('episodeNumber', 0):02d} – {ep_title}" if series_title else ep_title
            return True, label, "episode", f"ep:{episode_id}"

        return False, "", "", ""

    def _radarr_search(self, agent, record: dict) -> tuple[bool, str, str, str]:
        movie_id = record.get("id")
        title = record.get("title", f"Movie #{movie_id}")
        year = record.get("year", "")
        label = f"{title} ({year})" if year else title
        if movie_id:
            agent.http_post("/api/v3/command", {"name": "MoviesSearch", "movieIds": [movie_id]})
            agent.log("debug", self.name, f"MoviesSearch: {label}")
            return True, label, "movie", f"mov:{movie_id}"
        return False, "", "", ""
