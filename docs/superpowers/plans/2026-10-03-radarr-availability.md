# Radarr-Verfügbarkeit in der Fehlend-Suche Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Die Fehlend-Suche („Search Missing“) lässt bei Radarr jeden Film aus, den Radarr selbst noch nicht als verfügbar meldet (`isAvailable` ist `false` in `GET /api/v3/wanted/missing`), auf jedem Weg, auf dem sie Filme auswählt, mit Suchbefehl, geprüfter Suche (Probelauf und aktiv) und in Force-Läufen.

**Architecture:** Beide Sammelwege der Fehlend-Suche (`_collect_random` seitenweise, `_collect_ordered` für Newest/Oldest/Smart über die ganze Liste) geben ihre Datensätze an dieselbe Funktion `SearchMissingSkill._take_eligible()`. Dort kommt eine Bedingung dazu, direkt nach „hat eine Datei“ und vor dem Release-Fenster: ein Radarr-Datensatz mit `isAvailable is False` wird gezählt (`_Stats.skipped_unavailable`) und übersprungen. Er erreicht so weder `per_run`, noch die Kandidaten, noch den Searched-Cache, noch den Runner der geprüften Suche. `_Stats.describe()` nennt die Zahl in der Zusammenfassung des Laufs. Dazu kommen ein Satz im Tooltip und in der README zu *Hours after release* und eine Zeile im CHANGELOG-Abschnitt 0.10.0.

**Tech Stack:** Python 3.12, `pytest`. Keine neue Abhängigkeit, keine Datenbank-Änderung.

## Global Constraints

- Branch `feat/imports`. Dieser Plan läuft **nach** Plan A (`docs/superpowers/plans/2026-10-02-imports.md`, acht Tasks). Task 1 hängt nicht von Plan A ab; Task 2 braucht den CHANGELOG-Abschnitt `## [0.10.0]`, den Plan A Task 8 schreibt. Fehlt er, Task 2 nicht beginnen.
- Entscheidungen des Betreibers (fest, D1 bis D7):
  - D1 Nur Radarr: In der Fehlend-Suche wird ein Radarr-Datensatz, dessen `isAvailable` genau `false` ist, vor der Prüfung des Release-Fensters übersprungen, auf jedem Sammelweg in `backend/skills/search_missing.py`. Ein übersprungener Film zählt weder für `per_run` noch als Kandidat, wird nicht in den Searched-Cache geschrieben, löst keine Fehlerpause der geprüften Suche aus und wird normal gesucht, sobald Radarr ihn als verfügbar meldet. Fehlt `isAvailable` oder ist es `null`, gilt der Film als verfügbar (Verhalten wie bisher).
  - D2 Dieselbe Regel für die normale Suche, die geprüfte Suche (Probelauf und aktiv) und Force-Läufe („Run now“).
  - D3 `hours_after_release` bleibt als zweite, unabhängige Bedingung (beide müssen erfüllt sein, siehe Entscheidung 9).
  - D4 Keine neue Einstellung, kein Schalter. `search_upgrades` bleibt unverändert, Sonarr bleibt unverändert.
  - D5 Neuer Zähler `skipped_unavailable`, in der Zusammenfassung des Laufs neben den anderen übersprungenen Zahlen: „N not yet available in Radarr“.
  - D6 Doku: eine Zeile im CHANGELOG-Abschnitt `[0.10.0]`, die README-Zeile zu *Hours After Release*, der Tooltip zu `hours_after_release`. `VERSION` bleibt `0.10.0`.
  - D7 Begründung aus dem Radarr-Quelltext (Abschnitt „Hintergrund“).
- Tests nur in den neuen Dateien `tests/test_p2_radarr_availability.py` (Suchbefehl) und `tests/test_g3_radarr_availability.py` (geprüfte Suche) und am Ende von `tests/test_p6_docs.py`. Die neuen Testdateien holen Helfer aus `tests/test_p2_search_missing.py` und `tests/test_g3_runner.py`, nie Testfunktionen (pytest würde sie sonst ein zweites Mal sammeln). Keine `conftest.py`.
- Bestehender Code ändert sich nur in `backend/skills/search_missing.py` (Task 1) und `backend/tooltips.py` (Task 2), dazu `README.md` und `CHANGELOG.md` (Task 2).
- Testbefehle laufen im Repo-Wurzelverzeichnis: `.venv/bin/python -m pytest <dateien> -q -p no:cacheprovider`. Alle Tests laufen offline gegen Test-Doppel; kein Aufruf an Radarr, Sonarr oder andere Dienste.
- Dateien schreiben mit dem Write- bzw. Edit-Werkzeug, nie per Heredoc.
- Öffentliches Repo: keine Hostnamen, keine IPs außer `127.0.0.1`, keine absoluten Host-Pfade, keine Personennamen, keine Indexer-Namen. Testdaten sind erfunden („Some Film“).
- Code, Bezeichner, Testnamen, Kommentare, Oberfläche und Commit-Nachrichten englisch; ein Commit je Task mit den zwei Trailer-Zeilen aus dem Commit-Schritt; `git add` nur mit den Dateien des Tasks, nie `-A`. Task 3 (Feinschliff der Imports-Seite) hat keinen vorgegebenen Code, siehe dort.
- Nichts taggen, nichts pushen, nichts einspielen.

---

## Hintergrund (D7)

Pfade und Zeilen beziehen sich auf den Quelltext von Radarr im Verzeichnis `src/`:

1. **Woher `isAvailable` kommt.** `Radarr.Api.V3/Wanted/MissingController.cs:42-63`: `GetMissingMovies` baut jeden Datensatz der Wanted-Liste mit `MapToResource(v)`. Das ist `Radarr.Api.V3/Movies/MovieControllerWithSignalR.cs:72-91`: Zeile 79 liest `_configService.AvailabilityDelay`, Zeile 85 ruft `movie.ToResource(availDelay, …)`, und `Radarr.Api.V3/Movies/MovieResource.cs:145` setzt `IsAvailable = model.IsAvailable(availDelay)`. Das Feld berücksichtigt also die *Minimum Availability* des Films und die globale *Availability Delay* (Settings → Indexers).
2. **Was `IsAvailable` rechnet.** `NzbDrone.Core/Movies/Movie.cs:77-118`: *TBA*/*Announced* → immer verfügbar, ohne *Availability Delay* (Zeile 85 und 113-116); *In Cinemas* → ab dem Kinostart, aber nur, wenn ein Kinostart bekannt ist (Zeile 89); ohne Kinostart rechnet Radarr wie bei *Released* weiter. Bei *Released* und bei *In Cinemas* ohne Kinostart gilt das frühere von digitalem und physischem Release, ohne beide Kinostart + 90 Tage (Zeile 109), ohne jedes Datum nie (ebenfalls ohne Delay, Zeile 113-116). Auf ein gefundenes Datum kommen die Tage der *Availability Delay* (Zeile 118). Ein Film mit *In Cinemas*, ohne Kinostart, aber mit bekanntem digitalem Release kann also verfügbar sein. Für missingarr spielt das keine Rolle: der Filter liest nur das fertige `isAvailable`.
3. **Warum Radarr das bei missingarr nicht selbst prüft.** `Radarr.Api.V3/Commands/CommandController.cs:75` legt jeden Befehl aus der API mit `CommandTrigger.Manual` an. `NzbDrone.Core/IndexerSearch/MoviesSearchService.cs:42-45`: `userInvokedSearch = message.Trigger == CommandTrigger.Manual`, gesucht werden `(m.Monitored && m.IsAvailable()) || userInvokedSearch`, also bei einem `MoviesSearch` von missingarr jeder Film. `NzbDrone.Core/DecisionEngine/Specifications/RssSync/AvailabilitySpecification.cs:23-27`: bei `UserInvokedSearch` wird die Verfügbarkeit übersprungen („Skipping availability check during search“); nur sonst lehnt Zeile 29-34 ab. Die geprüfte Suche fragt `GET /api/v3/release?movieId=…`, und `Radarr.Api.V3/Indexers/ReleaseController.cs:149` sucht dort mit `MovieSearch(movieId, true, true)`, also ebenfalls als Nutzersuche.

Folge: Radarr selbst sucht und greift einen noch nicht verfügbaren Film nur über RSS nicht. Eine Suche von missingarr dagegen sucht ihn und greift (Suchbefehl) bzw. bekommt als „approved“ gemeldet (geprüfte Suche), was die Indexer zu dem Film haben, etwa Kinomitschnitte vor dem Heimkino-Release. *Hours after release* hilft dabei nicht immer: missingarr rechnet vom früheren von digitalem und physischem Release, ohne beide vom Kinostart (`backend/skills/base.py`, `release_date`), Radarr bei *Released* ohne Heimkino-Datum erst ab Kinostart + 90 Tage, dazu die *Availability Delay*.

## Dateien

| Datei | Art | Task | Inhalt |
|---|---|---|---|
| `backend/skills/search_missing.py` | ändern | 1 | `_Stats.skipped_unavailable`, Text in `describe()`, Bedingung und Docstring in `_take_eligible()` |
| `tests/test_p2_radarr_availability.py` | neu | 1 | Suchbefehl: jede Reihenfolge, Seitenlesen, `per_run`, Cache, Force, Release-Fenster (ab Release-Datum, nicht ab Radarrs Freigabe), fehlendes/`null`-Feld, Sonarr unverändert |
| `tests/test_g3_radarr_availability.py` | neu | 1 | geprüfte Suche: Probelauf und aktiv, mit und ohne Force; keine Pause, kein Cache, kein Log-Eintrag |
| `backend/tooltips.py` | ändern | 2 | Satz zu Radarr im Tooltip `hours_after_release` |
| `README.md` | ändern | 2 | Zeile **Hours After Release** in der Tabelle „Scheduling“ |
| `CHANGELOG.md` | ändern | 2 | Abschnitt `### Changed` mit einer Zeile in `## [0.10.0]` |
| `tests/test_p6_docs.py` | ändern | 2 | neuer Test am Dateiende |

## Entscheidungen

Innerhalb von D1 bis D7 offen gelassen, so festgelegt:

1. **Eine Stelle für alle Wege.** Die Fehlend-Suche wählt Filme nur in `_take_eligible()` aus: `_collect_random` ruft sie je gelesener Seite, `_collect_ordered` (Newest first, Oldest first, Smart) einmal mit der ganzen sortierten Liste. Die Probe in `_collect_random` (`pageSize=1`) liest nur `totalRecords` und wählt nichts aus. `run_checked` bekommt nur die Kandidaten. Die Bedingung steht deshalb einmal in `_take_eligible()`; die Tests prüfen trotzdem jeden Weg einzeln (zufällig mit Seitenwechsel, geordnet mit drei Seiten, jede Reihenfolge, geprüfte Suche).
2. **Reihenfolge der Prüfungen:** erst „hat eine Datei“ (wie bisher), dann Verfügbarkeit, dann Release-Fenster, dann Cache, Warteschlange, Fehlerpause. Ein Film, der weder verfügbar noch aus dem Fenster ist, zählt also als „not yet available“, nicht als „inside the release window“. Die Verfügbarkeitsprüfung steht außerhalb von `if cutoff is not None`, damit sie auch in Force-Läufen und bei *Hours after release* 0 greift.
3. **Genau `false`:** `record.get("isAvailable") is False`. Fehlt das Feld, ist es `null` oder etwas anderes als `false`, gilt der Film als verfügbar.
4. **Nur Radarr:** Die Bedingung prüft `arr_type == "radarr"`. Ein Sonarr-Datensatz mit einem Feld `isAvailable` wird nicht gefiltert (Test).
5. **Zählung:** `examined` zählt einen übersprungenen Film weiter mit, `total` bleibt die Zahl der Wanted-Liste. Der Text erscheint nur, wenn mindestens ein Film übersprungen wurde (wie „in the *arr queue“ und „paused after an error“), direkt nach „with a file“. Läufe ohne solchen Film und alle Sonarr-Läufe behalten ihren Text.
6. **Keine Notiz in der Historie:** Anders als Warteschlange und Fehlerpause schreibt der Lauf keine Notiz in `error_message`. Ein nicht verfügbarer Film ist kein Fehler und kein Sonderfall der geprüften Suche; die Zahl steht in der Zusammenfassung (`Nothing to search — …` als info, sonst im debug-Log).
7. **Zufällige Reihenfolge:** Nicht verfügbare Filme kosten wie gecachte Filme Seiten aus dem Budget `RANDOM_PAGE_BUDGET` (10 Seiten je Lauf). Besteht die Liste fast nur aus solchen Filmen, findet ein zufälliger Lauf einen verfügbaren Film vielleicht erst im nächsten Lauf. Hingenommen, wie bisher beim Cache.
8. **Doku:** Der CHANGELOG bekommt in `## [0.10.0]` einen neuen Abschnitt `### Changed` zwischen `### Added` und `### Security` (Reihenfolge wie in 0.9.0), darin eine Zeile. Die Hilfe-Seite (`templates/help.html`) nennt *Hours after release* nicht und bleibt unverändert.
9. **D3 heißt: zwei unabhängige Bedingungen.** *Hours after release* zählt weiter ab missingarrs Release-Datum (`release_date` in `backend/skills/base.py`), nicht ab dem Zeitpunkt, an dem Radarr den Film als verfügbar meldet. Ein Film wird gesucht, wenn beides erfüllt ist: Radarr meldet ihn verfügbar, und das Release-Fenster ist vorbei. „Obendrauf“ heißt also „zusätzlich als eigene Bedingung“, nicht „noch einmal so viele Stunden nach Radarrs Freigabe“. Grund: D1 setzt den Filter vor die bestehende Prüfung des Release-Fensters, D3 lässt die Einstellung „bleiben“, und die Wanted-Liste nennt keinen Zeitpunkt, seit dem ein Film verfügbar ist (ohne Datenbank-Änderung nicht zu merken). Beispiel: Heimkino-Release vor sieben Tagen, Radarrs *Availability Delay* läuft heute ab, *Hours after release* 9: Der Film wird heute sofort gesucht, nicht erst neun Stunden später (Test `test_the_release_window_still_counts_from_the_release_date`). Tooltip, README und CHANGELOG sagen das so.

---

## Tasks

### Task 1: Nicht verfügbare Radarr-Filme auslassen

**Files:**
- Modify: `backend/skills/search_missing.py` (`_Stats` Zeilen 41-60, Docstring von `_take_eligible` Zeilen 240-264, Bedingung nach Zeile 281)
- Create (Test): `tests/test_p2_radarr_availability.py`
- Create (Test): `tests/test_g3_radarr_availability.py`

**Interfaces:**
- Consumes: aus `tests/test_p2_search_missing.py`: `FakeArr(config, missing=…)` (zeichnet `gets` und `posts` auf, Seiten wie *arr), `WANTED`, `cache_keys()`, `episode(i, …)`, `iso(days_ago)`, `last_run()`. Aus `tests/test_g3_runner.py`: `make_instance(**fields)` (Radarr, Probelauf, Oldest first), `movie(id, title, year, **extra)`, `THE_THING` (Film 1, sauberer Release `RIGHT`), `the_thing_agent(inst, **kwargs)`, `searched_ids(agent)`, `log_rows()`, `pauses()`, `sql(statement, params)`, `activity_messages()`. `backend.checked_search.runner.HEALTH_SETTLE_SECONDS`.
- Produces: `_Stats.skipped_unavailable: int` (Standard 0); `_Stats.describe()` hängt `", N not yet available in Radarr"` nach `"… with a file"` an, wenn N > 0.

- [ ] **Step 1: Write the failing tests**

`tests/test_p2_radarr_availability.py` (neu, ganze Datei):

```python
"""Search Missing (command path) leaves out every movie Radarr does not
report as available yet: isAvailable false in GET /api/v3/wanted/missing.
Radarr counts a search command sent through its API as user-invoked and
then searches regardless of availability (MoviesSearchService), so
missingarr has to leave such movies out itself."""
import pytest

from backend import database, db
from backend.config import settings
from backend.skills import search_missing
from backend.skills.search_missing import SearchMissingSkill
from tests.test_p2_search_missing import WANTED, FakeArr, cache_keys, episode, iso, last_run

ORDERS = ["random", "smart", "newest_first", "oldest_first"]


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "missingarr.db"
    monkeypatch.setattr(settings, "database_url", str(path))
    monkeypatch.setattr(database, "_cached_secret_key", None)
    database.init_db()
    return path


@pytest.fixture
def no_shuffle(monkeypatch):
    """Pages and records stay in the order the Wanted list returns them."""
    monkeypatch.setattr(search_missing.random, "shuffle", lambda items: None)


def make_radarr(**fields):
    data = {"name": "Radarr", "type": "radarr", "url": "http://127.0.0.1:9", "api_key": "k" * 32,
            "seconds_between_actions": 0, "hours_after_release": 0, "rate_cap": 1000,
            "search_order": "oldest_first", "missing_per_run": 10}
    data.update(fields)
    return db.instances.create(data)


def film(movie_id, days_ago=30, **extra):
    """A missing movie as the Wanted list returns it; extra may set isAvailable."""
    data = {"id": movie_id, "title": f"Some Film {movie_id}", "year": 2026, "hasFile": False,
            "monitored": True, "digitalRelease": iso(days_ago)}
    data.update(extra)
    return data


def run(inst, records, force=False):
    agent = FakeArr(db.instances.get_by_id(inst["id"]), missing=records)
    SearchMissingSkill().execute(agent, force=force)
    return agent


def searched(agent):
    return [p["movieIds"][0] for p in agent.posts if p["name"] == "MoviesSearch"]


def summary():
    """The newest run summary (_Stats.describe) in the activity log."""
    messages = [r["message"] for r in db.activity.query(limit=200, include_debug=True)]
    return next(m for m in messages if "missing item(s) on" in m)


@pytest.mark.parametrize("order", ORDERS)
def test_unavailable_movies_are_never_searched_in_any_order(db_path, order):
    inst = make_radarr(search_order=order)
    records = [film(i, days_ago=10 * i, isAvailable=i % 2 == 0) for i in range(1, 7)]
    agent = run(inst, records)
    assert sorted(searched(agent)) == [2, 4, 6]
    assert cache_keys() == ["mov:2", "mov:4", "mov:6"]


def test_unavailable_movies_take_no_place_of_per_run(db_path, monkeypatch):
    # Page size 2: the full-list read of oldest_first spans three pages.
    monkeypatch.setattr(search_missing, "ORDERED_PAGE_SIZE", 2)
    inst = make_radarr(search_order="oldest_first", missing_per_run=2)
    # 1 is the oldest; 1-3 are not available yet.
    records = [film(i, days_ago=100 - i, isAvailable=i > 3) for i in range(1, 7)]
    agent = run(inst, records)
    assert searched(agent) == [4, 5]
    assert len([1 for path, _ in agent.gets if path == WANTED]) == 3
    assert cache_keys() == ["mov:4", "mov:5"]


def test_random_order_reads_on_past_a_page_of_unavailable_movies(db_path, no_shuffle):
    # per_run 1 reads pages of 50; nothing on the first page is available yet.
    inst = make_radarr(search_order="random", missing_per_run=1)
    records = [film(i, isAvailable=False) for i in range(1, 51)] + [film(51, isAvailable=True)]
    agent = run(inst, records)
    assert searched(agent) == [51]
    # The probe (page 1, size 1), then pages 1 and 2.
    assert [p["page"] for path, p in agent.gets if path == WANTED] == [1, 1, 2]


def test_a_missing_or_null_is_available_counts_as_available(db_path):
    inst = make_radarr()
    records = [film(1, days_ago=40), film(2, days_ago=39, isAvailable=None),
               film(3, days_ago=38, isAvailable=True), film(4, days_ago=37, isAvailable=False)]
    agent = run(inst, records)
    assert searched(agent) == [1, 2, 3]
    assert summary().endswith(", 1 not yet available in Radarr")


def test_a_force_run_leaves_unavailable_movies_out_too(db_path):
    # A force run ignores Hours after release, never Radarr's availability.
    inst = make_radarr(hours_after_release=48)
    records = [film(1, days_ago=0, isAvailable=False), film(2, days_ago=0, isAvailable=True)]
    agent = run(inst, records, force=True)
    assert searched(agent) == [2]
    assert cache_keys() == ["mov:2"]


def test_hours_after_release_still_waits_and_is_checked_second(db_path):
    inst = make_radarr(hours_after_release=48)
    records = [film(1, days_ago=30, isAvailable=True), film(2, days_ago=1, isAvailable=True),
               film(3, days_ago=30, isAvailable=False), film(4, days_ago=1, isAvailable=False)]
    agent = run(inst, records)
    assert searched(agent) == [1]
    # Movie 4 is neither available nor out of the window: it counts as not available.
    assert "1 inside the release window" in summary()
    assert "2 not yet available in Radarr" in summary()


def test_the_release_window_still_counts_from_the_release_date(db_path):
    # Two separate conditions: the home release was seven days ago and
    # Radarr's Availability Delay runs out today. Once Radarr reports the
    # movie available it is searched at once; the 9 hours passed long ago
    # and do not start again when Radarr reports it available.
    inst = make_radarr(hours_after_release=9)
    first = run(inst, [film(1, days_ago=7, isAvailable=False)])
    assert first.posts == []

    second = run(inst, [film(1, days_ago=7, isAvailable=True)])
    assert searched(second) == [1]


def test_a_movie_is_searched_once_radarr_reports_it_available(db_path):
    inst = make_radarr()
    first = run(inst, [film(1, isAvailable=False)])
    assert first.posts == []
    assert cache_keys() == []
    assert last_run()["status"] == "success"

    second = run(inst, [film(1, isAvailable=True)])
    assert searched(second) == [1]
    assert cache_keys() == ["mov:1"]


def test_the_summary_counts_unavailable_movies(db_path):
    inst = make_radarr()
    run(inst, [film(1, isAvailable=False), film(2, isAvailable=False)])
    assert summary() == (
        "Nothing to search — checked 2 of 2 missing item(s) on 1 page(s): 0 already searched, "
        "0 inside the release window, 0 with a file, 2 not yet available in Radarr"
    )
    run(inst, [film(3, isAvailable=True)])
    assert summary() == (
        "checked 1 of 1 missing item(s) on 1 page(s): 0 already searched, "
        "0 inside the release window, 0 with a file"
    )


def test_sonarr_records_are_not_filtered(db_path):
    inst = make_radarr(name="Sonarr", type="sonarr")
    records = [{**episode(1), "isAvailable": False}]
    agent = run(inst, records)
    assert [p["episodeIds"] for p in agent.posts if p["name"] == "EpisodeSearch"] == [[1]]
```

`tests/test_g3_radarr_availability.py` (neu, ganze Datei):

```python
"""Checked search (dry run and active, force runs too) leaves out movies
Radarr does not report as available yet, like the command path. Radarr's
release search (GET /api/v3/release) is a user-invoked search and skips
its own availability check (AvailabilitySpecification)."""
import pytest

from backend import database
from backend.checked_search import runner
from backend.config import settings
from backend.skills.search_missing import SearchMissingSkill
from tests.test_g3_runner import (
    THE_THING, activity_messages, log_rows, make_instance, movie, pauses, searched_ids, sql,
    the_thing_agent,
)

# Not out yet: newest_first puts it before The Thing (1982).
UPCOMING = movie(2, "Some Film", 2027, isAvailable=False)


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


def two_movies(inst, upcoming=UPCOMING):
    return the_thing_agent(inst, missing=[upcoming, THE_THING], movies=[upcoming, THE_THING])


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("mode,outcome", [("dry_run", "would_grab"), ("active", "grabbed")])
def test_checked_search_leaves_out_unavailable_movies(db_path, mode, outcome, force):
    inst = make_instance(checked_search=mode, search_order="newest_first", missing_per_run=1)
    agent = two_movies(inst)
    SearchMissingSkill().execute(agent, force=force)
    # The upcoming movie comes first but takes no place of per run.
    assert searched_ids(agent) == [1]
    assert [(r["arr_id"], r["outcome"]) for r in log_rows()] == [(1, outcome)]
    assert pauses() == []
    assert sql("SELECT cache_key FROM searched_items WHERE cache_key='mov:2'") == []
    assert any("1 not yet available in Radarr" in m for m in activity_messages())


def test_a_dry_run_checks_the_movie_once_radarr_reports_it_available(db_path):
    inst = make_instance(checked_search="dry_run", search_order="newest_first", missing_per_run=1)
    first = two_movies(inst)
    SearchMissingSkill().execute(first)
    assert searched_ids(first) == [1]

    released = two_movies(inst, upcoming={**UPCOMING, "isAvailable": True})
    SearchMissingSkill().execute(released)
    assert searched_ids(released) == [2]
    assert [r["arr_id"] for r in log_rows()] == [2, 1]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_p2_radarr_availability.py tests/test_g3_radarr_availability.py -q -p no:cacheprovider`

Expected: FAIL, `17 failed, 1 passed`. Grund jedes Fehlschlags:
- `test_unavailable_movies_are_never_searched_in_any_order` (4 Varianten): `assert [1, 2, 3, 4, 5, 6] == [2, 4, 6]`, jeder Film wird gesucht.
- `test_unavailable_movies_take_no_place_of_per_run`: `assert [1, 2] == [4, 5]`.
- `test_random_order_reads_on_past_a_page_of_unavailable_movies`: `assert [1] == [51]`.
- `test_a_missing_or_null_is_available_counts_as_available`: `assert [1, 2, 3, 4] == [1, 2, 3]`.
- `test_a_force_run_leaves_unavailable_movies_out_too`: `assert [1, 2] == [2]`.
- `test_hours_after_release_still_waits_and_is_checked_second`: `assert [1, 3] == [1]`.
- `test_the_release_window_still_counts_from_the_release_date`: `AssertionError: assert [{'name': 'MoviesSearch', 'movieIds': [1]}] == []` im ersten Lauf.
- `test_a_movie_is_searched_once_radarr_reports_it_available`: `AssertionError: assert [{'name': 'MoviesSearch', 'movieIds': [1]}] == []`.
- `test_the_summary_counts_unavailable_movies`: `AssertionError`, die Zusammenfassung heißt `checked 2 of 2 missing item(s) on 1 page(s): 0 already searched, 0 inside the release window, 0 with a file` (beide Filme werden gesucht, kein „Nothing to search“, kein „not yet available“).
- `test_checked_search_leaves_out_unavailable_movies` (4 Varianten): `assert [2] == [1]`, der kommende Film nimmt den einen Platz von *per run*.
- `test_a_dry_run_checks_the_movie_once_radarr_reports_it_available`: `assert [2] == [1]` im ersten Lauf.

`test_sonarr_records_are_not_filtered` läuft schon jetzt durch; er hält D4 fest (ohne die Prüfung `arr_type == "radarr"` scheitert er).

- [ ] **Step 3: Write minimal implementation**

`backend/skills/search_missing.py`, drei Stellen.

(1) `_Stats` (Zeilen 50-58), ersetze

```python
    skipped_paused: int = 0
    notes: list = field(default_factory=list)

    def describe(self) -> str:
        return (
            f"checked {self.examined} of {self.total} missing item(s) on {self.pages} page(s): "
            f"{self.skipped_cache} already searched, {self.skipped_window} inside the release "
            f"window, {self.skipped_file} with a file"
            + (f", {self.skipped_queue} in the *arr queue" if self.skipped_queue else "")
```

durch

```python
    skipped_paused: int = 0
    skipped_unavailable: int = 0
    notes: list = field(default_factory=list)

    def describe(self) -> str:
        return (
            f"checked {self.examined} of {self.total} missing item(s) on {self.pages} page(s): "
            f"{self.skipped_cache} already searched, {self.skipped_window} inside the release "
            f"window, {self.skipped_file} with a file"
            + (f", {self.skipped_unavailable} not yet available in Radarr" if self.skipped_unavailable else "")
            + (f", {self.skipped_queue} in the *arr queue" if self.skipped_queue else "")
```

(2) Docstring von `_take_eligible` (Zeilen 261-264), ersetze

```python
        paused (checked search, dry run and active, force run too): own
        keys of titles in their error pause (db.checked_search_pause). Left
        out the same way, so a title that keeps failing does not hold back
        the next one either."""
```

durch

```python
        paused (checked search, dry run and active, force run too): own
        keys of titles in their error pause (db.checked_search_pause). Left
        out the same way, so a title that keeps failing does not hold back
        the next one either.

        Radarr records with isAvailable false (the movie's Minimum
        Availability plus Radarr's Availability Delay not reached yet) are
        left out before the release window, on every path, also in force
        runs and checked runs: Radarr treats a search command or release
        search sent through its API as user-invoked and then skips its own
        availability check. They count toward neither per_run nor the
        candidates and are not remembered. A missing or null isAvailable
        counts as available."""
```

(3) Bedingung in `_take_eligible` (Zeilen 279-282), ersetze

```python
                if record.get("hasFile"):
                    stats.skipped_file += 1
                    continue
                if cutoff is not None:
```

durch

```python
                if record.get("hasFile"):
                    stats.skipped_file += 1
                    continue
                if arr_type == "radarr" and record.get("isAvailable") is False:
                    stats.skipped_unavailable += 1
                    continue
                if cutoff is not None:
```

Sonst ändert sich nichts. `search_upgrades.py` bleibt unverändert.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_p2_radarr_availability.py tests/test_g3_radarr_availability.py tests/test_p2_search_missing.py tests/test_p2_cache_rules.py tests/test_g3_runner.py -q -p no:cacheprovider`

Expected: PASS, `260 passed` (18 neue Tests, dazu die bestehenden Tests der Fehlend-Suche, der Cache-Regeln und der geprüften Suche unverändert grün). Die Dateien brauchen gut eine Minute.

- [ ] **Step 5: Commit**

```bash
git add backend/skills/search_missing.py tests/test_p2_radarr_availability.py tests/test_g3_radarr_availability.py
git commit -m "feat: leave out movies Radarr does not report as available yet" -m "Search Missing skips Radarr records with isAvailable false before the release window, on every search order, with checked search and in force runs. Radarr treats searches sent through its API as user-invoked and skips its own availability check. Skipped movies take no place of per run, are not cached and are counted in the run summary." -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ckkf3wL6aWTteiEvXgiDDg"
```

---

### Task 2: Doku (Tooltip, README, CHANGELOG 0.10.0)

Voraussetzung: Plan A ist fertig, `CHANGELOG.md` hat den Abschnitt `## [0.10.0]` mit `### Added`, `### Security` und `### Upgrade notes` (Plan A Task 8, Schritt 3b). Das Datum in der Überschrift spielt keine Rolle.

**Files:**
- Modify: `backend/tooltips.py:35` (Eintrag `"hours_after_release"`)
- Modify: `README.md:76` (Zeile **Hours After Release** in der Tabelle unter `### Scheduling`; vor Plan A Task 8 war es Zeile 75)
- Modify: `CHANGELOG.md` (im Abschnitt `## [0.10.0]` direkt vor dessen Zeile `### Security`)
- Modify: `tests/test_p6_docs.py` (neuer Test am Dateiende)

**Interfaces:**
- Consumes: `backend.tooltips.TOOLTIPS` (dict); `_Stats.describe()`-Text „not yet available in Radarr“ aus Task 1.
- Produces: Test `test_docs_explain_radarr_availability()`.

- [ ] **Step 1: Write the failing test**

`tests/test_p6_docs.py`: am Dateiende anfügen (eine Leerzeile davor wie zwischen den anderen Tests, also zwei Leerzeilen nach der letzten Zeile des vorigen Tests):

```python
def test_docs_explain_radarr_availability():
    from backend.tooltips import TOOLTIPS
    tooltip = TOOLTIPS["hours_after_release"]
    assert "Search Missing in Radarr" in tooltip
    assert "Minimum Availability" in tooltip
    assert "not from the moment Radarr" in tooltip
    readme = (ROOT / "README.md").read_text()
    assert "Search Missing in Radarr: a movie is only searched once Radarr reports it available" in readme
    assert "not from the moment Radarr reports it available" in readme
    assert "In Search Missing, Force Runs skip this wait" in readme
    changelog = (ROOT / "CHANGELOG.md").read_text()
    section = changelog[changelog.index("## [0.10.0]"):changelog.index("## [0.9.0]")]
    assert "not yet available in Radarr" in section
    assert "a separate condition, still counted from the release date" in section
    assert section.index("### Added") < section.index("### Changed") < section.index("### Security")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_p6_docs.py -q -p no:cacheprovider`

Expected: FAIL, `1 failed, 3 passed`. `test_docs_explain_radarr_availability` scheitert mit `AssertionError` an `assert "Search Missing in Radarr" in tooltip`.

- [ ] **Step 3: Write minimal implementation**

**3a.** `backend/tooltips.py`, Zeile 35, ersetze

```python
    "hours_after_release": "Wait X hours after the release date before searching for a title. Set to 0 to search immediately.",
```

durch

```python
    "hours_after_release": (
        "Wait X hours after the release date before searching for a title. Set to 0 to search immediately. "
        "Search Missing in Radarr: a movie is only searched once Radarr counts it as available (its "
        "Minimum Availability plus the Availability Delay). Both must be met: these hours still count "
        "from the release date, not from the moment Radarr counts the movie as available."
    ),
```

**3b.** `README.md`, Zeile 76 (Tabelle unter `### Scheduling`, die einzige Zeile, die mit `| **Hours After Release** |` beginnt), ersetze

```markdown
| **Hours After Release** | `9` | Missingarr waits this many hours after the release date before searching for a title. Prevents hammering indexers for content that isn't out yet. Set to `0` to search immediately. |
```

durch

```markdown
| **Hours After Release** | `9` | Missingarr waits this many hours after the release date before searching for a title. Prevents hammering indexers for content that isn't out yet. Set to `0` to search immediately. Search Missing in Radarr: a movie is only searched once Radarr reports it available (`isAvailable` in the Wanted list: its *Minimum Availability* plus Radarr's *Availability Delay*), also with checked search and in Force Runs. Both conditions must be met: these hours still count from the release date, not from the moment Radarr reports it available. In Search Missing, Force Runs skip this wait, never the availability check. |
```

**3c.** `CHANGELOG.md`: im Abschnitt `## [0.10.0]` direkt vor dessen Zeile `### Security` einfügen (Anker: die erste Zeile `### Security` nach `## [0.10.0]` und vor `## [0.9.0]`). Danach folgt `### Security` mit einer Leerzeile davor, wie bisher:

```markdown
### Changed

- Search Missing (Radarr) leaves out movies Radarr does not report as available yet (`isAvailable` false in the Wanted list: the movie's *Minimum Availability* plus Radarr's *Availability Delay* not reached), in every search order, with checked search (dry run and active) and in Force Runs. Radarr treats a search sent through its API as started by a user and then skips its own availability check, so missingarr could search and grab movies that were not out yet. Such movies take no place of *Missing per run*, are not remembered as searched and are searched as soon as Radarr reports them available; the run summary counts them ("N not yet available in Radarr"). *Hours after release* stays a separate condition, still counted from the release date, not from the moment Radarr reports a movie available. Upgrade searches and Sonarr are unchanged.

```

Ergebnis im Abschnitt: `### Added` (Plan A), Leerzeile, `### Changed`, Leerzeile, die Zeile, Leerzeile, `### Security`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_p6_docs.py tests/test_z_release.py tests/test_g5_form.py tests/test_p5_form.py -q -p no:cacheprovider`

Expected: PASS, `21 passed` (`test_p6_docs.py` 4 mit dem neuen Test, `test_z_release.py` 3 nach Plan A Task 8, `test_g5_form.py` 8, `test_p5_form.py` 6; die Formular-Tests rendern den geänderten Tooltip).

Danach die ganze Suite:

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider`

Expected: `0 failed`, genau `2 skipped` (die zwei Korpus-Tests in `tests/test_g1_corpus.py`). Gegenüber dem Stand nach Plan A kommen 19 Tests dazu (18 aus Task 1, 1 aus Task 2).

- [ ] **Step 5: Commit**

```bash
git add backend/tooltips.py README.md CHANGELOG.md tests/test_p6_docs.py
git commit -m "docs: Radarr's own availability comes before Hours after release" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ckkf3wL6aWTteiEvXgiDDg"
```

---

### Task 3: Feinschliff der Imports-Seite (aus der Schlussprüfung von Plan A)

Die Schlussprüfung von Plan A (03.10.2026) hat zwei kleine Punkte an der Imports-Seite gefunden, die der Plan so vorgegeben hatte, und einen dritten, der nur als bekannte Grenze notiert wird. Dieser Task hat keinen vorgegebenen Code: Wer ihn umsetzt, liest die betroffenen Stellen in `templates/imports.html` und den Node-Vorspann in `tests/test_h5_imports_page.py` und folgt deren Stil. Codex-Prüfung entfällt (kleine, klar testbare Änderung, Regel des Betreibers vom 03.10.2026).

**Files:**
- Modify: `templates/imports.html` (Alpine-Komponente der Seite)
- Modify: `tests/test_h5_imports_page.py` (neue Tests im vorhandenen Node-Stil)
- Modify: `docs/superpowers/specs/2026-10-02-imports-design.md` (eine Zeile in „Risiken“) — Ausnahme von der Regel „kein Task-Commit berührt docs/“ aus Plan A, die nur für Plan A galt

**Anforderungen:**

1. **Seite verlassen stoppt die Arbeit der Seite.** Die Seite läuft mit `hx-boost`; beim Wechsel auf eine andere Seite bleibt der JavaScript-Kontext erhalten, und die alte Komponente lädt weiter Vorschläge (die App liest dafür Dateien mit ffprobe) und belegt die zwei Vorschlags-Plätze je App. Die Komponente bekommt `destroy()` (Alpine 3 ruft es auf, wenn das Element entfernt wird). `destroy()` erhöht `this._request` und `this._listRequest`, sodass laufende Vorschlags-Worker und Listen-Ladevorgänge nach ihrer aktuellen Anfrage nichts mehr schreiben und keine weitere Anfrage starten, und setzt eine Markierung, die verhindert, dass ein noch laufendes `pollCommand` nach seinem Ende `load()`/`loadProposals()` startet. Der Toast eines zu Ende gehenden Imports erscheint weiter (der Store ist global); auch der Zähler im Menü wird weiter aufgefrischt.
2. **Zähler zuerst.** Nach einem Import (sobald der Befehl einen End- oder Abbruchzustand hat) und nach einem Verwerfen wird `refreshImportsCount()` aufgerufen, bevor `load()` startet, nicht erst nachdem `load()` mit allen Vorschlägen fertig ist.
3. **Bekannte Grenze notieren (kein Code):** In der Spec unter „Risiken“ eine Zeile: Der Wächter nach einem unklaren Import hängt am Bereich (Instanz, Typ, URL). Wird die URL der Instanz innerhalb der 10 Minuten auf eine andere Schreibweise derselben App geändert, sieht eine Aktion unter dem neuen Bereich den Wächter nicht. Sehr unwahrscheinlich (verlorene Antwort, URL-Änderung und Verwerfen in 10 Minuten), deshalb bewusst so.

- [ ] **Step 1: Failing Tests schreiben** in `tests/test_h5_imports_page.py`, im Stil der vorhandenen Node-Tests (gleicher Vorspann, gleiche Test-Uhr und zurückgehaltene Antworten):
  - `destroy()` während `loadProposals()` mit zurückgehaltenen Antworten: Nach dem Auflösen startet keine weitere Vorschlags-Anfrage und `proposals` wird nicht mehr beschrieben.
  - `destroy()` während `pollCommand()` eines Imports: Nach dem Endzustand startet keine Listen- oder Vorschlags-Anfrage; der Toast erscheint, der Zähler wird aufgefrischt.
  - Nach Verwerfen und nach dem Ende eines Imports startet die Zähler-Anfrage, bevor die Vorschläge fertig geladen sind (zurückgehaltene Vorschlags-Antwort).
- [ ] **Step 2: Tests laufen lassen, sie müssen scheitern.** Run: `.venv/bin/python -m pytest tests/test_h5_imports_page.py -q -p no:cacheprovider` — die neuen Tests scheitern (kein `destroy`, Zähler erst nach `load()`), die vorhandenen bestehen. Zahlen im Bericht festhalten.
- [ ] **Step 3: `templates/imports.html` ändern** (Anforderungen 1 und 2) und die Zeile in der Spec ergänzen (Anforderung 3).
- [ ] **Step 4: Tests laufen lassen.** Dieselbe Datei: alle bestehen. Danach einmal die ganze Suite: `.venv/bin/python -m pytest -q -p no:cacheprovider` — alle bestehen (Zahl im Bericht).
- [ ] **Step 5: Commit**

```bash
git add templates/imports.html tests/test_h5_imports_page.py docs/superpowers/specs/2026-10-02-imports-design.md
git commit -F <nachrichtendatei>
```

Nachricht:

```
fix(imports): stop page work after leaving and refresh the counter first

Leaving the Imports page (hx-boost keeps the JS context) now stops the
proposal workers and the follow-up load of a running import. After an
import or a discard the menu counter is refreshed before the cards
reload. The spec names the scope-keyed guard as a known limit.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ckkf3wL6aWTteiEvXgiDDg
```


## Prüfung des Plans (03.10.2026)

Der Plan wurde vor dem Festschreiben ausgeführt, zuletzt nach Codex-Runde 1 noch einmal von vorn (Klon auf den Stand vor Task 1 zurückgesetzt), in einem Klon von `feat/imports` (Stand: Plan A bis Task 7). Code, Tests und Doku kamen nur aus den Codeblöcken dieses Plans (Port-Skript: ganze Dateien schreiben, „ersetze … durch …“ mit genau einem Treffer, CHANGELOG-Block vor `### Security` von `## [0.10.0]`), die Commits aus den Commit-Blöcken. Für Task 2 stand vorher ein Ersatz für Plan A Task 8 im Klon: `VERSION` 0.10.0, der CHANGELOG-Block `## [0.10.0]` wörtlich aus Plan A Schritt 3b und `test_version_is_0_10_0`; README-Abschnitte und `tests/test_z_release.py` von Plan A Task 8 fehlten dort.

| Schritt | Erwartet (Plan) | Ergebnis im Klon |
|---|---|---|
| Task 1 Step 2 | `17 failed, 1 passed`, Gründe wie oben | `17 failed, 1 passed`, dieselben Gründe |
| Task 1 Step 4 | `260 passed` | `260 passed` |
| Task 2 Step 2 | `1 failed, 3 passed` | `1 failed, 3 passed`, `AssertionError` an `"Search Missing in Radarr" in tooltip` |
| Task 2 Step 4 | `21 passed` | `20 passed`: `tests/test_z_release.py` hatte ohne Plan A Task 8 zwei statt drei Tests |
| ganze Suite | `0 failed`, `2 skipped` | ganze Suite grün: `1284 passed, 2 skipped` |

Die Gesamtzahl hängt vom Stand von Plan A ab, das parallel Tests dazu bringt; dieser Plan selbst bringt 19 Tests (18 in Task 1, 1 in Task 2).

Mutationsprüfung (je eine Änderung an `search_missing.py`, danach die zwei neuen Testdateien, 18 Tests):

| Mutation | Ergebnis |
|---|---|
| Bedingung ganz entfernt | 17 failed |
| nur der zufällige Weg ohne Bedingung (`_collect_random` streicht `isAvailable` vor `_take_eligible`) | 2 failed: `…_in_any_order[random]`, `test_random_order_reads_on_past_a_page_of_unavailable_movies` |
| nur der geordnete Weg ohne Bedingung (`_collect_ordered` streicht `isAvailable` vor dem Sortieren) | 15 failed, darunter alle Tests der geprüften Suche |
| fehlendes `isAvailable` gilt als `false` (`is not True`) | 6 failed: `test_a_missing_or_null_is_available_counts_as_available` und die 5 Tests der geprüften Suche (`THE_THING` hat kein `isAvailable`) |
| Bedingung nur, wenn ein Release-Fenster gilt (`cutoff is not None and …`) | 15 failed, darunter `test_a_force_run_leaves_unavailable_movies_out_too` |
| Bedingung erst nach dem Release-Fenster | 1 failed: `test_hours_after_release_still_waits_and_is_checked_second` |
| Release-Fenster ab Radarrs Freigabe statt ab Release-Datum (für Radarr 7 Tage *Availability Delay* aufs Release-Datum) | 1 failed: `test_the_release_window_still_counts_from_the_release_date` |
| ohne `arr_type == "radarr"` | 1 failed: `test_sonarr_records_are_not_filtered` |

Mutationsprüfung der Doku (je eine Änderung im Klon nach Task 2, danach `tests/test_p6_docs.py`):

| Mutation | Ergebnis |
|---|---|
| Tooltip mit dem alten Satz („these hours come on top“) | 1 failed |
| Tooltip ohne „Search Missing in“ | 1 failed |
| README mit dem alten Satz („an extra wait on top“) | 1 failed |
| README ohne „Search Missing in“ vor „Radarr: a movie is only searched …“ | 1 failed |
| README-Satz zu Force Runs ohne „In Search Missing,“ | 1 failed |
| CHANGELOG mit dem alten Satz („still waits on top“) | 1 failed |

Prüfung auf Server- und Personendaten mit dem Push-Prüfskript (`--dateien` mit dieser Plandatei): ohne Fund.

## Codex-Durchsicht

### Runde 1 (03.10.2026)

Codex prüfte eine bereinigte Kopie (Plan, missingarr-Quelltext, Radarr-Quelltext), nur lesend. Kein Befund der Stufe hoch oder kritisch; der Filter selbst gilt als richtig.

| Befund | Erledigung | Codex-Stufe |
|---|---|---|
| „Hours on top“ verspricht eine Wartezeit nach Radarrs Freigabe, die der Code nicht baut: das Fenster zählt ab dem Release-Datum, unabhängig von `isAvailable` (Release vor sieben Tagen, Delay läuft heute ab, Fenster 9 h → sofort gesucht) | Übernommen: Entscheidung 9 legt D3 als zwei unabhängige Bedingungen aus (D1: Filter vor der bestehenden Fenster-Prüfung; D3: die Einstellung „bleibt“; die Wanted-Liste nennt keinen Freigabe-Zeitpunkt). Tooltip, README und CHANGELOG sagen jetzt „beide Bedingungen, Stunden ab dem Release-Datum, nicht ab Radarrs Freigabe“. Neuer Test `test_the_release_window_still_counts_from_the_release_date` mit genau dem Codex-Beispiel, Doku-Test prüft die neuen Sätze | mittel |
| README und Tooltip versprechen die Regel ohne Einschränkung auf die Fehlend-Suche; Upgrade-Suchen prüfen `isAvailable` nicht (D4) | Übernommen: Tooltip und README beginnen mit „Search Missing in Radarr:“, der Force-Satz im README mit „In Search Missing,“. Der CHANGELOG nannte die Fehlend-Suche schon. Doku-Test prüft die Präfixe | niedrig |
| „Hintergrund“ Punkt 2 nennt bei *In Cinemas* den Kinostart ohne Bedingung; Radarr nimmt ihn nur, wenn er bekannt ist, sonst digitales/physisches Release. *TBA*/*Announced* ohne Delay nicht erwähnt | Übernommen: Punkt 2 nennt den Rückfall ohne Kinostart (Movie.cs Zeile 89), dass *TBA*/*Announced* und „ohne jedes Datum“ ohne Delay gelten (Zeile 85, 113-116) und dass der Delay nur auf ein gefundenes Datum kommt (Zeile 118). Code und Tests unverändert, der Filter liest nur das fertige `isAvailable` | niedrig |

Codex' Frage an den Betreiber (D3: unabhängige Bedingungen oder Stunden nach Radarrs Freigabe) ist mit Entscheidung 9 aus D1 und D3 beantwortet; die andere Lesart bräuchte einen Freigabe-Zeitpunkt, den die Wanted-Liste nicht liefert.

### Änderungen nach Codex-Runde 1

- Hintergrund Punkt 2: Rückfall bei *In Cinemas* ohne Kinostart, *TBA*/*Announced* und „ohne jedes Datum“ ohne *Availability Delay*, Zeilenangaben in `Movie.cs`.
- Neue Entscheidung 9: D3 als zwei unabhängige Bedingungen, mit Begründung und Beispiel.
- Task 1: neuer Test `test_the_release_window_still_counts_from_the_release_date`; Step 2 jetzt `17 failed, 1 passed`, Step 4 `260 passed`; Dateien-Tabelle ergänzt.
- Task 2: Tooltip und README mit „Search Missing in Radarr:“ und „Both … must be met: these hours still count from the release date, not from the moment Radarr …“; README-Satz zu Force Runs mit „In Search Missing,“; CHANGELOG-Zeile „stays a separate condition, still counted from the release date …“ statt „still waits on top“; Doku-Test prüft diese Sätze, Step 2 scheitert jetzt an `"Search Missing in Radarr" in tooltip`.
- Prüfung des Plans: von vorn wiederholt, Zahlen (19 neue Tests, ganze Suite `1284 passed, 2 skipped`) und Mutationstabelle nachgezogen, neue Mutation für das Release-Fenster, neue Mutationsprüfung der Doku.
