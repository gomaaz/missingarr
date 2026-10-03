"""0.10.1: a checked search without an approved release ("no approved
release", outcome no_results) keeps how many releases *arr returned and its
most frequent rejection reasons (top 3), in the log row's error_message; the
Pre-filter page shows them and names the outcome "no approved release"."""
import pytest

from backend import database, db
from backend.checked_search import runner
from backend.config import settings
from backend.skills.search_missing import SearchMissingSkill
from tests.test_g3_runner import RIGHT, WRONG, activity_messages, log_rows, make_instance, release, the_thing_agent
from tests.test_g5_pages import client  # noqa: F401 (a fixture)
from tests.test_g5_pages import make_instance as make_page_instance

QUALITY = "Quality not wanted in profile"
UPGRADE = "Not an upgrade for existing movie file"
SCORE = "Custom Formats German DL have score -10 below the minimum 5600"
SIZE = "Size too large"


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


def rejected(title, guid, *reasons):
    return {**release(title, guid, approved=False), "rejections": list(reasons)}


REJECTED = [
    rejected(RIGHT, "g1", QUALITY, UPGRADE, QUALITY),        # a reason counts once per release
    rejected(WRONG, "g2", QUALITY),
    rejected("Some.Film.2020.1080p-GRP", "g3", {"reason": SCORE, "type": "permanent"}),
    rejected("Some.Film.2020.2160p-GRP", "g4", QUALITY, SIZE),
]
NOTE = (f"4 release(s) returned, none approved — most frequent rejections: {QUALITY} (3); {UPGRADE} (1); "
        f"{SCORE} (1)")


@pytest.mark.parametrize("mode", ["dry_run", "active"])
def test_the_row_names_the_releases_and_the_top_rejections(db_path, mode):
    inst = make_instance(checked_search=mode)
    SearchMissingSkill().execute(the_thing_agent(inst, releases={1: REJECTED}))
    [row] = log_rows()
    assert (row["outcome"], row["error_message"]) == ("no_results", NOTE)
    assert f"No approved release for Das Ding aus einer anderen Welt (1982) — {NOTE}" in activity_messages()


def test_an_empty_answer_says_so(db_path):
    inst = make_instance()
    SearchMissingSkill().execute(the_thing_agent(inst, releases={}))
    assert [(r["outcome"], r["error_message"]) for r in log_rows()] == [("no_results", "no release returned")]


def test_releases_without_reasons_are_counted(db_path):
    inst = make_instance()
    SearchMissingSkill().execute(the_thing_agent(inst, releases={1: [release(RIGHT, "g1", approved=False)]}))
    assert log_rows()[0]["error_message"] == "1 release(s) returned, none approved"


def test_rejections_count_only_as_a_list():
    payload = [{"rejections": "Not wanted"}, {"rejections": ["Not wanted"]}]
    assert runner._no_approved_note(payload) == (
        "2 release(s) returned, none approved — most frequent rejections: Not wanted (1)")


def test_the_page_names_the_outcome_no_approved_release(client):
    inst = make_page_instance(checked_search="dry_run")
    db.checked_search_log.insert({"instance_id": inst["id"], "mode": "dry_run", "skill": "search_missing",
                                  "title": "A", "outcome": "no_results", "cache_key": "mov:1",
                                  "dry_run_round": 0, "error_message": NOTE})
    page = client.get("/checked-search").text
    assert "no_results: ['badge-unknown', 'no approved release']" in page
    assert '<option value="no_results">No approved release</option>' in page
    assert "<td>no approved release</td>" in page
    assert "no results" not in page.lower()
