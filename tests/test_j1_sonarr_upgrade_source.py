"""0.13.0: Sonarr follows Upgrade Source. "Monitored Items Only" means the
monitored episodes whose file scores below the cutoff format score of its
quality profile, the lowest score first (spec 2026-10-07)."""

import copy
from pathlib import Path

import pytest
import requests

from backend import db
from backend.checked_search.runner import CheckedRunOutcome
from backend.db import history
from backend.skills import profiles as profiles_module
from backend.skills import search_upgrades
from backend.skills.profiles import ProfileState
from backend.skills.search_upgrades import SearchUpgradesSkill
from backend.tooltips import TOOLTIPS
from tests.test_p5_form import client  # noqa: F401 (fixture)
from tests.test_p2_search_upgrades import (  # noqa: F401
    CUTOFF, MOVIES, FakeArr, cache_all, cutoff_episode, db_path, make_instance,
)

QUALITY_PROFILES = "/api/v3/qualityprofile"
SERIES = "/api/v3/series"
EPISODES = "/api/v3/episode"
ROOT = Path(__file__).resolve().parent.parent

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
        if path in (QUALITY_PROFILES, "/api/v3/customformat", "/api/v3/releaseprofile",
                    "/api/v3/qualitydefinition", "/api/v3/config/indexer", SERIES):
            self.gets.append((path, params))
            if path in self.get_errors:
                raise self.get_errors[path]
        if path == QUALITY_PROFILES:
            return copy.deepcopy(self.profiles)
        if path in ("/api/v3/customformat", "/api/v3/releaseprofile", "/api/v3/qualitydefinition"):
            return []
        if path == "/api/v3/config/indexer":
            return {}
        if path == SERIES:
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


# ── Monitored episodes ───────────────────────────────────────────────────────


def show(series_id, profile=1, monitored=True, files=3):
    return {"id": series_id, "title": f"Show {series_id}", "monitored": monitored, "qualityProfileId": profile,
            "seriesType": "standard", "statistics": {"episodeFileCount": files}}


def owned(episode_id, series_id, score, monitored=True, season=1):
    """An episode with a file of the given custom format score (None: the
    file carries no score)."""
    episode_file = {"id": 500 + episode_id, "seriesId": series_id, "seasonNumber": season}
    if score is not None:
        episode_file["customFormatScore"] = score
    return {"id": episode_id, "seriesId": series_id, "seasonNumber": season, "episodeNumber": episode_id,
            "title": f"Episode {episode_id}", "monitored": monitored, "hasFile": True, "episodeFile": episode_file}


def missing(episode_id, series_id):
    return {"id": episode_id, "seriesId": series_id, "seasonNumber": 1, "episodeNumber": episode_id,
            "title": f"Episode {episode_id}", "monitored": True, "hasFile": False, "episodeFileId": 0}


def collect(agent, per_run=50, checked=True):
    """The candidates of a run (checked: keyed per episode), as episode ids."""
    state = profiles_module.refresh("search_upgrades", agent)
    candidates, failures, notes, _ = SearchUpgradesSkill()._collect_candidates(
        agent, agent.config, per_run, False, checked, None, state)
    return candidates, failures, notes


def episode_lists(agent):
    return [params["seriesId"] for path, params in agent.gets if path == EPISODES]


def run_upgrades(agent, force=False):
    SearchUpgradesSkill().execute(agent, force=force)


def last_run():
    return history.query(limit=1)[0]


@pytest.mark.parametrize("source,cutoff_read,series_read", [
    ("wanted_list_only", True, False),
    ("monitored_items_only", False, True),
    ("both", True, True),
    (None, False, True),          # the stored default is monitored_items_only
])
def test_sonarr_follows_the_upgrade_source(db_path, source, cutoff_read, series_read):
    fields = {"search_upgrades_enabled": True}
    if source:
        fields["upgrade_source"] = source
    agent = sonarr_agent(make_instance(**fields), series=[show(10)], episodes=[owned(1, 10, 100)],
                         cutoff=[cutoff_episode(2)])
    run_upgrades(agent)
    paths = [path for path, _ in agent.gets]
    assert (CUTOFF in paths) is cutoff_read
    assert (SERIES in paths) is series_read


def test_only_monitored_episodes_with_a_file_below_the_cutoff_score_are_candidates(db_path):
    inst = make_instance(upgrade_source="monitored_items_only")
    episodes = [
        owned(1, 10, 100),                  # candidate
        owned(2, 10, 999),                  # candidate: one point below
        owned(3, 10, 1000),                 # at the cutoff score
        owned(4, 10, 2000),                 # above it
        owned(5, 10, 100, monitored=False),  # episode not monitored
        missing(6, 10),                     # no file
        {**owned(7, 10, 100), "episodeFile": None},  # hasFile without the file
        owned(8, 10, None),                 # the file carries no score
        owned(11, 11, 100),                 # series not monitored
        owned(12, 12, 100),                 # profile allows no upgrades
        owned(13, 13, 100),                 # profile without a cutoff score
        owned(14, 14, 100),                 # unknown profile
        owned(15, 15, 100),                 # series without files
    ]
    series = [show(10), show(11, monitored=False), show(12, profile=2), show(13, profile=3),
              show(14, profile=99), show(15, files=0)]
    agent = sonarr_agent(inst, series=series, episodes=episodes)
    candidates, failures, _ = collect(agent)
    assert failures == []
    assert sorted(item["id"] for item in candidates) == [1, 2]
    assert episode_lists(agent) == [10]


def test_a_candidate_carries_what_the_cutoff_list_carries(db_path):
    inst = make_instance(upgrade_source="monitored_items_only")
    agent = sonarr_agent(inst, series=[show(10)], episodes=[owned(3, 10, 100, season=2)])
    [candidate], _, _ = collect(agent)
    assert candidate == {"id": 3, "label": "Show 10 S02E03 – Episode 3", "series_id": 10, "season_number": 2,
                         "qualityProfileId": 1}
    params = [params for path, params in agent.gets if path == EPISODES]
    assert params == [{"seriesId": 10, "includeEpisodeFile": "true"}]


def test_the_lowest_score_comes_first_across_series(db_path, monkeypatch):
    monkeypatch.setattr(search_upgrades.random, "shuffle", lambda seq: None)
    inst = make_instance(upgrade_source="monitored_items_only")
    episodes = [owned(1, 10, 900), owned(2, 10, 300), owned(3, 11, 500), owned(4, 11, -200), owned(5, 12, 700)]
    agent = sonarr_agent(inst, series=[show(10), show(11), show(12)], episodes=episodes)
    found = []
    SearchUpgradesSkill()._collect_monitored_episodes(
        agent, agent.config, 5, False, found, set(), [], True, None,
        profiles_module.refresh("search_upgrades", agent))
    assert [item["id"] for item in found] == [4, 2, 3, 5, 1]


def test_equal_scores_are_in_random_order(db_path, monkeypatch):
    # The candidates are shuffled before the (stable) sort by score.
    monkeypatch.setattr(search_upgrades.random, "shuffle", lambda seq: seq.reverse())
    inst = make_instance(upgrade_source="monitored_items_only")
    episodes = [owned(1, 10, 300), owned(2, 10, 300), owned(3, 10, 100)]
    agent = sonarr_agent(inst, series=[show(10)], episodes=episodes)
    found = []
    SearchUpgradesSkill()._collect_monitored_episodes(
        agent, agent.config, 3, False, found, set(), [], True, None,
        profiles_module.refresh("search_upgrades", agent))
    assert [item["id"] for item in found] == [3, 2, 1]


def test_the_lowest_score_is_searched_on_the_command_path(db_path):
    inst = make_instance(search_upgrades_enabled=True, upgrades_per_run=1, upgrade_source="monitored_items_only")
    episodes = [owned(1, 10, 900), owned(2, 11, 50, season=3), owned(3, 12, 400)]
    agent = sonarr_agent(inst, series=[show(10), show(11), show(12)], episodes=episodes)
    run_upgrades(agent)
    assert agent.posts == [{"name": "SeasonSearch", "seriesId": 11, "seasonNumber": 3}]


def test_at_most_ten_episode_lists_are_read_per_run(db_path):
    inst = make_instance(upgrade_source="monitored_items_only")
    series = [show(i) for i in range(1, 16)]
    episodes = [owned(100 + i, i, 100) for i in range(1, 16)]
    agent = sonarr_agent(inst, series=series, episodes=episodes)
    candidates, _, _ = collect(agent, per_run=1)
    assert search_upgrades.MONITORED_SERIES_BUDGET == 10
    assert len(episode_lists(agent)) == 10
    assert len(candidates) == 1


def test_reading_ends_early_with_enough_candidates(db_path):
    # per_run 1 wants at least 20 candidates; the first series has 25.
    inst = make_instance(upgrade_source="monitored_items_only")
    episodes = [owned(i, 10, i) for i in range(1, 26)] + [owned(100 + i, 10 + i, 1) for i in range(1, 5)]
    agent = sonarr_agent(inst, series=[show(10 + i) for i in range(5)], episodes=episodes)
    agent.series.sort(key=lambda s: s["id"] != 10)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(search_upgrades.random, "shuffle", lambda seq: None)
        collect(agent, per_run=1)
    assert episode_lists(agent) == [10]


@pytest.mark.parametrize("per_run,first,read", [
    (1, 19, [10, 11]),      # below max(per_run * 20, 20): the next series is read
    (1, 20, [10]),          # the threshold itself ends the reading
    (2, 25, [10, 11, 12]),  # the threshold grows with Upgrades Per Run (40)
    (2, 40, [10]),
])
def test_the_reading_threshold_is_20_candidates_per_upgrades_per_run(db_path, monkeypatch, per_run, first, read):
    monkeypatch.setattr(search_upgrades.random, "shuffle", lambda seq: None)
    inst = make_instance(upgrade_source="monitored_items_only")
    episodes = [owned(i, 10, i) for i in range(1, first + 1)] + [owned(100, 11, 1), owned(101, 12, 1)]
    agent = sonarr_agent(inst, series=[show(10), show(11), show(12)], episodes=episodes)
    candidates, _, _ = collect(agent, per_run=per_run)
    assert episode_lists(agent) == read
    assert len(candidates) == per_run


def test_a_series_whose_candidates_are_all_cached_does_not_end_the_reading(db_path, monkeypatch):
    # Command path, Retry 0: the first series' season was searched before
    # and stays cached. Its 25 episodes must not count as enough (A5).
    monkeypatch.setattr(search_upgrades.random, "shuffle", lambda seq: None)
    inst = make_instance(search_upgrades_enabled=True, upgrades_per_run=1, upgrade_source="monitored_items_only",
                         retry_hours=0)
    cache_all(inst["id"], ["upg:sea:10:1"])
    episodes = [owned(i, 10, 0) for i in range(1, 26)] + [owned(100, 11, 100, season=2)]
    agent = sonarr_agent(inst, series=[show(10), show(11)], episodes=episodes)
    run_upgrades(agent)
    assert episode_lists(agent) == [10, 11]
    assert agent.posts == [{"name": "SeasonSearch", "seriesId": 11, "seasonNumber": 2}]


def test_cached_episodes_do_not_count_toward_the_reading_threshold(db_path, monkeypatch):
    # Checked search keys per episode: 20 cached episodes in the first
    # series, one uncached in the second.
    monkeypatch.setattr(search_upgrades.random, "shuffle", lambda seq: None)
    inst = make_instance(upgrade_source="monitored_items_only")
    cache_all(inst["id"], [f"upg:{i}" for i in range(1, 21)])
    episodes = [owned(i, 10, 0) for i in range(1, 21)] + [owned(100, 11, 100)]
    agent = sonarr_agent(inst, series=[show(10), show(11)], episodes=episodes)
    candidates, _, _ = collect(agent, per_run=1)
    assert episode_lists(agent) == [10, 11]
    assert [item["id"] for item in candidates] == [100]


def test_episodes_left_out_for_queue_or_error_pause_do_not_count_either(db_path, monkeypatch):
    monkeypatch.setattr(search_upgrades.random, "shuffle", lambda seq: None)
    inst = make_instance(upgrade_source="monitored_items_only")
    episodes = [owned(i, 10, 0) for i in range(1, 21)] + [owned(100, 11, 100)]
    agent = sonarr_agent(inst, series=[show(10), show(11)], episodes=episodes)
    state = profiles_module.refresh("search_upgrades", agent)
    candidates, _, _, _ = SearchUpgradesSkill()._collect_candidates(
        agent, agent.config, 1, False, True, None, state,
        queued=frozenset(range(1, 11)), paused=frozenset(f"upg:{i}" for i in range(11, 21)))
    assert episode_lists(agent) == [10, 11]
    assert [item["id"] for item in candidates] == [100]


def test_the_series_list_failing_fails_the_source(db_path):
    inst = make_instance(search_upgrades_enabled=True, upgrade_source="monitored_items_only")
    agent = sonarr_agent(inst, get_errors={SERIES: requests.exceptions.ReadTimeout("slow")})
    run_upgrades(agent)
    run = last_run()
    assert run["status"] == "error"
    assert "monitored episodes" in run["error_message"]
    assert "monitored movies" not in run["error_message"]


def test_radarr_keeps_its_label(db_path):
    inst = make_instance(name="Radarr", type="radarr", search_upgrades_enabled=True,
                         upgrade_source="monitored_items_only")
    agent = sonarr_agent(inst, get_errors={MOVIES: requests.exceptions.ConnectionError("refused")})
    run_upgrades(agent)
    assert "monitored movies" in last_run()["error_message"]


def test_a_failing_episode_list_is_skipped_and_noted(db_path, monkeypatch):
    monkeypatch.setattr(search_upgrades.random, "shuffle", lambda seq: None)
    inst = make_instance(search_upgrades_enabled=True, upgrades_per_run=1, upgrade_source="monitored_items_only")
    agent = sonarr_agent(inst, series=[show(10), show(11)], episodes=[owned(1, 10, 100), owned(2, 11, 100)],
                         episode_errors={10})
    run_upgrades(agent)
    assert agent.posts == [{"name": "SeasonSearch", "seriesId": 11, "seasonNumber": 1}]
    run = last_run()
    assert run["status"] != "error"
    assert "episodes of Show 10 could not be loaded" in run["error_message"]


def test_no_episode_list_read_fails_the_source(db_path):
    inst = make_instance(search_upgrades_enabled=True, upgrades_per_run=1, upgrade_source="monitored_items_only")
    agent = sonarr_agent(inst, series=[show(10), show(11)], episodes=[owned(1, 10, 100), owned(2, 11, 100)],
                         episode_errors={10, 11})
    run_upgrades(agent)
    run = last_run()
    assert run["status"] == "error"
    assert run["error_message"] == "monitored episodes: slow"
    assert agent.state["last_sync"] is None
    assert agent.posts == []


@pytest.mark.parametrize("path", ["/api/v3/releaseprofile", "/api/v3/config/indexer", QUALITY_PROFILES])
def test_unread_quality_profiles_fail_the_source_before_the_series_list(db_path, path):
    inst = make_instance(search_upgrades_enabled=True, upgrades_per_run=1, upgrade_source="monitored_items_only")
    agent = sonarr_agent(inst, series=[show(10)], episodes=[owned(1, 10, 0)],
                         get_errors={path: requests.exceptions.ReadTimeout("slow")})
    run_upgrades(agent)
    run = last_run()
    assert run["status"] == "error"
    assert run["error_message"] == "monitored episodes: quality profiles could not be read"
    assert SERIES not in [p for p, _ in agent.gets]
    assert agent.state["last_sync"] is None


def test_unread_quality_profiles_with_both_sources_are_a_note(db_path):
    inst = make_instance(search_upgrades_enabled=True, upgrades_per_run=1, upgrade_source="both")
    agent = sonarr_agent(inst, series=[show(10)], episodes=[owned(1, 10, 0)], cutoff=[cutoff_episode(2)],
                         get_errors={"/api/v3/releaseprofile": requests.exceptions.ReadTimeout("slow")})
    run_upgrades(agent)
    run = last_run()
    assert run["status"] != "error"
    assert "monitored episodes: quality profiles could not be read" in run["error_message"]
    assert agent.posts == [{"name": "SeasonSearch", "seriesId": 2, "seasonNumber": 1}]


def test_both_sources_name_an_episode_once(db_path):
    inst = make_instance(upgrade_source="both")
    cutoff = {**owned(3, 10, 100), "series": {"title": "Show 10", "qualityProfileId": 1}}
    agent = sonarr_agent(inst, series=[show(10)], episodes=[owned(3, 10, 100)], cutoff=[cutoff])
    candidates, failures, _ = collect(agent, per_run=5)
    assert failures == []
    assert [item["id"] for item in candidates] == [3]


def test_checked_search_gets_episode_tasks(db_path, monkeypatch):
    tasks = []

    def fake_run_checked(skill_name, agent, run_id, checked_tasks, mode, **kwargs):
        tasks.extend(checked_tasks)
        return CheckedRunOutcome()

    monkeypatch.setattr(search_upgrades, "indexer_pause", lambda agent: "")
    monkeypatch.setattr(search_upgrades, "run_checked", fake_run_checked)
    inst = make_instance(search_upgrades_enabled=True, upgrades_per_run=1, upgrade_source="monitored_items_only",
                         checked_search="dry_run")
    agent = sonarr_agent(inst, series=[show(10)], episodes=[owned(3, 10, 100)])
    run_upgrades(agent)
    [task] = tasks
    assert (task.arr_id, task.item_type, task.cache_key, task.series_id) == (3, "episode", "upg:3", 10)
    assert task.title == "Show 10 S01E03 – Episode 3"
    assert task.profile_fingerprint is not None


def test_a_grab_from_the_new_source_does_not_count_as_wanted_list(db_path, monkeypatch):
    # By design, like "monitored movies": a grab from the new source is
    # released by retry_hours only, not by "Search again if still missing
    # after (days)", although the episode stays listed while its file scores
    # below the cutoff. With retry_hours 0 a failed upgrade grab is not
    # searched again.
    calls = []
    original = db.searched.lookup_many

    def spy(*args, **kwargs):
        calls.append(kwargs.get("grab_release_days"))
        return original(*args, **kwargs)

    monkeypatch.setattr(db.searched, "lookup_many", spy)
    inst = make_instance(upgrade_source="monitored_items_only")
    agent = sonarr_agent(inst, series=[show(10)], episodes=[owned(3, 10, 100)])
    collect(agent)
    assert calls and set(calls) == {0}


# ── Form, tooltips and docs ──────────────────────────────────────────────────


def test_tooltips_explain_both_meanings_of_monitored_items_only():
    tooltip = TOOLTIPS["upgrade_source"]
    assert "Radarr only" not in tooltip
    for text in ("monitored episodes", "cutoff format score", "lowest score first", "monitored movies",
                 "cutoff unmet"):
        assert text in tooltip, text
    assert "Sonarr: cutoff-unmet list" not in TOOLTIPS["search_upgrades_enabled"]
    assert "see Upgrade Source" in TOOLTIPS["search_upgrades_enabled"]


def test_the_form_shows_upgrade_source_for_sonarr(client):
    page = client.get("/instances/new").text
    assert "showUpgradeSource() { return this.upgradesEnabled; }" in page
    assert "this.type === 'radarr' && this.upgradesEnabled" not in page
    assert 'id="upgrade_source"' in page
    assert "Sonarr: monitored episodes whose file scores below the profile's cutoff format score" in page


def test_readme_documents_the_sonarr_upgrade_source():
    readme = (ROOT / "README.md").read_text()
    assert "### Upgrade search" in readme
    assert readme.index("### Search Behaviour") < readme.index("### Upgrade search") < readme.index("### Checked search")
    section = readme[readme.index("### Upgrade search"):readme.index("### Checked search")]
    for text in ("cutoffFormatScore", "customFormatScore", "lowest score first", "GET /api/v3/series",
                 "includeEpisodeFile=true", "10 series", "Search again if still missing after (days)",
                 "monitored episodes"):
        assert text in section, text
    assert "*(Radarr only)* Which movies" not in readme
    assert readme.index("## Upgrading to 0.13.0") < readme.index("## Upgrading to 0.12.0")
    upgrade = readme[readme.index("## Upgrading to 0.13.0"):readme.index("## Upgrading to 0.12.0")]
    for text in ("No database change", "Monitored Items Only", "**Both**", "Wanted List Only", "Rollback: 0.12.0"):
        assert text in upgrade, text


def test_changelog_and_version_are_0_13_0():
    assert (ROOT / "VERSION").read_text().strip() == "0.13.0"
    changelog = (ROOT / "CHANGELOG.md").read_text()
    assert changelog.index("## [Unreleased]") < changelog.index("## [0.13.0]") < changelog.index("## [0.12.0]")
    section = changelog[changelog.index("## [0.13.0]"):changelog.index("## [0.12.0]")]
    for text in ("### Added", "### Changed", "monitored episodes", "cutoffFormatScore", "Monitored Items Only",
                 "*Both*", "GET /api/v3/series"):
        assert text in section, text
