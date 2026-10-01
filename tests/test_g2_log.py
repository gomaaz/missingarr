import csv
import io

import pytest

from backend import database, db
from backend.config import settings
from backend.db import checked_search_log as log


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


def make_instance(name="Radarr", type_="radarr"):
    return db.instances.create({"name": name, "type": type_, "url": "http://127.0.0.1:9", "api_key": "k" * 32})


def candidate(title, verdict="pass", reasons=(), chosen=False, arr_choice=False, notes=()):
    return {"title": title, "indexer": "Indexer", "score": 100, "size": 2_000_000_000, "quality": "Bluray-1080p",
            "verdict": verdict, "reasons": list(reasons), "notes": list(notes),
            "chosen": chosen, "arr_choice": arr_choice}


def entry(inst, title="Movie (2020)", outcome="would_grab", mode="dry_run", key="mov:1", **extra):
    data = {"instance_id": inst["id"], "run_id": None, "mode": mode, "skill": "search_missing", "arr_id": 1,
            "cache_key": key, "title": title, "outcome": outcome, "arr_pick": None, "pick": None,
            "candidates": [], "error_message": None, "dry_run_round": 0 if mode == "dry_run" else None}
    data.update(extra)
    return data


def test_insert_and_query_round_trip(db_path):
    inst = make_instance()
    pick = {"title": "Movie.2020.1080p-B", "indexer": "Indexer", "score": 90, "size": 5, "quality": "WEBDL-1080p"}
    log.insert(entry(inst, arr_pick="Movie.2019.1080p-A", pick=pick, settings_fingerprint="s1", candidates=[
        candidate("Movie.2019.1080p-A", "reject", ["year"], arr_choice=True),
        candidate("Movie.2020.1080p-B", chosen=True),
    ]))
    [row] = log.query()
    assert row["instance_name"] == "Radarr" and row["arr_type"] == "radarr"
    assert (row["pick"], row["pick_indexer"], row["pick_score"], row["pick_quality"]) == (
        "Movie.2020.1080p-B", "Indexer", 90, "WEBDL-1080p")
    assert row["rejected_count"] == 1
    assert row["candidates"][0]["reasons"] == ["year"]
    assert (row["dry_run_round"], row["settings_fingerprint"]) == (0, "s1")
    assert len(row["created_at"]) == 23   # local time with milliseconds


def test_filters_and_count(db_path):
    radarr, sonarr = make_instance(), make_instance("Sonarr", "sonarr")
    log.insert(entry(radarr, "Same", outcome="would_grab", arr_pick="R1", pick={"title": "R1"}))
    log.insert(entry(radarr, "Other pick", outcome="would_grab", arr_pick="R1", pick={"title": "R2"}))
    log.insert(entry(radarr, "Nothing clean", outcome="no_clean_hit", arr_pick="R1"))
    log.insert(entry(radarr, "No results", outcome="no_results"))
    log.insert(entry(sonarr, "Episode", outcome="grabbed", mode="active", key="ep:1"))

    assert log.count() == 5
    assert log.count(instance_id=sonarr["id"]) == 1
    assert log.count(mode="active") == 1
    assert log.count(outcome="no_clean_hit") == 1
    assert log.count(search="other") == 1
    assert log.count(search="sonarr") == 1          # instance name
    assert {r["title"] for r in log.query(only_differences=True)} == {"Other pick", "Nothing clean"}
    assert [r["title"] for r in log.query(limit=2, offset=1)] == ["No results", "Nothing clean"]


def test_search_treats_wildcards_literally(db_path):
    inst = make_instance()
    log.insert(entry(inst, "100% Wolf"))
    log.insert(entry(inst, "1000 Wolves"))
    assert [r["title"] for r in log.query(search="100%")] == ["100% Wolf"]


def test_dry_run_keys_belong_to_their_round(db_path):
    inst = make_instance()
    log.insert(entry(inst, key="mov:1", profile_fingerprint="aaaa", settings_fingerprint="s1"))
    log.insert(entry(inst, key="mov:2", mode="active", outcome="grabbed"))
    log.insert(entry(inst, key="mov:3", profile_fingerprint="bbbb", settings_fingerprint="s1", dry_run_round=1))
    assert log.dry_run_keys(inst["id"], 0) == {"mov:1": {("aaaa", "s1")}}
    assert log.dry_run_keys(inst["id"], 1) == {"mov:3": {("bbbb", "s1")}}


def test_errors_do_not_count_for_the_round(db_path):
    inst = make_instance()
    log.insert(entry(inst, key="mov:1", outcome="error", error_message="release search failed: timeout"))
    log.insert(entry(inst, key="mov:2", outcome="no_results"))
    assert log.dry_run_keys(inst["id"], 0) == {"mov:2": {(None, None)}}


def test_dry_run_keys_collect_every_fingerprint_of_a_title(db_path):
    inst = make_instance()
    log.insert(entry(inst, key="mov:1", profile_fingerprint="aaaa", settings_fingerprint="s1"))
    log.insert(entry(inst, key="mov:1", profile_fingerprint="bbbb", settings_fingerprint="s2"))
    assert log.dry_run_keys(inst["id"], 0) == {"mov:1": {("aaaa", "s1"), ("bbbb", "s2")}}


def test_rows_of_an_older_round_stay_out_of_the_current_round(db_path):
    # A run that began before a reset writes its rows with the round it began in.
    inst = make_instance()
    sql("UPDATE instances SET dry_run_round=1")
    log.insert(entry(inst, "Began before the reset", dry_run_round=0))
    assert log.dry_run_keys(inst["id"], 1) == {}
    assert log.query(current_round=True) == []
    assert log.count(current_round=True) == 0


def test_current_round_shows_only_this_rounds_dry_run(db_path):
    inst = make_instance()
    log.insert(entry(inst, "Old round", outcome="would_grab", arr_pick="A", pick={"title": "B"}))
    log.insert(entry(inst, "Grabbed", outcome="grabbed", mode="active"))
    sql("UPDATE instances SET dry_run_round=1")
    log.insert(entry(inst, "New round", outcome="no_clean_hit", arr_pick="A", dry_run_round=1))
    assert log.count() == 3
    assert {r["title"] for r in log.query(current_round=True)} == {"Grabbed", "New round"}
    assert log.count(current_round=True) == 2
    assert {r["title"] for r in log.query(current_round=True, only_differences=True)} == {"New round"}
    assert [(s["mode"], s["outcome"], s["n"]) for s in log.summary(current_round=True)] == [
        ("active", "grabbed", 1), ("dry_run", "no_clean_hit", 1)]
    text = "".join(log.iter_csv(current_round=True))
    assert "New round" in text and "Old round" not in text


def test_rows_from_an_outdated_profile_are_marked(db_path):
    inst = make_instance()
    log.insert(entry(inst, "Before", key="mov:1", profile_fingerprint="aaaa"))
    log.insert(entry(inst, "Now", key="mov:2", profile_fingerprint="bbbb"))
    log.insert(entry(inst, "Unknown", key="mov:3"))
    assert {r["title"]: r["profile_changed"] for r in log.query()} == {
        "Before": False, "Now": False, "Unknown": False}   # never read yet (no baseline): nothing to compare
    sql("UPDATE instances SET profile_fingerprints='{\"1\": \"bbbb\"}', "
        "profile_fingerprints_baseline='{\"1\": \"aaaa\"}'")
    assert {r["title"]: r["profile_changed"] for r in log.query()} == {
        "Before": True, "Now": False, "Unknown": False}


def test_a_changed_profile_is_not_hidden_by_an_unchanged_twin(db_path):
    # Profiles 1 and 2 had the same rules (same fingerprint); only 1 changed.
    inst = make_instance()
    log.insert(entry(inst, "Under 1", key="mov:1", profile_fingerprint="aaaa", profile_id=1))
    log.insert(entry(inst, "Under 2", key="mov:2", profile_fingerprint="aaaa", profile_id=2))
    log.insert(entry(inst, "Under 3", key="mov:3", profile_fingerprint="aaaa", profile_id=3))
    sql("UPDATE instances SET profile_fingerprints='{\"1\": \"bbbb\", \"2\": \"aaaa\"}', "
        "profile_fingerprints_baseline='{\"1\": \"aaaa\", \"2\": \"aaaa\", \"3\": \"aaaa\"}'")
    assert {r["title"]: r["profile_changed"] for r in log.query()} == {
        "Under 1": True, "Under 2": False, "Under 3": True}   # profile 3 is gone: changed


def test_the_last_profile_deleted_marks_every_row(db_path):
    # Codex round 3, G6: *arr lets the last unused profile go (new default
    # profiles only at its next start). An empty list that was read is no
    # "never read": every row's profile is gone, so every row changed.
    inst = make_instance()
    log.insert(entry(inst, "With id", key="mov:1", profile_fingerprint="aaaa", profile_id=1))
    log.insert(entry(inst, "Without id", key="mov:2", profile_fingerprint="aaaa"))
    log.insert(entry(inst, "Unknown", key="mov:3"))
    sql("UPDATE instances SET profile_fingerprints='{}', profile_fingerprints_baseline='{\"1\": \"aaaa\"}'")
    assert {r["title"]: r["profile_changed"] for r in log.query()} == {
        "With id": True, "Without id": True, "Unknown": False}


def test_summary_counts_per_instance_mode_and_outcome(db_path):
    inst = make_instance()
    log.insert(entry(inst, outcome="would_grab"))
    log.insert(entry(inst, outcome="would_grab"))
    log.insert(entry(inst, outcome="no_clean_hit"))
    assert log.summary() == [
        {"instance_id": inst["id"], "instance_name": "Radarr", "mode": "dry_run", "outcome": "no_clean_hit", "n": 1},
        {"instance_id": inst["id"], "instance_name": "Radarr", "mode": "dry_run", "outcome": "would_grab", "n": 2},
    ]


def test_purge_old_keeps_recent_rows(db_path):
    inst = make_instance()
    log.insert(entry(inst, "old", mode="active", outcome="grabbed"))
    sql("UPDATE checked_search_log SET created_at=datetime('now','localtime','-400 days')")
    log.insert(entry(inst, "new", mode="active", outcome="grabbed"))
    assert log.purge_old(inst["id"], 0) == 0
    assert log.purge_old(inst["id"], 365) == 1
    assert [r["title"] for r in log.query()] == ["new"]


def test_purge_old_keeps_the_rows_of_the_running_round(db_path):
    # The round lives in its rows: purging them would check the titles again
    # without a reset (owner decision 01.10.2026).
    inst = make_instance()
    log.insert(entry(inst, "Checked long ago", key="mov:1", profile_fingerprint="aaaa", settings_fingerprint="s1"))
    sql("UPDATE checked_search_log SET created_at=datetime('now','localtime','-400 days')")
    assert log.purge_old(inst["id"], 7) == 0
    assert log.dry_run_keys(inst["id"], 0) == {"mov:1": {("aaaa", "s1")}}


def test_purge_old_drops_old_rows_of_an_earlier_round(db_path):
    inst = make_instance()
    log.insert(entry(inst, "Checked long ago", key="mov:1"))
    sql("UPDATE checked_search_log SET created_at=datetime('now','localtime','-400 days')")
    sql("UPDATE instances SET dry_run_round=1")    # what "Reset dry run" does to the counter
    log.insert(entry(inst, "This round, also old", key="mov:2", dry_run_round=1))
    sql("UPDATE checked_search_log SET created_at=datetime('now','localtime','-400 days') WHERE cache_key='mov:2'")
    assert log.purge_old(inst["id"], 7) == 1
    assert [r["title"] for r in log.query()] == ["This round, also old"]


def test_csv_has_one_line_per_candidate(db_path):
    inst = make_instance()
    log.insert(entry(inst, "Movie (2020)", arr_pick="A", pick={"title": "B"}, candidates=[
        candidate("A", "reject", ["year", "title"], arr_choice=True),
        candidate("B", chosen=True, notes=["published 20 days before air date"]),
        candidate("C", "unchecked"),
    ]))
    log.insert(entry(inst, "Empty (2021)", outcome="error", error_message="timeout"))
    rows = list(csv.reader(io.StringIO("".join(log.iter_csv()))))
    assert rows[0] == log.CSV_HEADER
    body = [dict(zip(rows[0], r)) for r in rows[1:]]
    assert [(r["title"], r["release"], r["verdict"], r["chosen"], r["arr_would_grab"]) for r in body] == [
        ("Empty (2021)", "", "", "no", "no"),
        ("Movie (2020)", "A", "reject", "no", "yes"),
        ("Movie (2020)", "B", "pass", "yes", "no"),
        ("Movie (2020)", "C", "unchecked", "no", "no"),
    ]
    assert body[1]["reasons"] == "year; title"
    assert body[2]["notes"] == "published 20 days before air date"
    assert body[0]["reasons"] == "timeout"


def test_csv_cells_never_start_a_formula(db_path):
    inst = make_instance()
    log.insert(entry(inst, "=HYPERLINK(\"x\")", candidates=[candidate("-Release")]))
    text = "".join(log.iter_csv())
    assert "'=HYPERLINK" in text and "'-Release" in text


def test_csv_respects_the_filter(db_path):
    inst = make_instance()
    log.insert(entry(inst, "Keep", outcome="no_clean_hit"))
    log.insert(entry(inst, "Drop", outcome="would_grab"))
    text = "".join(log.iter_csv(outcome="no_clean_hit"))
    assert "Keep" in text and "Drop" not in text


def csv_titles(chunks) -> list[str]:
    rows = list(csv.reader(io.StringIO("".join(chunks))))
    return [r[3] for r in rows[1:]]


def test_csv_reads_one_state_while_a_run_writes(db_path, monkeypatch):
    # Codex round 3, G5: pages read with OFFSET repeated the boundary row
    # when a running search wrote a new row during the download.
    monkeypatch.setattr(log, "_CSV_PAGE", 3)
    inst = make_instance()
    for n in range(6):
        log.insert(entry(inst, f"T{n}", key=f"mov:{n}"))
    chunks = log.iter_csv()
    started = [next(chunks), next(chunks)]                  # header, first page
    log.insert(entry(inst, "Written meanwhile", key="mov:9"))
    titles = csv_titles(started + list(chunks))
    assert titles == ["T5", "T4", "T3", "T2", "T1", "T0"]


def test_csv_of_the_current_round_survives_a_reset_during_the_download(db_path, monkeypatch):
    monkeypatch.setattr(log, "_CSV_PAGE", 3)
    inst = make_instance()
    for n in range(6):
        log.insert(entry(inst, f"R{n}", key=f"mov:{n}"))
    chunks = log.iter_csv(current_round=True)
    started = [next(chunks), next(chunks)]
    sql("UPDATE instances SET dry_run_round=dry_run_round + 1")   # "Reset dry run" while the export runs
    assert csv_titles(started + list(chunks)) == ["R5", "R4", "R3", "R2", "R1", "R0"]


def test_log_rows_follow_their_instance_and_outlive_their_run(db_path):
    sql("INSERT INTO instances (id, name, type, url, api_key) VALUES (1, 'R', 'radarr', 'http://r', 'k')")
    run = db.history.start_run(1, "R", "search_missing")
    db.checked_search_log.insert({"instance_id": 1, "run_id": run, "mode": "active", "skill": "search_missing",
                                  "arr_id": 5, "cache_key": "mov:5", "title": "A", "outcome": "no_clean_hit"})
    sql("DELETE FROM search_history WHERE id=?", (run,))
    assert sql("SELECT run_id FROM checked_search_log") == [(None,)]
    sql("DELETE FROM instances WHERE id=1")
    assert sql("SELECT COUNT(*) FROM checked_search_log")[0][0] == 0
