"""0.13.0: Sonarr follows Upgrade Source. "Monitored Items Only" means the
monitored episodes whose file scores below the cutoff format score of its
quality profile, the lowest score first (spec 2026-10-07)."""

import copy

import requests

from backend import db
from backend.skills import profiles as profiles_module
from backend.skills.profiles import ProfileState
from tests.test_p2_search_upgrades import FakeArr, db_path, make_instance  # noqa: F401 (fixture)

QUALITY_PROFILES = "/api/v3/qualityprofile"
SERIES = "/api/v3/series"
EPISODES = "/api/v3/episode"

PROFILES = [
    {"id": 1, "name": "HD", "upgradeAllowed": True, "cutoff": 7, "minFormatScore": 0, "cutoffFormatScore": 1000,
     "formatItems": []},
    {"id": 2, "name": "Frozen", "upgradeAllowed": False, "cutoff": 7, "minFormatScore": 0,
     "cutoffFormatScore": 1000, "formatItems": []},
    {"id": 3, "name": "No score", "upgradeAllowed": True, "cutoff": 7, "minFormatScore": 0, "formatItems": []},
]


class SonarrFake(FakeArr):
    """FakeArr plus what the new source reads: the quality profiles (and the
    other answers the fingerprints need), the series list and the episode
    list of a series with its files."""

    def __init__(self, config, series=(), profiles=None, episode_errors=(), **kwargs):
        super().__init__(config, **kwargs)
        self.series = copy.deepcopy(list(series))
        self.profiles = copy.deepcopy(PROFILES if profiles is None else profiles)
        self.episode_errors = set(episode_errors)

    def http_get(self, path, params=None, timeout=10):
        params = dict(params or {})
        if path == QUALITY_PROFILES:
            self.gets.append((path, params))
            return copy.deepcopy(self.profiles)
        if path in ("/api/v3/customformat", "/api/v3/releaseprofile", "/api/v3/qualitydefinition"):
            self.gets.append((path, params))
            return []
        if path == "/api/v3/config/indexer":
            self.gets.append((path, params))
            return {}
        if path == SERIES:
            self.gets.append((path, params))
            if path in self.get_errors:
                raise self.get_errors[path]
            return copy.deepcopy(self.series)
        if path == EPISODES and params.get("includeEpisodeFile") == "true":
            self.gets.append((path, params))
            if params["seriesId"] in self.episode_errors:
                raise requests.exceptions.ReadTimeout("slow")
            return [copy.deepcopy(e) for e in self.episodes if e["seriesId"] == params["seriesId"]]
        return super().http_get(path, params, timeout)


def sonarr_agent(inst, **kwargs):
    return SonarrFake(db.instances.get_by_id(inst["id"]), **kwargs)


# ── Profile values ───────────────────────────────────────────────────────────


def test_profile_state_keeps_cutoff_format_score_and_upgrade_allowed(db_path):
    inst = make_instance()
    state = profiles_module.refresh("search_upgrades", sonarr_agent(inst))
    assert state.cutoff_format_scores == {"1": 1000, "2": 1000}
    assert state.upgrade_allowed == {"1": True, "2": False, "3": True}
    assert state.upgrade_cutoff(1) == 1000
    assert state.upgrade_cutoff(2) is None      # upgrades not allowed
    assert state.upgrade_cutoff(3) is None      # no cutoff format score
    assert state.upgrade_cutoff(9) is None      # unknown profile
    assert state.upgrade_cutoff(None) is None


def test_profile_state_without_an_answer_knows_no_profile_values(db_path):
    inst = make_instance()
    agent = sonarr_agent(inst, get_errors={})
    agent.profiles = "not a list"
    state = profiles_module.refresh("search_upgrades", agent)
    assert state.cutoff_format_scores == {} and state.upgrade_allowed == {}
    assert ProfileState().upgrade_cutoff(1) is None
