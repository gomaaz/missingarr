"""0.10.1: texts of the checked-search dry run. It checks every title once
per round and does not use the search cache: the run summary says "already
checked in this dry-run round" instead of "already searched", "Nothing to
search" points to "Reset dry run" on the Pre-filter page, failures are
counted as titles (a dry run submits nothing), and the cache reset on the
Searched page says that an instance in dry run uses its own rounds."""
import json

import pytest
import requests

from backend import database
from backend.config import settings
from backend.skills.search_missing import SearchMissingSkill
from tests.test_g3_runner import THE_THING, activity_messages, last_run, make_instance, movie, the_thing_agent
from tests.test_p5_searched import NODE, client, component_script, run_js  # noqa: F401 (client is a fixture)
from tests.test_p5_searched import make_instance as make_page_instance

LOAD_FAILS = {"/api/v3/movie/2$": requests.exceptions.ConnectionError("down")}
OTHER = movie(2, "Some Film", 1990)


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "missingarr.db"
    monkeypatch.setattr(settings, "database_url", str(path))
    monkeypatch.setattr(database, "_cached_secret_key", None)
    database.init_db()
    return path


@pytest.mark.parametrize("order", ["random", "oldest_first"])
def test_a_dry_run_names_its_round_when_nothing_is_left(db_path, order):
    inst = make_instance(checked_search="dry_run", search_order=order)
    SearchMissingSkill().execute(the_thing_agent(inst))
    SearchMissingSkill().execute(the_thing_agent(inst))
    assert ("Nothing to search — checked 1 of 1 missing item(s) on 1 page(s): 1 already checked in this "
            "dry-run round, 0 inside the release window, 0 with a file — Reset dry run on the Pre-filter "
            "page starts a new round") in activity_messages()


def test_a_dry_run_counts_failed_titles_not_submissions(db_path):
    inst = make_instance(checked_search="dry_run")
    agent = the_thing_agent(inst, missing=[THE_THING, OTHER], movies=[THE_THING, OTHER], get_errors=LOAD_FAILS)
    SearchMissingSkill().execute(agent)
    assert last_run()["error_message"].startswith("1 of 2 title(s) failed — first error: Some Film (1990)")


def test_a_dry_run_where_every_title_failed(db_path):
    inst = make_instance(checked_search="dry_run")
    agent = the_thing_agent(inst, missing=[OTHER], movies=[OTHER], get_errors=LOAD_FAILS)
    SearchMissingSkill().execute(agent)
    assert last_run()["error_message"].startswith("All 1 title(s) failed — first error: Some Film (1990)")


def test_an_active_run_still_counts_submissions(db_path):
    inst = make_instance(checked_search="active")
    agent = the_thing_agent(inst, missing=[THE_THING, OTHER], movies=[THE_THING, OTHER], get_errors=LOAD_FAILS)
    SearchMissingSkill().execute(agent)
    assert last_run()["error_message"].startswith("1 of 2 submission(s) failed")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_cache_reset_sends_dry_runs_to_reset_dry_run(client, tmp_path):
    plain = make_page_instance(name="Sonarr")
    dry = make_page_instance(name="Radarr", type="radarr", checked_search="dry_run")
    script = component_script(client.get("/searched").text, "function searchedPage()")
    out = run_js(tmp_path, f"""
vm.runInThisContext({json.dumps(script)});
const asked = [];
globalThis.confirm = (text) => {{ asked.push(text); return false; }};
const page = searchedPage();
await page.reset({plain['id']}, 'Sonarr');
await page.reset({dry['id']}, 'Radarr');
await page.resetAll();
out.asked = asked;
""")
    hint = 'use "Reset dry run" on the Pre-filter page to check every title again.'
    assert out["asked"] == [
        'Reset searched cache for "Sonarr"? It will be re-searched on the next run.',
        'Reset searched cache for "Radarr"? "Radarr" is in dry run, which uses its own rounds and not this '
        f'cache: {hint}',
        'Reset ALL searched items? All instances will re-search everything on the next run. Instances in '
        f'dry run use their own rounds and not this cache: {hint}',
    ]
