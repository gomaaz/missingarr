"""Quality-profile fingerprints for the search cache and the dry run (0.9.0).

Spec addendum "notice profile changes": at the start of every search run the
skills read the quality profiles, custom formats and release profiles, and the
global size limits (quality definitions) and indexer settings that decide
`approved` for every profile too (no indexer load), and fingerprint each
profile (checked_search/fingerprint.py). One of them unreadable counts like
all of them unreadable.
A cached title blocks only while it was searched under the current
fingerprint of its profile; a dry-run row counts for the round only with it.
If the profiles cannot be read, the fingerprints stored by the last run stay
in force: nothing is released that was not released before. If they cannot
be stored on the first run of 0.9.0, what was read counts as the baseline
for that run, so a failed write releases nothing either.

Which profile a title has: Radarr movies carry qualityProfileId (wanted,
cutoff and movie lists). Sonarr episodes do not; their series has it, and
the wanted lists name the series only with includeSeries=true, which
missingarr sends since 0.10.1 (before, every Sonarr run read the whole
series list). A record without its series is not released while
candidates are picked, but a title that is searched is still stored with
its fingerprint: the checked search takes it from the series it loads
anyway, the command search asks for the one series (stored_fingerprint). A
NULL fingerprint would count as searched under the baseline and could
release the title again at once.

The dry run also compares the rule settings: a row counts for the round only
under the fingerprint of the settings in force (settings_fingerprint).
"""

from dataclasses import dataclass, field
from typing import Callable, Optional

from backend import db
from backend.checked_search.fingerprint import changes, fingerprints, short
from backend.checked_search.settings import CheckedSearchSettings

# In the order fingerprints() takes the answers.
# Radarr and Sonarr both serve /api/v3/releaseprofile: each has a
# ReleaseProfileController marked [V3ApiController] without a resource name,
# so the route is api/v3/[controller] (Radarr.Api.V3/Profiles/Release and
# Sonarr.Api.V3/Profiles/Release). Radarr has it since version 5, which
# renamed its Restrictions to ReleaseProfiles (migration 229).
PROFILE_PATHS = ("/api/v3/qualityprofile", "/api/v3/customformat", "/api/v3/releaseprofile",
                 "/api/v3/qualitydefinition", "/api/v3/config/indexer")
SERIES_PATH = "/api/v3/series"
# Read once per run.
PROFILE_TIMEOUT = 60


@dataclass
class ProfileState:
    current: dict = field(default_factory=dict)          # str(profile id) -> fingerprint
    baseline: Optional[dict] = None                      # set by the first run of 0.9.0
    series_profiles: dict = field(default_factory=dict)  # Sonarr: series id -> quality profile id
    search_again: bool = True                            # "Search again after profile changes"
    settings_fingerprint: Optional[str] = None           # rule settings of the instance (dry run)
    # Sonarr: GET /series/{id} for a record that came without its series.
    series_loader: Optional[Callable] = None
    loaded_series: dict = field(default_factory=dict)    # series id -> quality profile id (this run)

    def profile_of(self, record: dict) -> Optional[int]:
        """Quality profile of a wanted/cutoff record, a movie or an upgrade item."""
        profile_id = record.get("qualityProfileId") or (record.get("series") or {}).get("qualityProfileId")
        if profile_id:
            return profile_id
        series_id = record.get("seriesId") or record.get("series_id")
        return self.series_profiles.get(series_id) if series_id is not None else None

    def fingerprint(self, profile_id) -> Optional[str]:
        return self.current.get(str(profile_id)) if profile_id is not None else None

    def stored_fingerprint(self, record: dict) -> Optional[str]:
        """The fingerprint a search of this record is stored under. Like
        fingerprint(profile_of(record)); when a Sonarr record came without
        its series, the series is asked for (once per series and run, only
        for titles that are searched)."""
        current = self.fingerprint(self.profile_of(record))
        if current is not None or self.series_loader is None:
            return current
        series_id = record.get("seriesId") or record.get("series_id")
        if series_id is None:
            return None
        if series_id not in self.loaded_series:
            try:
                series = self.series_loader(series_id)
            except Exception:
                series = None
            self.loaded_series[series_id] = series.get("qualityProfileId") if isinstance(series, dict) else None
        return self.fingerprint(self.loaded_series[series_id])

    def expected(self, profile_id) -> tuple:
        """(current, baseline) fingerprint of a profile."""
        if profile_id is None:
            return None, None
        return self.current.get(str(profile_id)), (self.baseline or {}).get(str(profile_id))

    def cache_filter(self, keyed) -> Optional[dict]:
        """`fingerprints` for db.searched.lookup_many: cache key -> (current,
        baseline) of its record's profile. None while the setting is off:
        then every cache entry blocks, whatever the profile."""
        if not self.search_again:
            return None
        return {key: self.expected(self.profile_of(record)) for key, record in keyed}

    def round_blocks(self, round_keys: dict, key: str, record: dict) -> bool:
        """Dry run: was the title checked in this round under its current
        profile fingerprint and the current rule settings? An unknown current
        profile fingerprint counts as the same (nothing is released).
        Independent of the setting: a dry run always notices profile and
        settings changes."""
        current = self.fingerprint(self.profile_of(record))
        return any(
            (current is None or profile == current) and settings == self.settings_fingerprint
            for profile, settings in round_keys.get(key, ())
        )


def refresh(skill_name: str, agent) -> ProfileState:
    """Read the profiles, log every changed one, keep the new fingerprints.
    Never raises because of *arr: without an answer the stored state counts."""
    cfg = agent.config
    stored = db.instances.get_by_id(cfg["id"]) or {}
    state = ProfileState(
        current=dict(stored.get("profile_fingerprints") or {}),
        baseline=stored.get("profile_fingerprints_baseline"),
        search_again=bool(cfg.get("search_again_after_profile_change", 1)),
        settings_fingerprint=CheckedSearchSettings.from_stored(
            cfg.get("checked_search_settings")).rules_fingerprint(cfg.get("type") or ""),
    )
    try:
        answers = [agent.http_get(path, timeout=PROFILE_TIMEOUT) for path in PROFILE_PATHS]
        current = fingerprints(*answers)
    except Exception as exc:
        agent.log("warn", skill_name,
                  f"Could not read the quality profiles — the stored fingerprints stay in force: {exc}")
    else:
        names = {str(p.get("id")): p.get("name") for p in answers[0] if isinstance(p, dict)}
        for profile_id, old, new in changes(state.current, current):
            name = names.get(profile_id) or f"#{profile_id}"
            agent.log("info", skill_name, f"Quality profile changed: {name} ({short(old)} → {short(new)})")
        try:
            state.baseline = db.instances.store_profile_fingerprints(cfg["id"], current)
        except Exception as exc:
            agent.log("warn", skill_name, f"Could not store the quality profile fingerprints: {exc}")
            if state.baseline is None:
                # First run of 0.9.0 and the write failed: entries from before
                # count under what was just read, for this run — the update
                # alone releases nothing. The next run stores the baseline.
                state.baseline = dict(current)
        state.current = current
    if cfg.get("type") == "sonarr":
        # The wanted lists embed each episode's series (includeSeries); one
        # that comes without it asks for its series when it is searched.
        state.series_loader = lambda series_id: agent.http_get(f"{SERIES_PATH}/{series_id}")
    return state
