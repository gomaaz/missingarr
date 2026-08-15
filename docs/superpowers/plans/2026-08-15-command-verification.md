# Verifizierte Suchprotokolle — Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Jeder Eintrag in der Suchhistorie trägt die Befehls-ID aus Sonarr/Radarr und deren tatsächlichen Ausgang, sodass die Oberfläche nur noch anzeigt, was belegt ist.

**Architecture:** Der Trigger-Pfad gibt statt eines Tupels ein `SearchResult` zurück, das die tatsächlich gefeuerte Entitäts-ID und die Befehls-ID aus der \*arr-Antwort mitführt. Beide landen in `search_history_items`. Ein neuer Skill `verify_commands` fragt alle zwei Minuten offene Befehle über `GET /api/v3/command/{id}` nach und schreibt den Ausgang zurück; gescheiterte Befehle geben ihren Eintrag im `searched_items`-Cache wieder frei. Der Lauf-Status wird aus den Item-Zuständen abgeleitet.

**Tech Stack:** Python 3, FastAPI, APScheduler, SQLite (stdlib `sqlite3`), `requests`, Jinja2, Alpine.js, htmx. Neu für Tests: `pytest`.

## Global Constraints

- Kein Build-Schritt, kein npm, keine neuen Laufzeit-Abhängigkeiten. `pytest` kommt ausschließlich in `requirements-dev.txt`, nie in `requirements.txt`.
- Schema-Änderungen laufen über das bestehende Muster in `backend/database.py:132-140`: `ALTER TABLE` in der Liste, Ausnahme wird geschluckt. Zusätzlich in die `CREATE TABLE`-Anweisung aufnehmen, damit Neuinstallationen dasselbe Schema bekommen.
- Maßgeblich für den Ausgang ist das Feld `status` der \*arr-Antwort, **nicht** `result`. `result` verfällt nach kurzer Zeit auf `unknown`, `status` bleibt erhalten (gemessen: nach 56 Minuten weiterhin `completed`).
- Unbekannter Ausgang (`expired`) ist **kein** Fehlschlag. Nur bei belegtem `failed` wird der Cache-Eintrag gelöscht.
- Alle Log-Meldungen bleiben englisch, passend zum Bestand (`agent.log("info", …)`).
- Der Deckel liegt bei 50 nachgeprüften Befehlen je Durchlauf und Instanz.
- Bestandszeilen bekommen `command_status='legacy'` und werden nie verifiziert.

## Dateistruktur

| Datei | Verantwortung |
|---|---|
| `backend/verification.py` | **neu** — reine Zustandslogik: \*arr-Antwort → Item-Status, Item-Status → Lauf-Status. Kein HTTP, keine DB. |
| `tests/test_verification.py` | **neu** — Tests der reinen Logik |
| `requirements-dev.txt` | **neu** — `pytest` |
| `backend/database.py` | Schema-Migration |
| `backend/db/history.py` | Items mit Beleg schreiben, offene Items holen, Ausgang schreiben, Lauf-Aggregat |
| `backend/db/searched.py` | `delete()` zum Freigeben nach Fehlschlag |
| `backend/skills/base.py` | `SearchResult`-Wertobjekt |
| `backend/skills/search_missing.py` | Trigger-Pfad auf `SearchResult` umstellen |
| `backend/skills/search_upgrades.py` | dito |
| `backend/skills/verify_commands.py` | **neu** — der Verifikationsdurchlauf |
| `backend/agents/base.py` | `http_get_raw()`, Sperre je Skill, Scheduler-Job |
| `backend/agents/sonarr.py`, `radarr.py` | Skill registrieren |
| `templates/history.html` | Lauf-Status-Badges + Spalte für den Item-Ausgang |
| `templates/instances/card.html`, `static/js/app.js` | Kachel zeigt `bestätigt / abgeschickt` |

Die reine Logik liegt bewusst in `backend/verification.py` statt im Skill: sie ist der Teil, den man ohne laufende \*arr-Instanz prüfen kann, und der Skill bleibt dadurch ein dünner Anschluss an HTTP und Datenbank.

---

### Task 1: Reine Zustandslogik mit Tests

**Files:**
- Create: `backend/verification.py`
- Create: `tests/test_verification.py`
- Create: `tests/__init__.py` (leer)
- Create: `requirements-dev.txt`

**Interfaces:**
- Consumes: nichts
- Produces: `map_command_status(http_status: int, payload: dict | None) -> str`, `aggregate_run_status(item_statuses: list[str]) -> str`, sowie die Konstanten `ITEM_SUBMITTED`, `ITEM_COMPLETED`, `ITEM_FAILED`, `ITEM_EXPIRED`, `ITEM_LEGACY`, `RUN_SUCCESS`, `RUN_PENDING`, `RUN_PARTIAL`, `RUN_FAILED`, `RUN_UNVERIFIED` — alle mit ihren String-Werten identisch zum Konstantennamen in Kleinschreibung (`ITEM_SUBMITTED == "submitted"`).

- [ ] **Step 1: Dev-Abhängigkeit anlegen**

`requirements-dev.txt`:

```
-r requirements.txt
pytest>=8.0.0
```

Installieren: `pip install -r requirements-dev.txt`

- [ ] **Step 2: Den fehlschlagenden Test schreiben**

`tests/__init__.py` bleibt leer. `tests/test_verification.py`:

```python
from backend.verification import (
    map_command_status,
    aggregate_run_status,
    ITEM_SUBMITTED,
    ITEM_COMPLETED,
    ITEM_FAILED,
    ITEM_EXPIRED,
    ITEM_LEGACY,
    RUN_SUCCESS,
    RUN_PENDING,
    RUN_PARTIAL,
    RUN_FAILED,
    RUN_UNVERIFIED,
)


class TestMapCommandStatus:
    def test_completed_command_counts_as_completed(self):
        assert map_command_status(200, {"status": "completed"}) == ITEM_COMPLETED

    def test_failed_command_counts_as_failed(self):
        assert map_command_status(200, {"status": "failed"}) == ITEM_FAILED

    def test_aborted_and_cancelled_count_as_failed(self):
        assert map_command_status(200, {"status": "aborted"}) == ITEM_FAILED
        assert map_command_status(200, {"status": "cancelled"}) == ITEM_FAILED

    def test_status_is_matched_case_insensitively(self):
        assert map_command_status(200, {"status": "Completed"}) == ITEM_COMPLETED

    def test_running_command_stays_open_for_the_next_pass(self):
        assert map_command_status(200, {"status": "queued"}) == ITEM_SUBMITTED
        assert map_command_status(200, {"status": "started"}) == ITEM_SUBMITTED

    def test_unknown_command_is_expired(self):
        assert map_command_status(404, None) == ITEM_EXPIRED

    def test_transient_server_error_stays_open(self):
        # 500 or a network failure (0) must not be mistaken for a verdict.
        assert map_command_status(500, None) == ITEM_SUBMITTED
        assert map_command_status(0, None) == ITEM_SUBMITTED

    def test_malformed_payload_stays_open(self):
        assert map_command_status(200, None) == ITEM_SUBMITTED
        assert map_command_status(200, {}) == ITEM_SUBMITTED

    def test_result_field_is_ignored(self):
        # `result` decays to "unknown" over time; only `status` is authoritative.
        assert map_command_status(200, {"status": "completed", "result": "unknown"}) == ITEM_COMPLETED


class TestAggregateRunStatus:
    def test_run_without_items_is_success(self):
        assert aggregate_run_status([]) == RUN_SUCCESS

    def test_all_completed_is_success(self):
        assert aggregate_run_status([ITEM_COMPLETED, ITEM_COMPLETED]) == RUN_SUCCESS

    def test_any_open_item_keeps_the_run_pending(self):
        assert aggregate_run_status([ITEM_COMPLETED, ITEM_SUBMITTED]) == RUN_PENDING
        assert aggregate_run_status([ITEM_FAILED, ITEM_SUBMITTED]) == RUN_PENDING

    def test_mixed_outcome_is_partial(self):
        assert aggregate_run_status([ITEM_COMPLETED, ITEM_FAILED]) == RUN_PARTIAL
        assert aggregate_run_status([ITEM_COMPLETED, ITEM_EXPIRED]) == RUN_PARTIAL

    def test_only_failures_is_failed(self):
        assert aggregate_run_status([ITEM_FAILED, ITEM_FAILED]) == RUN_FAILED

    def test_failed_plus_expired_is_failed(self):
        assert aggregate_run_status([ITEM_FAILED, ITEM_EXPIRED]) == RUN_FAILED

    def test_only_expired_is_unverified_not_failed(self):
        # An outcome *arr no longer knows is not a proven failure.
        assert aggregate_run_status([ITEM_EXPIRED, ITEM_EXPIRED]) == RUN_UNVERIFIED

    def test_legacy_items_are_ignored(self):
        assert aggregate_run_status([ITEM_LEGACY, ITEM_LEGACY]) == RUN_SUCCESS
        assert aggregate_run_status([ITEM_LEGACY, ITEM_FAILED]) == RUN_FAILED
```

- [ ] **Step 3: Test laufen lassen, Fehlschlag bestätigen**

Run: `python -m pytest tests/test_verification.py -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'backend.verification'`

- [ ] **Step 4: Implementierung schreiben**

`backend/verification.py`:

```python
"""Pure decision logic for command verification.

No HTTP and no database access, so every rule here is testable without a
running *arr instance. The skill in backend/skills/verify_commands.py is the
thin adapter that feeds this module.
"""

ITEM_SUBMITTED = "submitted"
ITEM_COMPLETED = "completed"
ITEM_FAILED = "failed"
ITEM_EXPIRED = "expired"
ITEM_LEGACY = "legacy"

RUN_SUCCESS = "success"
RUN_PENDING = "pending"
RUN_PARTIAL = "partial"
RUN_FAILED = "failed"
RUN_UNVERIFIED = "unverified"

# *arr command states that mean the command will not run (or did not finish).
_ARR_FAILURE_STATES = {"failed", "aborted", "cancelled"}


def map_command_status(http_status: int, payload: dict | None) -> str:
    """Translate an *arr command lookup into our item status.

    `status` is authoritative, not `result`: *arr keeps `status` indefinitely
    but resets `result` to "unknown" once the command ages out of the live
    queue. Anything inconclusive stays ITEM_SUBMITTED so the next pass retries
    instead of recording a verdict we do not have.

    http_status 0 is used by the caller for network-level failures.
    """
    if http_status == 404:
        return ITEM_EXPIRED
    if http_status != 200 or not isinstance(payload, dict):
        return ITEM_SUBMITTED

    arr_state = str(payload.get("status") or "").lower()
    if arr_state == "completed":
        return ITEM_COMPLETED
    if arr_state in _ARR_FAILURE_STATES:
        return ITEM_FAILED
    return ITEM_SUBMITTED


def aggregate_run_status(item_statuses: list[str]) -> str:
    """Derive a run's status from the statuses of its items.

    Legacy rows carry no command id and can never be verified, so they do not
    influence the verdict.
    """
    relevant = [s for s in item_statuses if s != ITEM_LEGACY]

    if not relevant:
        return RUN_SUCCESS
    if ITEM_SUBMITTED in relevant:
        return RUN_PENDING
    if all(s == ITEM_COMPLETED for s in relevant):
        return RUN_SUCCESS
    if ITEM_COMPLETED in relevant:
        return RUN_PARTIAL
    if ITEM_FAILED in relevant:
        return RUN_FAILED
    return RUN_UNVERIFIED
```

- [ ] **Step 5: Tests laufen lassen, Erfolg bestätigen**

Run: `python -m pytest tests/test_verification.py -v`
Expected: PASS, 17 Tests

- [ ] **Step 6: Commit**

```bash
git add backend/verification.py tests/ requirements-dev.txt
git commit -m "feat: add pure verification rules for *arr command outcomes"
```

---

### Task 2: Schema und Datenzugriff

**Files:**
- Modify: `backend/database.py:100-106` (CREATE TABLE), `backend/database.py:132-140` (Migrationsliste)
- Modify: `backend/db/history.py:76-81` (`insert_item`), `backend/db/history.py:107-146` (`query_items_flat`)
- Modify: `backend/db/searched.py`

**Interfaces:**
- Consumes: `ITEM_SUBMITTED`, `ITEM_EXPIRED` aus `backend/verification` (Task 1)
- Produces:
  - `history.insert_item(run_id: int, title: str, arr_id: int | None, item_type: str, cache_key: str = "", command_id: int | None = None) -> None`
  - `history.get_pending_items(instance_id: int, limit: int = 50) -> list[dict]` — Schlüssel `id`, `run_id`, `command_id`, `cache_key`
  - `history.set_item_status(item_id: int, status: str) -> None`
  - `history.expire_stale_items(instance_id: int, hours: int = 24) -> list[int]` — betroffene `run_id`
  - `history.get_item_statuses(run_id: int) -> list[str]`
  - `history.update_run_verification(run_id: int, status: str, verified_count: int) -> None`
  - `searched.delete(instance_id: int, cache_key: str) -> int`

- [ ] **Step 1: CREATE TABLE erweitern**

In `backend/database.py`, die Anweisung `CREATE TABLE IF NOT EXISTS search_history_items` ersetzen durch:

```sql
            CREATE TABLE IF NOT EXISTS search_history_items (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id         INTEGER NOT NULL REFERENCES search_history(id) ON DELETE CASCADE,
                title          TEXT NOT NULL,
                arr_id         INTEGER,
                item_type      TEXT NOT NULL CHECK(item_type IN ('movie','episode','season','series')),
                command_id     INTEGER,
                command_status TEXT NOT NULL DEFAULT 'legacy',
                cache_key      TEXT NOT NULL DEFAULT '',
                verified_at    TEXT
            );
```

- [ ] **Step 2: Migrationen ergänzen**

In `backend/database.py` die Liste in `for sql in [...]` um diese fünf Einträge erweitern (bestehende Einträge stehen lassen):

```python
            "ALTER TABLE search_history_items ADD COLUMN command_id INTEGER",
            "ALTER TABLE search_history_items ADD COLUMN command_status TEXT NOT NULL DEFAULT 'legacy'",
            "ALTER TABLE search_history_items ADD COLUMN cache_key TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE search_history_items ADD COLUMN verified_at TEXT",
            "ALTER TABLE search_history ADD COLUMN verified_count INTEGER NOT NULL DEFAULT 0",
```

Der Default `'legacy'` erledigt die Altdaten ohne separates UPDATE: SQLite füllt bestehende Zeilen beim `ADD COLUMN` mit dem Default. Neue Zeilen setzen den Wert explizit.

Direkt nach der `for`-Schleife den Index anlegen:

```python
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_items_pending "
            "ON search_history_items(command_status)"
        )
```

- [ ] **Step 3: `insert_item` erweitern**

In `backend/db/history.py` oben ergänzen:

```python
from backend.verification import ITEM_SUBMITTED, ITEM_EXPIRED
```

und `insert_item` ersetzen:

```python
def insert_item(
    run_id: int,
    title: str,
    arr_id: Optional[int],
    item_type: str,
    cache_key: str = "",
    command_id: Optional[int] = None,
):
    """Record a triggered search.

    Without a command id from *arr there is nothing to verify later, so the
    row is filed as expired rather than claiming a pending verification.
    """
    status = ITEM_SUBMITTED if command_id is not None else ITEM_EXPIRED
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO search_history_items
                (run_id, title, arr_id, item_type, cache_key, command_id, command_status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, title, arr_id, item_type, cache_key, command_id, status),
        )
```

- [ ] **Step 4: Abfragen für die Verifikation ergänzen**

An `backend/db/history.py` anhängen:

```python
def get_pending_items(instance_id: int, limit: int = 50) -> list[dict]:
    """Items of this instance that still await a verdict, oldest first."""
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT si.id, si.run_id, si.command_id, si.cache_key
            FROM search_history_items si
            JOIN search_history h ON h.id = si.run_id
            WHERE h.instance_id = ?
              AND si.command_status = ?
              AND si.command_id IS NOT NULL
            ORDER BY si.id
            LIMIT ?
            """,
            (instance_id, ITEM_SUBMITTED, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def set_item_status(item_id: int, status: str) -> None:
    with get_db() as conn:
        conn.execute(
            """
            UPDATE search_history_items
            SET command_status=?, verified_at=datetime('now','localtime')
            WHERE id=?
            """,
            (status, item_id),
        )


def expire_stale_items(instance_id: int, hours: int = 24) -> list[int]:
    """Give up on items *arr never resolved. Returns the affected run ids.

    Without this, an item whose command id vanished silently would be looked
    up on every single pass, forever.
    """
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT si.id, si.run_id
            FROM search_history_items si
            JOIN search_history h ON h.id = si.run_id
            WHERE h.instance_id = ?
              AND si.command_status = ?
              AND h.started_at < datetime('now','localtime', ? || ' hours')
            """,
            (instance_id, ITEM_SUBMITTED, f"-{hours}"),
        ).fetchall()
        if not rows:
            return []
        item_ids = [r["id"] for r in rows]
        placeholders = ",".join("?" * len(item_ids))
        conn.execute(
            f"""
            UPDATE search_history_items
            SET command_status=?, verified_at=datetime('now','localtime')
            WHERE id IN ({placeholders})
            """,
            [ITEM_EXPIRED, *item_ids],
        )
        return sorted({r["run_id"] for r in rows})


def get_item_statuses(run_id: int) -> list[str]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT command_status FROM search_history_items WHERE run_id=?",
            (run_id,),
        ).fetchall()
        return [r["command_status"] for r in rows]


def update_run_verification(run_id: int, status: str, verified_count: int) -> None:
    """Write the derived verdict — but never overwrite a run that errored out.

    A run that threw has a failure of its own; the outcome of the commands it
    managed to send before dying does not change that.
    """
    with get_db() as conn:
        conn.execute(
            "UPDATE search_history SET status=?, verified_count=? WHERE id=? AND status!='error'",
            (status, verified_count, run_id),
        )
```

- [ ] **Step 5: Item-Status in die Flachliste aufnehmen**

In `backend/db/history.py`, in `query_items_flat`, die SELECT-Liste ersetzen. Vorher:

```sql
            SELECT
                h.started_at,
                h.instance_name,
                h.skill,
                h.status,
                COALESCE(inst.type, '') AS arr_type,
                si.title,
                si.item_type
```

Nachher:

```sql
            SELECT
                h.started_at,
                h.instance_name,
                h.skill,
                h.status,
                h.verified_count,
                COALESCE(inst.type, '') AS arr_type,
                si.title,
                si.item_type,
                si.arr_id,
                si.command_id,
                si.command_status
```

Der Rest der Abfrage (`FROM` bis `LIMIT`) bleibt unverändert.

- [ ] **Step 6: `delete` in searched.py ergänzen**

An `backend/db/searched.py` anhängen:

```python
def delete(instance_id: int, cache_key: str) -> int:
    """Release a cached key so the item becomes searchable again.

    Called when *arr reported the command as failed. With retry_hours=0 the
    cache has no time window, so without this the title would stay blocked
    forever despite never having been searched.
    """
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM searched_items WHERE instance_id=? AND cache_key=?",
            (instance_id, cache_key),
        )
        return cursor.rowcount
```

- [ ] **Step 7: Migration gegen eine Kopie der echten Datenbank prüfen**

```bash
cp /root/docker/missingarr/data/missingarr.db /tmp/mig-test.db
DATABASE_URL=/tmp/mig-test.db python -c "
from backend.database import init_db; init_db()
import sqlite3
c = sqlite3.connect('/tmp/mig-test.db')
cols = [r[1] for r in c.execute('PRAGMA table_info(search_history_items)')]
print('Spalten:', cols)
assert {'command_id','command_status','cache_key','verified_at'} <= set(cols)
print('legacy-Zeilen:', c.execute(\"SELECT COUNT(*) FROM search_history_items WHERE command_status='legacy'\").fetchone()[0])
print('verified_count vorhanden:', 'verified_count' in [r[1] for r in c.execute('PRAGMA table_info(search_history)')])
"
```

Expected: alle vier Spalten vorhanden, **12357** legacy-Zeilen, `verified_count vorhanden: True`

- [ ] **Step 8: Commit**

```bash
git add backend/database.py backend/db/history.py backend/db/searched.py
git commit -m "feat: store command id and outcome per history item"
```

---

### Task 3: `SearchResult` und Umstellung von `search_missing`

**Files:**
- Modify: `backend/skills/base.py`
- Modify: `backend/skills/search_missing.py:196-205` (Aufrufstelle), `:315-439` (Trigger-Pfad)

**Interfaces:**
- Consumes: `history.insert_item(...)` mit `cache_key` und `command_id` (Task 2)
- Produces: `SearchResult` aus `backend.skills.base` mit den Feldern `ok: bool`, `title: str`, `item_type: str`, `cache_key: str`, `arr_id: int | None`, `command_id: int | None`

- [ ] **Step 1: Wertobjekt anlegen**

In `backend/skills/base.py` oben ergänzen:

```python
from dataclasses import dataclass
```

und vor `class BaseSkill` einfügen:

```python
@dataclass(frozen=True)
class SearchResult:
    """Outcome of a single triggered search.

    arr_id is the entity the command actually addressed — the series id for a
    SeriesSearch, not the episode that happened to trigger it. command_id is
    the id *arr returned and the only thing that makes the entry checkable
    later.
    """

    ok: bool
    title: str = ""
    item_type: str = ""
    cache_key: str = ""
    arr_id: int | None = None
    command_id: int | None = None
```

- [ ] **Step 2: Aufrufstelle umstellen**

In `backend/skills/search_missing.py` den Import erweitern:

```python
from backend.skills.base import BaseSkill, SearchResult
```

Die Schleife `for record in candidates:` — der Block ab `success, title, item_type, stored_key = self._trigger_search(...)` bis `db.searched.add(...)` wird ersetzt durch:

```python
                result = self._trigger_search(agent, cfg, record, missing_mode, series_lookup)
                if result.ok:
                    triggered_count += 1
                    agent.record_action()
                    db.history.insert_item(
                        run_id,
                        result.title,
                        result.arr_id,
                        result.item_type,
                        cache_key=result.cache_key,
                        command_id=result.command_id,
                    )
                    if result.cache_key:
                        db.searched.add(cfg["id"], result.cache_key, result.title, result.item_type)
```

- [ ] **Step 3: `_trigger_search` umstellen**

```python
    def _trigger_search(self, agent, cfg: dict, record: dict, missing_mode: str, series_lookup: dict | None = None) -> SearchResult:
        try:
            if cfg["type"] == "sonarr":
                return self._sonarr_search(agent, record, missing_mode, series_lookup or {})
            else:
                return self._radarr_search(agent, record)
        except Exception as exc:
            agent.log("warn", self.name, f"Failed to trigger search: {exc}")
            return SearchResult(False)
```

- [ ] **Step 4: `_radarr_search` umstellen**

```python
    def _radarr_search(self, agent, record: dict) -> SearchResult:
        movie_id = record.get("id")
        title = record.get("title", f"Movie #{movie_id}")
        year = record.get("year", "")
        label = f"{title} ({year})" if year else title
        if movie_id:
            resp = agent.http_post("/api/v3/command", {"name": "MoviesSearch", "movieIds": [movie_id]})
            agent.log("debug", self.name, f"MoviesSearch: {label}")
            return SearchResult(True, label, "movie", f"mov:{movie_id}", movie_id, resp.get("id"))
        return SearchResult(False)
```

- [ ] **Step 5: `_sonarr_search` umstellen**

Alle sechs Rückgabestellen. Signatur: `def _sonarr_search(self, agent, record: dict, mode: str, series_lookup: dict | None = None) -> SearchResult:`

Der Kopf der Methode (Ermittlung von `episode_id`, `series_id`, `season_number`, `series_title`, `ep_title`) bleibt unverändert. Um Wiederholung zu vermeiden, direkt danach zwei lokale Helfer einfügen:

```python
        def _episode_label() -> str:
            if series_title:
                return f"{series_title} S{(season_number or 0):02d}E{record.get('episodeNumber', 0):02d} – {ep_title}"
            return ep_title

        def _fire_episode() -> SearchResult:
            resp = agent.http_post("/api/v3/command", {"name": "EpisodeSearch", "episodeIds": [episode_id]})
            return SearchResult(True, _episode_label(), "episode", f"ep:{episode_id}", episode_id, resp.get("id"))

        def _fire_season() -> SearchResult:
            resp = agent.http_post(
                "/api/v3/command",
                {"name": "SeasonSearch", "seriesId": series_id, "seasonNumber": season_number},
            )
            label = f"{series_title} Season {season_number}"
            return SearchResult(True, label, "season", f"sea:{series_id}:{season_number}", series_id, resp.get("id"))

        def _fire_series() -> SearchResult:
            resp = agent.http_post("/api/v3/command", {"name": "SeriesSearch", "seriesId": series_id})
            return SearchResult(True, series_title, "series", f"ser:{series_id}", series_id, resp.get("id"))
```

Damit werden die Zweige zu:

```python
        if mode == "episode" and episode_id:
            result = _fire_episode()
            agent.log("debug", self.name, f"EpisodeSearch: {result.title}")
            return result

        elif mode == "season_packs" and series_id is not None and season_number is not None:
            try:
                eps = agent.http_get(f"/api/v3/episode?seriesId={series_id}&seasonNumber={season_number}&includeImages=false")
                total_eps = len(eps) if isinstance(eps, list) else 0
                missing_eps = sum(1 for e in (eps if isinstance(eps, list) else []) if not e.get("hasFile"))
                ratio = missing_eps / total_eps if total_eps > 0 else 1.0
                if ratio >= 0.5:
                    agent.log("debug", self.name, f"SeasonSearch: {series_title} Season {season_number} (missing {missing_eps}/{total_eps} eps)")
                    return _fire_season()
                agent.log("debug", self.name, f"season_packs → EpisodeSearch (only {missing_eps}/{total_eps} eps missing)")
                return _fire_episode()
            except Exception as exc:
                agent.log("debug", self.name, f"season_packs ratio check failed, falling back to EpisodeSearch: {exc}")
                return _fire_episode()

        elif mode == "show_batch" and series_id is not None:
            try:
                eps = agent.http_get(f"/api/v3/episode?seriesId={series_id}&includeImages=false")
                eps_list = eps if isinstance(eps, list) else []
                total_eps = len(eps_list)
                missing_eps = sum(1 for e in eps_list if not e.get("hasFile"))
                series_ratio = missing_eps / total_eps if total_eps > 0 else 1.0
                if series_ratio >= 0.5:
                    agent.log("debug", self.name, f"SeriesSearch: {series_title} (missing {missing_eps}/{total_eps} eps)")
                    return _fire_series()
                sea_eps = [e for e in eps_list if e.get("seasonNumber") == season_number]
                total_sea = len(sea_eps)
                missing_sea = sum(1 for e in sea_eps if not e.get("hasFile"))
                sea_ratio = missing_sea / total_sea if total_sea > 0 else 1.0
                if sea_ratio >= 0.5 and season_number is not None:
                    agent.log("debug", self.name, f"show_batch → SeasonSearch (series {missing_eps}/{total_eps}, season {missing_sea}/{total_sea})")
                    return _fire_season()
                agent.log("debug", self.name, f"show_batch → EpisodeSearch (series {missing_eps}/{total_eps}, season {missing_sea}/{total_sea})")
                return _fire_episode()
            except Exception as exc:
                agent.log("debug", self.name, f"show_batch ratio check failed, falling back to EpisodeSearch: {exc}")
                return _fire_episode()

        elif mode == "smart" and series_id is not None and season_number is not None:
            try:
                eps = agent.http_get(f"/api/v3/episode?seriesId={series_id}&seasonNumber={season_number}&includeImages=false")
                total_eps = len(eps) if isinstance(eps, list) else 0
                missing_eps = sum(1 for e in (eps if isinstance(eps, list) else []) if not e.get("hasFile"))
                ratio = missing_eps / total_eps if total_eps > 0 else 1.0
                if ratio >= 0.5:
                    agent.log("debug", self.name, f"Smart: SeasonSearch (missing {missing_eps}/{total_eps} eps)")
                    return _fire_season()
                agent.log("debug", self.name, f"Smart: EpisodeSearch (missing {missing_eps}/{total_eps} eps)")
                return _fire_episode()
            except Exception as exc:
                agent.log("debug", self.name, f"Smart mode episode fetch failed, falling back to EpisodeSearch: {exc}")
                return _fire_episode()

        elif episode_id:
            return _fire_episode()

        return SearchResult(False)
```

**Achtung:** Die `_fire_*`-Helfer werfen bei HTTP-Fehlern weiter — das ist gewollt, `_trigger_search` fängt sie und liefert `SearchResult(False)`. Damit bleibt das bisherige Verhalten erhalten, dass abgelehnte Befehle keinen Eintrag erzeugen.

- [ ] **Step 6: Übersetzbarkeit prüfen**

Run: `python -c "import backend.skills.search_missing"`
Expected: keine Ausgabe

Run: `grep -n "success, title, item_type, stored_key" backend/skills/search_missing.py`
Expected: keine Treffer

- [ ] **Step 7: Commit**

```bash
git add backend/skills/base.py backend/skills/search_missing.py
git commit -m "fix: record the entity id actually searched, plus the *arr command id"
```

---

### Task 4: Umstellung von `search_upgrades`

**Files:**
- Modify: `backend/skills/search_upgrades.py:60-66` (Aufrufstelle), `:93-107` (`_trigger_upgrade`)

**Interfaces:**
- Consumes: `SearchResult` (Task 3), `history.insert_item(...)` (Task 2)
- Produces: nichts Neues

- [ ] **Step 1: `_trigger_upgrade` auf `SearchResult` umstellen**

Import erweitern: `from backend.skills.base import BaseSkill, SearchResult`

```python
    def _trigger_upgrade(self, agent, arr_type: str, item: dict) -> SearchResult:
        label = item.get("label") or item.get("title") or f"#{item['id']}"
        if arr_type == "radarr":
            movie_id = item["id"]
            resp = agent.http_post("/api/v3/command", {"name": "MoviesSearch", "movieIds": [movie_id]})
            return SearchResult(True, label, "movie", f"upg:{movie_id}", movie_id, resp.get("id"))

        series_id = item.get("series_id")
        season_number = item.get("season_number")
        episode_id = item["id"]
        if series_id is not None and season_number is not None:
            resp = agent.http_post(
                "/api/v3/command",
                {"name": "SeasonSearch", "seriesId": series_id, "seasonNumber": season_number},
            )
            return SearchResult(
                True, label, "season", f"upg:sea:{series_id}:{season_number}", series_id, resp.get("id")
            )

        resp = agent.http_post("/api/v3/command", {"name": "EpisodeSearch", "episodeIds": [episode_id]})
        return SearchResult(True, label, "episode", f"upg:{episode_id}", episode_id, resp.get("id"))
```

Der `cache_key` kommt jetzt aus dem Ergebnis statt aus einem separaten Aufruf von `_cache_key` — die Schlüsselbildung ist damit an genau einer Stelle und kann nicht mehr vom tatsächlich gefeuerten Befehl abweichen. `_cache_key` wird nach dieser Änderung nicht mehr benötigt und ist zu entfernen, sofern kein anderer Aufruf mehr darauf zeigt (`grep -n "_cache_key" backend/skills/search_upgrades.py`).

- [ ] **Step 2: Aufrufstelle anpassen**

Der Block liegt bereits in einem `try`, das Ausnahmen als „nicht ausgelöst" behandelt — dieser Rahmen bleibt. Zu ersetzen ist der Inhalt der Schleife `for item in candidates:` ab `item_id = item["id"]`. Vorher:

```python
                item_id = item["id"]
                label = item["label"]
                cache_key = self._cache_key(arr_type, item)

                try:
                    item_type = self._trigger_upgrade(agent, arr_type, item)
                    triggered_count += 1
                    agent.record_action()
                    db.history.insert_item(run_id, label, item_id, item_type)
                    db.searched.add(cfg["id"], cache_key, label, item_type)
                    agent.log("debug", self.name, f"Upgrade search: {label}")
                except Exception as exc:
                    agent.log("warn", self.name, f"Failed to trigger upgrade for {label}: {exc}")
```

Nachher:

```python
                label = item["label"]

                try:
                    result = self._trigger_upgrade(agent, arr_type, item)
                    triggered_count += 1
                    agent.record_action()
                    db.history.insert_item(
                        run_id,
                        result.title,
                        result.arr_id,
                        result.item_type,
                        cache_key=result.cache_key,
                        command_id=result.command_id,
                    )
                    db.searched.add(cfg["id"], result.cache_key, result.title, result.item_type)
                    agent.log("debug", self.name, f"Upgrade search: {result.title}")
                except Exception as exc:
                    agent.log("warn", self.name, f"Failed to trigger upgrade for {label}: {exc}")
```

`label` bleibt als lokale Variable erhalten, weil die `except`-Meldung sie braucht — `result` existiert dort nicht. `item_id` und `cache_key` entfallen, beides kommt jetzt aus `result`.

- [ ] **Step 3: Übersetzbarkeit prüfen**

Run: `python -c "import backend.skills.search_upgrades"`
Expected: keine Ausgabe

- [ ] **Step 4: Commit**

```bash
git add backend/skills/search_upgrades.py
git commit -m "fix: record command id for upgrade searches too"
```

---

### Task 5: Rohe HTTP-Abfrage und Verifikations-Skill

**Files:**
- Modify: `backend/agents/base.py:279-301` (nach `http_post` einfügen)
- Create: `backend/skills/verify_commands.py`

**Interfaces:**
- Consumes: `map_command_status`, `aggregate_run_status`, Item-Konstanten (Task 1); `history.get_pending_items`, `history.set_item_status`, `history.expire_stale_items`, `history.get_item_statuses`, `history.update_run_verification`, `searched.delete` (Task 2)
- Produces: `BaseAgent.http_get_raw(path: str) -> tuple[int, dict | None]`; `VerifyCommandsSkill` mit `name = "verify_commands"`; setzt `agent.state["last_verified"]`

- [ ] **Step 1: `http_get_raw` ergänzen**

In `backend/agents/base.py` direkt nach `http_post` einfügen:

```python
    def http_get_raw(self, path: str) -> tuple[int, dict | None]:
        """GET that reports the status code instead of raising on 4xx/5xx.

        Verification needs to tell "*arr does not know this command" (404,
        a real answer) apart from "*arr is unreachable" (retry later), which
        raise_for_status collapses into one exception. Returns status 0 for
        network-level failures.
        """
        url = self.config["url"].rstrip("/") + path
        try:
            resp = requests.get(
                url,
                headers={"X-Api-Key": self.config["api_key"]},
                timeout=10,
            )
        except requests.exceptions.RequestException:
            return 0, None

        if resp.status_code != 200:
            return resp.status_code, None
        try:
            return 200, resp.json()
        except ValueError:
            return 200, None
```

- [ ] **Step 2: Skill schreiben**

`backend/skills/verify_commands.py`:

```python
from backend.skills.base import BaseSkill
from backend import db
from backend.verification import (
    map_command_status,
    aggregate_run_status,
    ITEM_SUBMITTED,
    ITEM_COMPLETED,
    ITEM_FAILED,
)


class VerifyCommandsSkill(BaseSkill):
    """Resolve what *arr actually did with the commands we sent.

    Runs on its own schedule rather than at the end of a search run: commands
    are still queued when a run finishes, and blocking the run to wait would
    stall the agent for as long as *arr takes.
    """

    name = "verify_commands"

    MAX_PER_RUN = 50
    STALE_HOURS = 24

    def execute(self, agent, force: bool = False) -> None:
        instance_id = agent.config["id"]

        touched_runs = set(db.history.expire_stale_items(instance_id, self.STALE_HOURS))
        if touched_runs:
            agent.log(
                "warn",
                self.name,
                f"Gave up on commands still unresolved after {self.STALE_HOURS}h",
            )

        pending = db.history.get_pending_items(instance_id, self.MAX_PER_RUN)
        checked = 0
        confirmed = 0

        for item in pending:
            http_status, payload = agent.http_get_raw(f"/api/v3/command/{item['command_id']}")
            status = map_command_status(http_status, payload)
            if status == ITEM_SUBMITTED:
                continue  # still running, or *arr unreachable — try next pass

            db.history.set_item_status(item["id"], status)
            touched_runs.add(item["run_id"])
            checked += 1
            if status == ITEM_COMPLETED:
                confirmed += 1

            if status == ITEM_FAILED and item["cache_key"]:
                removed = db.searched.delete(instance_id, item["cache_key"])
                if removed:
                    agent.log(
                        "warn",
                        self.name,
                        f"Command {item['command_id']} failed in *arr — "
                        f"released '{item['cache_key']}' for another attempt",
                    )

        for run_id in touched_runs:
            statuses = db.history.get_item_statuses(run_id)
            db.history.update_run_verification(
                run_id,
                aggregate_run_status(statuses),
                statuses.count(ITEM_COMPLETED),
            )

        if checked:
            agent.log("info", self.name, f"Verified {checked} command(s), {confirmed} confirmed")
            agent.state["last_verified"] = confirmed
```

**Hinweis zu `last_verified`:** Der Wert ist die Zahl der in *diesem* Durchlauf bestätigten Befehle, nicht die des letzten Laufs. Für die Kachel „bestätigt / abgeschickt" ist das die passende Aussage, solange die Verifikation dem Lauf unmittelbar folgt. Task 9 setzt darauf auf.

- [ ] **Step 3: Übersetzbarkeit prüfen**

Run: `python -c "import backend.skills.verify_commands; import backend.agents.base"`
Expected: keine Ausgabe

- [ ] **Step 4: Commit**

```bash
git add backend/agents/base.py backend/skills/verify_commands.py
git commit -m "feat: add verify_commands skill resolving *arr command outcomes"
```

---

### Task 6: Sperre je Skill statt agent-weit

**Files:**
- Modify: `backend/agents/base.py:18-40` (`__init__`), `:132-178` (`_run_skill`)

**Interfaces:**
- Consumes: nichts
- Produces: `BaseAgent._skill_lock(name: str) -> threading.Lock`

Die bestehende Sperre nutzt `self.state["status"]`, das sich alle Skills eines Agenten teilen. Nachweisbare Folge im Bestand: 166 Meldungen `Already running — skipping duplicate trigger` für den `health_check`, jeweils zehn Sekunden nach Beginn eines Suchlaufs. Ohne diese Änderung träfe es `verify_commands` genauso.

- [ ] **Step 1: Sperren-Verwaltung ergänzen**

In `__init__` nach `self._lock = threading.Lock()` einfügen:

```python
        # One lock per skill. state["status"] is a display value shared by the
        # whole agent and must not double as a mutex — doing so let a running
        # search block the health check.
        self._skill_locks: dict[str, threading.Lock] = {}
        self._skill_locks_guard = threading.Lock()
```

Als Methode ergänzen:

```python
    def _skill_lock(self, skill_name: str) -> threading.Lock:
        with self._skill_locks_guard:
            return self._skill_locks.setdefault(skill_name, threading.Lock())
```

- [ ] **Step 2: `_run_skill` umbauen**

Den Block ab dem Kommentar `# Guard against concurrent runs of the same skill.` bis zum Ende der Methode ersetzen durch:

```python
        # Guard against concurrent runs of the same skill. Force triggers wait
        # up to 90 s for an active run of that skill to finish; scheduled
        # triggers are dropped immediately.
        lock = self._skill_lock(skill_name)
        deadline = time.monotonic() + (90 if force else 0)
        acquired = lock.acquire(blocking=False)
        while not acquired and time.monotonic() < deadline:
            time.sleep(1)
            acquired = lock.acquire(blocking=False)
        if not acquired:
            self.log("warn", skill_name, "Already running — skipping duplicate trigger")
            return

        # Only the search skills drive the dashboard status; health_check and
        # verify_commands run alongside them and must not make the card flicker.
        drives_display = skill_name in ("search_missing", "search_upgrades")

        try:
            if drives_display:
                self.state["status"] = "running"
            skill.execute(self, force=force)
        except Exception as exc:
            self.log("error", skill_name, f"Unhandled exception: {exc}")
        finally:
            if drives_display:
                self.state["status"] = "scheduled"
                self._update_next_run()
            lock.release()
```

`self._lock` bleibt unverändert — es schützt weiterhin `_action_timestamps` für die Ratenbegrenzung.

- [ ] **Step 3: Verhalten prüfen**

```bash
python - <<'PY'
import threading, time, types
from backend.agents.base import BaseAgent

class Dummy(BaseAgent):
    def build_skills(self): return []
    def log(self, *a, **k): print("LOG:", a[2])

a = Dummy({"id": 1, "name": "t", "type": "sonarr", "url": "http://x", "api_key": ""})

class Slow:
    name = "search_missing"
    def execute(self, agent, force=False): time.sleep(2)
class Fast:
    name = "health_check"
    def execute(self, agent, force=False): print("health_check RAN")

a._skills = [Slow(), Fast()]
threading.Thread(target=a._run_skill, args=["search_missing"]).start()
time.sleep(0.5)
a._run_skill("health_check")          # must run despite the search
a._run_skill("search_missing")        # must be skipped
time.sleep(2)
PY
```

Expected: `health_check RAN` erscheint, danach `LOG: Already running — skipping duplicate trigger` für den zweiten `search_missing`. Vor dieser Änderung wäre der `health_check` übersprungen worden.

- [ ] **Step 4: Commit**

```bash
git add backend/agents/base.py
git commit -m "fix: lock per skill so health checks and verification are not blocked by searches"
```

---

### Task 7: Skill registrieren und einplanen

**Files:**
- Modify: `backend/agents/sonarr.py`, `backend/agents/radarr.py`
- Modify: `backend/agents/base.py:114-122` (nach dem health-check-Job)

**Interfaces:**
- Consumes: `VerifyCommandsSkill` (Task 5)
- Produces: Scheduler-Job `verify_{instance_id}`, Intervall 2 Minuten

- [ ] **Step 1: In beiden Agenten registrieren**

In `backend/agents/sonarr.py` und `backend/agents/radarr.py` jeweils:

```python
from backend.skills.verify_commands import VerifyCommandsSkill
```

und die Rückgabe von `build_skills` erweitern:

```python
        return [SearchMissingSkill(), SearchUpgradesSkill(), HealthCheckSkill(), VerifyCommandsSkill()]
```

- [ ] **Step 2: Job einplanen**

In `backend/agents/base.py._run`, direkt nach dem `health_`-Job:

```python
        # Verification every 2 minutes. Runs regardless of the search-enabled
        # flags: entries submitted before a skill was switched off would stay
        # unresolved forever otherwise.
        self._scheduler.add_job(
            self._run_skill,
            "interval",
            minutes=2,
            args=["verify_commands"],
            id=f"verify_{cfg['id']}",
            next_run_time=datetime.now(timezone.utc) + timedelta(seconds=30),
        )
```

- [ ] **Step 3: Anwendung starten und Log beobachten**

```bash
docker compose -f docker-compose.yml up -d --build
docker logs -f missingarr 2>&1 | grep -i "verify"
```

Expected: innerhalb von zwei Minuten Einträge zum `verify_commands`-Job, keine Ausnahmen.

- [ ] **Step 4: Commit**

```bash
git add backend/agents/sonarr.py backend/agents/radarr.py backend/agents/base.py
git commit -m "feat: schedule command verification every two minutes"
```

---

### Task 8: History zeigt den belegten Ausgang

**Files:**
- Modify: `templates/history.html:84-90` (thead), `:123-133` (Status-Badge)

**Interfaces:**
- Consumes: `command_status` aus `/api/history/items` (Task 2, Step 5)
- Produces: nichts

- [ ] **Step 1: Spalte im Tabellenkopf ergänzen**

Nach `<th>Status</th>` einfügen:

```html
                    <th>Verified</th>
```

- [ ] **Step 2: Lauf-Status-Badge um die neuen Werte erweitern**

Den `:class`-Block des Status-Badges ersetzen:

```html
                            <span class="badge"
                                :class="{
                                    'badge-online': row.status === 'success',
                                    'badge-offline': row.status === 'error' || row.status === 'failed',
                                    'badge-error': row.status === 'partial',
                                    'badge-scheduled': row.status === 'pending',
                                    'badge-unknown': row.status === 'unverified',
                                    'badge-running': row.status === 'running'
                                }"
                                x-text="row.status">
                            </span>
```

- [ ] **Step 3: Zelle für den Item-Ausgang ergänzen**

Direkt nach der `<td>` mit dem Status-Badge einfügen:

```html
                        <td>
                            <template x-if="row.command_status === 'legacy'">
                                <span style="color:var(--text-muted);">—</span>
                            </template>
                            <template x-if="row.command_status !== 'legacy'">
                                <span class="badge"
                                    :class="{
                                        'badge-online': row.command_status === 'completed',
                                        'badge-offline': row.command_status === 'failed',
                                        'badge-scheduled': row.command_status === 'submitted',
                                        'badge-unknown': row.command_status === 'expired'
                                    }"
                                    x-text="row.command_status">
                                </span>
                            </template>
                        </td>
```

Altzeilen zeigen einen Strich statt eines Zustands — sie tragen keinen Beleg, also behauptet die Oberfläche für sie nichts.

- [ ] **Step 4: Im Browser prüfen**

`/history` öffnen. Erwartung: Altzeilen mit `—` in der Spalte *Verified*, nach dem nächsten Suchlauf neue Zeilen erst mit `submitted`, binnen zwei Minuten auf `completed` wechselnd. Der Lauf-Status geht von `pending` auf `success`.

- [ ] **Step 5: Commit**

```bash
git add templates/history.html
git commit -m "feat: show the verified outcome per history item"
```

---

### Task 9: Kachel zeigt bestätigt gegen abgeschickt

**Files:**
- Modify: `templates/instances/card.html:111-114`
- Modify: `static/js/app.js:219-221`

**Interfaces:**
- Consumes: `state["last_verified"]` (Task 5)
- Produces: nichts

- [ ] **Step 1: Kachel anpassen**

Den Block `Triggered (last)` ersetzen:

```html
            <div class="stat-item">
                <div class="stat-label">Confirmed / Sent</div>
                <div class="stat-value">
                    <span data-stat="last_verified">{{ state.get('last_verified', 0) if state else 0 }}</span>
                    <span style="color:var(--text-muted);font-weight:400;"> / </span>
                    <span data-stat="last_triggered">{{ state.get('last_triggered', 0) if state else 0 }}</span>
                </div>
            </div>
```

- [ ] **Step 2: Live-Aktualisierung ergänzen**

In `static/js/app.js` bei den `data-stat`-Zweigen ergänzen:

```javascript
            else if (key === 'last_verified') el.textContent = state.last_verified ?? '-';
```

- [ ] **Step 3: Im Browser prüfen**

Dashboard öffnen, Force-Run auslösen. Erwartung: unmittelbar `0 / 4`, binnen zwei Minuten `4 / 4`.

- [ ] **Step 4: Commit**

```bash
git add templates/instances/card.html static/js/app.js
git commit -m "feat: dashboard distinguishes confirmed from sent searches"
```

---

### Task 10: Abnahme gegen die laufende Instanz

**Files:** keine Änderungen — reine Verifikation

- [ ] **Step 1: Tests**

Run: `python -m pytest tests/ -v`
Expected: alle grün

- [ ] **Step 2: Mitschnitt während eines echten Laufs**

Dasselbe Verfahren, mit dem die Befunde belegt wurden. `/tmp/watch.py` im Container:

```python
import sqlite3, requests, time
from datetime import datetime
from backend import crypto

c = sqlite3.connect('/data/missingarr.db'); c.row_factory = sqlite3.Row
inst = [(r['name'], r['url'].rstrip('/'), crypto.decrypt(r['api_key']))
        for r in c.execute('select name,url,api_key from instances')]
SEARCH = {'EpisodeSearch','SeasonSearch','SeriesSearch','MoviesSearch'}
seen = {}
end = time.time() + 420
while time.time() < end:
    for name, base, key in inst:
        try:
            cmds = requests.get(base + '/api/v3/command', headers={'X-Api-Key': key}, timeout=10).json()
        except Exception:
            continue
        for cmd in cmds:
            if cmd.get('name') not in SEARCH:
                continue
            k = (name, cmd.get('id'))
            sig = (cmd.get('status'), cmd.get('result'))
            if seen.get(k) != sig:
                seen[k] = sig
                body = cmd.get('body', {}) or {}
                ids = body.get('episodeIds') or body.get('movieIds') or body.get('seriesId')
                print(f"[{datetime.now():%H:%M:%S}] {name} cmd#{cmd.get('id')} {cmd.get('name')} "
                      f"status={cmd.get('status')} ids={ids}", flush=True)
    time.sleep(3)
print(f"Ende. {len(seen)} Suchbefehle beobachtet.", flush=True)
```

```bash
docker cp /tmp/watch.py missingarr:/tmp/watch.py
docker exec -w /app -e PYTHONPATH=/app missingarr python /tmp/watch.py
```

- [ ] **Step 3: Beide Seiten abgleichen**

```bash
docker exec -w /app -e PYTHONPATH=/app missingarr python -c "
import sqlite3
c = sqlite3.connect('/data/missingarr.db'); c.row_factory = sqlite3.Row
for r in c.execute('''
  SELECT h.instance_name, h.status, h.triggered_count, h.verified_count,
         i.title, i.arr_id, i.item_type, i.command_id, i.command_status
  FROM search_history_items i JOIN search_history h ON h.id = i.run_id
  WHERE i.command_status != 'legacy' ORDER BY i.id DESC LIMIT 20'''):
    print(f\"{r['instance_name']:8s} run={r['status']:10s} {r['triggered_count']}/{r['verified_count']} \"
          f\"| cmd#{r['command_id']} {r['command_status']:10s} [{r['item_type']:7s}] \"
          f\"arr_id={r['arr_id']} {r['title'][:40]}\")
"
```

Erwartung, jeweils gegen den Mitschnitt geprüft:

1. Jede Zeile hat eine `command_id`, die im Mitschnitt vorkommt.
2. `arr_id` stimmt mit den `ids` des zugehörigen Befehls überein — bei `item_type='series'` die Serien-ID, nicht die Episoden-ID. Das ist der Kern von Befund B1.
3. `command_status` steht binnen zwei Minuten auf `completed`.
4. Der Lauf steht auf `success` mit `verified_count == triggered_count`.

- [ ] **Step 4: Gegenprobe für den Fehlerfall**

Belegen, dass ein gescheiterter Befehl den Cache freigibt — ohne auf einen echten Fehlschlag zu warten:

```bash
docker exec -w /app -e PYTHONPATH=/app missingarr python -c "
from backend import db
from backend.verification import ITEM_FAILED
items = db.history.get_pending_items(2, 1) or None
print('offene Items:', items)
"
```

Ist kein offenes Item vorhanden, stattdessen ein bereits verifiziertes Item künstlich auf `submitted` mit einer nicht existierenden `command_id` setzen und einen Verifikationsdurchlauf abwarten. Erwartung: Status wird `expired` (nicht `failed`), der Cache-Eintrag bleibt **erhalten** — unbekannter Ausgang ist kein Fehlschlag.

- [ ] **Step 5: Version anheben und committen**

`VERSION` auf `0.7.0` setzen (neue Spalten, neuer Skill, geänderte Statusbedeutung — kein reiner Patch).

```bash
git add VERSION
git commit -m "chore: bump version to 0.7.0"
```

---

## Selbstprüfung gegen die Spec

| Spec-Abschnitt | Umgesetzt in |
|---|---|
| 1. Datenmodell | Task 2, Steps 1–2 |
| 2. Rückgabewert des Trigger-Pfads | Task 3, Steps 1–5; Task 4 |
| 3. Verifikation als eigener Skill | Task 5, Step 2; Task 7 |
| 4. Selbstheilung bei Fehlschlag | Task 2, Step 6; Task 5, Step 2 |
| 5. Lauf-Status folgt der Verifikation | Task 1 (`aggregate_run_status`); Task 2, Step 4 |
| 6. Sperre auf Skill-Ebene | Task 6 |
| 7. Anzeige — History | Task 8 |
| 7. Anzeige — Dashboard | Task 9 |
| Testbarkeit | Task 1; Task 10 |

**Offen und bewusst nicht umgesetzt:** Die Reparatur der 7.447 falschen `arr_id` in Altzeilen — laut Spec ein Nicht-Ziel. Die Zeilen tragen `command_status='legacy'` und werden in der Oberfläche als nicht prüfbar ausgewiesen.

**Nicht durch Tests abgedeckt:** Der Skill selbst, die Datenbankzugriffe und die Oberfläche. Das Projekt hat keine Testinfrastruktur für HTTP oder SQLite, und diese Änderung ist nicht der Ort, eine einzuführen. Abgedeckt sind die beiden Entscheidungsregeln, in denen die eigentliche Wahrheitsfrage steckt; alles andere wird in Task 10 gegen die laufende Instanz abgenommen.
