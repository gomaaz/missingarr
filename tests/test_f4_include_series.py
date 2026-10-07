"""0.10.1: Sonarr runs read the wanted lists with includeSeries=true and take
each episode's quality profile from the embedded series; no run reads the
whole series list any more (GET /api/v3/series, tens of megabytes on a large
library). The profiles are read after the indexer-pause check: a paused
checked run reads nothing more."""
import pytest

from backend import database
from backend.checked_search import runner
from backend.config import settings
from backend.skills.profiles import PROFILE_TIMEOUT
from backend.skills.search_missing import SearchMissingSkill
from backend.skills.search_upgrades import SearchUpgradesSkill
from tests.test_g3_runner import (
    CUTOFF, GUEST_SERIES, INDEXERS, WANTED, agent_for, guest_episode, make_instance, sql, stored_fingerprint,
    the_thing_agent,
)

# A series as Sonarr embeds it: far more than missingarr reads.
FULL_SERIES = {**GUEST_SERIES, "seriesType": "standard", "overview": "x" * 500,
               "images": [{"coverType": "poster", "url": "/poster.jpg"}],
               "seasons": [{"seasonNumber": 1, "monitored": True}], "statistics": {"episodeCount": 8}}
TRIMMED_SERIES = {"id": 10, "title": "The Guest", "qualityProfileId": 1, "seriesType": "standard"}


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "missingarr.db"
    monkeypatch.setattr(settings, "database_url", str(path))
    monkeypatch.setattr(database, "_cached_secret_key", None)
    database.init_db()
    return path


@pytest.fixture(autouse=True)
def no_health_settle(monkeypatch):
    """*arr refreshes its health checks within 5 s; the fake answers at once."""
    monkeypatch.setattr(runner, "HEALTH_SETTLE_SECONDS", 0, raising=False)


@pytest.mark.parametrize("order", ["random", "oldest_first"])
@pytest.mark.parametrize("checked", ["off", "dry_run"])
def test_sonarr_reads_the_wanted_list_with_its_series_not_the_series_list(db_path, order, checked):
    inst = make_instance(name="Sonarr", type="sonarr", checked_search=checked, search_order=order)
    agent = agent_for(inst, missing=[guest_episode()], episodes=[guest_episode()], series=[GUEST_SERIES])
    SearchMissingSkill().execute(agent)
    wanted = [params for path, params in agent.gets if path == WANTED]
    assert wanted and all(params.get("includeSeries") == "true" for params in wanted)
    assert [path for path, _ in agent.gets if path == "/api/v3/series"] == []


def test_radarr_reads_the_wanted_list_as_before(db_path):
    inst = make_instance(checked_search="off")
    agent = the_thing_agent(inst)
    SearchMissingSkill().execute(agent)
    assert [set(params) for path, params in agent.gets if path == WANTED] == [{"page", "pageSize", "monitored"}]


def test_a_sonarr_upgrade_takes_the_profile_from_the_embedded_series(db_path):
    inst = make_instance(name="Sonarr", type="sonarr", checked_search="off", search_upgrades_enabled=True,
                         upgrade_source="wanted_list_only")
    episode = {**guest_episode(), "hasFile": True}
    agent = agent_for(inst, cutoff=[episode], episodes=[episode], series=[GUEST_SERIES])
    SearchUpgradesSkill().execute(agent)
    assert agent.commands == [{"name": "SeasonSearch", "seriesId": 10, "seasonNumber": 1}]
    assert [path for path, _ in agent.gets if path.startswith("/api/v3/series")] == []
    assert sql("SELECT profile_fingerprint FROM searched_items") == [(stored_fingerprint(inst),)]


@pytest.mark.parametrize("skill", [SearchMissingSkill, SearchUpgradesSkill], ids=["missing", "upgrades"])
def test_a_paused_checked_run_reads_no_profiles(db_path, skill):
    inst = make_instance(checked_search="dry_run", search_upgrades_enabled=True)
    differing = [{**INDEXERS[0], "enableInteractiveSearch": False}]
    agent = the_thing_agent(inst, indexers=differing)
    skill().execute(agent)
    assert [path for path, _ in agent.gets] == ["/api/v3/indexer"]


@pytest.mark.parametrize("order", ["random", "oldest_first", "smart"])
def test_sonarr_wanted_pages_get_the_long_timeout_and_trimmed_series(db_path, monkeypatch, order):
    """Both collection paths (random pages, ordered whole list) trim the
    embedded series right after the page is read; the profile still counts."""
    inst = make_instance(name="Sonarr", type="sonarr", checked_search="off", search_order=order)
    agent = agent_for(inst, missing=[guest_episode()], episodes=[guest_episode()], series=[FULL_SERIES])
    seen = []
    take_eligible = SearchMissingSkill._take_eligible

    def spy(self, agent, cfg, records, *args, **kwargs):
        seen.extend(records)
        return take_eligible(self, agent, cfg, records, *args, **kwargs)

    monkeypatch.setattr(SearchMissingSkill, "_take_eligible", spy)
    SearchMissingSkill().execute(agent)
    assert seen and all(record["series"] == TRIMMED_SERIES for record in seen)
    assert agent.timeouts[WANTED] == PROFILE_TIMEOUT
    assert agent.commands
    assert sql("SELECT profile_fingerprint FROM searched_items") == [(stored_fingerprint(inst),)]


def test_radarr_wanted_pages_keep_the_default_timeout(db_path):
    inst = make_instance(checked_search="off")
    agent = the_thing_agent(inst)
    SearchMissingSkill().execute(agent)
    assert agent.timeouts[WANTED] == 10


def test_sonarr_cutoff_pages_get_the_long_timeout_and_trimmed_series(db_path, monkeypatch):
    inst = make_instance(name="Sonarr", type="sonarr", checked_search="off", search_upgrades_enabled=True,
                         upgrade_source="wanted_list_only")
    episode = {**guest_episode(), "hasFile": True}
    agent = agent_for(inst, cutoff=[episode], episodes=[episode], series=[FULL_SERIES])
    seen = []
    cutoff_item = SearchUpgradesSkill._cutoff_item

    def spy(arr_type, record):
        seen.append(record)
        return cutoff_item(arr_type, record)

    monkeypatch.setattr(SearchUpgradesSkill, "_cutoff_item", staticmethod(spy))
    SearchUpgradesSkill().execute(agent)
    assert seen and all(record["series"] == TRIMMED_SERIES for record in seen)
    assert agent.timeouts[CUTOFF] == PROFILE_TIMEOUT
    assert sql("SELECT profile_fingerprint FROM searched_items") == [(stored_fingerprint(inst),)]
