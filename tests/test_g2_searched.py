import pytest

from backend import database, db
from backend.config import settings


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "missingarr.db"
    monkeypatch.setattr(settings, "database_url", str(path))
    monkeypatch.setattr(database, "_cached_secret_key", None)
    database.init_db()
    return path


def sql(statement, params=()):
    with database.get_db() as conn:
        return [tuple(r) for r in conn.execute(statement, params).fetchall()]


@pytest.fixture
def inst(db_path):
    return db.instances.create({"name": "Radarr", "type": "radarr", "url": "http://127.0.0.1:9",
                                "api_key": "k" * 32})


def blocked(inst, keys, fingerprints=None, retry_hours=0, grab_release_days=0, no_results_release_days=0):
    return set(db.searched.lookup_many(inst["id"], keys, retry_hours, fingerprints=fingerprints,
                                       grab_release_days=grab_release_days,
                                       no_results_release_days=no_results_release_days))


def test_without_fingerprints_every_entry_blocks(inst):
    db.searched.add(inst["id"], "mov:1", "A", "movie", profile_fingerprint="aaaa")
    db.searched.add(inst["id"], "mov:2", "B", "movie")
    assert blocked(inst, ["mov:1", "mov:2", "mov:3"]) == {"mov:1", "mov:2"}


def test_an_entry_blocks_only_under_the_current_fingerprint(inst):
    db.searched.add(inst["id"], "mov:1", "A", "movie", profile_fingerprint="aaaa")
    assert blocked(inst, ["mov:1"], {"mov:1": ("aaaa", "aaaa")}) == {"mov:1"}
    assert blocked(inst, ["mov:1"], {"mov:1": ("bbbb", "aaaa")}) == set()


def test_entries_from_before_0_9_0_count_under_the_baseline(inst):
    db.searched.add(inst["id"], "mov:1", "Old", "movie")          # no fingerprint
    assert blocked(inst, ["mov:1"], {"mov:1": ("aaaa", "aaaa")}) == {"mov:1"}   # baseline = now
    assert blocked(inst, ["mov:1"], {"mov:1": ("bbbb", "aaaa")}) == set()       # first change after it
    assert blocked(inst, ["mov:1"], {"mov:1": ("bbbb", None)}) == set()         # profile newer than the baseline


def test_an_unknown_profile_keeps_blocking(inst):
    db.searched.add(inst["id"], "mov:1", "A", "movie", profile_fingerprint="aaaa")
    db.searched.add(inst["id"], "mov:2", "B", "movie")
    assert blocked(inst, ["mov:1", "mov:2"], {"mov:1": (None, None)}) == {"mov:1", "mov:2"}


def test_a_new_search_takes_over_the_new_fingerprint(inst):
    db.searched.add(inst["id"], "mov:1", "A", "movie", profile_fingerprint="aaaa")
    db.searched.add(inst["id"], "mov:1", "A", "movie", profile_fingerprint="bbbb")
    assert sql("SELECT profile_fingerprint FROM searched_items") == [("bbbb",)]
    assert blocked(inst, ["mov:1"], {"mov:1": ("bbbb", "aaaa")}) == {"mov:1"}


def test_the_retry_window_still_applies(inst):
    db.searched.add(inst["id"], "mov:1", "A", "movie", profile_fingerprint="aaaa")
    sql("UPDATE searched_items SET searched_at=datetime('now','localtime','-48 hours')")
    assert blocked(inst, ["mov:1"], {"mov:1": ("aaaa", "aaaa")}, retry_hours=24) == set()
    assert blocked(inst, ["mov:1"], {"mov:1": ("aaaa", "aaaa")}, retry_hours=72) == {"mov:1"}


def test_a_search_without_a_fingerprint_keeps_the_stored_one(inst):
    db.searched.add(inst["id"], "mov:1", "A", "movie", profile_fingerprint="bbbb")
    db.searched.add(inst["id"], "mov:1", "A", "movie")
    assert sql("SELECT profile_fingerprint FROM searched_items") == [("bbbb",)]


def test_a_grab_still_wanted_after_the_set_days_is_released(inst):
    run = db.history.start_run(inst["id"], "Radarr", "search_missing")
    db.history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", "grabbed")
    db.history.record_checked(run, inst["id"], "B", 2, "movie", "mov:2", "no_hit")
    assert blocked(inst, ["mov:1", "mov:2"], grab_release_days=7) == {"mov:1", "mov:2"}
    sql("UPDATE searched_items SET searched_at=datetime('now','localtime','-8 days')")
    sql("UPDATE searched_items SET grabbed_at=datetime('now','localtime','-8 days') WHERE grabbed_at IS NOT NULL")
    assert blocked(inst, ["mov:1", "mov:2"], grab_release_days=7) == {"mov:2"}    # retry_hours 0: no_hit stays
    assert blocked(inst, ["mov:1", "mov:2"], grab_release_days=10) == {"mov:1", "mov:2"}
    assert blocked(inst, ["mov:1", "mov:2"]) == {"mov:1", "mov:2"}                # not asked for: blocks
    db.searched.add(inst["id"], "mov:1", "A", "movie")                             # searched again
    assert blocked(inst, ["mov:1"], grab_release_days=7) == {"mov:1"}


def test_a_grab_blocks_its_days_even_with_a_shorter_retry_window(inst):
    # Codex round 2, F1: the grab holds 7 days although retry_hours is 24.
    run = db.history.start_run(inst["id"], "Radarr", "search_missing")
    db.history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", "grabbed")
    db.history.record_checked(run, inst["id"], "B", 2, "movie", "mov:2", "no_hit")
    sql("UPDATE searched_items SET searched_at=datetime('now','localtime','-2 days')")
    sql("UPDATE searched_items SET grabbed_at=datetime('now','localtime','-2 days') WHERE grabbed_at IS NOT NULL")
    assert blocked(inst, ["mov:1", "mov:2"], retry_hours=24, grab_release_days=7) == {"mov:1"}
    # without "Search again" (monitored movies) a grab keeps the plain retry window
    assert blocked(inst, ["mov:1", "mov:2"], retry_hours=24) == set()
    # a changed profile releases it early, like any entry (spec addendum)
    assert blocked(inst, ["mov:1"], {"mov:1": ("bbbb", "aaaa")}, retry_hours=24, grab_release_days=7) == set()
    sql("UPDATE searched_items SET searched_at=datetime('now','localtime','-8 days'), "
        "grabbed_at=datetime('now','localtime','-8 days') WHERE cache_key='mov:1'")
    assert blocked(inst, ["mov:1"], retry_hours=24, grab_release_days=7) == set()


def age(days, where="1=1"):
    """Move an entry (and its marks) the given days into the past."""
    sql("UPDATE searched_items SET searched_at=datetime('now','localtime',?), "
        "grabbed_at=CASE WHEN grabbed_at IS NOT NULL THEN datetime('now','localtime',?) END, "
        f"no_results_at=CASE WHEN no_results_at IS NOT NULL THEN datetime('now','localtime',?) END WHERE {where}",
        (f"-{days} days",) * 3)


def test_an_empty_search_is_released_after_the_set_days_without_a_retry_window(inst):
    # Owner decision 02.10.2026: an indexer failure can vanish from the health
    # list before missingarr reads it, so an empty list is no proof. With
    # retry_hours 0 it is searched again after "Search again if still missing".
    run = db.history.start_run(inst["id"], "Radarr", "search_missing")
    db.history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", "no_hit", no_results=True)
    db.history.record_checked(run, inst["id"], "B", 2, "movie", "mov:2", "no_hit")     # filter rejected all
    assert blocked(inst, ["mov:1", "mov:2"], no_results_release_days=7) == {"mov:1", "mov:2"}
    age(6)
    assert blocked(inst, ["mov:1", "mov:2"], no_results_release_days=7) == {"mov:1", "mov:2"}
    age(8)
    assert blocked(inst, ["mov:1", "mov:2"], no_results_release_days=7) == {"mov:2"}   # no_clean_hit stays
    assert blocked(inst, ["mov:1", "mov:2"], no_results_release_days=10) == {"mov:1", "mov:2"}
    assert blocked(inst, ["mov:1", "mov:2"], grab_release_days=7) == {"mov:1", "mov:2"}  # not a grab
    assert blocked(inst, ["mov:1", "mov:2"]) == {"mov:1", "mov:2"}                     # not asked for: blocks
    db.searched.add(inst["id"], "mov:1", "A", "movie")                                  # searched again
    assert blocked(inst, ["mov:1"], no_results_release_days=7) == {"mov:1"}


def test_an_empty_search_never_blocks_longer_than_the_retry_window(inst):
    # Unlike a grab (a download may run) nothing argues for holding an empty
    # search past Retry: whichever ends first releases it.
    run = db.history.start_run(inst["id"], "Radarr", "search_missing")
    db.history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", "no_hit", no_results=True)
    age(2)
    assert blocked(inst, ["mov:1"], retry_hours=24, no_results_release_days=7) == set()
    assert blocked(inst, ["mov:1"], retry_hours=72, no_results_release_days=7) == {"mov:1"}
    age(8)
    assert blocked(inst, ["mov:1"], retry_hours=720, no_results_release_days=7) == set()


def test_a_profile_change_releases_an_empty_search_early(inst):
    run = db.history.start_run(inst["id"], "Radarr", "search_missing")
    db.history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", "no_hit", no_results=True,
                              profile_fingerprint="aaaa")
    assert blocked(inst, ["mov:1"], {"mov:1": ("aaaa", "aaaa")}, no_results_release_days=7) == {"mov:1"}
    assert blocked(inst, ["mov:1"], {"mov:1": ("bbbb", "aaaa")}, no_results_release_days=7) == set()
