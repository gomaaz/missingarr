# Codex-Review-Befunde beheben — Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Alle 53 geprüften Befund-IDs aus dem Codex-Review (49 eigenständige Befunde, 4 Duplikate) sind in missingarr 0.8.0 behoben, jede Behebung ist durch einen Test belegt, und die heutige Live-Konfiguration (Sonarr + Radarr, 0.7.0) läuft nach dem Update ohne Handarbeit weiter.

**Architecture:** Die Arbeit ist in sechs Pakete mit fester Dateizuständigkeit geschnitten (P1 Agent/Scheduler, P2 Suchlogik, P3 Datenbank/Verifikation, P4 Web/Auth, P5 Frontend, P6 Build). Kein Paket ändert eine Datei eines anderen Pakets. Die Pakete laufen in drei Wellen: Welle 1 (P1, P3a, P6 parallel) legt die Grundlagen, auf denen Welle 2 (P2, P3b, P4 parallel) aufbaut; Welle 3 (P5) baut die Oberfläche auf den fertigen Server-Schnittstellen. Parallele Pakete arbeiten je in einem eigenen Git-Worktree auf einem eigenen Zweig und werden am Wellenende nacheinander in `fix/codex-review` gemergt. Ein kurzer Task 0 vorher und ein Abschluss-Task Z danach laufen allein. Die Pakete sprechen nur über den Schnittstellenvertrag in diesem Dokument miteinander.

**Tech Stack:** Python 3.12, FastAPI 0.141 / Starlette 1.6, APScheduler 3.11, SQLite (stdlib `sqlite3`), `requests`, `cryptography` (Fernet, HKDF), `bcrypt` (ersetzt `passlib`), Jinja2, Alpine.js 3.14.1, htmx 2.0.4. Tests: `pytest`, neu `httpx2` für `fastapi.testclient.TestClient` (Starlette 1.6 verlangt es), Node.js (vorhanden, v24) für zwei JavaScript-Tests.

## Global Constraints

- Live läuft 0.7.0 = `main` (1c05737). Gearbeitet wird auf Branch `fix/codex-review`. Am Ende steht Version `0.8.0` in `VERSION`.
- Die Live-Datenbanken `/root/docker/missingarr/data` und `/root/missingarr/data` werden **nie** geöffnet, gelesen oder kopiert. Der laufende Container wird nicht angesprochen (kein `exec`, kein `stop`, kein Request an Port 8000).
- Jeder Test benutzt eine eigene SQLite-Datei unter `tmp_path`. Kein Test darf `backend.main` auf Modulebene importieren; der Import passiert erst in einer Fixture, nachdem `settings.database_url` auf `tmp_path` umgebogen ist (Grund: bis P4 fertig ist, öffnet `import backend.main` die Datenbank aus `settings.database_url`, und der Standardwert zeigt auf `./data/missingarr.db`).
- In Tests nie `monkeypatch.undo()` aufrufen: das setzt auch `settings.database_url` auf `./data/missingarr.db` zurück, und die nächste Abfrage im selben Test träfe die echte Entwicklungs-DB. Einzelne Attribute werden mit einem zweiten `monkeypatch.setattr(…, original)` zurückgesetzt.
- Tests nur in neuen Dateien `tests/test_<paket>_*.py` (z. B. `tests/test_p3_history.py`), jede Datei mit eigenen Fixtures. Keine `conftest.py`. Die vorhandenen Dateien `tests/test_database_regressions.py` und `tests/test_verification.py` bleiben unverändert und grün.
- Testbefehl für alles: `cd /root/missingarr && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`. Vor Beginn: 22 grün.
- **Worktrees in Welle 1 und 2.** Parallele Pakete teilen sich weder Arbeitsbaum noch Index: sonst wäre die Gesamtsuite rot, sobald das Nachbarpaket nach TDD-Step 1 einen absichtlich roten Test hat, und `git commit` nähme gestagte Dateien des Nachbarn mit (dazu `index.lock`-Kollisionen). Deshalb zu Beginn jeder Welle im Haupt-Arbeitsbaum, für jedes Paket `<p>` der Welle (Welle 1: `p1`, `p3a`, `p6`; Welle 2: `p2`, `p3b`, `p4`):
  `git -C /root/missingarr worktree add /root/missingarr-wt/<p> -b fix/codex-review-<p> fix/codex-review`
  Im Worktree gilt: jedes `cd /root/missingarr` in den Tasks heißt `cd /root/missingarr-wt/<p>`, und jedes `.venv/bin/…` heißt `/root/missingarr/.venv/bin/…` (eine gemeinsame `.venv`, nur Task 0 und Task Z installieren etwas hinein). Die Gesamtsuite im Worktree sieht nur die eigenen Änderungen auf dem Stand des Wellenbeginns und bleibt deshalb aussagekräftig. Commits nur im eigenen Worktree.
- **Wellen-Abnahme.** Am Ende einer Welle im Haupt-Arbeitsbaum `/root/missingarr` (Zweig `fix/codex-review`) die Paketzweige nacheinander mergen (`git merge --no-ff fix/codex-review-<p>`; wegen der Dateizuständigkeit ohne Konflikte), dann die Gesamtsuite. Erst wenn sie grün ist: `git worktree remove /root/missingarr-wt/<p>` und `git branch -d fix/codex-review-<p>` je Paket. Welle 3 (nur P5), Task 0 und Task Z laufen direkt im Haupt-Arbeitsbaum.
- **Scratchpad.** Befehle, die Wegwerf-Dateien brauchen, setzen am Anfang jedes Codeblocks selbst `SCRATCH=/tmp/claude-0/-root/b5f0b5e7-3436-4d16-9f25-cd6f007f969c/scratchpad` und prüfen `test -d "$SCRATCH"` (Umgebungsvariablen überleben zwischen zwei Bash-Aufrufen nicht; ein leeres `$SCRATCH` hieße Ordner unter `/` anlegen und dort löschen). Wer den Plan in einer anderen Sitzung ausführt, ersetzt den Pfad in allen Blöcken durch das eigene Scratchpad.
- Dateien schreiben nur mit dem Write- bzw. Edit-Werkzeug, nie per Heredoc (`cat <<EOF`).
- Diese Live-Werte müssen nach dem Update ohne Änderung gültig sein und genauso wirken: Sonarr `interval_minutes=60`, `missing_mode=episode`, `missing_per_run=4`, `rate_cap=300`, `rate_window_minutes=60`, Upgrades aus, `retry_hours=0`. Radarr `interval_minutes=30`, `missing_per_run=600`, `rate_cap=999999999`, Upgrades aus, `retry_hours=0`. Unbekannte Felder können jeden bisher speicherbaren Wert haben, auch `0` bei `*_per_run` eines abgeschalteten Skills.
- Diese dokumentierte Nutzung muss weiter funktionieren: `POST /login` mit curl (Formular, ohne `Origin`), Cookie weiterreichen, dann `POST /api/instances/<id>/trigger?skill=search_missing&force=false` ohne `Origin`-Header. Antwort bei Erfolg unverändert `200 {"status": "triggered", "skill": "search_missing"}`.
- Kein `sortKey`/`sortDirection` an Sonarr/Radarr senden. Die Commits 8a1a912 und c68178e haben das entfernt, weil *arr damit Einträge ohne Datum verschluckt.
- Private Netze (10/8, 172.16/12, 192.168/16, 127/8, Docker-Hostnamen) bleiben als Instanz-URL erlaubt. Sie sind der Zweck der App.
- Grenzen für Eingaben gelten nur in `InstanceCreate`/`InstanceUpdate`. Werte, die schon in der DB stehen, werden beim Lesen nie abgelehnt.
- Log-Meldungen und Oberflächentexte bleiben englisch wie im Bestand. Die deutschen Badge-Beschriftungen der History-Seite bleiben.
- Zeitstempel in der DB sind Ortszeit (`datetime('now','localtime')`, Container-TZ `Europe/Berlin`). Zeitangaben von *arr (`airDateUtc`, `digitalRelease` …) sind UTC. Wer beide vergleicht, rechnet vorher auf UTC um (`db.searched.local_to_utc`).
- Commit-Nachrichten englisch im Stil des Repos (`fix: …`, `feat: …`, `test: …`, `build: …`), Trailer nach der Vorgabe der ausführenden Sitzung.
- Nichts wird getaggt, nichts nach `main` gemergt. Ein Tag `v*` würde über `.github/workflows/docker-publish.yml` ein Image veröffentlichen. Am Ende wird nur der Branch gepusht (Task Z).

---

## Wellen und Dateizuständigkeit

| Schritt | Paket | Dateien (nur dieses Paket ändert sie) |
|---|---|---|
| vorab, allein | Task 0 (P6-Datei) | `requirements-dev.txt` |
| Welle 1 | **P1 Agent/Scheduler** | `backend/agents/base.py`, `backend/agents/orchestrator.py`, `backend/agents/sonarr.py`, `backend/agents/radarr.py`, `backend/models/instance.py`, `tests/test_p1_*.py` |
| Welle 1 | **P3a Datenbank** (P3.1, P3.2, P3.3, P3.6) | `backend/database.py`, `backend/config.py`, `backend/db/*.py` (inkl. neu `backend/db/app_settings.py`), `backend/api/history.py`, `backend/api/searched.py`, `tests/test_p3_database.py`, `tests/test_p3_searched.py`, `tests/test_p3_history.py`, `tests/test_p3_api.py` |
| Welle 1 | **P6 Build** | `Dockerfile`, `requirements.txt`, neu `requirements.lock`, neu `docker-entrypoint.sh`, `.github/workflows/docker-publish.yml`, `docker-compose.yml`, `.env.example`, `README.md`, `VERSION`, neu `static/vendor/**`, neu `scripts/vendor_assets.py`, `tests/test_p6_*.py` |
| Welle 2 | **P2 Suchlogik** | `backend/skills/search_missing.py`, `backend/skills/search_upgrades.py`, `backend/skills/health_check.py`, `backend/skills/base.py`, `tests/test_p2_*.py` |
| Welle 2 | **P3b Verifikation** (P3.4, P3.5) | `backend/verification.py`, `backend/db/history.py`, `backend/skills/verify_commands.py`, `tests/test_p3_verify.py`, `tests/test_p3_housekeeping.py` |
| Welle 2 | **P4 Web/Auth** | `backend/main.py`, `backend/auth.py`, `backend/crypto.py`, `backend/log_broadcaster.py`, `backend/api/instances.py`, `backend/api/health.py`, `backend/api/activity.py` (ganz), `tests/test_p4_*.py` |
| Welle 3 | **P5 Frontend** | `templates/**`, `static/js/**`, `static/css/**`, `backend/tooltips.py`, `tests/test_p5_*.py` |
| danach, allein | Task Z | Aufräumen in P1-/P3-Dateien, Container-Probe, Abnahme, Push |

Welle 2 startet erst, wenn alle Pakete von Welle 1 grün sind, in `fix/codex-review` gemergt sind und die Gesamtsuite dort grün läuft (Wellen-Abnahme, siehe Global Constraints). Welle 3 genauso nach Welle 2.

P3.4 und P3.5 brauchen `agent.stop_requested()`/`request_abort()` aus P1.4. In getrennten Worktrees sieht P3 das erst nach dem Merge von Welle 1; deshalb laufen sie als eigenes Paket P3b in Welle 2. In Welle 2 ändert kein anderes Paket `backend/db/history.py`, `backend/verification.py` oder `backend/skills/verify_commands.py`, und weder P2 noch P4 brauchen etwas aus P3.4/P3.5.

**Wo die vorgegebene Aufteilung nicht aufging, und wie es gelöst ist:**

1. `backend/api/activity.py` stand in P3 (Query-Grenzen) und P4 (SSE-Teil). Die Datei hat 67 Zeilen, der P3-Anteil sind zwei Parameterzeilen. Ein Teilen der Datei hätte eine Verschiebung der Route mit Zwischenstand ohne Route zwischen den Wellen bedeutet. Lösung: **P4 besitzt `activity.py` ganz** und setzt dort auch die Grenzen aus B9 um, nach dem Muster, das P3 in `history.py`/`searched.py` vorgibt.
2. `backend/config.py` stand in P4, aber P3 braucht in Welle 1 die neue Einstellung `HISTORY_RETENTION_DAYS`. Lösung: **P3 besitzt `config.py`** und legt dort alle drei neuen Einstellungen an (`secret_key` mit leerem Standard, `cookie_secure`, `history_retention_days`). P4 liest sie nur. `secret_key` wird heute nirgends benutzt, das Ändern des Standards in Welle 1 hat keine Wirkung.
3. `requirements-dev.txt` gehört zu P6, aber P3 und P4 brauchen `httpx2` für `TestClient` schon in ihren ersten Tests. Lösung: **Task 0** fügt `httpx2` vor Welle 1 hinzu.
4. `backend/tooltips.py` war keinem Paket zugeordnet. Die Texte gehören zur Oberfläche (Formular-Tooltips). Lösung: **P5 besitzt `tooltips.py`**.
5. `backend/db/instances.py` liegt unter `backend/db/*` und damit bei P3. Es braucht keine Änderung: das Maskieren des Schlüssels macht P4 in der API-Schicht (`public_instance`), `db.instances.update` behält den Schlüssel schon heute, wenn `api_key` leer ist.
6. Einige Befunde brauchen zwei Seiten (Server und Oberfläche, Agent und Skill). Jede Seite steht im Paket der Datei; die Befundtabelle nennt beide.
7. Übergangsmethoden: P1 führt `reserve_action()` ein, P2 stellt die Skills erst in Welle 2 um. Deshalb bleiben `check_rate_cap()`/`record_action()` bis Task Z als dünne Hüllen stehen. Ebenso bleiben `db.searched.exists()`/`exists_any()` bis Task Z. Task Z entfernt, was dann keinen Aufrufer mehr hat.

---

## Befund → Paket → Datei → Änderung

Duplikate sind zusammengeführt: **C-L1 = A2**, **C-L4 = A1**, **C-L2 = A-L1** (zweite Stelle `api/instances.py`), **C-L5 ≈ B-L5** (C-L5 ergänzt die 500er-Kappung der Progressed-Seite). Teil A von B-L2 ist laut Prüfer ein Duplikat von B2 und wird dort behoben.

| ID | Paket | Datei(en) | Konkrete Änderung | Task |
|---|---|---|---|---|
| A1 (= C-L4) | P1, P5 | `models/instance.py`, `agents/base.py`, `templates/instances/form.html` | `FIELD_BOUNDS` + Model-Validator nur in Create/Update (`interval_minutes` 1..10080, `rate_cap` 1..1e9, `*_per_run` ≥1 nur wenn Skill an). `_run`: Scheduler-Aufbau in `try`, bei Fehler `status="error"` + Logzeile, „Agent started“ erst nach `scheduler.start()`. Gespeichertes Intervall außerhalb 1..10080 wird zur Laufzeit geklemmt. Formular `min`/`max`. | P1.1, P1.2, P5.5 |
| A2 (= C-L1) | P1, P4 | `agents/base.py`, `agents/orchestrator.py`, `api/instances.py` | Beide Such-Jobs immer registrieren, `_run_skill` gated über frisches Flag. `toggle-skill` ruft `orchestrator.refresh_config(id)` (kein Neustart, laufender Lauf bleibt). | P1.2, P4.5 |
| A3 | P2 | `skills/search_missing.py` | `newest_first`/`oldest_first`/`smart`: ganze Wanted-Liste in Seiten zu 1000 holen, lokal sortieren, dann der Reihe nach Kandidaten ziehen. | P2.2 |
| A4 | P2 | `skills/search_missing.py` | Globale Sortierung nach Freigabedatum über die ganze Liste; `smart` mischt die ganze Liste im Muster 5 neu / 3 zufällig / 2 alt, ohne Einträge zu verwerfen. | P2.2 |
| A5 | P2 | `skills/search_upgrades.py` | Keine 10-Seiten-Grenze. Unbesuchte Zufallsseiten nachladen, Cache-Filter pro Seite, bis `per_run` Kandidaten oder Seitenbudget (20) erreicht. | P2.4 |
| A6 | P2, P3 | `skills/base.py`, `skills/search_*.py`, `db/history.py` | Gescheiterte Einreichung wird als Item mit `command_status='failed'` ohne `command_id` gespeichert (`record_failed_submission`), nicht gecacht. Alle gescheitert → Lauf `error`; teilweise → Lauf läuft normal weiter, `error_message` nennt Zahl und ersten Fehler, Verifikation ergibt dann `partial`. | P3.3, P2.1 |
| A7 | P2 | `skills/search_upgrades.py` | Sammler melden Fehler je Quelle. Alle Quellen gescheitert → Lauf `error`, `last_sync` bleibt. Eine Quelle gescheitert → weiter mit der anderen, Fehlertext am Lauf. | P2.4 |
| A8 | P2 | `skills/search_missing.py` | Dichte nur über `monitored` und bereits ausgestrahlte Folgen; `show_batch` rechnet das Serien-Verhältnis ohne Staffel 0; kein relevantes Material → EpisodeSearch. | P2.3 |
| A9 | P2, P3 | `skills/search_missing.py`, `db/searched.py`, `database.py` | Sonarr prüft immer `ep:`, `sea:`, `ser:`. Eigener Schlüssel sperrt immer, Vorfahren-Schlüssel nur, wenn `searched_at` (UTC-umgerechnet) nach Ausstrahlung + `hours_after_release` liegt **und** nicht vor `ancestor_rule_since` (Zeitpunkt des ersten Starts von 0.8.0, in `app_settings`). So sperren die ~6.700 alten `ser:`- und ~730 `sea:`-Zeilen den Live-Modus `episode` nicht wieder (offene Frage 7). `lookup_many` liefert die Zeitstempel. | P3.1, P3.2, P2.2, P2.3 |
| A10 | P1, P2 | `agents/base.py`, `skills/base.py` | `reserve_action()` prüft und trägt unter einer Sperre ein, `release_action(token)` gibt bei gescheitertem POST zurück. Skills nutzen nur noch das. | P1.3, P2.1 |
| A11 | P1, P4, P5 | `agents/base.py`, `agents/orchestrator.py`, `api/instances.py`, `static/js/app.js`, `templates/instances/card.html` | Kein 90-s-Warten mehr. `trigger_now` gibt `"busy"` zurück, wenn der Skill läuft; API antwortet 409; Karte zeigt Hinweis und sperrt FORCE während `running`. | P1.4, P4.5, P5.3 |
| A12 | P3, P2 | `db/history.py`, `skills/base.py` | `record_submission` cacht nur mit `command_id`. Skill loggt eine Warnung, wenn die ID fehlt. | P3.3, P2.1 |
| A13 | P1, P5 | `agents/base.py`, `templates/instances/card.html` | `next_run_at` = frühester Lauf der aktiven Such-Jobs (Missing oder Upgrades), neu berechnet nach Start, nach jedem Suchlauf und bei `refresh_config`. Kartenbeschriftung „Next search“. | P1.2, P5.3 |
| B1 | P3 | `database.py` | Zeile `UPDATE instances SET retry_hours=0 WHERE retry_hours IN (1, 168)` ersatzlos weg. | P3.1 |
| B2 | P1, P3, P2 | `agents/base.py`, `database.py`, `db/history.py`, `skills/base.py` | `BaseAgent.log` wirft nie. `record_submission` schreibt Item + Cache in einer Transaktion. `busy_timeout` 30 s. Speicherfehler nach erfolgreichem POST zählt als gesendet und wird mit Befehls-ID geloggt. | P1.3, P3.1, P3.3, P2.1 |
| B3 | P3 | `db/history.py`, `skills/verify_commands.py` | `resolve_item()` setzt Status und gibt den Cache in einer Transaktion frei. | P3.4 |
| B4 | P3 | `database.py`, `db/history.py` | Neue Spalte `search_history_items.last_checked_at`; `get_pending_items` sortiert ungeprüfte zuerst, dann nach ältester Prüfung. | P3.1, P3.4 |
| B5 | P1, P3 | `agents/base.py`, `db/history.py`, `skills/verify_commands.py` | `verify_commands` ist von der Ruhezeit ausgenommen. Ablauf nach 24 h nur, wenn *arr das Item nach Ablauf der 24 h mindestens einmal beantwortet hat (nur HTTP 200 mit JSON-Objekt zählt; 401/403/3xx/5xx eines Proxys vor einem toten *arr nicht); erst abfragen, dann ablaufen lassen. | P1.4, P3.4 |
| B6 | P2, P3 | `skills/search_missing.py`, `db/searched.py`, `skills/verify_commands.py` | Vorfahren-Regel wie A9. Bei `retry_hours>0` löscht die stündliche Hauspflege abgelaufene Cache-Zeilen (`purge_expired`). | P3.2, P3.5, P2.3 |
| B7 | P3, P6 | `config.py`, `db/history.py`, `database.py`, `skills/verify_commands.py`, README | Env `HISTORY_RETENTION_DAYS` (Standard 365, 0 = nie). Stündlich je Instanz abgeschlossene Läufe älter als N Tage löschen (Items per CASCADE). Index `(instance_id, started_at DESC, id DESC)`. | P3.1, P3.3, P3.5, P6.4 |
| B8 | P3 | `database.py` | Migrationen über `PRAGMA table_info`, nur `duplicate column name` wird geschluckt, danach Pflichtspalten prüfen, sonst Start mit klarer Meldung abbrechen. | P3.1 |
| B9 | P3, P4 | `api/history.py`, `api/searched.py`, `api/activity.py` | `limit`/`offset` mit `Query(ge=…, le=…)`; negative Werte → 422. | P3.6, P4.7 |
| B10 | alle | `tests/test_p*_*.py` | Tests für Verifikation, Cache-Freigabe, Alt-DB-Migration mit Items, Fehlerinjektion an jeder Umbau-Anweisung, Ruhezeit, Agent-Pfade. | alle |
| C1 | P4, P5 | `api/instances.py`, `main.py`, `templates/instances/form.html` | `public_instance()`: `api_key` = `"********"`, `api_key_set: bool`, in allen API-Antworten und Template-Kontexten. PUT mit leerem oder maskiertem Schlüssel behält den gespeicherten; geänderte URL ohne neuen Schlüssel → 400. | P4.5, P4.6, P5.5 |
| C2 | P5, P1 | `templates/instances/list.html`, `templates/searched.html`, `models/instance.py` | Namen nur noch in `data-*`-Attributen, nie in Inline-JS. Namen mit Steuerzeichen oder über 100 Zeichen werden abgelehnt. | P5.2, P5.4, P1.1 |
| C3 | P4, P5 | `auth.py`, `main.py`, `templates/base.html` | `CSRFMiddleware` für POST/PUT/PATCH/DELETE: `Sec-Fetch-Site` muss `same-origin`/`none` sein, sonst `Origin` gleich Host; ohne beide Header erlaubt (curl). Logout nur per `POST /logout`, Formular im Menü. | P4.3, P4.4, P5.1 |
| C4 | P1, P4 | `agents/base.py`, `models/instance.py`, `api/instances.py` | `allow_redirects=False` bei allen *arr-Requests, 3xx wird als Fehler gemeldet. URL-Validator lehnt Query, Fragment und Benutzerdaten ab. | P1.1, P1.5, P4.5 |
| C5 | P4, P3, P6 | `crypto.py`, `database.py`, `config.py`, README/Compose | `SECRET_KEY` opt-in: gesetzt → Fernet- und Sitzungsschlüssel per HKDF, einmalige Umschlüsselung, Marker `key_source=env` + Prüfwert, alte Schlüssel aus der DB gelöscht. Marker gesetzt, aber kein/falscher `SECRET_KEY` → Start bricht mit klarer Meldung ab. Ungesetzt → wie heute. DB-Datei 0600. | P4.2, P3.1, P6.4 |
| C6 | P4, P3 | `auth.py`, `main.py`, `config.py` | Remember-Token `v2.<ausgestellt>.<version>.<sig>`, 30 Tage serverseitig, `token_version` in `app_settings` (Logout widerruft alle Tokens und Sitzungen), Passwort-Fingerabdruck in der Signatur. `COOKIE_SECURE` (Standard aus). | P4.4 |
| C7 | P4 | `auth.py`, `main.py` | `LoginThrottle` pro IP im Speicher: ab 5 Fehlversuchen Sperre 30 s, verdoppelt bis 15 min, Antwort 429 + `Retry-After`. Jeder Fehlversuch wird mit IP geloggt. | P4.4 |
| C8 | P6, P5 | `static/vendor/*`, `scripts/vendor_assets.py`, `requirements.lock`, `Dockerfile`, Workflow, `templates/base.html` | Alpine/HTMX lokal, geprüft gegen npm-`integrity`; `requirements.lock` mit Hashes und `--require-hashes`; Basisimage per Digest; Actions per Commit-SHA. | P6.1–P6.3, P5.1 |
| C9 | P6 | `docker-entrypoint.sh`, `Dockerfile` | Entrypoint chownt `/data` nur bei Bedarf auf `PUID:PGID` (Standard 1000, Symlinks übersprungen), nimmt Gruppe/Anderen die Rechte (`go-rwx`, auch Sicherungskopien), `umask 077`, startet per `setpriv` ohne root. Live-Stack: `PUID/PGID=568`, weil 1000 auf hetzner2 `codeuser` ist. | P6.3, Task Z |
| C10 | P4 | `main.py` | `safe_next()` lässt nur lokale Pfade durch, sonst `/`. | P4.4 |
| C11 | P5, P1 | `templates/logs.html`, `static/js/app.js`, `agents/base.py` | Logs-Seite füllt den Store aus `recent` vor; Instanzfilter nutzt `instances`. Live-Einträge tragen jetzt `created_at`. | P5.2, P1.3 |
| C12 | P5 | `static/js/app.js` | `onerror` handelt nur bei `readyState === CLOSED` der aktuellen Quelle, höchstens ein Timer, vor dem Neuverbinden Sitzung prüfen. | P5.1 |
| C13 | P5 | `templates/searched.html` | Überschrift und Reset über Alpine-Methoden (`loadInstance(id, name)`, `reset(id, name)`, `resetAll()`), offene Tabelle wird geleert. | P5.4 |
| A-L1 (= C-L2) | P2, P4 | `skills/health_check.py`, `api/instances.py` | `exc.response is not None` statt Wahrheitsprüfung. 401/403 → `error` „Invalid API key“. | P2.5, P4.5 |
| A-L2 | P2 | `skills/base.py`, `skills/search_missing.py` | `release_date()`: Radarr = früheres von `digitalRelease`/`physicalRelease`, sonst `inCinemas`; für Filter und Sortierung. | P2.1, P2.2 |
| A-L3 | P2, P3 | `skills/search_missing.py`, `db/searched.py` | Zufallsmodus: höchstens 10 Seiten je Lauf, Cache-Prüfung je Seite mit einer SQL-Abfrage (`lookup_many`). | P3.2, P2.2 |
| A-L4 | P1 | `agents/base.py`, `agents/orchestrator.py` | `InstanceRuntime` (Rate-Deque, Skill-Sperren) lebt im Orchestrator pro Instanz und überlebt Reload, Aus/An und Wegwerf-Agenten. | P1.3 |
| A-L5 | P1, P2, P4 | `agents/base.py`, `agents/orchestrator.py`, `skills/base.py`, `api/instances.py` | Abbruch-Signal (`stop_requested`, `wait_or_stop`) in allen Suchschleifen. Deaktivieren bricht ab, Löschen bricht ab und wartet bis 15 s auf freie Sperren (`forget_instance`). Reload (Speichern) bricht nicht ab. | P1.4, P2.1, P4.5 |
| B-L1 | P3, P4 | `db/history.py`, `main.py` | `close_interrupted_runs()` beim Start: Läufe mit Items → `pending`, ohne Items → `error` „Interrupted by restart“. | P3.3, P4.6 |
| B-L2 | P3 | `db/history.py`, `api/history.py` | `clear()` löscht nur abgeschlossene Läufe; offene bleiben mit ihren Items. | P3.3, P3.6 |
| B-L3 | P3 | `verification.py` | `orphaned` zählt als `failed`. | P3.4 |
| B-L4 | P3, P2 | `db/history.py`, `skills/verify_commands.py`, `skills/base.py` | Kachel liest beide Zahlen aus demselben zuletzt beendeten Lauf; Skills setzen am Laufende `last_triggered` und `last_verified=0` gemeinsam. | P3.4, P2.1 |
| B-L5 (≈ C-L5) | P3, P5 | `db/history.py`, `db/searched.py`, `api/history.py`, `api/searched.py`, `templates/history.html`, `templates/searched.html` | Filter serverseitig (`instance_id`, `item_type`, `skill`, `q`), `offset`, Gesamtzahl im Header `X-Total-Count`. Option „Upgrade“ filtert auf `skill=search_upgrades`. Progressed-Seite blättert serverseitig. | P3.3, P3.6, P5.2, P5.4 |
| C-L1 | — | — | Duplikat von A2. | — |
| C-L2 | — | — | Duplikat von A-L1. | — |
| C-L3 | P4, P6 | `auth.py`, `requirements.txt` | `passlib` raus, `bcrypt.checkpw` direkt, Fehler werden geloggt. | P4.4, P6.2 |
| C-L4 | — | — | Duplikat von A1. | — |
| C-L5 | — | — | Mit B-L5 zusammengeführt. | — |
| C-L6 | P4, P5 | `auth.py`, `static/js/app.js`, `templates/instances/card.html` | Ohne Sitzung: `/api/*` → 401 JSON, htmx-Anfragen (auch auf `/api/*`, z. B. der 5-s-Status der Karte) → 401 + `HX-Redirect` auf `/login?next=<aktuelle Seite aus HX-Current-URL>`. `apiFetch()` leitet bei 401 auf `/login?next=…`. Die Karte wertet nur erfolgreiche Antworten aus. | P4.3, P5.1, P5.3 |
| C-L7 | P4, P6 | `log_broadcaster.py`, `api/activity.py`, `main.py`, `Dockerfile` | SSE-Generator endet auf ein Shutdown-Ereignis (SIGTERM-Kette), `uvicorn --timeout-graceful-shutdown 5`. | P4.6, P4.7, P6.3 |

Zählung: 53 IDs, davon 4 Duplikate (C-L1, C-L2, C-L4, C-L5) → 49 eigenständige Befunde, alle mit Task.

---
## Schnittstellenvertrag zwischen den Paketen

Was hier steht, ist verbindlich. Ein Paket darf nur diese Namen, Signaturen und Formate der anderen Pakete benutzen. Weicht ein Paket beim Bauen ab, muss es diesen Abschnitt im selben Commit anpassen und die betroffenen Pakete nennen.

### P1 liefert (Welle 1)

`backend/agents/base.py`

```python
TRIGGER_STARTED = "started"
TRIGGER_BUSY = "busy"
TRIGGER_UNKNOWN_SKILL = "unknown_skill"
QUIET_HOURS_EXEMPT = ("health_check", "verify_commands")
MIN_INTERVAL_MINUTES = 1
MAX_INTERVAL_MINUTES = 10080

@dataclass
class InstanceRuntime:
    rate_lock: threading.Lock
    action_timestamps: deque            # time.monotonic() floats, aufsteigend
    skill_locks: dict[str, threading.Lock]
    skill_locks_guard: threading.Lock
    def skill_lock(self, skill_name: str) -> threading.Lock: ...
    def busy_skills(self) -> list[str]: ...

class BaseAgent(ABC):
    def __init__(self, config: dict, broadcaster=None, runtime: InstanceRuntime | None = None): ...
    runtime: InstanceRuntime
    state: dict   # "status" in {"starting","scheduled","running","off","quiet","error"}, "next_run_at" (ISO-UTC oder None),
                  # "last_wanted", "last_triggered", "last_verified", "last_sync", "connection_status", "last_seen_at"
    def start(self) -> None: ...                       # loggt NICHT mehr selbst
    def stop(self, abort_running: bool = True) -> None: ...
    def request_abort(self) -> None: ...               # nur das Abbruch-Signal setzen (Wegwerf-Agent, Tests)
    def stop_requested(self) -> bool: ...              # True nach stop(abort_running=True) oder request_abort()
    def wait_or_stop(self, seconds: float) -> bool: ...  # wartet; True = abgebrochen
    def wait_idle(self, timeout: float) -> bool: ...   # True, wenn keine Skill-Sperre der Runtime mehr belegt ist
    def refresh_config(self) -> None: ...              # Konfig frisch aus DB, next_run_at neu
    def trigger_now(self, skill_name: str, force: bool = True,
                    on_done: Callable[["BaseAgent"], None] | None = None) -> str: ...  # TRIGGER_*; on_done nach Laufende im Trigger-Thread
    def reserve_action(self) -> float | None: ...      # Token oder None, wenn rate_cap erreicht
    def release_action(self, token: float) -> None: ...
    def get_rate_used(self) -> int: ...
    def log(self, level: str, skill: str, message: str) -> None: ...  # wirft nie
    def http_get(self, path: str, params: dict | None = None) -> dict: ...   # 3xx → requests.HTTPError mit .response
    def http_post(self, path: str, body: dict) -> dict: ...                  # 3xx → requests.HTTPError mit .response
    def http_get_raw(self, path: str) -> tuple[int, dict | None]: ...        # 3xx → (Statuscode, None)
    # nur bis Task Z: check_rate_cap() -> bool, record_action() -> None
```

Broadcast-Format von `BaseAgent.log` (SSE-Nutzlast):
`{"instance_id": int, "instance_name": str, "level": "info|warn|error|debug", "skill": str, "message": str, "created_at": "YYYY-MM-DD HH:MM:SS"}` (Ortszeit, gleiches Format wie `activity_log.created_at`).

`backend/agents/orchestrator.py`

```python
TRIGGER_NOT_FOUND = "not_found"

class Orchestrator:
    def start_all(self) -> None
    def stop_all(self) -> None
    def start_agent(self, instance_id: int) -> None
    def stop_agent(self, instance_id: int, abort_running: bool = True, wait_seconds: float = 0.0) -> None
    def reload_agent(self, instance_id: int) -> None          # stoppt ohne Abbruch, startet neu
    def refresh_config(self, instance_id: int) -> None         # ohne Neustart
    def forget_instance(self, instance_id: int, wait_seconds: float = 15.0) -> None  # vor dem Löschen
    def trigger(self, instance_id: int, skill_name: str, force: bool = True) -> str  # TRIGGER_* oder TRIGGER_NOT_FOUND
    def get_agent_state(self, instance_id: int) -> dict | None  # wie bisher + rate_used, rate_cap, rate_window
    def get_all_states(self) -> dict[int, dict]
    def is_running(self, instance_id: int) -> bool
```

`backend/models/instance.py`

```python
FIELD_BOUNDS: dict[str, tuple[int, int]] = {
    "interval_minutes": (1, 10080),
    "retry_hours": (0, 87600),
    "rate_window_minutes": (1, 525600),
    "rate_cap": (1, 1_000_000_000),
    "missing_per_run": (0, 100_000),
    "upgrades_per_run": (0, 100_000),
    "seconds_between_actions": (0, 3600),
    "hours_after_release": (0, 87600),
}
NAME_MAX_LENGTH = 100
```

`InstanceCreate`/`InstanceUpdate` lehnen Werte außerhalb `FIELD_BOUNDS` ab und verlangen `missing_per_run >= 1` bzw. `upgrades_per_run >= 1` nur, wenn der jeweilige Skill an ist. Fehlerform: FastAPI-422 mit `detail[0].msg` = `"Value error, <feld> must be between <min> and <max>; …"`.

### P3 liefert (P3a in Welle 1; mit „P3b“ markierte Teile erst in Welle 2)

`backend/config.py` — neue/geänderte Felder von `Settings`:

```python
secret_key: str = ""                 # SECRET_KEY, opt-in (P4 wertet aus)
cookie_secure: bool = False          # COOKIE_SECURE
history_retention_days: int = 365    # HISTORY_RETENTION_DAYS, 0 = nie löschen, negativ → Startfehler
```

`backend/db/app_settings.py` (neu, in `backend/db/__init__.py` exportiert als `db.app_settings`)

```python
def get_value(key: str) -> str | None
def set_value(key: str, value: str) -> None     # upsert
def delete_value(key: str) -> None
```

Belegte Schlüssel in `app_settings`: `encryption_key`, `secret_key` (Bestand), neu `key_source` (`"env"` nach Umschlüsselung), `secret_key_check` (Hex-Prüfwert), `token_version` (Ganzzahl als Text, fehlt = 0), `ancestor_rule_since` (Ortszeit `YYYY-MM-DD HH:MM:SS`, schreibt `init_db()` einmal per `INSERT OR IGNORE` beim ersten Start von 0.8.0; P2 liest ihn mit `db.app_settings.get_value` und rechnet mit `db.searched.local_to_utc` um).

`backend/database.py`: `ANCESTOR_RULE_SINCE_SETTING = "ancestor_rule_since"`.

`backend/db/searched.py`

```python
def local_to_utc(value: str) -> datetime                     # "YYYY-MM-DD HH:MM:SS" Ortszeit → aware UTC
def lookup_many(instance_id: int, keys: Iterable[str], retry_hours: int = 0) -> dict[str, datetime]
    # cache_key → searched_at (aware UTC) für jeden vorhandenen (bei retry_hours>0: nicht abgelaufenen) Schlüssel;
    # eine Verbindung, Abfragen in Blöcken zu 500 Schlüsseln
def purge_expired(instance_id: int, retry_hours: int) -> int  # 0, wenn retry_hours <= 0
def count_filtered(instance_id: int | None = None, item_type: str | None = None) -> int
# unverändert: exists, exists_any (bis Task Z), add, query, count, delete, clear
```

`backend/db/history.py`

```python
def record_submission(run_id: int, instance_id: int, title: str, arr_id: int | None, item_type: str,
                      cache_key: str, command_id: int | None) -> int
    # eine Transaktion: Item (submitted bzw. expired ohne command_id) + Cache-Upsert nur wenn command_id is not None und cache_key
def record_failed_submission(run_id: int, title: str, arr_id: int | None, item_type: str) -> int
    # Item command_status='failed', command_id NULL, verified_at=jetzt, kein Cache
def resolve_item(item_id: int, status: str, instance_id: int, cache_key: str) -> bool   # P3b
    # eine Transaktion: Status + verified_at + last_checked_at; bei status=='failed' und cache_key Cache löschen; True = Cache freigegeben
def mark_checked(item_ids: list[int]) -> None   # P3b
def get_pending_items(instance_id: int, limit: int = 50) -> list[dict]   # Schlüssel id, run_id, command_id, cache_key, last_checked_at   (P3b)
def expire_stale_items(instance_id: int, hours: int = 24) -> int         # nur Items mit last_checked_at >= created_at + hours   (P3b)
def close_interrupted_runs() -> int
def purge_old_runs(instance_id: int, days: int) -> int                   # 0, wenn days <= 0; nie running/pending
def clear() -> dict                                                      # {"deleted": int, "kept_open": int}
def get_latest_run_verification(instance_id: int) -> dict | None        # nur beendete Läufe; Schlüssel id, status, triggered_count, verified_count
def query_items_flat(instance_id: int | None = None, item_type: str | None = None, skill: str | None = None,
                     search: str | None = None, limit: int = 250, offset: int = 0) -> list[dict]
def count_items_flat(instance_id: int | None = None, item_type: str | None = None, skill: str | None = None,
                     search: str | None = None) -> int
# unverändert: start_run, finish_run, query, get_last_for_instance, insert_item, get_items_for_run,
#              query_with_items, set_item_status, get_unresolved_run_ids, get_item_statuses, update_run_verification
```

Zeilenformat von `query_items_flat` (auch Body von `GET /api/history/items`): `item_id, run_id, instance_id, started_at, instance_name, skill, status, verified_count, error_message, arr_type, title, item_type, arr_id, command_id, command_status`.

`backend/verification.py` (P3b): `map_command_status(200, {"status": "orphaned"}) == "failed"`.

`backend/skills/verify_commands.py` (P3b, Welle 2): setzt nach jedem Durchlauf `agent.state["last_verified"]` **und** `agent.state["last_triggered"]` aus `get_latest_run_verification`. Hauspflege höchstens einmal pro Stunde und Instanz (`VerifyCommandsSkill._last_housekeeping: dict[int, float]`, Klassenattribut).

HTTP (P3):

| Route | Parameter | Antwort |
|---|---|---|
| `GET /api/history` | `instance_id`, `skill`, `limit` 1..200 (50), `offset` ≥0 | Liste wie bisher |
| `GET /api/history/items` | `instance_id`, `item_type` ∈ movie/episode/season/series, `skill` ∈ search_missing/search_upgrades, `q` (≤200 Zeichen, Titel oder Instanzname, ohne Groß/Klein), `limit` 1..1000 (500), `offset` ≥0 | Liste (Zeilenformat oben), Header `X-Total-Count: <Treffer ohne limit/offset>` |
| `DELETE /api/history` | — | `{"status": "cleared", "deleted": n, "kept_open": m}` |
| `GET /api/searched` | `instance_id`, `item_type`, `limit` 1..500 (100), `offset` ≥0 | Liste wie bisher, Header `X-Total-Count` |
| ungültige Parameter | | 422 |

### P2 liefert (Welle 2)

`backend/skills/base.py`

```python
@dataclass(frozen=True)
class SearchResult:
    ok: bool
    title: str = ""
    item_type: str = ""
    cache_key: str = ""
    arr_id: int | None = None
    command_id: int | None = None
    error: str = ""              # neu: nur bei ok=False gefüllt

def parse_arr_date(value) -> datetime | None           # ISO mit/ohne Z → aware UTC, sonst None
def release_date(record: dict, arr_type: str) -> datetime | None
    # sonarr: airDateUtc; radarr: min(digitalRelease, physicalRelease), sonst inCinemas

@dataclass
class SubmitOutcome:
    triggered: int = 0
    errors: list[str]            # "<Titel>: <Fehler>"
    stopped: bool = False
    rate_capped: bool = False

def submit_candidates(skill_name: str, agent, run_id: int, candidates: list, fire, delay: float) -> SubmitOutcome
def finish_search_run(skill_name: str, agent, run_id: int, wanted: int, outcome: SubmitOutcome, notes=()) -> str
    # setzt finish_run + state last_wanted/last_triggered/last_verified=0 (+ last_sync außer bei "error"); gibt Status zurück
```

### P4 liefert (Welle 2)

Umgebungsvariablen (alle optional):

| Variable | Standard | Wirkung |
|---|---|---|
| `SECRET_KEY` | leer | leer: Schlüssel liegen wie bisher in der DB. Gesetzt: Fernet- und Sitzungsschlüssel werden per HKDF-SHA256 daraus abgeleitet, beim ersten Start wird umgeschlüsselt und die alten Schlüssel werden aus der DB gelöscht. Danach ist `SECRET_KEY` Pflicht und darf sich nie ändern. |
| `COOKIE_SECURE` | `false` | `true`: `ma_session` und `ma_remember` bekommen `Secure` (nur hinter HTTPS setzen). |
| `HISTORY_RETENTION_DAYS` | `365` | Abgeschlossene Läufe älter als N Tage werden stündlich gelöscht; `0` = nie. |
| `AUTH_PASSWORD` | leer | Klartext oder bcrypt-Hash (`$2a$`/`$2b$`/`$2y$`). |
| `PUID` / `PGID` | `1000` | nur im Container, siehe P6. |

HTTP (P4):

| Fall | Antwort |
|---|---|
| ohne Sitzung, Pfad beginnt mit `/api/`, ohne `HX-Request` | `401 {"detail": "Not authenticated"}` |
| ohne Sitzung, Pfad beginnt mit `/api/`, Header `HX-Request: true` | `401 {"detail": "Not authenticated"}`, Header `HX-Redirect: /login?next=<Pfad+Query aus HX-Current-URL, sonst />` |
| ohne Sitzung, Seite, Header `HX-Request: true` | `401`, Header `HX-Redirect: /login?next=<pfad>` |
| ohne Sitzung, sonst | `302` nach `/login?next=<pfad>` (wie bisher) |
| unsichere Methode, `Sec-Fetch-Site` nicht `same-origin`/`none`, oder (ohne `Sec-Fetch-Site`) `Origin` ≠ Host | `403 {"detail": "Cross-site request blocked"}` |
| `POST /login` gesperrt | `429`, Header `Retry-After: <s>`, Login-Seite mit Fehlertext |
| `POST /logout` | widerruft alle Remember-Tokens und Sitzungen, `303` nach `/login` |
| `GET /logout` | `405` |
| `POST /api/instances/{id}/trigger` | `200 {"status": "triggered", "skill": s}` · `409 {"detail": "<skill> is already running — try again when it has finished"}` · `404` · `400` bei unbekanntem Skill |
| `POST /api/instances/{id}/toggle-skill?skill=missing|upgrades&enabled=bool` | `200 {"status": "ok", "skill": s, "enabled": b}` · `409`, wenn eingeschaltet werden soll und `*_per_run < 1` |
| `PUT /api/instances/{id}` mit geänderter URL, ohne neuen Schlüssel | `400 {"detail": "The URL changed — enter the API key again so it is not sent to a new address."}` |
| alle Instanz-Antworten | `api_key` = `"********"` (oder `""` wenn keiner gespeichert), zusätzlich `api_key_set: bool` |
| `GET /api/instances/{id}/test` bei 3xx | `502 {"detail": "Instance answered with a redirect (HTTP 302) — check the URL"}` |
| `GET /api/activity` | `limit` 1..500 (100), `offset` ≥0, sonst 422 |

Remember-Cookie `ma_remember` = `v2.<issued_unix>.<token_version>.<hex_sig>`, `hex_sig = HMAC-SHA256(key=Sitzungsschlüssel, msg="v2|<username>|<issued>|<version>|<sha256('missingarr-password|'+AUTH_PASSWORD)>")`. Gültig, wenn Version gleich `token_version`, Alter ≤ 30 Tage, `issued` höchstens 300 s in der Zukunft. Die Sitzung trägt `session["tv"]` (Token-Version); passt sie nicht mehr, gilt sie als abgemeldet.

Python (P4):

```python
# backend/api/instances.py
API_KEY_MASK = "********"
def public_instance(inst: dict) -> dict
# backend/main.py
def safe_next(value: str | None) -> str
def install_shutdown_signal_hook(broadcaster) -> None
# backend/auth.py
REMEMBER_COOKIE = "ma_remember"; REMEMBER_MAX_AGE = 30 * 24 * 3600
def create_remember_token(username: str, now: int | None = None) -> str
def verify_remember_token(token: str, now: int | None = None) -> str | None
def revoke_all_tokens() -> int
def current_token_version() -> int
def is_same_origin_request(headers) -> bool
class LoginThrottle; login_throttle: LoginThrottle
class LazySessionMiddleware; class CSRFMiddleware; class AuthMiddleware
# backend/crypto.py
def init_crypto() -> None; def get_session_secret() -> str; def encrypt(plain: str) -> str; def decrypt(value: str) -> str
# backend/log_broadcaster.py
LogBroadcaster.request_shutdown() -> None; LogBroadcaster.shutdown_event -> asyncio.Event | None
```

Template-Kontext aus `main.py` (alle Seiten zusätzlich `request`, `app_name`, `version`, `tooltips`, `auth_enabled`):

| Template | Variablen |
|---|---|
| `dashboard.html` | `cards: list[{"instance": public_instance, "state": dict, "recent": list[run]}]` |
| `instances/list.html` | `instances: list[public_instance]` |
| `instances/form.html` | `instance: public_instance | None`, `action: str`, `method: "POST"|"PUT"`, `bounds: FIELD_BOUNDS` |
| `instances/card.html` | `inst: public_instance`, `state: dict`, `recent: list[run]`, `conn: str` |
| `history.html` | `instances: list[public_instance]` |
| `logs.html` | `recent: list[activity row]` (neueste zuerst, ohne debug, 100), `instances: list[public_instance]` |
| `searched.html` | `instances`, `counts` wie bisher |
| `login.html` | `app_name`, `next` (bereits durch `safe_next`), `error` |

`state["status"]` kann jetzt auch `"error"` sein (Scheduler startete nicht).

### P5 liefert (Welle 3)

`static/js/app.js` (global, nur `function`/`var` auf oberster Ebene): `sessionExpiredError() -> Error` (Name `SessionExpiredError`), `isSessionExpired(err) -> bool`, `async apiFetch(url, options = {}) -> Response` (wirft diesen Fehler und leitet auf `/login?next=…` um bei 401 oder Umleitung auf `/login`), `toast(message, type)`, `createLogStore() -> object` (der Alpine-Store `logs`, mit `seed(rows)`), `updateCardState(id, responseText)` kennt Status `error`.

### P6 liefert (Welle 1)

Container: `ENTRYPOINT /usr/local/bin/docker-entrypoint.sh`. Läuft als root, bringt `/data` (bzw. `DATA_DIR`) auf `PUID:PGID` (nur Dateien, die noch nicht passen, Symlinks nie), entzieht Gruppe und Anderen alle Rechte (`go-rwx`, nur wo nötig), setzt `umask 077` und startet dann den Befehl per `setpriv --reuid --regid --clear-groups`. `PUID=0` = weiter als root ohne chown. Standard `1000:1000`; auf hetzner2 ist 1000 der Login-Benutzer `codeuser`, der Live-Stack bekommt deshalb `PUID=568`/`PGID=568` (siehe Release-Notiz Punkt 4). uvicorn mit `--timeout-graceful-shutdown 5`. Statische Bibliotheken unter `/static/vendor/alpinejs-3.14.1.min.js` und `/static/vendor/htmx-2.0.4.min.js`.

---

## Schemaänderungen und Migrationen

Geprüfte CHECK-Constraints (0.7.0):

- `search_history.status` erlaubt `running, success, error, pending, partial, failed, unverified`. Alle neuen Pfade nutzen nur diese Werte (`error` für „alles gescheitert“ und „durch Neustart unterbrochen“, `pending` für unterbrochene Läufe mit Items). **Kein Tabellenumbau nötig.**
- `search_history_items.command_status` hat keinen CHECK. Der neue Einsatz `failed` ohne `command_id` (gescheiterte Einreichung) ist erlaubt.
- `search_history_items.item_type` erlaubt `movie, episode, season, series`. Gescheiterte Einreichungen tragen den beabsichtigten Typ (`movie` bei Radarr, `episode`/`season` bei Sonarr), nie einen neuen Wert.
- `search_history.skill` bleibt `search_missing, search_upgrades`.

Änderungen (alle in P3, `backend/database.py`):

| Änderung | Art | Auf 0.7.0-DB |
|---|---|---|
| `search_history_items.last_checked_at TEXT` | `ALTER TABLE … ADD COLUMN` + in `CREATE TABLE` | neue Spalte, `NULL` für alle Bestandszeilen (= „nie geprüft“, kommt zuerst dran) |
| `search_history.verified_count` | nur in `CREATE TABLE` ergänzt | auf 0.7.0 schon vorhanden, Migration überspringt |
| Index `idx_history_instance_started ON search_history(instance_id, started_at DESC, id DESC)` | `CREATE INDEX IF NOT EXISTS`, **nach** dem Umbau von `search_history` | neu |
| `UPDATE instances SET retry_hours=0 WHERE retry_hours IN (1,168)` | entfällt | Werte bleiben ab jetzt erhalten |
| Migrationsliste | `PRAGMA table_info` statt `try/except: pass` | nur fehlende Spalten werden angelegt; „duplicate column name“ wird geschluckt, alles andere bricht den Start ab |
| Pflichtspalten-Prüfung | nach allen Migrationen und nach `_widen_history_status_check()` | fehlt eine Spalte aus der Migrationsliste → `RuntimeError("Database schema incomplete after migration: …")` |
| `app_settings`-Schlüssel `key_source`, `secret_key_check`, `token_version` | Datenzeilen, keine Schemaänderung | entstehen erst bei Bedarf |
| `app_settings`-Schlüssel `ancestor_rule_since` | Datenzeile, `INSERT OR IGNORE` in `init_db()` | einmal beim ersten Start von 0.8.0 (Ortszeit), danach unverändert |
| Dateirechte | `chmod 0600` auf DB, `-wal`, `-shm` nach `init_db()` | einmalig und bei jedem Start |

Idempotenz: `init_db()` darf beliebig oft laufen. Zweiter Lauf ändert nichts (Test in P3.1). `busy_timeout` 30 s auf jeder Verbindung.

Tests mit Alt-DB (P3.1, `tests/test_p3_database.py`):

1. `SCHEMA_0_7_0` — wörtliche Kopie des 0.7.0-Schemas (Tabellen wie nach allen 0.7.0-Migrationen und dem Umbau), gefüllt mit einer Instanz (`retry_hours=168`), einem `running`-Lauf, einem `pending`-Lauf mit `submitted`-Item, einem `success`-Lauf mit `legacy`-Item, Cache-Zeilen und `app_settings` (`encryption_key`, `secret_key`). Nach `init_db()` zweimal: gleiche Zeilenzahl in allen Tabellen, `retry_hours` weiter 168, Spalte `last_checked_at` da, Index da, `PRAGMA foreign_key_check` leer.
2. `SCHEMA_0_6_13` — Schema vor der Verifikation (CHECK nur `running/success/error`, Items ohne Befehlsspalten), mit Instanz, zwei Läufen und drei Items. Nach `init_db()`: alle drei Items noch da (fängt die Mutante „Umbau mit `foreign_keys=ON`“ aus B10), neue Spalten da, Status-CHECK erweitert.
3. Fehlerinjektion an jeder Anweisung des Umbaus (parametrisiert) → Originaltabelle und Items unversehrt.
4. Echte Release-Kette: `backend/database.py` aus den Tags `v0.5.2`, `v0.6.0`, `v0.6.12`, `v0.6.13`, `v0.7.0` (per `git show`) nacheinander auf dieselbe Datei, mit Zeilen aus der frühesten und der letzten Version. Danach neues `init_db()` zweimal: gleiche Zeilenzahlen (außer dem einen Marker), `retry_hours` 168, `last_checked_at`, Index, `foreign_key_check` leer, Item-Status unverändert. Fängt Abweichungen der handgeschriebenen DDL vom gewachsenen Live-Schema (Spaltenreihenfolge, CHECKs). Ohne Tags im Checkout wird der Test übersprungen.

---
## Task 0: `httpx2` für TestClient (vor Welle 1, allein)

**Files:**
- Modify: `requirements-dev.txt`

**Interfaces:**
- Consumes: nichts
- Produces: `fastapi.testclient.TestClient` ist in `.venv` benutzbar (P3, P4, P5 brauchen es). Starlette 1.6 verlangt dafür das Paket `httpx2` (mit dem alten `httpx` läuft es nur mit Deprecation-Warnung). Geprüft bei Planerstellung: `httpx2` 2.13.1 liefert `resp.cookies`, `client.cookies.set/delete/clear`, `headers.get_list` und `resp.context` wie erwartet.

- [ ] **Step 1: Fehlen belegen**

Run: `cd /root/missingarr && .venv/bin/python -c "import fastapi.testclient"`
Expected: FAIL mit `RuntimeError: The starlette.testclient module requires the httpx2 package to be installed.`

- [ ] **Step 2: Abhängigkeit eintragen**

`requirements-dev.txt` komplett:

```
-r requirements.txt
pytest>=8.0.0
httpx2>=2.13
```

- [ ] **Step 3: Installieren und prüfen**

Run: `cd /root/missingarr && .venv/bin/pip install "httpx2>=2.13" && .venv/bin/python -c "import fastapi.testclient; print('ok')" && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: `ok`, danach `22 passed`.

- [ ] **Step 4: Commit**

```bash
git add requirements-dev.txt
git commit -m "test: add httpx2 so Starlette's TestClient can run"
```

---

## Paket P1 — Agent und Scheduler (Welle 1)

Abnahme P1: `tests/test_p1_*.py` grün, Gesamtsuite grün; kein Aufruf von `requests.get/post` in `backend/agents/base.py` ohne `allow_redirects=False` (`grep -n "requests\.\(get\|post\)" backend/agents/base.py` zeigt nur Aufrufe mit `allow_redirects=False`); `FakeAgent`-Tests zeigen: beide Such-Jobs existieren immer, `next_run_at` folgt den aktiven Jobs, Scheduler-Fehler ergibt `status="error"`.

Gemeinsame Test-Bausteine (in jede P1-Testdatei kopieren, die sie braucht — keine gemeinsame `conftest.py`):

```python
import time

import pytest

from backend import database, db
from backend.agents.base import BaseAgent
from backend.config import settings
from backend.skills.base import BaseSkill


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "missingarr.db"
    monkeypatch.setattr(settings, "database_url", str(path))
    monkeypatch.setattr(database, "_cached_secret_key", None)
    database.init_db()
    return path


def make_instance(**fields):
    data = {
        "name": "Sonarr", "type": "sonarr", "url": "http://127.0.0.1:9",
        "api_key": "k" * 32, "seconds_between_actions": 0,
    }
    data.update(fields)
    return db.instances.create(data)


class FakeSkill(BaseSkill):
    def __init__(self, name):
        self.name = name
        self.calls = []

    def execute(self, agent, force=False):
        self.calls.append(force)


class FakeAgent(BaseAgent):
    def build_skills(self):
        return [FakeSkill(n) for n in
                ("search_missing", "search_upgrades", "health_check", "verify_commands")]


def wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not reached in time")
```

### Task P1.1: Eingabegrenzen, URL- und Namensprüfung im Modell

**Files:**
- Modify: `backend/models/instance.py` (ganze Datei)
- Test: `tests/test_p1_models.py`

**Interfaces:**
- Consumes: nichts
- Produces: `FIELD_BOUNDS`, `NAME_MAX_LENGTH`, Validatoren in `InstanceCreate`/`InstanceUpdate` (siehe Vertrag). P4 übergibt `FIELD_BOUNDS` als `bounds` an das Formular, P5 setzt daraus `min`/`max`.

- [ ] **Step 1: Failing test schreiben**

`tests/test_p1_models.py`:

```python
import pytest
from pydantic import ValidationError

from backend.models.instance import FIELD_BOUNDS, InstanceCreate, InstanceUpdate

BASE = {"name": "Sonarr", "type": "sonarr", "url": "http://sonarr:8989", "api_key": "k" * 32}

LIVE_SONARR = dict(interval_minutes=60, missing_mode="episode", missing_per_run=4,
                   rate_cap=300, rate_window_minutes=60, search_upgrades_enabled=False,
                   retry_hours=0)
LIVE_RADARR = dict(type="radarr", interval_minutes=30, missing_per_run=600,
                   rate_cap=999_999_999, search_upgrades_enabled=False, retry_hours=0,
                   upgrades_per_run=0)


def make(model=InstanceCreate, **fields):
    return model(**{**BASE, **fields})


@pytest.mark.parametrize("model", [InstanceCreate, InstanceUpdate])
def test_live_settings_stay_valid(model):
    make(model, **LIVE_SONARR)
    make(model, **LIVE_RADARR)


@pytest.mark.parametrize("field,value", [
    ("interval_minutes", 0), ("interval_minutes", -5), ("interval_minutes", 10081),
    ("interval_minutes", 10_000_000_000), ("rate_cap", 0), ("rate_cap", 1_000_000_001),
    ("rate_window_minutes", 0), ("retry_hours", -1), ("seconds_between_actions", -1),
    ("hours_after_release", -1), ("missing_per_run", -3), ("upgrades_per_run", -1),
])
@pytest.mark.parametrize("model", [InstanceCreate, InstanceUpdate])
def test_out_of_range_values_are_rejected(model, field, value):
    with pytest.raises(ValidationError, match=field):
        make(model, **{field: value})


def test_per_run_zero_is_only_allowed_while_the_skill_is_off():
    make(search_upgrades_enabled=False, upgrades_per_run=0)
    make(search_missing_enabled=False, missing_per_run=0)
    with pytest.raises(ValidationError, match="upgrades_per_run"):
        make(search_upgrades_enabled=True, upgrades_per_run=0)
    with pytest.raises(ValidationError, match="missing_per_run"):
        make(search_missing_enabled=True, missing_per_run=0)


def test_every_bound_names_a_real_field():
    for field in FIELD_BOUNDS:
        assert field in InstanceCreate.model_fields


@pytest.mark.parametrize("url", [
    "http://sonarr:8989", "http://10.0.0.5:8989/sonarr", "https://127.0.0.1:7878/",
    "http://[::1]:8989", "http://192.168.1.10:7878",
])
def test_private_and_local_urls_stay_allowed(url):
    assert make(url=url).url == url.rstrip("/")


@pytest.mark.parametrize("url", [
    "http://sonarr:8989/?x=", "http://sonarr:8989/api?", "http://sonarr:8989#frag",
    "http://user:pw@sonarr:8989", "http://user@sonarr:8989", "ftp://sonarr",
    "http://", "http://sonarr:99999",
])
def test_urls_with_query_fragment_userinfo_or_bad_port_are_rejected(url):
    with pytest.raises(ValidationError):
        make(url=url)


@pytest.mark.parametrize("name", ["bad\x00name", "tab\tname", "x" * 101])
def test_names_with_control_characters_or_too_long_are_rejected(name):
    with pytest.raises(ValidationError):
        make(name=name)


def test_names_with_quotes_and_brackets_stay_allowed():
    assert make(name="Daniel's \"Sonarr\" <4K>").name == "Daniel's \"Sonarr\" <4K>"
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p1_models.py -q -p no:cacheprovider`
Expected: FAIL mit `ImportError: cannot import name 'FIELD_BOUNDS'`.

- [ ] **Step 3: Implementierung**

`backend/models/instance.py` komplett:

```python
from typing import Literal, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, field_validator, model_validator


SearchOrder = Literal["random", "smart", "newest_first", "oldest_first"]
MissingMode = Literal["smart", "season_packs", "show_batch", "episode"]
UpgradeSource = Literal["wanted_list_only", "monitored_items_only", "both"]
InstanceType = Literal["sonarr", "radarr"]
ConnectionStatus = Literal["unknown", "online", "offline", "error"]

# Generous on purpose: Radarr runs live with rate_cap=999999999 and
# missing_per_run=600, and d8aadd8 once removed tighter limits that blocked
# such values. The bounds only stop values that break the scheduler (A1).
FIELD_BOUNDS: dict[str, tuple[int, int]] = {
    "interval_minutes": (1, 10080),
    "retry_hours": (0, 87600),
    "rate_window_minutes": (1, 525600),
    "rate_cap": (1, 1_000_000_000),
    "missing_per_run": (0, 100_000),
    "upgrades_per_run": (0, 100_000),
    "seconds_between_actions": (0, 3600),
    "hours_after_release": (0, 87600),
}
NAME_MAX_LENGTH = 100


class InstanceBase(BaseModel):
    name: str
    type: InstanceType
    url: str
    enabled: bool = True
    search_missing_enabled: bool = True
    search_upgrades_enabled: bool = False
    interval_minutes: int = 15
    retry_hours: int = 0
    rate_window_minutes: int = 60
    rate_cap: int = 25
    search_order: SearchOrder = "random"
    missing_mode: MissingMode = "episode"
    missing_per_run: int = 5
    upgrades_per_run: int = 1
    seconds_between_actions: int = 2
    hours_after_release: int = 9
    upgrade_source: UpgradeSource = "monitored_items_only"
    quiet_start: Optional[str] = None
    quiet_end: Optional[str] = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        # The path is appended to this base, so a '?' or '#' would let the
        # stored value retarget any path on the host (C4).
        if "?" in v or "#" in v:
            raise ValueError("URL must not contain '?' or '#'")
        parts = urlsplit(v)
        if parts.scheme not in ("http", "https"):
            raise ValueError("URL must start with http:// or https://")
        if not parts.hostname:
            raise ValueError("URL must contain a host name")
        if "@" in parts.netloc:
            raise ValueError("URL must not contain a user name or password")
        try:
            parts.port
        except ValueError:
            raise ValueError("URL contains an invalid port")
        return v

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Name must not be empty")
        if len(v) > NAME_MAX_LENGTH:
            raise ValueError(f"Name must not exceed {NAME_MAX_LENGTH} characters")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in v):
            raise ValueError("Name must not contain control characters")
        return v

    @field_validator("quiet_start", "quiet_end")
    @classmethod
    def validate_time(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        parts = v.split(":")
        if len(parts) != 2:
            raise ValueError("Time must be in HH:MM format")
        try:
            h, m = int(parts[0]), int(parts[1])
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError
        except ValueError:
            raise ValueError("Time must be in HH:MM format (00:00–23:59)")
        return f"{h:02d}:{m:02d}"


class _InstanceWrite(InstanceBase):
    """Bounds for values a user saves. Deliberately not on InstanceBase:
    rows already in the database are never rejected when they are read."""

    @model_validator(mode="after")
    def _check_bounds(self):
        errors = []
        for field, (low, high) in FIELD_BOUNDS.items():
            value = getattr(self, field)
            if not low <= value <= high:
                errors.append(f"{field} must be between {low} and {high}")
        if self.search_missing_enabled and self.missing_per_run < 1:
            errors.append("missing_per_run must be at least 1 while missing search is enabled")
        if self.search_upgrades_enabled and self.upgrades_per_run < 1:
            errors.append("upgrades_per_run must be at least 1 while upgrade search is enabled")
        if errors:
            raise ValueError("; ".join(errors))
        return self


class InstanceCreate(_InstanceWrite):
    api_key: str

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("API key must not be empty")
        return v.strip()


class InstanceUpdate(_InstanceWrite):
    api_key: Optional[str] = None

    @field_validator("api_key")
    @classmethod
    def validate_api_key_optional(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        return v.strip()


class InstanceRead(InstanceBase):
    id: int
    connection_status: ConnectionStatus = "unknown"
    last_seen_at: Optional[str] = None
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}
```

- [ ] **Step 4: Tests grün**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p1_models.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/models/instance.py tests/test_p1_models.py
git commit -m "fix: bound instance settings on save and reject URLs with query, fragment or credentials"
```

### Task P1.2: Scheduler — beide Such-Jobs immer, robuster Start, `next_run_at`

**Files:**
- Modify: `backend/agents/base.py` (`start`, `_run`, neu `_build_scheduler`, `_interval_minutes`, `_update_next_run`, neu `refresh_config`, `_load_fresh_config`, Anfang von `_run_skill`)
- Modify: `backend/agents/sonarr.py`, `backend/agents/radarr.py` (nur Kommentar in `build_skills`)
- Test: `tests/test_p1_scheduler.py`

**Interfaces:**
- Consumes: `db.instances.get_by_id`, `db.instances.toggle_skill` (unverändert)
- Produces: `BaseAgent.refresh_config()`, `state["status"] == "error"` nach Scheduler-Fehler, `state["next_run_at"]` = frühester aktiver Such-Job.

- [ ] **Step 1: Failing test schreiben**

`tests/test_p1_scheduler.py` (Bausteine von oben einfügen, dazu):

```python
from datetime import timedelta


@pytest.fixture
def running_agent():
    agents = []

    def start(config):
        agent = FakeAgent(config)
        agent.start()
        wait_until(lambda: agent.state["status"] in ("scheduled", "error"))
        agents.append(agent)
        return agent

    yield start
    for agent in agents:
        agent.stop()


def job_ids(agent):
    return sorted(job.id for job in agent._scheduler.get_jobs())


def log_messages():
    return [row["message"] for row in db.activity.query(include_debug=True, limit=500)]


def test_both_search_jobs_exist_even_when_both_skills_are_off(db_path, running_agent):
    inst = make_instance(search_missing_enabled=False, search_upgrades_enabled=False)
    agent = running_agent(db.instances.get_by_id(inst["id"]))
    i = inst["id"]
    assert job_ids(agent) == sorted([f"health_{i}", f"missing_{i}", f"upgrades_{i}", f"verify_{i}"])
    assert agent.state["next_run_at"] is None


def test_next_run_follows_the_active_search_jobs(db_path, running_agent):
    inst = make_instance(interval_minutes=30, search_missing_enabled=False,
                         search_upgrades_enabled=True)
    agent = running_agent(db.instances.get_by_id(inst["id"]))
    upgrades = agent._scheduler.get_job(f"upgrades_{inst['id']}").next_run_time
    assert agent.state["next_run_at"] == upgrades.isoformat()

    db.instances.toggle_skill(inst["id"], "missing", True)
    agent.refresh_config()
    missing = agent._scheduler.get_job(f"missing_{inst['id']}").next_run_time
    assert missing < upgrades
    assert agent.state["next_run_at"] == missing.isoformat()

    db.instances.toggle_skill(inst["id"], "missing", False)
    db.instances.toggle_skill(inst["id"], "upgrades", False)
    agent.refresh_config()
    assert agent.state["next_run_at"] is None


def test_a_skill_switched_on_later_runs_through_its_existing_job(db_path):
    inst = make_instance(search_missing_enabled=False)
    agent = FakeAgent(db.instances.get_by_id(inst["id"]))
    agent._skills = agent.build_skills()
    agent._run_skill("search_missing")
    assert agent._get_skill("search_missing").calls == []
    db.instances.toggle_skill(inst["id"], "missing", True)
    agent._run_skill("search_missing")
    assert agent._get_skill("search_missing").calls == [False]


def test_scheduler_failure_is_reported_instead_of_agent_started(db_path, monkeypatch):
    inst = make_instance()
    agent = FakeAgent(db.instances.get_by_id(inst["id"]))

    def boom():
        raise OverflowError("date value out of range")

    monkeypatch.setattr(agent, "_build_scheduler", boom)
    agent.start()
    wait_until(lambda: agent.state["status"] == "error")
    messages = log_messages()
    assert any("Scheduler failed to start" in m for m in messages)
    assert not any(m.startswith("Agent started") for m in messages)
    agent.stop()


def test_agent_started_is_logged_once_the_scheduler_runs(db_path, running_agent):
    inst = make_instance()
    agent = running_agent(db.instances.get_by_id(inst["id"]))
    wait_until(lambda: any(m.startswith("Agent started") for m in log_messages()))
    assert agent._scheduler.running


@pytest.mark.parametrize("stored,expected", [(0, 1), (-5, 1), (10_000_000_000, 10080)])
def test_stored_interval_outside_bounds_is_clamped(db_path, running_agent, stored, expected):
    inst = make_instance()
    with database.get_db() as conn:
        conn.execute("UPDATE instances SET interval_minutes=? WHERE id=?", (stored, inst["id"]))
    agent = running_agent(db.instances.get_by_id(inst["id"]))
    assert agent.state["status"] == "scheduled"
    job = agent._scheduler.get_job(f"missing_{inst['id']}")
    assert job.trigger.interval == timedelta(minutes=expected)
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p1_scheduler.py -q -p no:cacheprovider`
Expected: FAIL (`job_ids` ohne `missing_…`/`upgrades_…`; `AttributeError: 'FakeAgent' object has no attribute 'refresh_config'`; Status bleibt `scheduled` beim Fehler).

- [ ] **Step 3: Implementierung**

In `backend/agents/base.py` oben ergänzen:

```python
import logging

logger = logging.getLogger("missingarr.agent")

MIN_INTERVAL_MINUTES = 1
MAX_INTERVAL_MINUTES = 10080
UPGRADE_INTERVAL_FACTOR = 4
```

In `BaseAgent.__init__` nach `self._thread = None`: `self._scheduler_failed = False`. Den Startwert in `self.state` von `"status": "scheduled"` auf `"status": "starting"` ändern: bis `_run` den Scheduler wirklich gestartet hat, darf die Karte nicht „scheduled“ behaupten (A1); die Karte zeigt für `starting` wie für jeden unbekannten Wert „WAIT“.

`start`, `_run` und neue Hilfsmethoden ersetzen bzw. einfügen:

```python
    SEARCH_JOBS = (("missing", "search_missing_enabled"), ("upgrades", "search_upgrades_enabled"))

    def start(self):
        self._stop_event.clear()
        self._abort_event.clear()
        self._skills = self.build_skills()
        self._thread = threading.Thread(
            target=self._run,
            name=f"agent-{self.config['id']}-{self.config['name']}",
            daemon=True,
        )
        self._thread.start()
        # "Agent started" is logged by _run once the scheduler really runs (A1).

    def _interval_minutes(self) -> int:
        raw = self.config.get("interval_minutes", 15)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = 15
        clamped = min(max(value, MIN_INTERVAL_MINUTES), MAX_INTERVAL_MINUTES)
        if clamped != value:
            self.log(
                "warn", "system",
                f"interval_minutes={raw} is outside {MIN_INTERVAL_MINUTES}..{MAX_INTERVAL_MINUTES} "
                f"— using {clamped}",
            )
        return clamped

    def _build_scheduler(self) -> BackgroundScheduler:
        scheduler = BackgroundScheduler(
            timezone="UTC",
            job_defaults={"misfire_grace_time": 60, "coalesce": True},
        )
        instance_id = self.config["id"]
        interval = self._interval_minutes()
        upgrade_interval = interval * UPGRADE_INTERVAL_FACTOR
        now = datetime.now(timezone.utc)

        # Both search jobs are always registered and _run_skill gates them on
        # the enable flag it re-reads from the database. Registering only the
        # enabled ones meant a skill switched on from the card never ran (A2).
        scheduler.add_job(
            self._run_skill, "interval", minutes=interval,
            start_date=now + timedelta(minutes=interval),
            args=["search_missing"], id=f"missing_{instance_id}",
        )
        scheduler.add_job(
            self._run_skill, "interval", minutes=upgrade_interval,
            start_date=now + timedelta(minutes=upgrade_interval),
            args=["search_upgrades"], id=f"upgrades_{instance_id}",
        )
        scheduler.add_job(
            self._run_skill, "interval", minutes=self.HEALTH_CHECK_INTERVAL_MINUTES,
            args=["health_check"], id=f"health_{instance_id}",
            next_run_time=now + timedelta(seconds=10),
        )
        # Verification runs regardless of the search flags: entries submitted
        # before a skill was switched off would stay unresolved otherwise.
        scheduler.add_job(
            self._run_skill, "interval", minutes=2,
            args=["verify_commands"], id=f"verify_{instance_id}",
            next_run_time=now + timedelta(seconds=30),
        )
        return scheduler

    def _run(self):
        scheduler = None
        try:
            scheduler = self._build_scheduler()
            scheduler.start()
        except Exception as exc:
            logger.exception("Scheduler for instance %s failed to start", self.config.get("id"))
            self._scheduler_failed = True
            # Log first, then publish the status: whoever sees "error" must find the reason.
            self.log("error", "system", f"Scheduler failed to start — no searches will run: {exc}")
            self.state["next_run_at"] = None
            self.state["status"] = "error"
            if scheduler is not None and scheduler.running:
                scheduler.shutdown(wait=False)
            return

        self._scheduler = scheduler
        self._update_next_run()
        self.state["status"] = "scheduled"
        self.log("info", "system",
                 f"Agent started — {self.config['type'].upper()} '{self.config['name']}'")

        self._stop_event.wait()
        if scheduler.running:
            scheduler.shutdown(wait=False)

    def _load_fresh_config(self) -> dict | None:
        try:
            return db.instances.get_by_id(self.config["id"])
        except Exception as exc:
            logger.warning("Could not reload config of instance %s: %s", self.config.get("id"), exc)
            return None

    def refresh_config(self) -> None:
        """Pick up changed flags without restarting — a running search keeps going."""
        fresh = self._load_fresh_config()
        if fresh:
            self.config = fresh
        self._update_next_run()

    def _update_next_run(self):
        scheduler = self._scheduler
        if scheduler is None:
            return
        times = []
        for prefix, flag in self.SEARCH_JOBS:
            if not self.config.get(flag):
                continue
            job = scheduler.get_job(f"{prefix}_{self.config['id']}")
            if job is not None and job.next_run_time is not None:
                times.append(job.next_run_time)
        self.state["next_run_at"] = min(times).isoformat() if times else None
```

`self._abort_event` entsteht erst in P1.4. Damit `start()` schon jetzt läuft, in `__init__` sofort ergänzen: `self._abort_event = threading.Event()`.

Am Anfang von `_run_skill` die Zeilen `fresh = db.instances.get_by_id(...)` / `if fresh: self.config = fresh` ersetzen durch:

```python
        fresh = self._load_fresh_config()
        if fresh:
            self.config = fresh
```

Im `finally` von `_run_skill`:

```python
        finally:
            if drives_display:
                self.state["status"] = "error" if self._scheduler_failed else "scheduled"
                self._update_next_run()
            lock.release()
```

In `backend/agents/sonarr.py` und `radarr.py` den Kommentar in `build_skills` ersetzen durch:

```python
        # Always register all skills: force triggers need them, and the
        # scheduler registers a job for every skill and gates it on the
        # enable flags in _run_skill.
```

- [ ] **Step 4: Tests grün**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p1_scheduler.py -q -p no:cacheprovider && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/agents/ tests/test_p1_scheduler.py
git commit -m "fix: always schedule both search jobs, report scheduler start failures, track the earliest next run"
```

### Task P1.3: `InstanceRuntime`, atomare Rate-Reservierung, fehlertolerantes Log

**Files:**
- Modify: `backend/agents/base.py` (neu `InstanceRuntime`, `__init__`, `_skill_lock`, Rate-Methoden, `log`)
- Modify: `backend/agents/orchestrator.py` (`__init__`, `_runtime`, `_agent_class`, `_make_agent`)
- Test: `tests/test_p1_runtime.py`

**Interfaces:**
- Consumes: nichts Neues
- Produces: `InstanceRuntime`, `BaseAgent(config, broadcaster=None, runtime=None)`, `reserve_action()`, `release_action(token)`, `get_rate_used()`, Broadcast mit `created_at`, `log()` wirft nie. Übergangsweise `check_rate_cap()`/`record_action()` auf der Runtime.

- [ ] **Step 1: Failing test schreiben**

`tests/test_p1_runtime.py` (Bausteine von oben einfügen, dazu):

```python
import sqlite3
import threading

from backend.agents.base import InstanceRuntime
from backend.agents.orchestrator import Orchestrator


class RecordingBroadcaster:
    def __init__(self):
        self.entries = []

    def broadcast(self, entry):
        self.entries.append(entry)


def config(**fields):
    return {"id": 1, "name": "Sonarr", "type": "sonarr", "url": "http://127.0.0.1:9",
            "api_key": "k" * 32, "rate_cap": 1, "rate_window_minutes": 60, **fields}


def test_concurrent_reservations_respect_the_cap():
    agent = FakeAgent(config(rate_cap=1))
    barrier = threading.Barrier(8)
    tokens = []

    def reserve():
        barrier.wait()
        tokens.append(agent.reserve_action())

    threads = [threading.Thread(target=reserve) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len([t for t in tokens if t is not None]) == 1
    assert agent.get_rate_used() == 1


def test_released_reservation_frees_the_slot():
    agent = FakeAgent(config(rate_cap=1))
    token = agent.reserve_action()
    assert agent.reserve_action() is None
    agent.release_action(token)
    assert agent.reserve_action() is not None


def test_old_actions_leave_the_window():
    agent = FakeAgent(config(rate_cap=1, rate_window_minutes=60))
    agent.runtime.action_timestamps.append(time.monotonic() - 3601)
    assert agent.reserve_action() is not None


def test_runtime_is_shared_between_agents_of_one_instance():
    runtime = InstanceRuntime()
    first = FakeAgent(config(rate_cap=2), runtime=runtime)
    first.reserve_action()
    second = FakeAgent(config(rate_cap=2), runtime=runtime)
    assert second.get_rate_used() == 1
    assert second.runtime.skill_lock("search_missing") is first.runtime.skill_lock("search_missing")


def test_rate_window_survives_reload_and_disable(db_path):
    inst = make_instance(rate_cap=5)
    orch = Orchestrator()
    orch._agent_class = lambda arr_type: FakeAgent
    orch.start_agent(inst["id"])
    old = orch._agents[inst["id"]]
    assert old.reserve_action() is not None
    orch.reload_agent(inst["id"])
    new = orch._agents[inst["id"]]
    assert new is not old
    assert new.get_rate_used() == 1
    orch.stop_agent(inst["id"])
    orch.start_agent(inst["id"])
    assert orch._agents[inst["id"]].get_rate_used() == 1
    orch.stop_all()


def test_throwaway_force_agent_uses_the_instance_runtime(db_path):
    inst = make_instance(enabled=False)
    orch = Orchestrator()
    orch._agent_class = lambda arr_type: FakeAgent
    agent = orch._make_agent(db.instances.get_by_id(inst["id"]))
    assert agent.runtime is orch._runtime(inst["id"])


def test_log_never_raises_and_still_broadcasts(db_path, monkeypatch):
    broadcaster = RecordingBroadcaster()
    agent = FakeAgent(config(), broadcaster=broadcaster)

    def locked(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(db.activity, "insert", locked)
    agent.log("debug", "search_missing", "EpisodeSearch: Show S01E01")
    assert broadcaster.entries[0]["message"] == "EpisodeSearch: Show S01E01"
    assert len(broadcaster.entries[0]["created_at"]) == 19
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p1_runtime.py -q -p no:cacheprovider`
Expected: FAIL mit `ImportError: cannot import name 'InstanceRuntime'`.

- [ ] **Step 3: Implementierung**

`backend/agents/base.py`: Import `from dataclasses import dataclass, field` ergänzen und vor `class BaseAgent` einfügen:

```python
@dataclass
class InstanceRuntime:
    """Per-instance state that must outlive one agent object.

    Saving the form, switching the instance off and on, and a force run on a
    disabled instance each create a new agent. The rate window and the skill
    locks belong to the instance: otherwise every save refilled the rate cap
    (A-L4) and a run of the old agent could overlap one of the new agent.
    """

    rate_lock: threading.Lock = field(default_factory=threading.Lock)
    action_timestamps: deque = field(default_factory=deque)
    skill_locks: dict = field(default_factory=dict)
    skill_locks_guard: threading.Lock = field(default_factory=threading.Lock)

    def skill_lock(self, skill_name: str) -> threading.Lock:
        with self.skill_locks_guard:
            return self.skill_locks.setdefault(skill_name, threading.Lock())

    def busy_skills(self) -> list[str]:
        with self.skill_locks_guard:
            return sorted(name for name, lock in self.skill_locks.items() if lock.locked())


def wait_runtime_idle(runtime: InstanceRuntime, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while runtime.busy_skills():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True
```

`BaseAgent.__init__` Signatur `def __init__(self, config: dict, broadcaster=None, runtime: InstanceRuntime | None = None):`. Die Zeilen für `self._lock`, `self._skill_locks`, `self._skill_locks_guard`, `self._action_timestamps` entfernen und ersetzen durch `self.runtime = runtime if runtime is not None else InstanceRuntime()`.

`_skill_lock` ersetzen:

```python
    def _skill_lock(self, skill_name: str) -> threading.Lock:
        return self.runtime.skill_lock(skill_name)
```

`check_rate_cap`, `record_action`, `get_rate_used` ersetzen durch:

```python
    def _rate_window_seconds(self) -> float:
        try:
            return max(0, int(self.config.get("rate_window_minutes", 60))) * 60
        except (TypeError, ValueError):
            return 3600

    def _rate_cap(self) -> int:
        try:
            return int(self.config.get("rate_cap", 25))
        except (TypeError, ValueError):
            return 25

    def _prune_actions(self, now: float) -> None:
        """Caller holds runtime.rate_lock."""
        cutoff = now - self._rate_window_seconds()
        stamps = self.runtime.action_timestamps
        while stamps and stamps[0] < cutoff:
            stamps.popleft()

    def reserve_action(self) -> float | None:
        """Check the cap and claim a slot in one step (A10).

        Missing and upgrade runs hold different skill locks and run in
        parallel; checking and recording separately let both pass the last
        free slot. Returns a token for release_action(), or None when the cap
        is reached.
        """
        now = time.monotonic()
        with self.runtime.rate_lock:
            self._prune_actions(now)
            if len(self.runtime.action_timestamps) >= self._rate_cap():
                return None
            self.runtime.action_timestamps.append(now)
            return now

    def release_action(self, token: float) -> None:
        """Give a slot back when the command was not accepted by *arr."""
        with self.runtime.rate_lock:
            try:
                self.runtime.action_timestamps.remove(token)
            except ValueError:
                pass

    def get_rate_used(self) -> int:
        with self.runtime.rate_lock:
            self._prune_actions(time.monotonic())
            return len(self.runtime.action_timestamps)

    # Deprecated: only until the skills use reserve_action() (P2). Task Z removes them.
    def check_rate_cap(self) -> bool:
        with self.runtime.rate_lock:
            self._prune_actions(time.monotonic())
            return len(self.runtime.action_timestamps) < self._rate_cap()

    def record_action(self) -> None:
        with self.runtime.rate_lock:
            self.runtime.action_timestamps.append(time.monotonic())
```

`log` ersetzen:

```python
    def log(self, level: str, skill: str, message: str):
        cfg = self.config
        instance_id = cfg.get("id")
        instance_name = cfg.get("name", "unknown")

        api_key = cfg.get("api_key") or ""
        if api_key and api_key in message:
            message = message.replace(api_key, "****")

        try:
            db.activity.insert(instance_id, instance_name, level, message, skill)
        except Exception as exc:
            # A log line must never turn a successful action into a failure:
            # the POST to *arr has already happened when the debug line after
            # it is written (B2).
            logger.warning("Could not store log line for instance %s (%s): %s",
                           instance_id, exc, message)

        if self.broadcaster:
            try:
                self.broadcaster.broadcast({
                    "instance_id": instance_id,
                    "instance_name": instance_name,
                    "level": level,
                    "skill": skill,
                    "message": message,
                    "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })
            except Exception as exc:
                logger.warning("Could not broadcast log line: %s", exc)
```

`backend/agents/orchestrator.py`: Import `from backend.agents.base import BaseAgent, InstanceRuntime`; in `__init__` ergänzen:

```python
        self._runtimes: dict[int, InstanceRuntime] = {}
        self._runtimes_lock = threading.Lock()
```

`_make_agent` ersetzen:

```python
    def _runtime(self, instance_id: int) -> InstanceRuntime:
        with self._runtimes_lock:
            return self._runtimes.setdefault(instance_id, InstanceRuntime())

    def _agent_class(self, arr_type: str):
        if arr_type == "sonarr":
            return SonarrAgent
        if arr_type == "radarr":
            return RadarrAgent
        raise ValueError(f"Unknown instance type: {arr_type}")

    def _make_agent(self, config: dict) -> BaseAgent:
        agent_class = self._agent_class(config.get("type", "sonarr"))
        return agent_class(config, self.broadcaster, runtime=self._runtime(config["id"]))
```

- [ ] **Step 4: Tests grün**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p1_runtime.py tests/test_p1_scheduler.py -q -p no:cacheprovider && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/agents/ tests/test_p1_runtime.py
git commit -m "fix: keep the rate window per instance, reserve rate slots atomically, never fail on a log line"
```

### Task P1.4: Trigger-Rückgabe, Abbruch-Signal, Ruhezeit-Ausnahme, Orchestrator-API

**Files:**
- Modify: `backend/agents/base.py` (`stop`, neu `request_abort`, `stop_requested`, `wait_or_stop`, `wait_idle`, `_run_skill`, `trigger_now`, `reload`, Konstanten)
- Modify: `backend/agents/orchestrator.py` (`start_agent`, `stop_agent`, `reload_agent`, neu `refresh_config`, `forget_instance`, `trigger`, `_adhoc`)
- Test: `tests/test_p1_control.py`

**Interfaces:**
- Consumes: `InstanceRuntime`, `wait_runtime_idle` (P1.3)
- Produces: `TRIGGER_STARTED/BUSY/UNKNOWN_SKILL`, `TRIGGER_NOT_FOUND`, `QUIET_HOURS_EXEMPT`, `stop(abort_running)`, `request_abort()`, `stop_requested()`, `wait_or_stop()`, `wait_idle()`, Orchestrator `stop_agent(…, abort_running, wait_seconds)`, `refresh_config`, `forget_instance`, `trigger() -> str`.

- [ ] **Step 1: Failing test schreiben**

`tests/test_p1_control.py` (Bausteine von oben einfügen, dazu):

```python
import threading
from datetime import datetime, timedelta

from backend.agents.base import (
    QUIET_HOURS_EXEMPT, TRIGGER_BUSY, TRIGGER_STARTED, TRIGGER_UNKNOWN_SKILL,
)
from backend.agents.orchestrator import TRIGGER_NOT_FOUND, Orchestrator


def fake_orchestrator():
    orch = Orchestrator()
    orch._agent_class = lambda arr_type: FakeAgent
    return orch


def prepared_agent(inst):
    agent = FakeAgent(db.instances.get_by_id(inst["id"]))
    agent._skills = agent.build_skills()
    return agent


def test_trigger_reports_busy_unknown_and_started(db_path):
    inst = make_instance()
    agent = prepared_agent(inst)
    assert agent.trigger_now("does_not_exist") == TRIGGER_UNKNOWN_SKILL
    lock = agent.runtime.skill_lock("search_missing")
    lock.acquire()
    try:
        assert agent.trigger_now("search_missing") == TRIGGER_BUSY
    finally:
        lock.release()
    assert agent.trigger_now("search_missing") == TRIGGER_STARTED
    wait_until(lambda: agent._get_skill("search_missing").calls == [True])


def test_orchestrator_trigger_for_unknown_instance(db_path):
    assert fake_orchestrator().trigger(999, "search_missing") == TRIGGER_NOT_FOUND


def test_force_run_no_longer_waits_for_a_running_skill(db_path):
    agent = prepared_agent(make_instance())
    lock = agent.runtime.skill_lock("search_missing")
    lock.acquire()
    try:
        started = time.monotonic()
        agent._run_skill("search_missing", force=True)
        assert time.monotonic() - started < 0.5
    finally:
        lock.release()
    assert agent._get_skill("search_missing").calls == []


def test_verification_runs_during_quiet_hours_but_searches_do_not(db_path):
    now = datetime.now()
    start = (now - timedelta(minutes=5)).strftime("%H:%M")
    end = (now + timedelta(minutes=5)).strftime("%H:%M")
    inst = make_instance(quiet_start=start, quiet_end=end)
    agent = prepared_agent(inst)
    assert set(QUIET_HOURS_EXEMPT) == {"health_check", "verify_commands"}
    agent._run_skill("verify_commands")
    agent._run_skill("search_missing")
    assert agent._get_skill("verify_commands").calls == [False]
    assert agent._get_skill("search_missing").calls == []


def test_stop_aborts_running_work_but_reload_does_not(db_path):
    inst = make_instance()
    orch = fake_orchestrator()
    orch.start_agent(inst["id"])
    old = orch._agents[inst["id"]]
    orch.reload_agent(inst["id"])
    assert old.stop_requested() is False
    new = orch._agents[inst["id"]]
    orch.stop_agent(inst["id"])
    assert new.stop_requested() is True
    assert new.wait_or_stop(5) is True


def test_a_run_skipped_for_quiet_hours_moves_the_countdown_on(db_path):
    now = datetime.now()
    inst = make_instance(interval_minutes=1,
                         quiet_start=(now - timedelta(minutes=5)).strftime("%H:%M"),
                         quiet_end=(now + timedelta(minutes=5)).strftime("%H:%M"))
    agent = FakeAgent(db.instances.get_by_id(inst["id"]))
    agent.start()
    wait_until(lambda: agent.state["status"] == "scheduled")
    try:
        agent.state["next_run_at"] = "2000-01-01T00:00:00+00:00"   # the fire time that just passed
        agent._run_skill("search_missing")
        job = agent._scheduler.get_job(f"missing_{inst['id']}")
        assert agent.state["status"] == "quiet"
        assert agent.state["next_run_at"] == job.next_run_time.isoformat()
        assert agent._get_skill("search_missing").calls == []
    finally:
        agent.stop()


def test_wait_or_stop_times_out_without_abort(db_path):
    agent = prepared_agent(make_instance())
    started = time.monotonic()
    assert agent.wait_or_stop(0.05) is False
    assert time.monotonic() - started >= 0.05


class SlowSkill(FakeSkill):
    def execute(self, agent, force=False):
        self.calls.append(force)
        agent.wait_or_stop(5)   # a long search that ends early on abort


class SlowAgent(BaseAgent):
    def build_skills(self):
        return [SlowSkill(n) for n in
                ("search_missing", "search_upgrades", "health_check", "verify_commands")]


def test_disabling_aborts_every_throwaway_force_agent(db_path):
    inst = make_instance(enabled=False)
    orch = Orchestrator()
    orch._agent_class = lambda arr_type: SlowAgent
    assert orch.trigger(inst["id"], "search_missing") == TRIGGER_STARTED
    assert orch.trigger(inst["id"], "search_upgrades") == TRIGGER_STARTED
    adhocs = set(orch._adhoc[inst["id"]])
    assert len(adhocs) == 2
    orch.stop_agent(inst["id"])
    assert all(adhoc.stop_requested() for adhoc in adhocs)
    wait_until(lambda: not orch._runtime(inst["id"]).busy_skills(), timeout=2)


def test_finished_throwaway_agents_are_forgotten(db_path):
    inst = make_instance(enabled=False)
    orch = fake_orchestrator()
    assert orch.trigger(inst["id"], "search_missing") == TRIGGER_STARTED
    wait_until(lambda: inst["id"] not in orch._adhoc)


def test_forget_instance_waits_for_running_skills(db_path):
    inst = make_instance()
    orch = fake_orchestrator()
    orch.start_agent(inst["id"])
    lock = orch._runtime(inst["id"]).skill_lock("search_missing")
    lock.acquire()
    threading.Timer(0.3, lock.release).start()
    started = time.monotonic()
    orch.forget_instance(inst["id"], wait_seconds=3)
    assert time.monotonic() - started >= 0.25
    assert inst["id"] not in orch._runtimes
    assert not orch.is_running(inst["id"])


def test_refresh_config_updates_a_running_agent(db_path):
    inst = make_instance(search_upgrades_enabled=False)
    orch = fake_orchestrator()
    orch.start_agent(inst["id"])
    agent = orch._agents[inst["id"]]
    wait_until(lambda: agent.state["status"] == "scheduled")
    db.instances.toggle_skill(inst["id"], "upgrades", True)
    orch.refresh_config(inst["id"])
    assert agent.config["search_upgrades_enabled"] == 1
    assert orch._agents[inst["id"]] is agent
    orch.stop_all()
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p1_control.py -q -p no:cacheprovider`
Expected: FAIL mit `ImportError: cannot import name 'QUIET_HOURS_EXEMPT'`.

- [ ] **Step 3: Implementierung**

`backend/agents/base.py`, Konstanten oben:

```python
TRIGGER_STARTED = "started"
TRIGGER_BUSY = "busy"
TRIGGER_UNKNOWN_SKILL = "unknown_skill"

# Verification only reads command status from *arr; holding it back during
# quiet hours just delayed verdicts and, near 24 h, lost them (B5).
QUIET_HOURS_EXEMPT = ("health_check", "verify_commands")
```

Methoden in `BaseAgent`:

```python
    def stop(self, abort_running: bool = True):
        """Stop scheduling. With abort_running a search in progress ends at its
        next check (A-L5); a reload passes False so saving the form does not
        cut a long run short."""
        if abort_running:
            self._abort_event.set()
        self._stop_event.set()
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self.state["status"] = "off"
        self.state["next_run_at"] = None
        self.log("info", "system", f"Agent stopped — '{self.config['name']}'")

    def request_abort(self) -> None:
        self._abort_event.set()

    def stop_requested(self) -> bool:
        return self._abort_event.is_set()

    def wait_or_stop(self, seconds: float) -> bool:
        """Sleep between actions; returns True as soon as an abort is requested."""
        if seconds <= 0:
            return self._abort_event.is_set()
        return self._abort_event.wait(seconds)

    def wait_idle(self, timeout: float) -> bool:
        return wait_runtime_idle(self.runtime, timeout)

    def reload(self, new_config: dict):
        self.stop(abort_running=False)
        self.config = new_config
        self.state["connection_status"] = new_config.get("connection_status", "unknown")
        self.start()
```

In `_run_skill` die beiden Deaktiviert-Zweige, die Ruhezeit-Bedingung und das Sperren ersetzen. Jeder vorzeitige Ausstieg eines Such-Jobs rechnet `next_run_at` neu; sonst zählte die Karte nach einem übersprungenen Lauf auf `00m 00s` und bliebe dort bis zum nächsten echten Lauf (A13):

```python
        if not force and skill_name == "search_missing" and not self.config.get("search_missing_enabled"):
            self.log("debug", skill_name, "Skipping — missing search disabled")
            self._update_next_run()
            return
        if not force and skill_name == "search_upgrades" and not self.config.get("search_upgrades_enabled"):
            self.log("debug", skill_name, "Skipping — upgrades search disabled")
            self._update_next_run()
            return

        if skill_name not in QUIET_HOURS_EXEMPT and not force and self._in_quiet_hours():
            self.log("debug", skill_name, "Skipping — quiet hours active")
            self.state["status"] = "quiet"
            self._update_next_run()
            return

        # One run per skill at a time. A second request is dropped at once —
        # the API reports "busy" before it gets here (A11), so waiting would
        # only turn a double click into a second, repeated run.
        lock = self.runtime.skill_lock(skill_name)
        if not lock.acquire(blocking=False):
            self.log("warn", skill_name, "Already running — skipping duplicate trigger")
            return
```

(`import time` bleibt für `wait_runtime_idle`; die 90-s-Schleife mit `deadline` entfällt.)

`trigger_now` ersetzen:

```python
    def trigger_now(self, skill_name: str, force: bool = True, on_done=None) -> str:
        """Manual trigger — runs in its own thread. Returns TRIGGER_*.

        on_done(agent) is called in that thread once the run is over; the
        orchestrator uses it to forget a throwaway agent (and its decrypted
        config) as soon as it is finished."""
        if not self._get_skill(skill_name):
            self.log("warn", "system", f"Trigger ignored — skill '{skill_name}' not registered on this agent")
            return TRIGGER_UNKNOWN_SKILL
        if self.runtime.skill_lock(skill_name).locked():
            self.log("info", "system", f"Trigger for '{skill_name}' rejected — it is already running")
            return TRIGGER_BUSY
        self.log("info", "system", f"{'Force' if force else 'Manual'} trigger received for '{skill_name}'")

        def run():
            try:
                self._run_skill(skill_name, force)
            finally:
                if on_done is not None:
                    on_done(self)

        threading.Thread(
            target=run,
            name=f"trigger-{self.config['id']}-{skill_name}",
            daemon=True,
        ).start()
        return TRIGGER_STARTED
```

Import oben: `from typing import Callable` (für den Vertrag; die Signatur im Code darf `on_done=None` ohne Annotation bleiben).

`backend/agents/orchestrator.py` — Import erweitern: `from backend.agents.base import BaseAgent, InstanceRuntime, wait_runtime_idle`, `import logging`, `logger = logging.getLogger("missingarr.orchestrator")`, `TRIGGER_NOT_FOUND = "not_found"`. In `__init__`: `self._adhoc: dict[int, set[BaseAgent]] = {}` — mehrere Wegwerf-Agenten je Instanz (etwa `search_missing` und `search_upgrades` gleichzeitig auf einer abgeschalteten Instanz); jeder wird nach seinem Lauf wieder ausgetragen. Methoden ersetzen/ergänzen:

```python
    def start_agent(self, instance_id: int):
        config = db.instances.get_by_id(instance_id)
        if not config or not config.get("enabled"):
            return
        with self._lock:
            old = self._agents.pop(instance_id, None)
        if old is not None:
            old.stop(abort_running=False)
        agent = self._make_agent(config)
        with self._lock:
            self._agents[instance_id] = agent
        agent.start()

    def stop_agent(self, instance_id: int, abort_running: bool = True, wait_seconds: float = 0.0):
        with self._lock:
            agent = self._agents.pop(instance_id, None)
            adhocs = self._adhoc.pop(instance_id, set()) if abort_running else set()
        if agent is not None:
            agent.stop(abort_running=abort_running)
        for adhoc in adhocs:
            adhoc.request_abort()
        if wait_seconds > 0 and not wait_runtime_idle(self._runtime(instance_id), wait_seconds):
            logger.warning("Instance %s still busy after %.0fs", instance_id, wait_seconds)

    def reload_agent(self, instance_id: int):
        self.stop_agent(instance_id, abort_running=False)
        config = db.instances.get_by_id(instance_id)
        if config and config.get("enabled"):
            self.start_agent(instance_id)

    def refresh_config(self, instance_id: int) -> None:
        with self._lock:
            agent = self._agents.get(instance_id)
        if agent is not None:
            agent.refresh_config()

    def forget_instance(self, instance_id: int, wait_seconds: float = 15.0) -> None:
        """Before deleting: abort, wait until no skill holds a lock, drop state."""
        self.stop_agent(instance_id, abort_running=True, wait_seconds=wait_seconds)
        with self._runtimes_lock:
            self._runtimes.pop(instance_id, None)

    def trigger(self, instance_id: int, skill_name: str, force: bool = True) -> str:
        with self._lock:
            agent = self._agents.get(instance_id)
        if agent is None:
            config = db.instances.get_by_id(instance_id)
            if not config:
                return TRIGGER_NOT_FOUND
            # Disabled instance: a throwaway agent on the shared runtime, kept
            # while it runs so that disabling or deleting can still abort it.
            agent = self._make_agent(config)
            agent._skills = agent.build_skills()
            with self._lock:
                self._adhoc.setdefault(instance_id, set()).add(agent)
            result = agent.trigger_now(
                skill_name, force=force,
                on_done=lambda done: self._forget_adhoc(instance_id, done),
            )
            if result != TRIGGER_STARTED:
                self._forget_adhoc(instance_id, agent)
            return result
        return agent.trigger_now(skill_name, force=force)

    def _forget_adhoc(self, instance_id: int, agent: BaseAgent) -> None:
        with self._lock:
            agents = self._adhoc.get(instance_id)
            if agents is None:
                return
            agents.discard(agent)
            if not agents:
                del self._adhoc[instance_id]
```

Import in `orchestrator.py` zusätzlich `TRIGGER_STARTED` aus `backend.agents.base`.

- [ ] **Step 4: Tests grün**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p1_control.py -q -p no:cacheprovider && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/agents/ tests/test_p1_control.py
git commit -m "fix: reject triggers for a running skill, abort searches on disable/delete, verify during quiet hours"
```

### Task P1.5: Keine Weiterleitungen bei *arr-Requests

**Files:**
- Modify: `backend/agents/base.py` (`http_get`, `http_post`, `http_get_raw`, neu `_check_response`)
- Test: `tests/test_p1_http.py`

**Interfaces:**
- Consumes: nichts
- Produces: 3xx → `requests.exceptions.HTTPError` mit gesetztem `.response` (P2 `health_check` wertet `exc.response.status_code` aus); `http_get_raw` liefert `(3xx, None)`.

- [ ] **Step 1: Failing test schreiben**

`tests/test_p1_http.py`:

```python
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from backend.agents.base import BaseAgent


class FakeAgent(BaseAgent):
    def build_skills(self):
        return []


class Target(BaseHTTPRequestHandler):
    hits = []

    def _answer(self):
        Target.hits.append((self.path, self.headers.get("X-Api-Key")))
        body = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _answer
    do_POST = _answer

    def log_message(self, *args):
        pass


class Redirect(BaseHTTPRequestHandler):
    target = ""

    def _answer(self):
        self.send_response(302)
        self.send_header("Location", Redirect.target + self.path)
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_GET = _answer
    do_POST = _answer

    def log_message(self, *args):
        pass


@pytest.fixture
def servers():
    started = []

    def serve(handler):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        started.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    Target.hits = []
    target = serve(Target)
    Redirect.target = target.replace("127.0.0.1", "localhost")
    redirect = serve(Redirect)
    yield target, redirect
    for server in started:
        server.shutdown()


def agent_for(url):
    return FakeAgent({"id": 1, "name": "x", "type": "sonarr", "url": url, "api_key": "SECRET"})


def test_direct_requests_still_work(servers):
    target, _ = servers
    assert agent_for(target).http_get("/api/v3/system/status") == {"ok": True}
    assert Target.hits == [("/api/v3/system/status", "SECRET")]


def test_redirects_are_not_followed_and_the_key_stays_home(servers):
    _, redirect = servers
    agent = agent_for(redirect)
    with pytest.raises(requests.exceptions.HTTPError) as caught:
        agent.http_get("/api/v3/wanted/missing")
    assert caught.value.response is not None
    assert caught.value.response.status_code == 302
    with pytest.raises(requests.exceptions.HTTPError):
        agent.http_post("/api/v3/command", {"name": "EpisodeSearch"})
    assert agent.http_get_raw("/api/v3/command/1") == (302, None)
    assert Target.hits == []
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p1_http.py -q -p no:cacheprovider`
Expected: FAIL — `Target.hits` enthält den weitergeleiteten Aufruf mit `SECRET`.

- [ ] **Step 3: Implementierung**

In `BaseAgent` ersetzen:

```python
    @staticmethod
    def _check_response(resp: requests.Response) -> None:
        # requests would follow a redirect and send X-Api-Key along to the new
        # host (only Authorization is stripped). *arr never redirects its API,
        # so a 3xx means a wrong URL — report it instead of following (C4).
        if 300 <= resp.status_code < 400:
            raise requests.exceptions.HTTPError(
                f"{resp.status_code} redirect not followed — check the instance URL",
                response=resp,
            )
        resp.raise_for_status()

    def http_get(self, path: str, params: Optional[dict] = None) -> dict:
        url = self.config["url"].rstrip("/") + path
        resp = requests.get(
            url,
            headers={"X-Api-Key": self.config["api_key"]},
            params=params or {},
            timeout=10,
            allow_redirects=False,
        )
        self._check_response(resp)
        return resp.json()

    def http_post(self, path: str, body: dict) -> dict:
        url = self.config["url"].rstrip("/") + path
        resp = requests.post(
            url,
            headers={"X-Api-Key": self.config["api_key"], "Content-Type": "application/json"},
            json=body,
            timeout=10,
            allow_redirects=False,
        )
        self._check_response(resp)
        return resp.json()
```

In `http_get_raw` den Aufruf um `allow_redirects=False` ergänzen (der Rest bleibt; ein 3xx fällt in `if resp.status_code != 200: return resp.status_code, None`).

- [ ] **Step 4: Tests grün**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p1_http.py -q -p no:cacheprovider && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/agents/base.py tests/test_p1_http.py
git commit -m "fix: never follow redirects on *arr requests so the API key stays with the configured host"
```

---
## Paket P3 — Datenbank und Verifikation (Welle 1)

Reihenfolge innerhalb von P3: in Welle 1 als Paket **P3a** P3.1, P3.2, P3.3, P3.6; in Welle 2 als Paket **P3b** P3.4 und P3.5 (sie rufen `agent.stop_requested()` aus P1.4 auf, das erst nach dem Merge von Welle 1 im Worktree steht). Die Überschrift „Welle 1“ gilt für P3a.

Abnahme P3a (Welle 1): `tests/test_p3_database.py`, `test_p3_searched.py`, `test_p3_history.py`, `test_p3_api.py` grün. Abnahme P3b (Welle 2): `tests/test_p3_verify.py`, `test_p3_housekeeping.py` grün. Beides: Gesamtsuite grün; `init_db()` auf den Alt-DBs 0.7.0 und 0.6.13 verliert keine Zeile und ist beim zweiten Lauf ohne Wirkung; `grep -n "retry_hours IN (1, 168)" backend/` findet nichts; `grep -n "except Exception:" backend/database.py` findet keinen Migrationsblock mehr.

Gemeinsame Test-Bausteine (in jede P3-Testdatei kopieren, die sie braucht):

```python
import sqlite3

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


def make_instance(**fields):
    data = {"name": "Sonarr", "type": "sonarr", "url": "http://127.0.0.1:9", "api_key": "k" * 32}
    data.update(fields)
    return db.instances.create(data)


def sql(statement, params=()):
    with database.get_db() as conn:
        return conn.execute(statement, params).fetchall()


class FailOn:
    """Connection proxy that fails the first statement containing `needle`."""

    def __init__(self, conn, needle):
        self._conn = conn
        self._needle = needle

    def execute(self, statement, params=()):
        if self._needle in statement:
            raise sqlite3.OperationalError(f"injected failure at {self._needle}")
        return self._conn.execute(statement, params)

    def __getattr__(self, name):
        return getattr(self._conn, name)
```

### Task P3.1: Migrationen, `retry_hours`, `busy_timeout`, Dateirechte, neue Einstellungen

**Files:**
- Modify: `backend/database.py` (ganze Datei außer `_widen_history_status_check` und `get_or_create_secret_key`)
- Modify: `backend/config.py`
- Test: `tests/test_p3_database.py`

**Interfaces:**
- Consumes: nichts
- Produces: `settings.secret_key`, `settings.cookie_secure`, `settings.history_retention_days` (`.env` mit unbekannten Schlüsseln wird toleriert); `app_settings.ancestor_rule_since` und `database.ANCESTOR_RULE_SINCE_SETTING`; neue Spalte `last_checked_at`; Index `idx_history_instance_started`; `database._COLUMN_MIGRATIONS`, `database._add_missing_columns(conn)`, `database._assert_schema(conn)`, `database._restrict_file_permissions()`.

- [ ] **Step 1: Failing test schreiben**

`tests/test_p3_database.py` (Bausteine von oben einfügen, dazu):

```python
import os
import stat

INSTANCES_DDL = """
CREATE TABLE instances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('sonarr','radarr')),
    url TEXT NOT NULL,
    api_key TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    search_missing_enabled INTEGER NOT NULL DEFAULT 1,
    search_upgrades_enabled INTEGER NOT NULL DEFAULT 0,
    interval_minutes INTEGER NOT NULL DEFAULT 15,
    retry_hours INTEGER NOT NULL DEFAULT 0,
    rate_window_minutes INTEGER NOT NULL DEFAULT 60,
    rate_cap INTEGER NOT NULL DEFAULT 25,
    search_order TEXT NOT NULL DEFAULT 'random'
        CHECK(search_order IN ('random','smart','newest_first','oldest_first')),
    missing_mode TEXT NOT NULL DEFAULT 'episode'
        CHECK(missing_mode IN ('smart','season_packs','show_batch','episode')),
    missing_per_run INTEGER NOT NULL DEFAULT 5,
    upgrades_per_run INTEGER NOT NULL DEFAULT 1,
    seconds_between_actions INTEGER NOT NULL DEFAULT 2,
    hours_after_release INTEGER NOT NULL DEFAULT 9,
    upgrade_source TEXT NOT NULL DEFAULT 'monitored_items_only'
        CHECK(upgrade_source IN ('wanted_list_only','monitored_items_only','both')),
    quiet_start TEXT,
    quiet_end TEXT,
    connection_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK(connection_status IN ('unknown','online','offline','error')),
    last_seen_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE activity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id INTEGER REFERENCES instances(id) ON DELETE CASCADE,
    instance_name TEXT NOT NULL,
    level TEXT NOT NULL CHECK(level IN ('info','warn','error','debug')),
    skill TEXT,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX idx_activity_created ON activity_log(created_at DESC);
CREATE TABLE searched_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id INTEGER NOT NULL REFERENCES instances(id) ON DELETE CASCADE,
    cache_key TEXT NOT NULL,
    title TEXT NOT NULL,
    item_type TEXT NOT NULL,
    searched_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(instance_id, cache_key)
);
CREATE INDEX idx_searched_instance ON searched_items(instance_id);
CREATE INDEX idx_searched_at ON searched_items(searched_at DESC);
CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

# Layout of a 0.7.0 database after all 0.7.0 migrations and the status rebuild.
SCHEMA_0_7_0 = INSTANCES_DDL + """
CREATE TABLE search_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id INTEGER REFERENCES instances(id) ON DELETE CASCADE,
    instance_name TEXT NOT NULL,
    skill TEXT NOT NULL CHECK(skill IN ('search_missing','search_upgrades')),
    wanted_count INTEGER NOT NULL DEFAULT 0,
    triggered_count INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running'
        CHECK(status IN ('running','success','error','pending','partial','failed','unverified')),
    error_message TEXT,
    verified_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_history_instance ON search_history(instance_id);
CREATE INDEX idx_history_started ON search_history(started_at DESC);
CREATE TABLE search_history_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES search_history(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    arr_id INTEGER,
    item_type TEXT NOT NULL CHECK(item_type IN ('movie','episode','season','series')),
    command_id INTEGER,
    command_status TEXT NOT NULL DEFAULT 'legacy',
    cache_key TEXT NOT NULL DEFAULT '',
    verified_at TEXT,
    created_at TEXT
);
CREATE INDEX idx_history_items_run ON search_history_items(run_id);
CREATE INDEX idx_history_items_pending ON search_history_items(command_status);
"""

SEED_0_7_0 = """
INSERT INTO instances (id, name, type, url, api_key, retry_hours)
VALUES (1, 'Sonarr', 'sonarr', 'http://sonarr:8989', 'enc:abc', 168),
       (2, 'Radarr', 'radarr', 'http://radarr:7878', 'enc:def', 1);
INSERT INTO search_history (id, instance_id, instance_name, skill, started_at, status)
VALUES (1, 1, 'Sonarr', 'search_missing', '2026-09-01 10:00:00', 'running'),
       (2, 1, 'Sonarr', 'search_missing', '2026-09-01 11:00:00', 'pending'),
       (3, 2, 'Radarr', 'search_missing', '2026-09-01 12:00:00', 'success');
INSERT INTO search_history_items (run_id, title, item_type, command_id, command_status, cache_key, created_at)
VALUES (2, 'Show S01E01', 'episode', 101, 'submitted', 'ep:1', '2026-09-01 11:00:01'),
       (3, 'Movie (2020)', 'movie', NULL, 'legacy', '', NULL);
INSERT INTO searched_items (instance_id, cache_key, title, item_type, searched_at)
VALUES (1, 'ep:1', 'Show S01E01', 'episode', '2026-09-01 11:00:01');
INSERT INTO activity_log (instance_id, instance_name, level, message) VALUES (1, 'Sonarr', 'info', 'hello');
INSERT INTO app_settings (key, value) VALUES ('encryption_key', 'x'), ('secret_key', 'y');
"""

# Before command verification (0.6.13): narrow status CHECK, items without command columns.
SCHEMA_0_6_13 = INSTANCES_DDL + """
CREATE TABLE search_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id INTEGER REFERENCES instances(id) ON DELETE CASCADE,
    instance_name TEXT NOT NULL,
    skill TEXT NOT NULL CHECK(skill IN ('search_missing','search_upgrades')),
    wanted_count INTEGER NOT NULL DEFAULT 0,
    triggered_count INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running','success','error')),
    error_message TEXT
);
CREATE INDEX idx_history_instance ON search_history(instance_id);
CREATE INDEX idx_history_started ON search_history(started_at DESC);
CREATE TABLE search_history_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES search_history(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    arr_id INTEGER,
    item_type TEXT NOT NULL CHECK(item_type IN ('movie','episode','season','series'))
);
CREATE INDEX idx_history_items_run ON search_history_items(run_id);
"""

SEED_0_6_13 = """
INSERT INTO instances (id, name, type, url, api_key) VALUES (1, 'Sonarr', 'sonarr', 'http://sonarr:8989', 'enc:abc');
INSERT INTO search_history (id, instance_id, instance_name, skill, started_at, status)
VALUES (1, 1, 'Sonarr', 'search_missing', '2026-03-01 10:00:00', 'success'),
       (2, 1, 'Sonarr', 'search_upgrades', '2026-03-01 11:00:00', 'success');
INSERT INTO search_history_items (run_id, title, arr_id, item_type)
VALUES (1, 'A', 1, 'episode'), (1, 'B', 2, 'episode'), (2, 'C', 3, 'season');
"""

TABLES = ["instances", "search_history", "search_history_items", "searched_items",
          "activity_log", "app_settings"]


def build(path, script):
    conn = sqlite3.connect(path)
    conn.executescript(script)
    conn.commit()
    conn.close()


def counts(path):
    conn = sqlite3.connect(path)
    try:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
    finally:
        conn.close()


def columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def ancestor_rule_since(path):
    conn = sqlite3.connect(path)
    try:
        row = conn.execute("SELECT value FROM app_settings WHERE key='ancestor_rule_since'").fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def test_retry_hours_survive_restarts(db_path):
    for hours in (1, 168):
        make_instance(name=f"i{hours}", retry_hours=hours)
    database.init_db()
    database.init_db()
    assert sorted(r[0] for r in sql("SELECT retry_hours FROM instances")) == [1, 168]


def test_0_7_0_database_upgrades_in_place_and_idempotently(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    build(path, SCHEMA_0_7_0 + SEED_0_7_0)
    before = counts(path)
    monkeypatch.setattr(settings, "database_url", str(path))
    database.init_db()
    marker = ancestor_rule_since(path)
    database.init_db()
    # Only new row: the ancestor-rule marker, written once (A9).
    assert counts(path) == {**before, "app_settings": before["app_settings"] + 1}
    assert marker is not None and ancestor_rule_since(path) == marker
    conn = sqlite3.connect(path)
    try:
        assert dict(conn.execute("SELECT id, retry_hours FROM instances")) == {1: 168, 2: 1}
        assert "last_checked_at" in columns(conn, "search_history_items")
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_history_instance_started'"
        ).fetchone()
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("SELECT status FROM search_history WHERE id=1").fetchone()[0] == "running"
    finally:
        conn.close()


def test_0_6_13_database_keeps_every_history_item(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    build(path, SCHEMA_0_6_13 + SEED_0_6_13)
    monkeypatch.setattr(settings, "database_url", str(path))
    database.init_db()
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM search_history_items").fetchone()[0] == 3
        assert {r[0] for r in conn.execute("SELECT command_status FROM search_history_items")} == {"legacy"}
        table_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='search_history'"
        ).fetchone()[0]
        assert "'pending'" in table_sql
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


REBUILD_STEPS = [
    "CREATE TABLE search_history_rebuilt",
    "INSERT INTO search_history_rebuilt",
    "DROP TABLE search_history",
    "ALTER TABLE search_history_rebuilt RENAME",
    "CREATE INDEX IF NOT EXISTS idx_history_instance",
    "CREATE INDEX IF NOT EXISTS idx_history_started",
]


@pytest.mark.parametrize("step", REBUILD_STEPS)
def test_rebuild_failure_at_any_step_leaves_history_and_items_intact(tmp_path, monkeypatch, step):
    path = tmp_path / "legacy.db"
    build(path, SCHEMA_0_6_13 + SEED_0_6_13 +
          "ALTER TABLE search_history ADD COLUMN verified_count INTEGER NOT NULL DEFAULT 0;")
    real_connect = sqlite3.connect

    class FailingStep:
        def __init__(self, connection):
            object.__setattr__(self, "connection", connection)

        def __getattr__(self, name):
            return getattr(self.connection, name)

        def __setattr__(self, name, value):
            setattr(self.connection, name, value)

        def execute(self, statement, parameters=()):
            if statement.strip().startswith(step):
                raise sqlite3.OperationalError("injected failure")
            return self.connection.execute(statement, parameters)

    monkeypatch.setattr(settings, "database_url", str(path))
    monkeypatch.setattr(database.sqlite3, "connect",
                        lambda *a, **k: FailingStep(real_connect(*a, **k)))
    with pytest.raises(sqlite3.OperationalError, match="injected failure"):
        database._widen_history_status_check()

    conn = real_connect(path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM search_history").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM search_history_items").fetchone()[0] == 3
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='search_history_rebuilt'"
        ).fetchone() is None
    finally:
        conn.close()


def test_unexpected_migration_error_stops_the_start(db_path, monkeypatch):
    # SQLite refuses this on every table, empty or not (a non-constant default
    # only fails on tables that already hold rows).
    broken = ("search_history_items", "broken", "INTEGER PRIMARY KEY")
    monkeypatch.setattr(database, "_COLUMN_MIGRATIONS", database._COLUMN_MIGRATIONS + [broken])
    with pytest.raises(sqlite3.OperationalError, match="PRIMARY KEY"):
        database.init_db()


def test_a_column_added_in_the_meantime_is_tolerated(db_path, monkeypatch):
    monkeypatch.setattr(database, "_columns", lambda conn, table: set())
    with database.get_db() as conn:
        database._add_missing_columns(conn)


def test_incomplete_schema_is_reported(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    build(path, SCHEMA_0_7_0)
    monkeypatch.setattr(settings, "database_url", str(path))
    monkeypatch.setattr(database, "_add_missing_columns", lambda conn: None)
    with pytest.raises(RuntimeError, match="search_history_items.last_checked_at"):
        database.init_db()


def test_database_file_is_private(db_path):
    assert stat.S_IMODE(os.stat(db_path).st_mode) == 0o600


def test_connections_wait_for_locks(db_path):
    with database.get_db() as conn:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000


def test_new_settings_have_safe_defaults():
    from backend.config import Settings
    fresh = Settings(_env_file=None)
    assert fresh.secret_key == ""
    assert fresh.cookie_secure is False
    assert fresh.history_retention_days == 365
    with pytest.raises(ValueError):
        Settings(_env_file=None, history_retention_days=-1)


def test_env_file_copied_from_the_example_is_accepted(tmp_path):
    # .env.example carries container-only keys (PUID/PGID) that Settings does
    # not know; a local `cp .env.example .env` must not crash the import.
    from backend.config import Settings
    env_file = tmp_path / ".env"
    env_file.write_text("PUID=1000\nPGID=1000\nHISTORY_RETENTION_DAYS=30\n")
    loaded = Settings(_env_file=str(env_file))
    assert loaded.history_retention_days == 30


def test_ancestor_rule_marker_is_written_once(db_path):
    first = ancestor_rule_since(db_path)
    assert first is not None and len(first) == 19
    sql("UPDATE app_settings SET value='2026-01-01 00:00:00' WHERE key='ancestor_rule_since'")
    database.init_db()
    assert ancestor_rule_since(db_path) == "2026-01-01 00:00:00"


# Real releases, not hand-written DDL: the live database dates from March 2026
# and grew through many versions by ALTER TABLE and the 0.6.13 rebuild.
HISTORIC_TAGS = ["v0.5.2", "v0.6.0", "v0.6.12", "v0.6.13", "v0.7.0"]


def historic_database_module(tag):
    import subprocess
    import types
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    try:
        source = subprocess.run(
            ["git", "-C", str(root), "show", f"{tag}:backend/database.py"],
            capture_output=True, text=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip(f"git tag {tag} is not available in this checkout")
    module = types.ModuleType(f"historic_database_{tag.replace('.', '_')}")
    exec(compile(source, f"{tag}:backend/database.py", "exec"), module.__dict__)
    return module


def test_database_grown_through_real_releases_upgrades_cleanly(tmp_path, monkeypatch):
    path = tmp_path / "grown.db"
    monkeypatch.setattr(settings, "database_url", str(path))
    first, *later = [historic_database_module(tag) for tag in HISTORIC_TAGS]
    first.init_db()
    build(path, """
        INSERT INTO instances (id, name, type, url, api_key) VALUES (1, 'Sonarr', 'sonarr', 'http://sonarr:8989', 'enc:abc');
        INSERT INTO search_history (id, instance_id, instance_name, skill, started_at, status)
        VALUES (1, 1, 'Sonarr', 'search_missing', '2026-03-22 10:00:00', 'success');
        INSERT INTO search_history_items (run_id, title, arr_id, item_type) VALUES (1, 'A', 1, 'episode'), (1, 'B', 2, 'season');
        INSERT INTO searched_items (instance_id, cache_key, title, item_type) VALUES (1, 'ser:1', 'Show', 'series');
    """)
    for module in later:
        module.init_db()
    # A run from 0.7.0 with an open command; retry_hours set after 0.7.0's reset.
    build(path, """
        UPDATE instances SET retry_hours=168 WHERE id=1;
        INSERT INTO search_history (id, instance_id, instance_name, skill, started_at, status)
        VALUES (2, 1, 'Sonarr', 'search_missing', '2026-09-01 11:00:00', 'pending');
        INSERT INTO search_history_items (run_id, title, item_type, command_id, command_status, cache_key, created_at)
        VALUES (2, 'C', 'episode', 101, 'submitted', 'ep:3', '2026-09-01 11:00:01');
    """)
    before = counts(path)

    database.init_db()
    database.init_db()

    assert counts(path) == {**before, "app_settings": before["app_settings"] + 1}
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("SELECT retry_hours FROM instances WHERE id=1").fetchone()[0] == 168
        assert "last_checked_at" in columns(conn, "search_history_items")
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_history_instance_started'"
        ).fetchone()
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        statuses = dict(conn.execute(
            "SELECT title, command_status FROM search_history_items"
        ).fetchall())
        assert statuses == {"A": "legacy", "B": "legacy", "C": "submitted"}
        assert "'pending'" in conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='search_history'"
        ).fetchone()[0]
    finally:
        conn.close()
```

(Bei Planerstellung im Scratchpad nachgestellt: die Kette `v0.5.2 → v0.6.0 → v0.6.12 → v0.6.13 → v0.7.0` baut mit den Tag-Ständen von `backend/database.py` eine DB mit genau diesen Zeilen; die Items A/B stehen danach auf `legacy`, C auf `submitted`.)

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p3_database.py -q -p no:cacheprovider`
Expected: FAIL (u. a. `retry_hours` wird 0, `last_checked_at` fehlt, `_COLUMN_MIGRATIONS` existiert nicht).

- [ ] **Step 3: Implementierung**

`backend/config.py` komplett:

```python
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings


def _read_version_file() -> str:
    """Fallback when the VERSION env var is not set."""
    for candidate in (Path("VERSION"), Path(__file__).parent.parent / "VERSION"):
        if candidate.exists():
            return candidate.read_text().strip()
    return "dev"


class Settings(BaseSettings):
    database_url: str = "./data/missingarr.db"
    log_level: str = "INFO"
    tz: str = "Europe/Berlin"
    version: str = _read_version_file()
    app_name: str = "Missingarr"
    max_log_entries: int = 10000

    # Auth is always on. Without AUTH_PASSWORD a temporary password is
    # generated at start and printed to the log.
    auth_username: str = "admin"
    auth_password: str = ""

    # Opt-in: when set, the key that encrypts the *arr API keys and the
    # session signing key are derived from it instead of being stored in the
    # database next to the data they protect (C5). Once used it must never
    # change or go missing.
    secret_key: str = ""

    # Mark session and remember-me cookies Secure. Only when missingarr is
    # reached over HTTPS, otherwise the browser drops them and login fails (C6).
    cookie_secure: bool = False

    # Finished search runs older than this many days are deleted; 0 keeps
    # them forever (B7).
    history_retention_days: int = Field(default=365, ge=0)

    # "ignore": a .env copied from .env.example also carries container-only
    # keys such as PUID/PGID; pydantic-settings would reject them otherwise.
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
```

`backend/database.py`: Kopf, `get_connection`, `init_db` und die neuen Hilfen ersetzen; `get_db`, `_widen_history_status_check`, `get_or_create_secret_key` bleiben wörtlich.

```python
import logging
import os
import sqlite3
from contextlib import contextmanager

from backend.config import settings

logger = logging.getLogger("missingarr.database")

BUSY_TIMEOUT_SECONDS = 30


def get_connection() -> sqlite3.Connection:
    # A writer holding the lock for a few seconds must not turn a search that
    # *arr already accepted into an untracked one (B2).
    conn = sqlite3.connect(
        settings.database_url, timeout=BUSY_TIMEOUT_SECONDS, check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_SECONDS * 1000}")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn
```

(`get_db` unverändert.)

Schema als Modulkonstante `_SCHEMA`: den bisherigen `executescript`-Text als Modulkonstante `_SCHEMA` übernehmen und darin genau zwei Stellen ändern — in `search_history` nach `error_message TEXT` die Zeile `, verified_count INTEGER NOT NULL DEFAULT 0` ergänzen, in `search_history_items` nach `created_at TEXT` die Zeile `, last_checked_at TEXT` ergänzen. Alles andere bleibt Zeichen für Zeichen.

```python
# (table, column, definition) for every column added after a table was first
# released. Existing databases get the missing ones; fresh ones have them
# from _SCHEMA already.
_COLUMN_MIGRATIONS = [
    ("search_history_items", "item_type", "TEXT NOT NULL DEFAULT 'episode'"),
    ("searched_items", "item_type", "TEXT NOT NULL DEFAULT 'episode'"),
    ("searched_items", "title", "TEXT NOT NULL DEFAULT ''"),
    # Command verification. The 'legacy' default settles existing rows in one
    # go: they carry no command id and can never be verified.
    ("search_history_items", "command_id", "INTEGER"),
    ("search_history_items", "command_status", "TEXT NOT NULL DEFAULT 'legacy'"),
    ("search_history_items", "cache_key", "TEXT NOT NULL DEFAULT ''"),
    ("search_history_items", "verified_at", "TEXT"),
    ("search_history_items", "created_at", "TEXT"),
    ("search_history", "verified_count", "INTEGER NOT NULL DEFAULT 0"),
    # Fair rotation and "expire only after asking" in verify_commands (B4, B5).
    ("search_history_items", "last_checked_at", "TEXT"),
]


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    for table, column, definition in _COLUMN_MIGRATIONS:
        if column in _columns(conn, table):
            continue
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        except sqlite3.OperationalError as exc:
            # Only a column that appeared in the meantime is harmless. A bad
            # default, an I/O error or a missing table must stop the start
            # instead of leaving a half-migrated schema behind (B8).
            if "duplicate column name" not in str(exc).lower():
                raise


def _assert_schema(conn: sqlite3.Connection) -> None:
    missing = [
        f"{table}.{column}"
        for table, column, _ in _COLUMN_MIGRATIONS
        if column not in _columns(conn, table)
    ]
    if missing:
        raise RuntimeError("Database schema incomplete after migration: " + ", ".join(missing))


def _restrict_file_permissions() -> None:
    """The database holds the encrypted API keys and, without SECRET_KEY, the
    keys to decrypt them — keep it readable by the app user only (C5). SQLite
    creates -wal/-shm with the permissions of the database file."""
    path = settings.database_url
    if not path or path == ":memory:" or path.startswith("file:"):
        return
    for candidate in (path, f"{path}-wal", f"{path}-shm"):
        try:
            if os.path.exists(candidate):
                os.chmod(candidate, 0o600)
        except OSError as exc:
            logger.warning("Could not restrict permissions of %s: %s", candidate, exc)


def init_db():
    with get_db() as conn:
        conn.executescript(_SCHEMA)
        _add_missing_columns(conn)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_items_pending "
            "ON search_history_items(command_status)"
        )

    # Needs its own connection with foreign keys off — see the docstring.
    _widen_history_status_check()

    with get_db() as conn:
        # After the rebuild: DROP TABLE removes the table's indexes with it.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_instance_started "
            "ON search_history(instance_id, started_at DESC, id DESC)"
        )
        # First start of 0.8.0. Season and series keys cached before it were
        # written under the old rules (show_batch, no air-date check) and must
        # not block episodes now (A9, see search_missing._blocked).
        conn.execute(
            "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, datetime('now','localtime'))",
            (ANCESTOR_RULE_SINCE_SETTING,),
        )
        _assert_schema(conn)

    _restrict_file_permissions()
```

Modulkonstante oben in `database.py`: `ANCESTOR_RULE_SINCE_SETTING = "ancestor_rule_since"`.

Die frühere Zeile `conn.execute("UPDATE instances SET retry_hours=0 WHERE retry_hours IN (1, 168)")` samt Kommentar entfällt ersatzlos (B1): die alte 168er-Migration gibt es seit 51b2d20 nicht mehr, alle betroffenen Installationen sind längst zurückgesetzt.

- [ ] **Step 4: Tests grün**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p3_database.py tests/test_database_regressions.py -q -p no:cacheprovider && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/database.py backend/config.py tests/test_p3_database.py
git commit -m "fix: migrate columns explicitly, stop resetting retry_hours, keep the database private"
```

### Task P3.2: Cache-Abfrage mit Zeitstempeln, Aufräumen, `app_settings`

**Files:**
- Modify: `backend/db/searched.py` (neu `local_to_utc`, `lookup_many`, `purge_expired`, `count_filtered`)
- Create: `backend/db/app_settings.py`
- Modify: `backend/db/__init__.py`
- Test: `tests/test_p3_searched.py`

**Interfaces:**
- Consumes: `database.get_db` (P3.1)
- Produces: siehe Vertrag (`lookup_many`, `local_to_utc`, `purge_expired`, `count_filtered`, `db.app_settings.*`). P2 nutzt `lookup_many`, P4 nutzt `app_settings`.

- [ ] **Step 1: Failing test schreiben**

`tests/test_p3_searched.py` (Bausteine von oben einfügen, dazu):

```python
import os
import time
from datetime import datetime, timezone


def put(instance_id, key, searched_at):
    sql("INSERT INTO searched_items (instance_id, cache_key, title, item_type, searched_at) "
        "VALUES (?, ?, ?, 'episode', ?)", (instance_id, key, key, searched_at))


def local(delta_hours=0):
    return datetime.fromtimestamp(time.time() - delta_hours * 3600).strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture
def berlin():
    # Not via monkeypatch.undo(): that would also undo db_path's
    # settings.database_url and point later statements at ./data.
    old = os.environ.get("TZ")
    os.environ["TZ"] = "Europe/Berlin"
    time.tzset()
    yield
    if old is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old
    time.tzset()


def test_local_timestamps_are_converted_to_utc(berlin):
    assert db.searched.local_to_utc("2026-09-30 12:00:00") == datetime(2026, 9, 30, 10, tzinfo=timezone.utc)
    assert db.searched.local_to_utc("2026-01-15 12:00:00") == datetime(2026, 1, 15, 11, tzinfo=timezone.utc)


def test_lookup_returns_utc_times_of_present_keys(db_path, berlin):
    inst = make_instance()
    put(inst["id"], "ep:1", "2026-09-30 12:00:00")
    put(inst["id"], "sea:5:1", "2026-09-29 08:30:00")
    found = db.searched.lookup_many(inst["id"], ["ep:1", "sea:5:1", "ser:5", "", "ep:1"])
    assert found == {
        "ep:1": datetime(2026, 9, 30, 10, tzinfo=timezone.utc),
        "sea:5:1": datetime(2026, 9, 29, 6, 30, tzinfo=timezone.utc),
    }


def test_lookup_respects_the_retry_window(db_path):
    inst = make_instance()
    put(inst["id"], "ep:old", local(30))
    put(inst["id"], "ep:new", local(1))
    assert set(db.searched.lookup_many(inst["id"], ["ep:old", "ep:new"], retry_hours=24)) == {"ep:new"}
    assert set(db.searched.lookup_many(inst["id"], ["ep:old", "ep:new"], retry_hours=0)) == {"ep:old", "ep:new"}


def test_lookup_of_many_keys_uses_one_connection(db_path, monkeypatch):
    inst = make_instance()
    with database.get_db() as conn:
        conn.executemany(
            "INSERT INTO searched_items (instance_id, cache_key, title, item_type) VALUES (?, ?, 'x', 'episode')",
            [(inst["id"], f"ep:{i}") for i in range(1200)],
        )
    opened = []
    real = database.get_connection
    monkeypatch.setattr(database, "get_connection", lambda: opened.append(1) or real())
    found = db.searched.lookup_many(inst["id"], [f"ep:{i}" for i in range(1500)])
    assert len(found) == 1200
    assert len(opened) == 1


def test_purge_expired_only_with_a_retry_window(db_path):
    inst = make_instance()
    put(inst["id"], "ep:old", local(30))
    put(inst["id"], "ep:new", local(1))
    assert db.searched.purge_expired(inst["id"], 0) == 0
    assert db.searched.purge_expired(inst["id"], 24) == 1
    assert [r[0] for r in sql("SELECT cache_key FROM searched_items")] == ["ep:new"]


def test_count_filtered(db_path):
    inst = make_instance()
    put(inst["id"], "ep:1", local())
    sql("INSERT INTO searched_items (instance_id, cache_key, title, item_type) VALUES (?, 'mov:1', 'm', 'movie')",
        (inst["id"],))
    assert db.searched.count_filtered() == 2
    assert db.searched.count_filtered(instance_id=inst["id"], item_type="movie") == 1


def test_app_settings_roundtrip(db_path):
    assert db.app_settings.get_value("token_version") is None
    db.app_settings.set_value("token_version", "1")
    db.app_settings.set_value("token_version", "2")
    assert db.app_settings.get_value("token_version") == "2"
    db.app_settings.delete_value("token_version")
    assert db.app_settings.get_value("token_version") is None
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p3_searched.py -q -p no:cacheprovider`
Expected: FAIL mit `AttributeError: module 'backend.db.searched' has no attribute 'local_to_utc'`.

- [ ] **Step 3: Implementierung**

In `backend/db/searched.py` Kopf ersetzen und Funktionen anhängen:

```python
from datetime import datetime, timezone
from typing import Iterable, Optional

from backend.database import get_db

LOOKUP_CHUNK = 500  # well below SQLite's variable limit


def local_to_utc(value: str) -> datetime:
    """searched_at is written with datetime('now','localtime'). Read it back in
    the same local zone (the process TZ) and return aware UTC, so it compares
    with *arr's airDateUtc."""
    naive = datetime.strptime(value.replace("T", " ")[:19], "%Y-%m-%d %H:%M:%S")
    return naive.astimezone(timezone.utc)


def lookup_many(instance_id: int, keys: Iterable[str], retry_hours: int = 0) -> dict[str, datetime]:
    """cache_key -> searched_at (UTC) for every key in the cache.

    One connection for a whole page of candidates instead of one per record
    (A-L3). With retry_hours > 0 only entries inside the window count.
    """
    wanted = list(dict.fromkeys(k for k in keys if k))
    found: dict[str, datetime] = {}
    if not wanted:
        return found
    with get_db() as conn:
        for start in range(0, len(wanted), LOOKUP_CHUNK):
            chunk = wanted[start:start + LOOKUP_CHUNK]
            placeholders = ",".join("?" * len(chunk))
            statement = (
                "SELECT cache_key, searched_at FROM searched_items "
                f"WHERE instance_id=? AND cache_key IN ({placeholders})"
            )
            params: list = [instance_id, *chunk]
            if retry_hours > 0:
                statement += " AND searched_at > datetime('now', 'localtime', ? || ' hours')"
                params.append(f"-{retry_hours}")
            for row in conn.execute(statement, params):
                found[row["cache_key"]] = local_to_utc(row["searched_at"])
    return found


def purge_expired(instance_id: int, retry_hours: int) -> int:
    """Delete entries outside the retry window. They no longer block anything,
    they only pile up (B6). No window (0) means permanent — nothing expires."""
    if retry_hours <= 0:
        return 0
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM searched_items WHERE instance_id=? "
            "AND searched_at <= datetime('now', 'localtime', ? || ' hours')",
            (instance_id, f"-{retry_hours}"),
        )
        return cursor.rowcount


def count_filtered(instance_id: Optional[int] = None, item_type: Optional[str] = None) -> int:
    conditions, params = [], []
    if instance_id is not None:
        conditions.append("instance_id=?")
        params.append(instance_id)
    if item_type:
        conditions.append("item_type=?")
        params.append(item_type)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    with get_db() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM searched_items {where}", params).fetchone()[0]
```

`backend/db/app_settings.py`:

```python
"""Small key/value store in the app_settings table."""
from typing import Optional

from backend.database import get_db


def get_value(key: str) -> Optional[str]:
    with get_db() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None


def set_value(key: str, value: str) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def delete_value(key: str) -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM app_settings WHERE key=?", (key,))
```

`backend/db/__init__.py`:

```python
from backend.db import instances, activity, history, searched, app_settings
```

- [ ] **Step 4: Tests grün**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p3_searched.py -q -p no:cacheprovider && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/db/ tests/test_p3_searched.py
git commit -m "feat: bulk cache lookup with UTC timestamps, expiry cleanup and an app_settings store"
```

### Task P3.3: History — Einreichen in einer Transaktion, Fehlschläge, Aufräumen, Filter

**Files:**
- Modify: `backend/db/history.py` (neu `record_submission`, `record_failed_submission`, `close_interrupted_runs`, `purge_old_runs`, `count_items_flat`, `_items_filter`; geändert `clear`, `get_latest_run_verification`, `query_items_flat`)
- Test: `tests/test_p3_history.py`

**Interfaces:**
- Consumes: `ITEM_SUBMITTED`, `ITEM_EXPIRED`, `ITEM_FAILED` aus `backend.verification`
- Produces: siehe Vertrag. P2 nutzt `record_submission`/`record_failed_submission`, P4 `close_interrupted_runs`, P3.6 `query_items_flat`/`count_items_flat`/`clear`.

- [ ] **Step 1: Failing test schreiben**

`tests/test_p3_history.py` (Bausteine von oben einfügen, dazu):

```python
from backend.db import history


def new_run(instance, skill="search_missing"):
    return history.start_run(instance["id"], instance["name"], skill)


def set_run(run_id, **fields):
    assignments = ", ".join(f"{k}=?" for k in fields)
    sql(f"UPDATE search_history SET {assignments} WHERE id=?", (*fields.values(), run_id))


def cache_keys():
    return [r[0] for r in sql("SELECT cache_key FROM searched_items ORDER BY cache_key")]


def test_submission_with_command_id_is_stored_and_cached(db_path):
    inst = make_instance()
    run = new_run(inst)
    item_id = history.record_submission(run, inst["id"], "Show S01E01", 11, "episode", "ep:11", 501)
    row = sql("SELECT command_status, command_id FROM search_history_items WHERE id=?", (item_id,))[0]
    assert tuple(row) == ("submitted", 501)
    assert cache_keys() == ["ep:11"]


def test_submission_without_command_id_is_not_cached(db_path):
    inst = make_instance()
    run = new_run(inst)
    history.record_submission(run, inst["id"], "Show S01E01", 11, "episode", "ep:11", None)
    assert sql("SELECT command_status FROM search_history_items")[0][0] == "expired"
    assert cache_keys() == []


def test_submission_is_all_or_nothing(db_path, monkeypatch):
    inst = make_instance()
    run = new_run(inst)
    real = database.get_connection
    monkeypatch.setattr(database, "get_connection",
                        lambda: FailOn(real(), "INSERT INTO searched_items"))
    with pytest.raises(sqlite3.OperationalError):
        history.record_submission(run, inst["id"], "Show S01E01", 11, "episode", "ep:11", 501)
    # Restore only this attribute; monkeypatch.undo() would also reset
    # settings.database_url to ./data and make sql() touch the wrong file.
    monkeypatch.setattr(database, "get_connection", real)
    assert sql("SELECT COUNT(*) FROM search_history_items")[0][0] == 0


def test_failed_submission_is_recorded_without_cache(db_path):
    inst = make_instance()
    run = new_run(inst)
    history.record_failed_submission(run, "Show S01E02", 12, "episode")
    row = sql("SELECT command_status, command_id, verified_at FROM search_history_items")[0]
    assert row["command_status"] == "failed"
    assert row["command_id"] is None
    assert row["verified_at"] is not None
    assert cache_keys() == []
    assert history.get_item_statuses(run) == ["failed"]


def test_interrupted_runs_are_closed_at_startup(db_path):
    inst = make_instance()
    with_items = new_run(inst)
    history.record_submission(with_items, inst["id"], "A", 1, "episode", "ep:1", 1)
    history.record_submission(with_items, inst["id"], "B", 2, "episode", "ep:2", 2)
    history.record_failed_submission(with_items, "C", 3, "episode")
    empty = new_run(inst)
    finished = new_run(inst)
    history.finish_run(finished, 0, 0, "success")

    assert history.close_interrupted_runs() == 2

    runs = {r["id"]: r for r in history.query(limit=10)}
    assert runs[with_items]["status"] == "pending"
    assert runs[with_items]["triggered_count"] == 2
    assert runs[with_items]["finished_at"] is not None
    assert runs[empty]["status"] == "error"
    assert runs[empty]["error_message"] == "Interrupted by restart"
    assert runs[finished]["status"] == "success"


def test_clear_keeps_open_runs_and_their_items(db_path):
    inst = make_instance()
    open_run = new_run(inst)
    history.record_submission(open_run, inst["id"], "A", 1, "episode", "ep:1", 1)
    history.finish_run(open_run, 1, 1, "success")  # becomes pending
    running = new_run(inst)
    done = new_run(inst)
    history.finish_run(done, 0, 0, "success")

    assert history.clear() == {"deleted": 1, "kept_open": 2}
    assert {r["id"] for r in history.query(limit=10)} == {open_run, running}
    assert sql("SELECT COUNT(*) FROM search_history_items")[0][0] == 1


def test_purge_old_runs_only_touches_old_finished_runs(db_path):
    inst = make_instance()
    old_done = new_run(inst)
    history.record_submission(old_done, inst["id"], "A", 1, "episode", "ep:1", None)
    history.finish_run(old_done, 1, 1, "error", "boom")
    old_pending = new_run(inst)
    history.finish_run(old_pending, 0, 0, "success")
    set_run(old_pending, status="pending")
    recent = new_run(inst)
    history.finish_run(recent, 0, 0, "success")
    for run in (old_done, old_pending):
        sql("UPDATE search_history SET started_at=datetime('now','localtime','-400 days') WHERE id=?", (run,))

    assert history.purge_old_runs(inst["id"], 0) == 0
    assert history.purge_old_runs(inst["id"], 365) == 1
    assert {r["id"] for r in history.query(limit=10)} == {old_pending, recent}
    assert sql("SELECT COUNT(*) FROM search_history_items")[0][0] == 0


def test_latest_verification_ignores_running_runs(db_path):
    inst = make_instance()
    done = new_run(inst)
    history.finish_run(done, 3, 3, "error")
    set_run(done, verified_count=2)
    new_run(inst)  # still running
    latest = history.get_latest_run_verification(inst["id"])
    assert (latest["id"], latest["triggered_count"], latest["verified_count"]) == (done, 3, 2)


def test_item_listing_filters_counts_and_pages(db_path):
    sonarr = make_instance(name="Sonarr")
    radarr = make_instance(name="Radarr", type="radarr")
    missing = new_run(sonarr)
    upgrades = new_run(radarr, "search_upgrades")
    for i in range(5):
        history.record_submission(missing, sonarr["id"], f"Show 100% S01E0{i}", i, "episode", f"ep:{i}", i)
    history.record_submission(upgrades, radarr["id"], "Movie (2020)", 9, "movie", "upg:9", 9)

    assert history.count_items_flat() == 6
    assert history.count_items_flat(skill="search_upgrades") == 1
    assert history.count_items_flat(instance_id=sonarr["id"], item_type="episode") == 5
    assert history.count_items_flat(search="movie") == 1
    assert history.count_items_flat(search="100%") == 5
    assert history.count_items_flat(search="100_") == 0
    assert history.count_items_flat(search="radarr") == 1

    page = history.query_items_flat(instance_id=sonarr["id"], limit=2, offset=2)
    assert len(page) == 2
    assert {"item_id", "run_id", "instance_id", "error_message", "command_status"} <= set(page[0])
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p3_history.py -q -p no:cacheprovider`
Expected: FAIL mit `AttributeError: module 'backend.db.history' has no attribute 'record_submission'`.

- [ ] **Step 3: Implementierung**

`backend/db/history.py` — Import erweitern: `from backend.verification import ITEM_SUBMITTED, ITEM_EXPIRED, ITEM_FAILED`. Neu bzw. ersetzt:

```python
_INSERT_ITEM = """
    INSERT INTO search_history_items
        (run_id, title, arr_id, item_type, cache_key, command_id,
         command_status, created_at, verified_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now','localtime'),
            CASE WHEN ? = 'now' THEN datetime('now','localtime') ELSE NULL END)
"""


def record_submission(
    run_id: int,
    instance_id: int,
    title: str,
    arr_id: Optional[int],
    item_type: str,
    cache_key: str,
    command_id: Optional[int],
) -> int:
    """Store a command *arr accepted — history item and retry-cache entry in
    one transaction, so a crash or lock between the two cannot leave a sent
    command without its cache entry or the other way round (B2).

    Without a command id nothing is cached: the entry could never be
    verified, and with retry_hours=0 it would block the title for good (A12).
    """
    if command_id is not None:
        status, verified = ITEM_SUBMITTED, None
    else:
        status, verified = ITEM_EXPIRED, "now"
    with get_db() as conn:
        cursor = conn.execute(
            _INSERT_ITEM,
            (run_id, title, arr_id, item_type, cache_key, command_id, status, verified),
        )
        if command_id is not None and cache_key:
            conn.execute(
                """
                INSERT INTO searched_items (instance_id, cache_key, title, item_type)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(instance_id, cache_key) DO UPDATE SET searched_at=datetime('now','localtime')
                """,
                (instance_id, cache_key, title, item_type),
            )
        return cursor.lastrowid


def record_failed_submission(run_id: int, title: str, arr_id: Optional[int], item_type: str) -> int:
    """A command *arr did not accept. Filed as failed with no command id and
    no cache entry: the next run picks the title up again, and the run's
    verdict can no longer come out as a clean success (A6)."""
    with get_db() as conn:
        cursor = conn.execute(
            _INSERT_ITEM,
            (run_id, title, arr_id, item_type, "", None, ITEM_FAILED, "now"),
        )
        return cursor.lastrowid


def close_interrupted_runs() -> int:
    """Close runs a restart cut off (B-L1). Only finish_run() moves a run out of
    'running'; a killed process never gets there. Runs that sent something go
    to 'pending' so verify_commands settles them; the rest become 'error'."""
    with get_db() as conn:
        rows = conn.execute("SELECT id FROM search_history WHERE status='running'").fetchall()
        for row in rows:
            sent = conn.execute(
                "SELECT COUNT(*) FROM search_history_items WHERE run_id=? AND command_status != ?",
                (row["id"], ITEM_FAILED),
            ).fetchone()[0]
            has_items = conn.execute(
                "SELECT 1 FROM search_history_items WHERE run_id=? LIMIT 1", (row["id"],)
            ).fetchone()
            conn.execute(
                """
                UPDATE search_history
                SET status=?, triggered_count=?, error_message=?,
                    finished_at=datetime('now','localtime')
                WHERE id=?
                """,
                ("pending" if has_items else "error", sent, "Interrupted by restart", row["id"]),
            )
        return len(rows)


def purge_old_runs(instance_id: int, days: int) -> int:
    """Delete finished runs older than `days` (B7). Items go with them (ON
    DELETE CASCADE). Open runs stay: their items still link command ids to
    cache keys."""
    if days <= 0:
        return 0
    with get_db() as conn:
        cursor = conn.execute(
            """
            DELETE FROM search_history
            WHERE instance_id=? AND status NOT IN ('running','pending')
              AND started_at < datetime('now','localtime', ? || ' days')
            """,
            (instance_id, f"-{days}"),
        )
        return cursor.rowcount


def _items_filter(instance_id, item_type, skill, search) -> tuple[str, list]:
    conditions: list[str] = []
    params: list = []
    if instance_id is not None:
        conditions.append("h.instance_id=?")
        params.append(instance_id)
    if item_type:
        conditions.append("si.item_type=?")
        params.append(item_type)
    if skill:
        conditions.append("h.skill=?")
        params.append(skill)
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        conditions.append("(si.title LIKE ? ESCAPE '\\' OR h.instance_name LIKE ? ESCAPE '\\')")
        params += [pattern, pattern]
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    return where, params


def query_items_flat(
    instance_id: Optional[int] = None,
    item_type: Optional[str] = None,
    skill: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 250,
    offset: int = 0,
) -> list[dict]:
    """Flat list of triggered items joined with their run + instance data."""
    where, params = _items_filter(instance_id, item_type, skill, search)
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT
                si.id AS item_id,
                si.run_id,
                h.instance_id,
                h.started_at,
                h.instance_name,
                h.skill,
                h.status,
                h.verified_count,
                h.error_message,
                COALESCE(inst.type, '') AS arr_type,
                si.title,
                si.item_type,
                si.arr_id,
                si.command_id,
                si.command_status
            FROM search_history_items si
            JOIN search_history h ON h.id = si.run_id
            LEFT JOIN instances inst ON inst.id = h.instance_id
            {where}
            ORDER BY h.started_at DESC, h.id DESC, si.id
            LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        ).fetchall()
        return [dict(r) for r in rows]


def count_items_flat(
    instance_id: Optional[int] = None,
    item_type: Optional[str] = None,
    skill: Optional[str] = None,
    search: Optional[str] = None,
) -> int:
    where, params = _items_filter(instance_id, item_type, skill, search)
    with get_db() as conn:
        return conn.execute(
            f"""
            SELECT COUNT(*)
            FROM search_history_items si
            JOIN search_history h ON h.id = si.run_id
            {where}
            """,
            params,
        ).fetchone()[0]


def get_latest_run_verification(instance_id: int) -> Optional[dict]:
    """The instance's most recently finished run. Both numbers on the card come
    from this one row, never from two different runs (B-L4)."""
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT id, status, triggered_count, verified_count
            FROM search_history
            WHERE instance_id=? AND finished_at IS NOT NULL AND status != 'running'
            ORDER BY finished_at DESC, id DESC LIMIT 1
            """,
            (instance_id,),
        ).fetchone()
        return dict(row) if row else None


def clear() -> dict:
    """Delete finished runs only. Open runs keep their submitted items: an item
    is the only link from a command id to its cache key, and without it a
    command that later fails would keep its title blocked (B-L2)."""
    with get_db() as conn:
        kept = conn.execute(
            "SELECT COUNT(*) FROM search_history WHERE status IN ('running','pending')"
        ).fetchone()[0]
        cursor = conn.execute("DELETE FROM search_history WHERE status NOT IN ('running','pending')")
        return {"deleted": cursor.rowcount, "kept_open": kept}
```

`insert_item` bleibt (P2 benutzt ab Welle 2 nur noch `record_submission`; Task Z prüft, ob `insert_item` dann noch Aufrufer hat).

- [ ] **Step 4: Tests grün**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p3_history.py -q -p no:cacheprovider && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/db/history.py tests/test_p3_history.py
git commit -m "fix: store submissions atomically, record failed ones, close interrupted runs, keep open runs on clear"
```

### Task P3.6: API-Grenzen, Filter und Gesamtzahl

**Files:**
- Modify: `backend/api/history.py` (ganze Datei), `backend/api/searched.py` (ganze Datei)
- Test: `tests/test_p3_api.py`

**Interfaces:**
- Consumes: P3.2 `count_filtered`, P3.3 `query_items_flat`, `count_items_flat`, `clear`
- Produces: HTTP-Vertrag P3 (Tabelle im Vertrag). P5 liest `X-Total-Count`.

- [ ] **Step 1: Failing test schreiben**

`tests/test_p3_api.py` (Bausteine von oben einfügen, dazu):

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import history as history_api
from backend.api import searched as searched_api
from backend.db import history


@pytest.fixture
def client(db_path):
    app = FastAPI()
    app.include_router(history_api.router, prefix="/api")
    app.include_router(searched_api.router, prefix="/api")
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize("url", [
    "/api/history?limit=-1", "/api/history?offset=-3", "/api/history?limit=201",
    "/api/history/items?limit=-1", "/api/history/items?limit=1001", "/api/history/items?offset=-1",
    "/api/history/items?item_type=upgrade", "/api/history/items?skill=health_check",
    "/api/searched?limit=-1", "/api/searched?limit=501", "/api/searched?offset=-1",
])
def test_out_of_range_parameters_are_rejected(client, url):
    assert client.get(url).status_code == 422


def test_items_endpoint_filters_pages_and_reports_the_total(client):
    inst = make_instance()
    missing = history.start_run(inst["id"], inst["name"], "search_missing")
    upgrades = history.start_run(inst["id"], inst["name"], "search_upgrades")
    for i in range(7):
        history.record_submission(missing, inst["id"], f"Show S01E0{i}", i, "episode", f"ep:{i}", i)
    history.record_submission(upgrades, inst["id"], "Show Season 2", 5, "season", "upg:sea:5:2", 99)

    resp = client.get("/api/history/items", params={"limit": 3, "offset": 3, "skill": "search_missing"})
    assert resp.status_code == 200
    assert resp.headers["X-Total-Count"] == "7"
    assert len(resp.json()) == 3

    resp = client.get("/api/history/items", params={"skill": "search_upgrades"})
    assert resp.headers["X-Total-Count"] == "1"
    assert resp.json()[0]["title"] == "Show Season 2"

    resp = client.get("/api/history/items", params={"q": "s01e03", "instance_id": inst["id"]})
    assert resp.headers["X-Total-Count"] == "1"


def test_clear_reports_deleted_and_kept_runs(client):
    inst = make_instance()
    history.start_run(inst["id"], inst["name"], "search_missing")  # running, kept
    done = history.start_run(inst["id"], inst["name"], "search_missing")
    history.finish_run(done, 0, 0, "success")
    assert client.delete("/api/history").json() == {"status": "cleared", "deleted": 1, "kept_open": 1}


def test_searched_endpoint_reports_the_total(client):
    inst = make_instance()
    for i in range(4):
        db.searched.add(inst["id"], f"ep:{i}", f"E{i}", "episode")
    resp = client.get("/api/searched", params={"instance_id": inst["id"], "limit": 2, "offset": 2})
    assert resp.headers["X-Total-Count"] == "4"
    assert len(resp.json()) == 2
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p3_api.py -q -p no:cacheprovider`
Expected: FAIL (negative Limits liefern 200, Header `X-Total-Count` fehlt).

- [ ] **Step 3: Implementierung**

`backend/api/history.py`:

```python
from typing import Literal, Optional

from fastapi import APIRouter, Query, Response

from backend import db

router = APIRouter(prefix="/history")

ItemType = Literal["movie", "episode", "season", "series"]
SkillName = Literal["search_missing", "search_upgrades"]


@router.get("")
def list_history(
    instance_id: Optional[int] = None,
    skill: Optional[SkillName] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return db.history.query(instance_id=instance_id, skill=skill, limit=limit, offset=offset)


@router.get("/items")
def list_items_flat(
    response: Response,
    instance_id: Optional[int] = None,
    item_type: Optional[ItemType] = None,
    skill: Optional[SkillName] = None,
    q: Optional[str] = Query(None, max_length=200),
    limit: int = Query(500, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    filters = {
        "instance_id": instance_id,
        "item_type": item_type,
        "skill": skill,
        "search": (q or "").strip() or None,
    }
    # The page shows "N items" and page numbers; they must describe the whole
    # filtered set, not the slice that was loaded (B-L5).
    response.headers["X-Total-Count"] = str(db.history.count_items_flat(**filters))
    return db.history.query_items_flat(**filters, limit=limit, offset=offset)


@router.delete("")
def clear_history():
    return {"status": "cleared", **db.history.clear()}
```

`backend/api/searched.py`:

```python
from typing import Literal, Optional

from fastapi import APIRouter, Query, Response

from backend import db

router = APIRouter(prefix="/searched")

ItemType = Literal["movie", "episode", "season", "series"]


@router.get("")
def list_searched(
    response: Response,
    instance_id: Optional[int] = None,
    item_type: Optional[ItemType] = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    response.headers["X-Total-Count"] = str(
        db.searched.count_filtered(instance_id=instance_id, item_type=item_type)
    )
    return db.searched.query(instance_id=instance_id, item_type=item_type, limit=limit, offset=offset)


@router.get("/count")
def count_searched(instance_id: Optional[int] = None):
    return db.searched.count(instance_id=instance_id)


@router.delete("")
def clear_all_searched():
    return {"deleted": db.searched.clear()}


@router.delete("/{instance_id}")
def clear_searched_for_instance(instance_id: int):
    return {"deleted": db.searched.clear(instance_id=instance_id)}
```

- [ ] **Step 4: Tests grün**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p3_api.py -q -p no:cacheprovider && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/api/history.py backend/api/searched.py tests/test_p3_api.py
git commit -m "fix: validate list limits and filter history server-side with a total count"
```

### Task P3.4: Verifikation — atomare Freigabe, faire Rotation, Ablauf erst nach Antwort, `orphaned` (Paket P3b, Welle 2)

**Files:**
- Modify: `backend/verification.py` (`_ARR_FAILURE_STATES`)
- Modify: `backend/db/history.py` (neu `resolve_item`, `mark_checked`; geändert `get_pending_items`, `expire_stale_items`)
- Modify: `backend/skills/verify_commands.py` (`execute`)
- Test: `tests/test_p3_verify.py`

**Interfaces:**
- Consumes: P1.4 `BaseAgent.stop_requested()`, `request_abort()`; `BaseAgent.http_get_raw` (P1.5)
- Produces: `resolve_item`, `mark_checked`; `state["last_verified"]` und `state["last_triggered"]` aus demselben beendeten Lauf.

- [ ] **Step 1: Failing test schreiben**

`tests/test_p3_verify.py` (Bausteine von oben einfügen, dazu):

```python
from backend.agents.base import BaseAgent
from backend.db import history
from backend.skills.verify_commands import VerifyCommandsSkill
from backend.verification import ITEM_FAILED, map_command_status


class ScriptedAgent(BaseAgent):
    def __init__(self, config, answers=None, default=(200, {"status": "started"})):
        super().__init__(config)
        self.answers = answers or {}
        self.default = default
        self.asked = []

    def build_skills(self):
        return []

    def http_get_raw(self, path):
        command_id = int(path.rsplit("/", 1)[1])
        self.asked.append(command_id)
        return self.answers.get(command_id, self.default)


@pytest.fixture(autouse=True)
def fresh_housekeeping():
    VerifyCommandsSkill._last_housekeeping.clear()
    yield
    VerifyCommandsSkill._last_housekeeping.clear()


def submit(inst, command_ids, cache_prefix="ep"):
    run = history.start_run(inst["id"], inst["name"], "search_missing")
    for cid in command_ids:
        history.record_submission(run, inst["id"], f"T{cid}", cid, "episode", f"{cache_prefix}:{cid}", cid)
    history.finish_run(run, len(command_ids), len(command_ids), "success")
    return run


def agent_for(inst, **kwargs):
    return ScriptedAgent(db.instances.get_by_id(inst["id"]), **kwargs)


@pytest.mark.parametrize("state", ["orphaned", "Orphaned", "aborted", "cancelled", "failed"])
def test_unfinished_commands_count_as_failed(state):
    assert map_command_status(200, {"status": state}) == ITEM_FAILED


def test_orphaned_command_releases_its_cache_key(db_path):
    inst = make_instance()
    run = submit(inst, [7])
    agent = agent_for(inst, answers={7: (200, {"status": "orphaned"})})
    VerifyCommandsSkill().execute(agent)
    assert history.get_item_statuses(run) == ["failed"]
    assert sql("SELECT COUNT(*) FROM searched_items")[0][0] == 0
    assert sql("SELECT status FROM search_history WHERE id=?", (run,))[0][0] == "failed"


def test_status_and_cache_release_are_one_transaction(db_path, monkeypatch):
    inst = make_instance()
    run = submit(inst, [7])
    item_id = sql("SELECT id FROM search_history_items")[0][0]
    real = database.get_connection
    monkeypatch.setattr(database, "get_connection",
                        lambda: FailOn(real(), "DELETE FROM searched_items"))
    with pytest.raises(sqlite3.OperationalError):
        history.resolve_item(item_id, ITEM_FAILED, inst["id"], "ep:7")
    monkeypatch.setattr(database, "get_connection", real)
    assert history.get_item_statuses(run) == ["submitted"]
    assert sql("SELECT COUNT(*) FROM searched_items")[0][0] == 1


def test_items_never_checked_come_first(db_path):
    inst = make_instance()
    submit(inst, list(range(1, 61)))
    agent = agent_for(inst)
    VerifyCommandsSkill().execute(agent)
    assert agent.asked == list(range(1, 51))
    agent.asked.clear()
    VerifyCommandsSkill().execute(agent)
    assert agent.asked[:10] == list(range(51, 61))


@pytest.mark.parametrize("no_answer", [(0, None), (502, None), (503, None), (401, None), (302, None)])
def test_unreachable_arr_does_not_use_up_the_grace_period(db_path, no_answer):
    inst = make_instance()
    run = submit(inst, [7])
    sql("UPDATE search_history_items SET created_at=datetime('now','localtime','-25 hours')")
    VerifyCommandsSkill().execute(agent_for(inst, default=no_answer))
    assert history.get_item_statuses(run) == ["submitted"]
    assert sql("SELECT last_checked_at FROM search_history_items")[0][0] is None
    VerifyCommandsSkill().execute(agent_for(inst))
    assert history.get_item_statuses(run) == ["expired"]


def test_verdict_after_a_long_pause_is_read_before_anything_expires(db_path):
    inst = make_instance()
    run = submit(inst, [7])
    sql("UPDATE search_history_items SET created_at=datetime('now','localtime','-30 hours'), "
        "last_checked_at=datetime('now','localtime','-29 hours')")
    VerifyCommandsSkill().execute(agent_for(inst, answers={7: (200, {"status": "failed"})}))
    assert history.get_item_statuses(run) == ["failed"]
    assert sql("SELECT COUNT(*) FROM searched_items")[0][0] == 0


def test_card_numbers_come_from_one_finished_run(db_path):
    inst = make_instance()
    done = submit(inst, [1, 2, 3])
    agent = agent_for(inst, default=(200, {"status": "completed"}))
    history.start_run(inst["id"], inst["name"], "search_missing")  # newer, still running
    VerifyCommandsSkill().execute(agent)
    assert agent.state["last_triggered"] == 3
    assert agent.state["last_verified"] == 3
    assert sql("SELECT status FROM search_history WHERE id=?", (done,))[0][0] == "success"


def test_abort_stops_the_pass(db_path):
    inst = make_instance()
    submit(inst, [1, 2])
    agent = agent_for(inst)
    agent.request_abort()
    VerifyCommandsSkill().execute(agent)
    assert agent.asked == []
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p3_verify.py -q -p no:cacheprovider`
Expected: FAIL (`orphaned` bleibt `submitted`, `resolve_item` fehlt, Rotation fragt dieselben 50).

- [ ] **Step 3: Implementierung**

`backend/verification.py`:

```python
# *arr command states that mean the command did not finish its search.
# 'orphaned' is what *arr sets on start for commands that were running when it
# went down (CommandStatus enum in Sonarr/Radarr core) — same as aborted (B-L3).
_ARR_FAILURE_STATES = {"failed", "aborted", "cancelled", "orphaned"}
```

`backend/db/history.py` ersetzen/ergänzen:

```python
def get_pending_items(instance_id: int, limit: int = 50) -> list[dict]:
    """Items still awaiting a verdict. Never-checked items first, then the one
    checked longest ago, so fifty commands *arr never settles cannot starve
    every newer one (B4)."""
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT si.id, si.run_id, si.command_id, si.cache_key, si.last_checked_at
            FROM search_history_items si
            JOIN search_history h ON h.id = si.run_id
            WHERE h.instance_id = ?
              AND si.command_status = ?
              AND si.command_id IS NOT NULL
            ORDER BY (si.last_checked_at IS NOT NULL), si.last_checked_at, si.id
            LIMIT ?
            """,
            (instance_id, ITEM_SUBMITTED, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def mark_checked(item_ids: list[int]) -> None:
    if not item_ids:
        return
    with get_db() as conn:
        conn.executemany(
            "UPDATE search_history_items SET last_checked_at=datetime('now','localtime') WHERE id=?",
            [(item_id,) for item_id in item_ids],
        )


def resolve_item(item_id: int, status: str, instance_id: int, cache_key: str) -> bool:
    """Write a verdict and, for a failed command, release its cache key — in
    one transaction (B3). Returns True when a cache entry was released."""
    with get_db() as conn:
        conn.execute(
            """
            UPDATE search_history_items
            SET command_status=?, verified_at=datetime('now','localtime'),
                last_checked_at=datetime('now','localtime')
            WHERE id=?
            """,
            (status, item_id),
        )
        if status == ITEM_FAILED and cache_key:
            cursor = conn.execute(
                "DELETE FROM searched_items WHERE instance_id=? AND cache_key=?",
                (instance_id, cache_key),
            )
            return cursor.rowcount > 0
    return False


def expire_stale_items(instance_id: int, hours: int = 24) -> int:
    """Give up on items *arr never resolved. Returns how many were expired.

    Only items *arr has answered at least once after their `hours` were up:
    an instance switched off for a day must first ask, then give up (B5).
    Ages by the item's own created_at; legacy rows fall back to the run start.
    """
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE search_history_items
            SET command_status=?, verified_at=datetime('now','localtime')
            WHERE command_status=?
              AND id IN (
                  SELECT si.id
                  FROM search_history_items si
                  JOIN search_history h ON h.id = si.run_id
                  WHERE h.instance_id = ?
                    AND si.command_status = ?
                    AND si.last_checked_at IS NOT NULL
                    AND si.last_checked_at
                        >= datetime(COALESCE(si.created_at, h.started_at), ? || ' hours')
              )
            """,
            (ITEM_EXPIRED, ITEM_SUBMITTED, instance_id, ITEM_SUBMITTED, f"+{hours}"),
        )
        return cursor.rowcount
```

`backend/skills/verify_commands.py` — `execute` ersetzen (Imports bleiben; `set_item_status` wird hier nicht mehr benutzt):

```python
    def execute(self, agent, force: bool = False) -> None:
        instance_id = agent.config["id"]

        # Ask first, expire afterwards: an item gets its answer from *arr even
        # when the instance was off for longer than STALE_HOURS (B5).
        pending = db.history.get_pending_items(instance_id, self.MAX_PER_RUN)
        resolved = 0
        answered: list[int] = []

        for item in pending:
            if agent.stop_requested():
                break
            http_status, payload = agent.http_get_raw(f"/api/v3/command/{item['command_id']}")
            status = map_command_status(http_status, payload)
            if status == ITEM_SUBMITTED:
                # Only *arr's own word on this command counts as a check. A
                # 502/503/504 from a reverse proxy in front of a dead Sonarr,
                # a 401/403 for a wrong key or a 3xx are no answer, and must
                # not use up the grace period (B5). 404 never gets here: it
                # is settled as expired by resolve_item above.
                if http_status == 200 and isinstance(payload, dict):
                    answered.append(item["id"])
                continue

            released = db.history.resolve_item(item["id"], status, instance_id, item["cache_key"])
            resolved += 1
            if released:
                agent.log(
                    "warn",
                    self.name,
                    f"Command {item['command_id']} failed in *arr — "
                    f"released '{item['cache_key']}' for another attempt",
                )

        db.history.mark_checked(answered)

        expired = db.history.expire_stale_items(instance_id, self.STALE_HOURS)
        if expired:
            agent.log(
                "warn",
                self.name,
                f"Gave up on {expired} command(s) still unresolved after {self.STALE_HOURS}h",
            )

        # Settle every run that has nothing open left — not just the ones touched
        # above. A run whose items were all filed as expired on insert (no command
        # id came back) never passes through the loop and would stay pending.
        for run_id in db.history.get_unresolved_run_ids(instance_id):
            statuses = db.history.get_item_statuses(run_id)
            db.history.update_run_verification(
                run_id,
                aggregate_run_status(statuses),
                statuses.count(ITEM_COMPLETED),
            )

        latest = db.history.get_latest_run_verification(instance_id)
        if latest:
            # Both numbers of the card from the same finished run (B-L4).
            agent.state["last_verified"] = latest["verified_count"]
            agent.state["last_triggered"] = latest["triggered_count"]

        self._housekeeping(agent)

        if pending:
            # Both numbers, always: 50 queried with 0 resolved is a backlog, and
            # logging only the resolved count would hide it.
            agent.log("info", self.name, f"Queried {len(pending)} command(s), {resolved} resolved")
```

Bis P3.5 fertig ist, als Platzhalter-freie Minimalfassung in der Klasse:

```python
    _last_housekeeping: dict[int, float] = {}

    def _housekeeping(self, agent) -> None:
        """Filled in by Task P3.5."""
        return None
```

(P3.5 ersetzt genau diese Methode; der Docstring wird dort durch den echten ersetzt.)

- [ ] **Step 4: Tests grün**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p3_verify.py tests/test_verification.py -q -p no:cacheprovider && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/verification.py backend/db/history.py backend/skills/verify_commands.py tests/test_p3_verify.py
git commit -m "fix: settle verdicts atomically, rotate unresolved commands, expire only after asking, treat orphaned as failed"
```

### Task P3.5: Hauspflege — Aufbewahrung der History, abgelaufene Cache-Zeilen (Paket P3b, Welle 2)

**Files:**
- Modify: `backend/skills/verify_commands.py` (`_housekeeping`, Imports)
- Test: `tests/test_p3_housekeeping.py`

**Interfaces:**
- Consumes: `settings.history_retention_days` (P3.1), `db.searched.purge_expired` (P3.2), `db.history.purge_old_runs` (P3.3)
- Produces: stündliche Hauspflege je Instanz; Logzeile `Housekeeping: removed …`.

- [ ] **Step 1: Failing test schreiben**

`tests/test_p3_housekeeping.py` (Bausteine von oben einfügen, dazu):

```python
from backend.agents.base import BaseAgent
from backend.db import history
from backend.skills.verify_commands import VerifyCommandsSkill


class QuietAgent(BaseAgent):
    def build_skills(self):
        return []

    def http_get_raw(self, path):
        return 200, {"status": "started"}  # nothing gets settled during these tests


@pytest.fixture(autouse=True)
def fresh_housekeeping():
    VerifyCommandsSkill._last_housekeeping.clear()
    yield
    VerifyCommandsSkill._last_housekeeping.clear()


def old_cache(instance_id, key, hours):
    sql("INSERT INTO searched_items (instance_id, cache_key, title, item_type, searched_at) "
        "VALUES (?, ?, ?, 'episode', datetime('now','localtime', ?))", (instance_id, key, key, f"-{hours} hours"))


def old_run(inst, status, days):
    run = history.start_run(inst["id"], inst["name"], "search_missing")
    sql("UPDATE search_history SET status=?, finished_at=datetime('now','localtime'), "
        "started_at=datetime('now','localtime', ?) WHERE id=?", (status, f"-{days} days", run))
    return run


def run_housekeeping(inst):
    VerifyCommandsSkill().execute(QuietAgent(db.instances.get_by_id(inst["id"])))


def test_old_runs_and_expired_cache_rows_are_removed(db_path, monkeypatch):
    monkeypatch.setattr(settings, "history_retention_days", 365)
    inst = make_instance(retry_hours=24)
    old_cache(inst["id"], "ep:old", 30)
    old_cache(inst["id"], "ep:new", 1)
    gone = old_run(inst, "success", 400)
    kept_pending = old_run(inst, "pending", 400)
    # An open command keeps the run pending; a pending run without open items
    # would be settled by the same verify pass and then count as finished.
    history.record_submission(kept_pending, inst["id"], "Open", 1, "episode", "", 77)
    kept_recent = old_run(inst, "error", 10)

    run_housekeeping(inst)

    assert [r[0] for r in sql("SELECT cache_key FROM searched_items")] == ["ep:new"]
    assert {r["id"] for r in history.query(limit=10)} == {kept_pending, kept_recent}
    assert gone not in {r["id"] for r in history.query(limit=10)}


def test_housekeeping_runs_at_most_hourly(db_path):
    inst = make_instance(retry_hours=24)
    run_housekeeping(inst)
    old_cache(inst["id"], "ep:old", 30)
    run_housekeeping(inst)
    assert sql("SELECT COUNT(*) FROM searched_items")[0][0] == 1
    VerifyCommandsSkill._last_housekeeping.clear()
    run_housekeeping(inst)
    assert sql("SELECT COUNT(*) FROM searched_items")[0][0] == 0


def test_permanent_cache_and_zero_retention_keep_everything(db_path, monkeypatch):
    monkeypatch.setattr(settings, "history_retention_days", 0)
    inst = make_instance(retry_hours=0)
    old_cache(inst["id"], "ep:old", 30_000)
    old_run(inst, "success", 4000)
    run_housekeeping(inst)
    assert sql("SELECT COUNT(*) FROM searched_items")[0][0] == 1
    assert len(history.query(limit=10)) == 1
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p3_housekeeping.py -q -p no:cacheprovider`
Expected: FAIL (Zeilen bleiben stehen).

- [ ] **Step 3: Implementierung**

`backend/skills/verify_commands.py`: Imports `import time` und `from backend.config import settings`; in der Klasse:

```python
    HOUSEKEEPING_INTERVAL_SECONDS = 3600
    # Per instance id, shared by every skill object: a reload creates new
    # skills, and the throttle must survive that.
    _last_housekeeping: dict[int, float] = {}

    def _housekeeping(self, agent) -> None:
        """Hourly per instance: drop finished runs past HISTORY_RETENTION_DAYS
        (B7) and cache rows outside the retry window (B6)."""
        instance_id = agent.config["id"]
        now = time.monotonic()
        last = self._last_housekeeping.get(instance_id)
        if last is not None and now - last < self.HOUSEKEEPING_INTERVAL_SECONDS:
            return
        self._last_housekeeping[instance_id] = now

        try:
            retry_hours = int(agent.config.get("retry_hours") or 0)
            cache = db.searched.purge_expired(instance_id, retry_hours)
            runs = db.history.purge_old_runs(instance_id, settings.history_retention_days)
        except Exception as exc:
            agent.log("warn", self.name, f"Housekeeping failed: {exc}")
            return

        if cache or runs:
            agent.log(
                "info",
                self.name,
                f"Housekeeping: removed {runs} run(s) older than "
                f"{settings.history_retention_days} days and {cache} expired cache entr"
                f"{'y' if cache == 1 else 'ies'}",
            )
```

- [ ] **Step 4: Tests grün**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p3_housekeeping.py tests/test_p3_verify.py -q -p no:cacheprovider && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/skills/verify_commands.py tests/test_p3_housekeeping.py
git commit -m "feat: hourly housekeeping for old history runs and expired cache rows"
```

Abnahme-Hinweis P3: `test_items_never_checked_come_first` belegt B4, `test_verdict_after_a_long_pause…` B5, `test_status_and_cache_release_are_one_transaction` B3, `test_submission_is_all_or_nothing` B2, `test_0_6_13_database_keeps_every_history_item` die Mutante aus B10.

---
## Paket P6 — Build und Auslieferung (Welle 1)

Abnahme P6: `tests/test_p6_*.py` grün; `docker build` des Branches klappt mit `--require-hashes`, das Image nennt den gepinnten Basis-Digest und den Entrypoint; alle `uses:` im Workflow sind auf 40-stellige SHAs gepinnt; README, `.env.example` und `docker-compose.yml` nennen `SECRET_KEY`, `COOKIE_SECURE`, `HISTORY_RETENTION_DAYS`, `PUID`/`PGID` und den bcrypt-Hash.

Einen Container startet P6 **nicht**: In Welle 1 importiert `backend/auth.py` noch `passlib` (das entfernt erst P4.4 in Welle 2), das Image hat es aber nicht mehr, `uvicorn` bräche beim Import ab. Außerdem hängt `missingarr.db` mit `600` an P3.1, das parallel läuft. Container-Start, UID-Prüfung, Dateirechte und `docker stop` prüft deshalb Task Z Step 4 am fertigen Branch.

`SCRATCH` wird in jedem Codeblock selbst gesetzt (siehe Global Constraints).

### Task P6.1: Alpine und htmx lokal, geprüft gegen die npm-Integrity

**Files:**
- Create: `scripts/vendor_assets.py`
- Create: `static/vendor/alpinejs-3.14.1.min.js`, `static/vendor/htmx-2.0.4.min.js`, `static/vendor/checksums.json` (vom Skript erzeugt)
- Test: `tests/test_p6_vendor.py`

**Interfaces:**
- Consumes: nichts
- Produces: `/static/vendor/alpinejs-3.14.1.min.js`, `/static/vendor/htmx-2.0.4.min.js` (P5 bindet sie in `base.html` ein).

- [ ] **Step 1: Failing test schreiben**

`tests/test_p6_vendor.py`:

```python
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "static" / "vendor"

EXPECTED = {
    "alpinejs-3.14.1.min.js": (
        "alpinejs", "3.14.1",
        "sha512-ICar8UsnRZAYvv/fCNfNeKMXNoXGUfwHrjx7LqXd08zIP95G2d9bAOuaL97re+1mgt/HojqHsfdOLo/A5LuWgQ==",
    ),
    "htmx-2.0.4.min.js": (
        "htmx.org", "2.0.4",
        "sha512-HLxMCdfXDOJirs3vBZl/ZLoY+c7PfM4Ahr2Ad4YXh6d22T5ltbTXFFkpx9Tgb2vvmWFMbIc3LqN2ToNkZJvyYQ==",
    ),
}


def test_vendor_files_match_their_recorded_checksums():
    checksums = json.loads((VENDOR / "checksums.json").read_text())
    assert set(checksums) == set(EXPECTED)
    for name, (package, version, integrity) in EXPECTED.items():
        entry = checksums[name]
        assert (entry["package"], entry["version"], entry["npm_integrity"]) == (package, version, integrity)
        assert hashlib.sha256((VENDOR / name).read_bytes()).hexdigest() == entry["sha256"]


def test_vendor_script_pins_the_same_integrity():
    script = (ROOT / "scripts" / "vendor_assets.py").read_text()
    for _, _, integrity in EXPECTED.values():
        assert integrity in script
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p6_vendor.py -q -p no:cacheprovider`
Expected: FAIL mit `FileNotFoundError` (`static/vendor/checksums.json`).

- [ ] **Step 3: Skript schreiben**

`scripts/vendor_assets.py`:

```python
"""Download the pinned front-end libraries into static/vendor and verify them.

Run from the repository root:  .venv/bin/python scripts/vendor_assets.py

The npm registry publishes a sha512 'integrity' for every package tarball.
Nothing is written unless the downloaded tarball matches the value pinned
here, so a changed CDN or registry cannot slip in unnoticed (C8). To update a
library, change version and integrity together (from
https://registry.npmjs.org/<package>/<version>, field dist.integrity) and run
the script again.
"""
import base64
import hashlib
import io
import json
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "static" / "vendor"

ASSETS = [
    {
        "package": "alpinejs",
        "version": "3.14.1",
        "member": "package/dist/cdn.min.js",
        "target": "alpinejs-3.14.1.min.js",
        "integrity": "sha512-ICar8UsnRZAYvv/fCNfNeKMXNoXGUfwHrjx7LqXd08zIP95G2d9bAOuaL97re+1mgt/HojqHsfdOLo/A5LuWgQ==",
    },
    {
        "package": "htmx.org",
        "version": "2.0.4",
        "member": "package/dist/htmx.min.js",
        "target": "htmx-2.0.4.min.js",
        "integrity": "sha512-HLxMCdfXDOJirs3vBZl/ZLoY+c7PfM4Ahr2Ad4YXh6d22T5ltbTXFFkpx9Tgb2vvmWFMbIc3LqN2ToNkZJvyYQ==",
    },
]


def tarball_url(package: str, version: str) -> str:
    return f"https://registry.npmjs.org/{package}/-/{package.split('/')[-1]}-{version}.tgz"


def main() -> None:
    VENDOR.mkdir(parents=True, exist_ok=True)
    checksums = {}
    for asset in ASSETS:
        with urllib.request.urlopen(tarball_url(asset["package"], asset["version"]), timeout=60) as resp:
            data = resp.read()
        actual = "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()
        if actual != asset["integrity"]:
            raise SystemExit(f"{asset['package']}@{asset['version']}: integrity mismatch, got {actual}")
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            content = tar.extractfile(asset["member"]).read()
        (VENDOR / asset["target"]).write_bytes(content)
        checksums[asset["target"]] = {
            "package": asset["package"],
            "version": asset["version"],
            "npm_integrity": asset["integrity"],
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        print(f"ok  {asset['target']}")
    (VENDOR / "checksums.json").write_text(json.dumps(checksums, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Dateien erzeugen und Tests laufen lassen**

Run: `cd /root/missingarr && .venv/bin/python scripts/vendor_assets.py && .venv/bin/python -m pytest tests/test_p6_vendor.py -q -p no:cacheprovider`
Expected: `ok  alpinejs-3.14.1.min.js`, `ok  htmx-2.0.4.min.js`, dann PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/vendor_assets.py static/vendor tests/test_p6_vendor.py
git commit -m "build: vendor Alpine.js and htmx, verified against the npm integrity hashes"
```

### Task P6.2: `passlib` raus, Lockdatei mit Hashes

**Files:**
- Modify: `requirements.txt`
- Create: `requirements.lock`
- Test: `tests/test_p6_requirements.py`

**Interfaces:**
- Consumes: nichts
- Produces: `bcrypt>=4.1` als direkte Abhängigkeit (P4 importiert `bcrypt`), `requirements.lock` für das Dockerfile (P6.3).

- [ ] **Step 1: Failing test schreiben**

`tests/test_p6_requirements.py`:

```python
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def lock_entries():
    """{name: (version, [hashes])} from a pip-compile --generate-hashes file."""
    entries, current = {}, None
    for line in (ROOT / "requirements.lock").read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s;\\]+)", stripped)
        if match and not line.startswith(" "):
            current = match.group(1).lower().replace("_", "-")
            entries[current] = (match.group(2), [])
        elif stripped.startswith("--hash=sha256:") and current:
            entries[current][1].append(stripped.split(":", 1)[1].rstrip(" \\"))
    return entries


def test_passlib_is_gone_and_bcrypt_is_direct():
    requirements = (ROOT / "requirements.txt").read_text().lower()
    assert "passlib" not in requirements
    assert re.search(r"^bcrypt>=4\.1", requirements, re.M)
    assert re.search(r"^apscheduler>=3\.10\.4,<4", requirements, re.M)


def test_every_locked_package_is_pinned_with_hashes():
    entries = lock_entries()
    for name in ("fastapi", "uvicorn", "starlette", "bcrypt", "cryptography", "apscheduler", "requests"):
        assert name in entries, name
    assert "passlib" not in entries
    for name, (version, hashes) in entries.items():
        assert hashes, f"{name}=={version} has no hash"
        assert all(re.fullmatch(r"[0-9a-f]{64}", h) for h in hashes), name
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p6_requirements.py -q -p no:cacheprovider`
Expected: FAIL (`passlib` steht noch drin, `requirements.lock` fehlt).

- [ ] **Step 3: `requirements.txt` ändern**

```
fastapi>=0.111.0
uvicorn[standard]>=0.29.0
jinja2>=3.1.4
python-multipart>=0.0.9
requests>=2.31.0
apscheduler>=3.10.4,<4
pydantic-settings>=2.2.1
aiofiles>=23.2.1
itsdangerous>=2.1.2
bcrypt>=4.1
cryptography>=42.0.0
```

(`apscheduler<4`: 4.x hat eine andere API.)

- [ ] **Step 4: Lockdatei erzeugen — auf die getesteten Versionen der `.venv` festgelegt**

Run:

```bash
SCRATCH=/tmp/claude-0/-root/b5f0b5e7-3436-4d16-9f25-cd6f007f969c/scratchpad
test -d "$SCRATCH" || { echo "SCRATCH fehlt: $SCRATCH" >&2; exit 1; }
cd /root/missingarr
python3 -m venv "$SCRATCH/lockvenv"
"$SCRATCH/lockvenv/bin/pip" install --quiet "pip-tools>=7.4"
.venv/bin/pip freeze --exclude-editable > "$SCRATCH/venv-freeze.txt"
"$SCRATCH/lockvenv/bin/pip-compile" --quiet --generate-hashes --allow-unsafe --strip-extras \
  --no-emit-index-url --constraint "$SCRATCH/venv-freeze.txt" \
  --output-file requirements.lock requirements.txt
grep -c -- "--hash=sha256:" requirements.lock
```

Expected: `requirements.lock` entsteht, die Hash-Zahl ist deutlich größer als die Paketzahl (mehrere Wheels je Paket, auch für `linux/arm64`). `bcrypt==5.0.0`, `fastapi==0.141.1`, `starlette==1.6.0` wie in der `.venv`.

- [ ] **Step 5: Tests grün, Installation mit Hashprüfung trocken probieren**

Run: `SCRATCH=/tmp/claude-0/-root/b5f0b5e7-3436-4d16-9f25-cd6f007f969c/scratchpad && test -d "$SCRATCH" && cd /root/missingarr && .venv/bin/python -m pytest tests/test_p6_requirements.py -q -p no:cacheprovider && "$SCRATCH/lockvenv/bin/pip" install --dry-run --quiet --require-hashes -r requirements.lock`
Expected: PASS, der Trockenlauf endet ohne Hash-Fehler.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt requirements.lock tests/test_p6_requirements.py
git commit -m "build: replace passlib with bcrypt and lock runtime dependencies with hashes"
```

### Task P6.3: Dockerfile per Digest, Entrypoint ohne root, Actions per SHA

**Files:**
- Modify: `Dockerfile`
- Create: `docker-entrypoint.sh`
- Modify: `.github/workflows/docker-publish.yml`
- Test: `tests/test_p6_build.py`, `tests/test_p6_entrypoint.py`

**Interfaces:**
- Consumes: `requirements.lock` (P6.2), `static/vendor` (P6.1)
- Produces: Container-Vertrag (siehe Schnittstellenvertrag P6).

- [ ] **Step 1: Aktuelle Pins nachschlagen (nur lesen)**

Run:

```bash
docker buildx imagetools inspect python:3.12-slim | sed -n '1,4p'
for a in actions/checkout:v4 docker/setup-qemu-action:v3 docker/setup-buildx-action:v3 \
         docker/login-action:v3 docker/metadata-action:v5 docker/build-push-action:v5; do
  repo=${a%%:*}; tag=${a##*:}
  echo "$repo $tag $(git ls-remote https://github.com/$repo "refs/tags/$tag^{}" "refs/tags/$tag" | tail -1 | cut -f1)"
done
```

Stand bei Planerstellung (30.09.2026), bei Abweichung die neuen Werte nehmen:

| Pin | Wert |
|---|---|
| `python:3.12-slim` (Index-Digest, amd64+arm64) | `sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f` |
| `actions/checkout` v4 | `11d5960a326750d5838078e36cf38b85af677262` |
| `docker/setup-qemu-action` v3 | `c7c53464625b32c7a7e944ae62b3e17d2b600130` |
| `docker/setup-buildx-action` v3 | `8d2750c68a42422c14e847fe6c8ac0403b4cbd6f` |
| `docker/login-action` v3 | `c94ce9fb468520275223c153574b00df6fe4bcc9` |
| `docker/metadata-action` v5 | `c299e40c65443455700f0fdfc63efafe5b349051` |
| `docker/build-push-action` v5 | `ca052bb54ab0790a636c9b5f226502c73d547a25` |

Das Image enthält `/usr/bin/setpriv` (Debian 13, geprüft mit `docker run --rm --network none python:3.12-slim@sha256:f77ac9e4… command -v setpriv`).

- [ ] **Step 2: Failing tests schreiben**

`tests/test_p6_build.py`:

```python
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_dockerfile_is_pinned_and_hash_checked():
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert re.search(r"^FROM python:3\.12-slim@sha256:[0-9a-f]{64}$", dockerfile, re.M)
    assert "--require-hashes -r requirements.lock" in dockerfile
    assert 'ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]' in dockerfile
    assert "--timeout-graceful-shutdown" in dockerfile
    assert "requirements.txt" not in dockerfile


def test_every_action_is_pinned_to_a_commit():
    workflow = (ROOT / ".github" / "workflows" / "docker-publish.yml").read_text()
    uses = re.findall(r"uses:\s*(\S+)", workflow)
    assert uses
    for ref in uses:
        assert re.fullmatch(r"[\w.\-]+/[\w.\-]+@[0-9a-f]{40}", ref), ref
```

`tests/test_p6_entrypoint.py`:

```python
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "docker-entrypoint.sh"

pytestmark = [
    pytest.mark.skipif(os.geteuid() != 0, reason="needs root to chown and drop privileges"),
    pytest.mark.skipif(shutil.which("setpriv") is None, reason="setpriv not installed"),
]


def run(data_dir, *command, **env):
    return subprocess.run(
        ["sh", str(SCRIPT), *command],
        env={**os.environ, "DATA_DIR": str(data_dir), **env},
        capture_output=True, text=True, cwd="/",
    )


def test_root_owned_data_is_handed_over_and_the_app_drops_root(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "missingarr.db").write_text("x")
    result = run(data, "sh", "-c", "id -u; id -g", PUID="4321", PGID="4322")
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["4321", "4322"]
    for path in (data, data / "missingarr.db"):
        assert (path.stat().st_uid, path.stat().st_gid) == (4321, 4322)


def test_files_that_already_fit_are_not_touched(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    db_file = data / "missingarr.db"
    db_file.write_text("x")
    data.chmod(0o700)
    db_file.chmod(0o600)
    for path in (data, db_file):
        os.chown(path, 4321, 4322)
    before = db_file.stat().st_ctime_ns
    assert run(data, "true", PUID="4321", PGID="4322").returncode == 0
    assert db_file.stat().st_ctime_ns == before


def test_data_and_backups_become_private(tmp_path):
    # A copy such as missingarr.db.bak-… holds the same API keys (C5, C9).
    data = tmp_path / "data"
    data.mkdir()
    data.chmod(0o755)
    backup = data / "missingarr.db.bak-20260817-000513"
    backup.write_text("x")
    backup.chmod(0o644)
    result = run(data, "sh", "-c", "umask", PUID="4321", PGID="4322")
    assert result.returncode == 0, result.stderr
    assert stat.S_IMODE(data.stat().st_mode) == 0o700
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert result.stdout.strip() == "0077"


def test_symlinks_in_the_data_directory_are_not_followed(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    outside.chmod(0o644)
    data = tmp_path / "data"
    data.mkdir()
    (data / "link").symlink_to(outside)
    assert run(data, "true", PUID="4321", PGID="4322").returncode == 0
    assert (outside.stat().st_uid, outside.stat().st_gid) == (0, 0)
    assert stat.S_IMODE(outside.stat().st_mode) == 0o644


def test_puid_zero_keeps_root(tmp_path):
    result = run(tmp_path, "id", "-u", PUID="0")
    assert result.stdout.strip() == "0"


def test_non_numeric_ids_are_rejected(tmp_path):
    assert run(tmp_path, "true", PUID="abc").returncode == 64
```

- [ ] **Step 3: Tests laufen lassen, Fehlschlag bestätigen**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p6_build.py tests/test_p6_entrypoint.py -q -p no:cacheprovider`
Expected: FAIL (kein Digest, Skript fehlt).

- [ ] **Step 4: Implementierung**

`docker-entrypoint.sh` (danach `chmod 0755 docker-entrypoint.sh`):

```sh
#!/bin/sh
# Start missingarr without root (C9).
#
# The container starts as root only long enough to hand the data directory to
# PUID:PGID. Existing installations have a root-owned ./data from earlier
# versions; this keeps them working without a manual chown.
set -eu

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
DATA_DIR="${DATA_DIR:-/data}"

if [ "$(id -u)" != "0" ]; then
    # Started with --user: nothing to hand over.
    exec "$@"
fi

case "$PUID:$PGID" in
    *[!0-9:]*|:*|*:)
        echo "docker-entrypoint: PUID and PGID must be numbers" >&2
        exit 64
        ;;
esac

if [ "$PUID" = "0" ]; then
    echo "docker-entrypoint: PUID=0 — running as root" >&2
    exec "$@"
fi

mkdir -p "$DATA_DIR"
# Only what does not already fit, so a normal restart touches nothing.
# Symlinks are skipped: chown and chmod would act on their target, which may
# lie outside the data directory.
find "$DATA_DIR" ! -type l \( ! -user "$PUID" -o ! -group "$PGID" \) -exec chown -h "$PUID:$PGID" {} +
# Private to the app user. The database holds the API keys and, without
# SECRET_KEY, the key to decrypt them — and so does every copy of it lying
# next to it (missingarr.db.bak-…).
find "$DATA_DIR" ! -type l -perm /077 -exec chmod go-rwx {} +
umask 077

exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups -- "$@"
```

`Dockerfile` komplett:

```dockerfile
FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

LABEL org.opencontainers.image.title="Missingarr" \
      org.opencontainers.image.description="Automated missing content & upgrade searcher for Sonarr and Radarr" \
      org.opencontainers.image.url="https://github.com/gomaaz/missingarr" \
      org.opencontainers.image.source="https://github.com/gomaaz/missingarr" \
      org.opencontainers.image.licenses="MIT"

ARG APP_VERSION=dev

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*

COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

COPY backend/ ./backend/
COPY templates/ ./templates/
COPY static/ ./static/
COPY VERSION ./VERSION
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 0755 /usr/local/bin/docker-entrypoint.sh

VOLUME ["/data"]

ENV DATABASE_URL=/data/missingarr.db \
    LOG_LEVEL=INFO \
    TZ=Europe/Berlin \
    VERSION=${APP_VERSION} \
    PUID=1000 \
    PGID=1000 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
# Single worker required — agents run as threads within one process.
# The graceful timeout ends open SSE streams so the lifespan shutdown runs (C-L7).
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--timeout-graceful-shutdown", "5"]
```

`.github/workflows/docker-publish.yml`: nur die sechs `uses:`-Zeilen ersetzen:

```yaml
        uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4
        uses: docker/setup-qemu-action@c7c53464625b32c7a7e944ae62b3e17d2b600130 # v3
        uses: docker/setup-buildx-action@8d2750c68a42422c14e847fe6c8ac0403b4cbd6f # v3
        uses: docker/login-action@c94ce9fb468520275223c153574b00df6fe4bcc9 # v3
        uses: docker/metadata-action@c299e40c65443455700f0fdfc63efafe5b349051 # v5
        uses: docker/build-push-action@ca052bb54ab0790a636c9b5f226502c73d547a25 # v5
```

- [ ] **Step 5: Tests grün**

Run: `cd /root/missingarr && chmod 0755 docker-entrypoint.sh && .venv/bin/python -m pytest tests/test_p6_build.py tests/test_p6_entrypoint.py -q -p no:cacheprovider`
Expected: PASS (als root; ohne root werden die Entrypoint-Tests übersprungen).

- [ ] **Step 6: Image bauen (ohne Container)**

Run:

```bash
cd /root/missingarr
docker build -t missingarr:codex-review-test .
docker image inspect missingarr:codex-review-test --format '{{json .Config.Entrypoint}} {{json .Config.Cmd}} {{.Config.User}}'
```

Expected: Build ohne Hash-Fehler (`pip install --require-hashes` läuft durch); Entrypoint `["/usr/local/bin/docker-entrypoint.sh"]`, `Cmd` mit `--timeout-graceful-shutdown`, `User` leer (der Entrypoint wechselt selbst den Benutzer). Kein `docker run` hier: `backend/auth.py` importiert in Welle 1 noch `passlib`, das im Image fehlt; der Container-Lauf mit UID-, Rechte- und Stop-Prüfung steht in Task Z Step 4.

- [ ] **Step 7: Commit**

```bash
git add Dockerfile docker-entrypoint.sh .github/workflows/docker-publish.yml tests/test_p6_build.py tests/test_p6_entrypoint.py
git commit -m "build: pin base image and actions, install with hash checks, run the app without root"
```

### Task P6.4: Dokumentation, Compose-Beispiel, Version 0.8.0

**Files:**
- Modify: `README.md`, `.env.example`, `docker-compose.yml`, `VERSION`
- Test: `tests/test_p6_docs.py`

**Interfaces:**
- Consumes: Umgebungsvariablen aus dem Vertrag (P3/P4)
- Produces: Doku für die Release-Notiz.

- [ ] **Step 1: Failing test schreiben**

`tests/test_p6_docs.py`:

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VARIABLES = ["SECRET_KEY", "COOKIE_SECURE", "HISTORY_RETENTION_DAYS", "PUID", "PGID", "AUTH_PASSWORD"]


def test_version_is_0_8_0():
    assert (ROOT / "VERSION").read_text().strip() == "0.8.0"


def test_every_new_variable_is_documented():
    for name in ("README.md", ".env.example", "docker-compose.yml"):
        text = (ROOT / name).read_text()
        for variable in VARIABLES:
            assert variable in text, f"{variable} missing in {name}"


def test_readme_explains_the_bcrypt_hash_and_the_upgrade():
    readme = (ROOT / "README.md").read_text()
    assert "bcrypt" in readme
    assert "Upgrading to 0.8.0" in readme
    assert "passlib" not in readme
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p6_docs.py -q -p no:cacheprovider`
Expected: FAIL.

- [ ] **Step 3: Dateien schreiben**

`VERSION`: `0.8.0`

`docker-compose.yml` komplett:

```yaml
services:
  missingarr:
    image: gomaaz/missingarr:latest
    container_name: missingarr
    ports:
      - "8000:8000"
    volumes:
      - ./data:/data
      - /etc/localtime:/etc/localtime:ro
    environment:
      - LOG_LEVEL=INFO
      - TZ=Europe/Berlin
      # The container starts as root only to hand ./data to this user, then drops to it.
      - PUID=1000
      - PGID=1000
      # Authentication is always active. Plain text or a bcrypt hash ($2b$...; write every $ as $$ here).
      # Without AUTH_PASSWORD a temporary password is printed in the logs.
      - AUTH_USERNAME=admin
      - AUTH_PASSWORD=
      # Optional. When set, the key that encrypts the *arr API keys and the session key are derived
      # from it and no longer stored in the database. Once set it must never change or go missing —
      # keep a copy. Generate: python -c "import secrets; print(secrets.token_hex(32))"
      - SECRET_KEY=
      # true only when missingarr is reached over HTTPS, otherwise login stops working.
      - COOKIE_SECURE=false
      # Finished search runs older than this many days are deleted; 0 keeps them forever.
      - HISTORY_RETENTION_DAYS=365
    stop_grace_period: 15s
    restart: unless-stopped
```

`.env.example` komplett:

```
DATABASE_URL=/data/missingarr.db
LOG_LEVEL=INFO
TZ=Europe/Berlin
# VERSION is set automatically at Docker build time from the git tag
# VERSION=dev

# Container user (the entrypoint hands /data to it, then drops root)
PUID=1000
PGID=1000

# Authentication is always active. AUTH_PASSWORD may be plain text or a bcrypt hash ($2b$...).
# Without AUTH_PASSWORD a temporary password is printed in the logs.
AUTH_USERNAME=admin
AUTH_PASSWORD=

# Optional. Derive the API-key encryption key and the session key from this value instead of
# storing them in the database. Once set it must never change or be removed.
# Generate: python -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY=

# true only behind HTTPS
COOKIE_SECURE=false

# Finished search runs older than this many days are deleted; 0 keeps them forever
HISTORY_RETENTION_DAYS=365
```

`README.md`:

1. Tabelle „Container Environment Variables“ ersetzen durch:

```markdown
| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `/data/missingarr.db` | SQLite database path |
| `LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARN`, `ERROR`) |
| `TZ` | `Europe/Berlin` | Timezone for quiet hours, display and stored timestamps |
| `PUID` / `PGID` | `1000` | User and group the app runs as. On start the container hands `/data` to them (only files that do not belong to them yet; symlinks are left alone), removes all group/other permissions there, then drops root. Pick an ID that no login user on the host has — whoever owns `/data` can read the API keys. `PUID=0` keeps root. |
| `AUTH_USERNAME` | `admin` | Login name |
| `AUTH_PASSWORD` | — | Plain text or a bcrypt hash (`$2b$…`). Create a hash with `python -c "import bcrypt; print(bcrypt.hashpw(b'your-password', bcrypt.gensalt()).decode())"`; in `docker-compose.yml` write every `$` as `$$`. Without it a temporary password is printed in the log. |
| `SECRET_KEY` | — | Optional. When set, the key that encrypts the \*arr API keys and the session key are derived from it (HKDF-SHA256). On the first start with it, stored API keys are re-encrypted and the old keys are deleted from the database. From then on the same `SECRET_KEY` is required — without it, or with a different one, missingarr refuses to start. Keep a copy. |
| `COOKIE_SECURE` | `false` | Mark the login cookies `Secure`. Only when missingarr is reached over HTTPS. |
| `HISTORY_RETENTION_DAYS` | `365` | Finished search runs older than this are deleted once per hour. `0` keeps them forever. Open runs are never deleted. |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | uvicorn setting. Set it to your reverse proxy's IP so login throttling sees the real client address. |
```

2. In „Scheduling“ die Zeile „Retry (hours)“ ersetzen durch:

```markdown
| **Retry (hours)** | `0` | How long a searched title stays in the Searched cache. `0` = never search it again automatically (reset the cache on the Progressed page to retry). A broader season or series search only blocks episodes that were already out (air date plus *Hours after release*) when it ran; season and series searches from before 0.8.0 do not block single episodes. |
```

3. Neuer Abschnitt vor „Compatibility“:

```markdown
## Security notes

- API keys are never sent back to the browser. The API shows `********` and `api_key_set`. Leave the key field empty to keep the stored key; when you change the URL you must enter the key again.
- Without `SECRET_KEY` the database holds the encrypted API keys *and* the key to decrypt them — treat backups of `/data` as secret. The database file is created with mode `600`, and the container makes everything in `/data` private to `PUID` on start. Copies such as `missingarr.db.bak-…` keep the keys they were made with, also after a switch to `SECRET_KEY` — delete them or store them as carefully.
- Lost `SECRET_KEY`: the stored API keys cannot be decrypted any more. Stop the container, run `DELETE FROM app_settings WHERE key IN ('key_source','secret_key_check'); UPDATE instances SET api_key='';` on `data/missingarr.db` with any SQLite tool, start again (with a new `SECRET_KEY` or none) and enter every API key again in the instance form.
- "Remember me" lasts 30 days on the server side. Signing out ends every session and every remembered login on all devices. Changing `AUTH_PASSWORD` does the same.
- After 5 failed sign-ins from one address, further attempts are blocked for 30 seconds, doubling up to 15 minutes. Every failed attempt is logged with the address.
- State-changing requests from other sites are rejected (checked with `Sec-Fetch-Site`/`Origin`). Scripts without these headers, such as `curl`, keep working.
- Missingarr does not follow redirects from Sonarr/Radarr. If the connection test reports a redirect, fix the URL.

## Upgrading to 0.8.0

- Everyone has to sign in once again (the remember-me cookie format changed).
- On the first start the container hands `./data` to `PUID:PGID` (default `1000:1000`) and makes it private to that user. If UID 1000 is a login user on your host, set another `PUID`/`PGID` first.
- On the first start, finished search runs older than `HISTORY_RETENTION_DAYS` (default 365) are deleted.
- Sign out is now a button that sends a POST; `GET /logout` no longer works.
- If `SECRET_KEY` is already set in your compose file, 0.8.0 re-encrypts the stored API keys with it on the first start. Back up `./data` before upgrading and keep the value.
```

4. In „Development“ ersetzen durch:

````markdown
## Development

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/ -q
uvicorn backend.main:app --reload
```

Front-end libraries are vendored in `static/vendor`. To update them, change version and npm integrity in `scripts/vendor_assets.py` and run it. Runtime dependencies are locked with hashes in `requirements.lock` (pip-compile --generate-hashes).
````

- [ ] **Step 4: Tests grün**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p6_docs.py -q -p no:cacheprovider && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add README.md .env.example docker-compose.yml VERSION tests/test_p6_docs.py
git commit -m "docs: document 0.8.0 settings, security behaviour and the upgrade; bump version"
```

---
## Paket P2 — Suchlogik (Welle 2)

Voraussetzung: Welle 1 komplett (P1: `reserve_action`, `release_action`, `stop_requested`, `wait_or_stop`, `request_abort`, 3xx als `HTTPError`; P3: `lookup_many`, `local_to_utc`, `db.app_settings`, `ANCESTOR_RULE_SINCE_SETTING` samt Marker aus `init_db()`, `record_submission`, `record_failed_submission`).

**Vor P2.2 Daniel fragen (offene Frage 7):** Der Plan setzt die Vorfahren-Regel mit dem Marker `ancestor_rule_since` um, damit die alten `ser:`/`sea:`-Zeilen den Live-Modus `episode` nicht wieder sperren. Entscheidet Daniel anders, ändert sich nur `_blocked` (und die zwei Marker-Tests in P2.3).

Abnahme P2: `tests/test_p2_*.py` grün, Gesamtsuite grün; `grep -n "check_rate_cap\|record_action\|exists_any\|searched.exists(\|insert_item\|sortKey" backend/skills/` findet nichts; die Live-Konfigurationen (Sonarr `episode`/`random` bzw. unbekannte Reihenfolge, `per_run=4`; Radarr `per_run=600`) laufen in den Tests `test_live_like_*` durch.

Gemeinsame Test-Bausteine (in jede P2-Testdatei kopieren, die sie braucht):

```python
from datetime import datetime, timedelta, timezone

import pytest
import requests

from backend import database, db
from backend.agents.base import BaseAgent
from backend.config import settings
from backend.db import history

WANTED = "/api/v3/wanted/missing"
CUTOFF = "/api/v3/wanted/cutoff"
MOVIES = "/api/v3/movie"


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "missingarr.db"
    monkeypatch.setattr(settings, "database_url", str(path))
    monkeypatch.setattr(database, "_cached_secret_key", None)
    database.init_db()
    return path


def make_instance(**fields):
    data = {
        "name": "Sonarr", "type": "sonarr", "url": "http://127.0.0.1:9", "api_key": "k" * 32,
        "seconds_between_actions": 0, "hours_after_release": 0, "rate_cap": 1000,
        "search_order": "random", "missing_mode": "episode", "missing_per_run": 5,
    }
    data.update(fields)
    return db.instances.create(data)


def iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def episode(i, series=1, season=1, aired=10, has_file=False, monitored=True):
    return {
        "id": i, "seriesId": series, "seasonNumber": season, "episodeNumber": i,
        "title": f"E{i}", "airDateUtc": iso(aired), "hasFile": has_file,
        "monitored": monitored, "series": {"title": f"Show {series}"},
    }


class FakeArr(BaseAgent):
    """A Sonarr/Radarr stand-in: paginates like *arr and records every call."""

    def __init__(self, config, missing=(), cutoff=(), movies=(), episodes=(),
                 fail_posts=(), all_posts_fail=False, command_ids=True,
                 stop_after_posts=None, get_errors=None):
        super().__init__(config)
        self.missing, self.cutoff = list(missing), list(cutoff)
        self.movies, self.episodes = list(movies), list(episodes)
        self.fail_posts, self.all_posts_fail = set(fail_posts), all_posts_fail
        self.command_ids, self.stop_after_posts = command_ids, stop_after_posts
        self.get_errors = get_errors or {}
        self.gets, self.posts = [], []

    def build_skills(self):
        return []

    @staticmethod
    def _page(records, params):
        size, page = int(params["pageSize"]), int(params["page"])
        return {"totalRecords": len(records), "records": records[(page - 1) * size: page * size]}

    def http_get(self, path, params=None):
        params = dict(params or {})
        self.gets.append((path, params))
        if path in self.get_errors:
            raise self.get_errors[path]
        if path == WANTED:
            return self._page(self.missing, params)
        if path == CUTOFF:
            return self._page(self.cutoff, params)
        if path == MOVIES:
            return list(self.movies)
        if path == "/api/v3/episode":
            found = [e for e in self.episodes if e["seriesId"] == params["seriesId"]]
            if "seasonNumber" in params:
                found = [e for e in found if e["seasonNumber"] == params["seasonNumber"]]
            return found
        if path.startswith("/api/v3/series/"):
            return {"title": "Looked up"}
        raise AssertionError(f"unexpected GET {path}")

    def http_post(self, path, body):
        self.posts.append(body)
        number = len(self.posts)
        if self.stop_after_posts is not None and number >= self.stop_after_posts:
            self.request_abort()
        if self.all_posts_fail or number in self.fail_posts:
            raise requests.exceptions.HTTPError("500 Server Error")
        return {"id": 1000 + number} if self.command_ids else {}


def agent_for(inst, **kwargs):
    return FakeArr(db.instances.get_by_id(inst["id"]), **kwargs)


def episode_ids(agent):
    return [p["episodeIds"][0] for p in agent.posts if p["name"] == "EpisodeSearch"]


def cache_keys():
    with database.get_db() as conn:
        return sorted(r[0] for r in conn.execute("SELECT cache_key FROM searched_items"))


def last_run():
    return history.query(limit=1)[0]


def cache_all(instance_id, keys, age_sql="-1 minutes"):
    with database.get_db() as conn:
        conn.executemany(
            "INSERT INTO searched_items (instance_id, cache_key, title, item_type, searched_at) "
            "VALUES (?, ?, ?, 'episode', datetime('now','localtime', ?))",
            [(instance_id, key, key, age_sql) for key in keys],
        )
```

### Task P2.1: Gemeinsame Einreich-Schleife, Freigabedatum, `SearchResult.error`

**Files:**
- Modify: `backend/skills/base.py` (ganze Datei)
- Test: `tests/test_p2_base.py`

**Interfaces:**
- Consumes: P1 `reserve_action`, `release_action`, `stop_requested`, `wait_or_stop`, `log`; P3 `record_submission`, `record_failed_submission`, `finish_run`
- Produces: `SearchResult.error`, `parse_arr_date`, `release_date`, `SubmitOutcome`, `submit_candidates`, `finish_search_run` (Signaturen im Vertrag).

- [ ] **Step 1: Failing test schreiben**

`tests/test_p2_base.py` (Bausteine von oben einfügen, dazu):

```python
from backend.skills.base import (
    SearchResult, SubmitOutcome, finish_search_run, parse_arr_date, release_date, submit_candidates,
)


def test_parse_arr_date_handles_z_offset_and_garbage():
    assert parse_arr_date("2026-09-30T10:00:00Z") == datetime(2026, 9, 30, 10, tzinfo=timezone.utc)
    assert parse_arr_date("2026-09-30T10:00:00") == datetime(2026, 9, 30, 10, tzinfo=timezone.utc)
    assert parse_arr_date("") is None
    assert parse_arr_date("not a date") is None
    assert parse_arr_date(None) is None


def test_radarr_release_is_the_earlier_home_release_then_cinema():
    both = {"digitalRelease": "2026-08-01T00:00:00Z", "physicalRelease": "2026-10-01T00:00:00Z",
            "inCinemas": "2026-06-01T00:00:00Z"}
    assert release_date(both, "radarr") == datetime(2026, 8, 1, tzinfo=timezone.utc)
    only_cinema = {"inCinemas": "2026-06-01T00:00:00Z"}
    assert release_date(only_cinema, "radarr") == datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert release_date({"airDateUtc": "2026-05-01T20:00:00Z"}, "sonarr") == datetime(2026, 5, 1, 20, tzinfo=timezone.utc)
    assert release_date({}, "sonarr") is None


def fire_with(results):
    queue = list(results)

    def fire(candidate):
        return queue.pop(0)

    return fire


def ok(i, command_id=True):
    return SearchResult(True, f"T{i}", "episode", f"ep:{i}", i, 500 + i if command_id else None)


def failed(i):
    return SearchResult(False, f"T{i}", "episode", "", i, None, "500 Server Error")


def test_successes_are_stored_failures_recorded_and_slots_returned(db_path):
    inst = make_instance(rate_cap=2)
    agent = agent_for(inst)
    run = history.start_run(inst["id"], inst["name"], "search_missing")
    outcome = submit_candidates("search_missing", agent, run, [1, 2, 3],
                                fire_with([failed(1), ok(2), ok(3)]), 0)
    assert outcome.triggered == 2
    assert outcome.errors == ["T1: 500 Server Error"]
    assert agent.get_rate_used() == 2
    assert history.get_item_statuses(run) == ["failed", "submitted", "submitted"]
    assert cache_keys() == ["ep:2", "ep:3"]


def test_rate_cap_stops_the_loop(db_path):
    inst = make_instance(rate_cap=1)
    agent = agent_for(inst)
    run = history.start_run(inst["id"], inst["name"], "search_missing")
    outcome = submit_candidates("search_missing", agent, run, [1, 2], fire_with([ok(1), ok(2)]), 0)
    assert (outcome.triggered, outcome.rate_capped) == (1, True)


def test_abort_during_the_pause_stops_the_loop(db_path):
    inst = make_instance()
    agent = agent_for(inst)
    run = history.start_run(inst["id"], inst["name"], "search_missing")

    def fire(candidate):
        agent.request_abort()
        return ok(candidate)

    outcome = submit_candidates("search_missing", agent, run, [1, 2, 3], fire, delay=5)
    assert (outcome.triggered, outcome.stopped) == (1, True)


def test_missing_command_id_is_stored_but_not_cached(db_path):
    inst = make_instance()
    agent = agent_for(inst)
    run = history.start_run(inst["id"], inst["name"], "search_missing")
    submit_candidates("search_missing", agent, run, [1], fire_with([ok(1, command_id=False)]), 0)
    assert history.get_item_statuses(run) == ["expired"]
    assert cache_keys() == []


def test_run_status_rules(db_path):
    inst = make_instance()
    agent = agent_for(inst)

    def finish(outcome, notes=()):
        run = history.start_run(inst["id"], inst["name"], "search_missing")
        status = finish_search_run("search_missing", agent, run, 3, outcome, notes)
        return status, last_run()["error_message"]

    assert finish(SubmitOutcome(errors=["A: x", "B: y"]))[0] == "error"
    status, message = finish(SubmitOutcome(triggered=2, errors=["A: x"]))
    assert status == "success" and message.startswith("1 of 3 submission(s) failed")
    assert finish(SubmitOutcome(stopped=True))[0] == "error"
    assert finish(SubmitOutcome(), ["cutoff list: down"]) == ("success", "cutoff list: down")
    assert agent.state["last_verified"] == 0
    assert agent.state["last_triggered"] == 0
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p2_base.py -q -p no:cacheprovider`
Expected: FAIL mit `ImportError: cannot import name 'SubmitOutcome'`.

- [ ] **Step 3: Implementierung**

`backend/skills/base.py` komplett:

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable, Iterable, Optional

from backend import db

if TYPE_CHECKING:
    from backend.agents.base import BaseAgent


@dataclass(frozen=True)
class SearchResult:
    """Outcome of a single triggered search.

    arr_id is the entity the command actually addressed — the series id for a
    SeriesSearch, not the episode that happened to trigger it. command_id is
    the id *arr returned and the only thing that makes the entry checkable
    later. error is set when ok is False.
    """

    ok: bool
    title: str = ""
    item_type: str = ""
    cache_key: str = ""
    arr_id: int | None = None
    command_id: int | None = None
    error: str = ""


class BaseSkill(ABC):
    name: str = ""

    @abstractmethod
    def execute(self, agent: "BaseAgent", force: bool = False) -> None:
        """Execute this skill using the provided agent context."""
        ...


def parse_arr_date(value) -> Optional[datetime]:
    """*arr sends ISO timestamps in UTC, mostly with a trailing Z."""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def release_date(record: dict, arr_type: str) -> Optional[datetime]:
    """When a title became available — used for hours_after_release and for
    newest/oldest ordering.

    Radarr: the earlier of digital and physical release, like Radarr itself
    treats a film as released; the cinema date only when neither is known
    (A-L2). Sonarr: the episode's air date.
    """
    if arr_type == "radarr":
        home = [
            d for d in (parse_arr_date(record.get("digitalRelease")),
                        parse_arr_date(record.get("physicalRelease")))
            if d is not None
        ]
        if home:
            return min(home)
        return parse_arr_date(record.get("inCinemas"))
    return parse_arr_date(record.get("airDateUtc"))


@dataclass
class SubmitOutcome:
    triggered: int = 0
    errors: list = field(default_factory=list)
    stopped: bool = False
    rate_capped: bool = False


def _store_submission(skill_name: str, agent, run_id: int, result: SearchResult) -> None:
    if result.command_id is None:
        agent.log("warn", skill_name,
                  f"*arr returned no command id for {result.title} — not cached, cannot be verified")
    try:
        db.history.record_submission(
            run_id, agent.config["id"], result.title, result.arr_id,
            result.item_type, result.cache_key, result.command_id,
        )
    except Exception as exc:
        # The command is out; only the bookkeeping failed. Count it as sent and
        # name the command id so it can be traced in *arr (B2).
        agent.log("error", skill_name,
                  f"Command {result.command_id} for {result.title} was sent but could not be stored: {exc}")


def submit_candidates(
    skill_name: str,
    agent,
    run_id: int,
    candidates: Iterable,
    fire: Callable[[object], SearchResult],
    delay: float,
) -> SubmitOutcome:
    """Send one search per candidate, shared by missing and upgrade searches.

    Every command reserves its rate slot first and returns it when *arr did
    not accept the command (A10). Failed submissions are recorded as failed
    items (A6). The loop ends early on an abort (instance disabled or deleted,
    A-L5) or when the rate cap is reached.
    """
    outcome = SubmitOutcome()
    candidates = list(candidates)
    for index, candidate in enumerate(candidates):
        if agent.stop_requested():
            outcome.stopped = True
            break
        token = agent.reserve_action()
        if token is None:
            agent.log("warn", skill_name, "Rate cap reached — stopping run")
            outcome.rate_capped = True
            break

        result = fire(candidate)
        if result.ok:
            outcome.triggered += 1
            _store_submission(skill_name, agent, run_id, result)
        else:
            agent.release_action(token)
            outcome.errors.append(f"{result.title}: {result.error}")
            agent.log("warn", skill_name, f"Failed to trigger search for {result.title}: {result.error}")
            try:
                db.history.record_failed_submission(run_id, result.title, result.arr_id, result.item_type)
            except Exception as exc:
                agent.log("error", skill_name, f"Could not record the failed submission: {exc}")

        if delay > 0 and index < len(candidates) - 1 and agent.wait_or_stop(delay):
            outcome.stopped = True
            break
    return outcome


def finish_search_run(
    skill_name: str,
    agent,
    run_id: int,
    wanted: int,
    outcome: SubmitOutcome,
    notes: Iterable[str] = (),
) -> str:
    """Close the run with an honest status and publish the card numbers.

    Every submission failed, or the run was stopped before sending anything:
    'error'. Otherwise 'success' (finish_run turns it into 'pending' while
    items await verification) with the failures named in error_message.
    """
    notes = list(notes)
    failed = len(outcome.errors)
    if outcome.stopped:
        notes.append("Stopped early: instance disabled or deleted")

    if failed and outcome.triggered == 0:
        status = "error"
        notes.insert(0, f"All {failed} submission(s) failed — first error: {outcome.errors[0]}")
    elif outcome.stopped and outcome.triggered == 0:
        status = "error"
    else:
        status = "success"
        if failed:
            notes.insert(0, f"{failed} of {failed + outcome.triggered} submission(s) failed "
                            f"— first error: {outcome.errors[0]}")

    db.history.finish_run(run_id, wanted, outcome.triggered, status, "; ".join(notes) or None)

    # last_triggered and last_verified always describe the same run (B-L4):
    # a run that just ended has nothing verified yet.
    agent.state["last_wanted"] = wanted
    agent.state["last_triggered"] = outcome.triggered
    agent.state["last_verified"] = 0
    if status != "error":
        agent.state["last_sync"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    return status
```

- [ ] **Step 4: Tests grün**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p2_base.py -q -p no:cacheprovider && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/skills/base.py tests/test_p2_base.py
git commit -m "feat: shared submission loop with atomic rate slots, failed-item records and honest run status"
```

### Task P2.2: Fehlend-Suche — ganze Liste für geordnete Reihenfolgen, Seitenbudget für Zufall

**Files:**
- Modify: `backend/skills/search_missing.py` (ganze Datei; Suchlogik `_sonarr_search` samt Dichte folgt in P2.3, hier schon in der Endfassung enthalten)
- Test: `tests/test_p2_search_missing.py`

**Interfaces:**
- Consumes: P2.1, P3 `db.searched.lookup_many`
- Produces: `SearchMissingSkill` mit Konstanten `ORDERED_PAGE_SIZE=1000`, `ORDERED_MAX_PAGES=100`, `RANDOM_PAGE_BUDGET=10`.

- [ ] **Step 1: Failing test schreiben**

`tests/test_p2_search_missing.py` (Bausteine von oben einfügen, dazu):

```python
from backend.skills import search_missing
from backend.skills.search_missing import RANDOM_PAGE_BUDGET, SearchMissingSkill


def run_missing(agent, force=False):
    SearchMissingSkill().execute(agent, force=force)


def backlog(n):
    """id 1 aired yesterday, id n aired n days ago; server returns id order."""
    return [episode(i, series=i, aired=i) for i in range(1, n + 1)]


def test_oldest_first_starts_with_the_globally_oldest(db_path):
    inst = make_instance(search_order="oldest_first")
    agent = agent_for(inst, missing=backlog(1500))
    run_missing(agent)
    assert episode_ids(agent) == [1500, 1499, 1498, 1497, 1496]
    wanted_params = [p for path, p in agent.gets if path == WANTED]
    assert {p["page"] for p in wanted_params} == {1, 2}
    assert all(set(p) == {"page", "pageSize", "monitored"} for p in wanted_params)


def test_newest_first_starts_with_the_globally_newest(db_path):
    inst = make_instance(search_order="newest_first")
    agent = agent_for(inst, missing=list(reversed(backlog(1500))))
    run_missing(agent)
    assert episode_ids(agent) == [1, 2, 3, 4, 5]


def test_ordered_modes_reach_the_whole_backlog(db_path):
    inst = make_instance(search_order="oldest_first")
    seen = set()
    for _ in range(20):
        agent = agent_for(inst, missing=backlog(1000))
        run_missing(agent)
        seen.update(episode_ids(agent))
    assert len(seen) == 100


def test_smart_puts_recent_items_from_later_pages_first(db_path):
    inst = make_instance(search_order="smart", missing_per_run=10)
    records = [episode(i, series=i, aired=100 + i) for i in range(1, 1498)]
    records += [episode(i, series=i, aired=i - 1497) for i in range(1498, 1501)]  # 1-3 days ago
    agent = agent_for(inst, missing=records)
    run_missing(agent)
    assert {1498, 1499, 1500} <= set(episode_ids(agent))
    assert len(episode_ids(agent)) == 10


def test_random_mode_reads_a_bounded_number_of_pages(db_path, monkeypatch):
    inst = make_instance(search_order="random")
    cache_all(inst["id"], [f"ep:{i}" for i in range(1, 5001)])
    lookups = []
    real = db.searched.lookup_many
    monkeypatch.setattr(db.searched, "lookup_many", lambda *a, **k: lookups.append(1) or real(*a, **k))
    agent = agent_for(inst, missing=[episode(i, series=i) for i in range(1, 5001)])
    run_missing(agent)
    wanted_gets = [1 for path, _ in agent.gets if path == WANTED]
    assert len(wanted_gets) <= 1 + RANDOM_PAGE_BUDGET
    assert len(lookups) <= RANDOM_PAGE_BUDGET
    assert agent.posts == []
    assert last_run()["status"] == "success"


def test_random_mode_still_finds_the_last_uncached_item(db_path):
    inst = make_instance(search_order="random")
    cache_all(inst["id"], [f"ep:{i}" for i in range(1, 501) if i != 250])
    agent = agent_for(inst, missing=[episode(i, series=i) for i in range(1, 501)])
    run_missing(agent)
    assert episode_ids(agent) == [250]


def test_zero_per_run_does_nothing(db_path):
    inst = make_instance(search_missing_enabled=False, missing_per_run=0)
    agent = agent_for(inst, missing=backlog(10))
    run_missing(agent)
    assert agent.gets == [] and agent.posts == []


def test_unreachable_arr_makes_the_run_an_error(db_path):
    inst = make_instance()
    agent = agent_for(inst, get_errors={WANTED: requests.exceptions.ConnectionError("refused")})
    run_missing(agent)
    assert last_run()["status"] == "error"


def test_every_post_failing_makes_the_run_an_error(db_path):
    inst = make_instance(search_order="oldest_first", missing_per_run=3)
    agent = agent_for(inst, missing=backlog(3), all_posts_fail=True)
    run_missing(agent)
    run = last_run()
    assert run["status"] == "error"
    assert run["error_message"].startswith("All 3 submission(s) failed")
    assert history.get_item_statuses(run["id"]) == ["failed", "failed", "failed"]
    assert cache_keys() == []


def test_some_posts_failing_are_named_on_the_run(db_path):
    inst = make_instance(search_order="oldest_first", missing_per_run=3)
    agent = agent_for(inst, missing=backlog(3), fail_posts={2})
    run_missing(agent)
    run = last_run()
    assert run["status"] == "pending"
    assert run["error_message"].startswith("1 of 3 submission(s) failed")
    assert agent.state["last_triggered"] == 2
    assert agent.state["last_verified"] == 0


def test_response_without_command_id_is_not_cached(db_path):
    inst = make_instance(search_order="oldest_first", missing_per_run=1)
    agent = agent_for(inst, missing=backlog(1), command_ids=False)
    run_missing(agent)
    assert cache_keys() == []
    second = agent_for(inst, missing=backlog(1))
    run_missing(second)
    assert episode_ids(second) == [1]


def test_rate_cap_is_respected_and_failed_posts_give_their_slot_back(db_path):
    inst = make_instance(search_order="oldest_first", missing_per_run=3, rate_cap=1)
    agent = agent_for(inst, missing=backlog(3), fail_posts={1})
    run_missing(agent)
    assert len(agent.posts) == 2
    assert agent.get_rate_used() == 1


def test_disabling_stops_a_running_search(db_path):
    inst = make_instance(search_order="oldest_first", missing_per_run=5, seconds_between_actions=1)
    agent = agent_for(inst, missing=backlog(5), stop_after_posts=1)
    run_missing(agent)
    assert len(agent.posts) == 1
    assert "Stopped early" in last_run()["error_message"]


def test_live_like_radarr_settings_run_through(db_path):
    inst = make_instance(name="Radarr", type="radarr", missing_per_run=600,
                         rate_cap=999_999_999, interval_minutes=30)
    movies = [{"id": i, "title": f"M{i}", "year": 2020, "hasFile": False,
               "digitalRelease": iso(30)} for i in range(1, 51)]
    agent = agent_for(inst, missing=movies)
    run_missing(agent)
    assert len(agent.posts) == 50
    assert all(p["name"] == "MoviesSearch" for p in agent.posts)


def test_live_like_sonarr_settings_run_through(db_path):
    inst = make_instance(missing_per_run=4, rate_cap=300, interval_minutes=60)
    agent = agent_for(inst, missing=backlog(20))
    run_missing(agent)
    assert len(episode_ids(agent)) == 4
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p2_search_missing.py -q -p no:cacheprovider`
Expected: FAIL (`ImportError: cannot import name 'RANDOM_PAGE_BUDGET'`).

- [ ] **Step 3: Implementierung**

`backend/skills/search_missing.py` komplett:

```python
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from backend import db
from backend.database import ANCESTOR_RULE_SINCE_SETTING
from backend.skills.base import (
    BaseSkill, SearchResult, SubmitOutcome, finish_search_run, parse_arr_date,
    release_date, submit_candidates,
)

WANTED_PATH = "/api/v3/wanted/missing"

# newest_first / oldest_first / smart read the whole list and sort it here.
# *arr's sortKey is deliberately not used: it drops records without the sort
# field (specials, films without a physical date — see 8a1a912, c68178e).
ORDERED_PAGE_SIZE = 1000
ORDERED_MAX_PAGES = 100

# random: at most this many pages per run, so a mostly searched backlog does
# not mean reading the whole list every interval (A-L3).
RANDOM_PAGE_BUDGET = 10

RATIO_THRESHOLD = 0.5
CHECK_CHUNK = 1000


@dataclass
class _Stats:
    total: int = 0
    pages: int = 0
    examined: int = 0
    skipped_file: int = 0
    skipped_window: int = 0
    skipped_cache: int = 0
    truncated: bool = False
    notes: list = field(default_factory=list)

    def describe(self) -> str:
        return (
            f"checked {self.examined} of {self.total} missing item(s) on {self.pages} page(s): "
            f"{self.skipped_cache} already searched, {self.skipped_window} inside the release "
            f"window, {self.skipped_file} with a file"
        )


class SearchMissingSkill(BaseSkill):
    name = "search_missing"

    # 50 % recent, 30 % random, 20 % oldest — repeated over the whole list.
    SMART_PATTERN = (0, 0, 0, 0, 0, 1, 1, 1, 2, 2)

    def execute(self, agent, force: bool = False) -> None:
        cfg = agent.config
        run_id = db.history.start_run(cfg["id"], cfg["name"], self.name)
        wanted_count = 0
        outcome = SubmitOutcome()

        try:
            per_run = int(cfg.get("missing_per_run", 5) or 0)
            if per_run <= 0:
                agent.log("info", self.name, "Missing per run is 0 — nothing to do")
                finish_search_run(self.name, agent, run_id, 0, outcome)
                return

            order = cfg.get("search_order", "random")
            mode = cfg.get("missing_mode", "episode")
            hours = int(cfg.get("hours_after_release", 9) or 0)
            cutoff = (
                datetime.now(timezone.utc) - timedelta(hours=hours)
                if (not force and hours > 0) else None
            )

            agent.log("info", self.name,
                      f"Searching for missing content (order={order}, per_run={per_run})...")
            if order == "random":
                candidates, stats = self._collect_random(agent, cfg, per_run, mode, cutoff, force)
            else:
                candidates, stats = self._collect_ordered(agent, cfg, per_run, mode, order, cutoff, force)
            if stats.truncated:
                stats.notes.append(
                    f"only the first {ORDERED_MAX_PAGES * ORDERED_PAGE_SIZE} missing items were considered"
                )
            wanted_count = len(candidates)
            agent.log("debug", self.name, stats.describe())

            if not candidates:
                agent.log("info", self.name, f"Nothing to search — {stats.describe()}")
                finish_search_run(self.name, agent, run_id, 0, outcome, stats.notes)
                return

            series_lookup = self._series_lookup(agent, cfg, candidates)
            delay = cfg.get("seconds_between_actions", 2) or 0
            outcome = submit_candidates(
                self.name, agent, run_id, candidates,
                lambda record: self._trigger_search(agent, cfg, record, mode, series_lookup),
                delay,
            )
            agent.log(
                "info", self.name,
                f"Done — candidates: {wanted_count}, triggered: {outcome.triggered}, "
                f"failed: {len(outcome.errors)} (total missing: {stats.total})",
            )
            finish_search_run(self.name, agent, run_id, wanted_count, outcome, stats.notes)

        except Exception as exc:
            agent.log("error", self.name, f"Search failed: {exc}")
            db.history.finish_run(run_id, wanted_count, outcome.triggered, "error", str(exc))

    # ── Collecting candidates ────────────────────────────────────────────────

    def _collect_random(self, agent, cfg, per_run, mode, cutoff, force):
        stats = _Stats()
        page_size = min(max(per_run * 10, 50), 250)
        probe = agent.http_get(WANTED_PATH, params={"page": 1, "pageSize": 1, "monitored": "true"})
        stats.total = int(probe.get("totalRecords", 0) or 0)

        pages = list(range(1, max(1, math.ceil(stats.total / page_size)) + 1))
        random.shuffle(pages)
        budget = max(RANDOM_PAGE_BUDGET, math.ceil(per_run * 4 / page_size))

        candidates: list = []
        seen: set = set()
        for page in pages[:budget]:
            if len(candidates) >= per_run or agent.stop_requested():
                break
            try:
                resp = agent.http_get(
                    WANTED_PATH, params={"page": page, "pageSize": page_size, "monitored": "true"}
                )
            except Exception as exc:
                if stats.pages == 0:
                    raise
                stats.notes.append(f"page {page} could not be loaded: {exc}")
                break
            stats.pages += 1
            records = list(resp.get("records") or [])
            random.shuffle(records)
            self._take_eligible(cfg, records, mode, cutoff, force, per_run, candidates, seen, stats)
        return candidates, stats

    def _collect_ordered(self, agent, cfg, per_run, mode, order, cutoff, force):
        """Read the whole wanted list, sort it here, then walk it in order (A3/A4)."""
        stats = _Stats()
        records: list = []
        seen_ids: set = set()
        page = 1
        while True:
            if page > ORDERED_MAX_PAGES:
                stats.truncated = True
                break
            if agent.stop_requested():
                break
            resp = agent.http_get(
                WANTED_PATH, params={"page": page, "pageSize": ORDERED_PAGE_SIZE, "monitored": "true"}
            )
            batch = resp.get("records") or []
            stats.total = int(resp.get("totalRecords", 0) or 0)
            stats.pages += 1
            for record in batch:
                record_id = record.get("id")
                if record_id in seen_ids:
                    continue
                seen_ids.add(record_id)
                records.append(record)
            if not batch or page * ORDERED_PAGE_SIZE >= stats.total:
                break
            page += 1

        ordered = self._apply_order(records, order, cfg["type"])
        candidates: list = []
        self._take_eligible(cfg, ordered, mode, cutoff, force, per_run, candidates, set(), stats)
        return candidates, stats

    def _take_eligible(self, cfg, records, mode, cutoff, force, per_run, candidates, seen, stats):
        """Append records, in the given order, that are missing, released and
        not in the cache, until per_run candidates exist. The cache is asked
        once per chunk, not once per record (A-L3)."""
        arr_type = cfg["type"]
        retry_hours = int(cfg.get("retry_hours", 0) or 0)
        # For season/series keys: a search inside the release window proves
        # nothing about the episode (A9/B6). Force runs skip the cache anyway.
        window = timedelta(hours=max(0, int(cfg.get("hours_after_release", 9) or 0)))
        since = None if force else self._ancestor_rule_since()
        for start in range(0, len(records), CHECK_CHUNK):
            if len(candidates) >= per_run:
                return
            pool = []
            for record in records[start:start + CHECK_CHUNK]:
                stats.examined += 1
                if record.get("hasFile"):
                    stats.skipped_file += 1
                    continue
                if cutoff is not None:
                    released = release_date(record, arr_type)
                    if released is not None and released > cutoff:
                        stats.skipped_window += 1
                        continue
                pool.append(record)

            hits = {} if force else db.searched.lookup_many(
                cfg["id"], [key for r in pool for key in self._check_keys(arr_type, r)], retry_hours
            )
            for record in pool:
                if len(candidates) >= per_run:
                    return
                if not force and self._blocked(arr_type, record, hits, window, since):
                    stats.skipped_cache += 1
                    continue
                dedup = self._cache_key(arr_type, record, mode)
                if dedup in seen:
                    continue
                seen.add(dedup)
                candidates.append(record)

    # ── Cache keys ───────────────────────────────────────────────────────────

    def _cache_key(self, arr_type: str, record: dict, mode: str) -> str:
        """Broad intent key for within-run deduplication, so one run does not
        send the same season or series search twice."""
        if arr_type == "radarr":
            return f"mov:{record.get('id')}"
        if mode in ("season_packs", "smart"):
            return f"sea:{record.get('seriesId')}:{record.get('seasonNumber')}"
        if mode == "show_batch":
            return f"ser:{record.get('seriesId')}"
        return f"ep:{record.get('id')}"

    def _check_keys(self, arr_type: str, record: dict) -> list:
        """Every cache key that can cover this record — independent of the
        current mode, so a mode switch does not bypass earlier season or
        series searches (A9)."""
        if arr_type == "radarr":
            return [f"mov:{record.get('id')}"]
        keys = [f"ep:{record.get('id')}"]
        series_id = record.get("seriesId")
        if series_id is not None:
            keys.append(f"ser:{series_id}")
            if record.get("seasonNumber") is not None:
                keys.append(f"sea:{series_id}:{record.get('seasonNumber')}")
        return keys

    @staticmethod
    def _ancestor_rule_since() -> datetime | None:
        """First start of 0.8.0 (aware UTC), written once by init_db()."""
        value = db.app_settings.get_value(ANCESTOR_RULE_SINCE_SETTING)
        if not value:
            return None
        try:
            return db.searched.local_to_utc(value)
        except ValueError:
            return None

    def _blocked(self, arr_type: str, record: dict, hits: dict,
                 window: timedelta = timedelta(0), since: datetime | None = None) -> bool:
        """The record's own key always blocks.

        A season or series key blocks only when that search ran after the
        episode aired *and* after its release window (hours_after_release):
        a season search from last month, or one a few hours after the air
        date while indexers do not have it yet, cannot have found it (A8/B6).
        Keys cached before `since` (first start of 0.8.0) never block an
        episode: they come from show_batch runs under the old rules and would
        lock most of the live backlog again (A9). Without an air date a
        broader key from after `since` blocks, as before."""
        own = f"mov:{record.get('id')}" if arr_type == "radarr" else f"ep:{record.get('id')}"
        if own in hits:
            return True
        if arr_type == "radarr":
            return False
        aired = release_date(record, arr_type)
        for key in self._check_keys(arr_type, record):
            if key == own or key not in hits:
                continue
            searched = hits[key]
            if since is not None and searched < since:
                continue
            if aired is None or searched >= aired + window:
                return True
        return False

    # ── Ordering ─────────────────────────────────────────────────────────────

    def _apply_order(self, records: list, order: str, arr_type: str) -> list:
        undated = datetime.min.replace(tzinfo=timezone.utc)

        def when(record):
            return release_date(record, arr_type) or undated

        if order == "newest_first":
            return sorted(records, key=when, reverse=True)
        if order == "oldest_first":
            return sorted(records, key=when)
        if order == "smart":
            return self._smart_order(records, when)
        shuffled = list(records)
        random.shuffle(shuffled)
        return shuffled

    def _smart_order(self, records: list, when) -> list:
        """Order the whole list so that, from the front, half the picks are
        recent (last 30 days), 30 % random and 20 % the oldest — without
        dropping anything (A4)."""
        recent_cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        recent = [r for r in records if when(r) >= recent_cutoff]
        rest = [r for r in records if when(r) < recent_cutoff]
        random.shuffle(recent)
        shuffled_rest = list(rest)
        random.shuffle(shuffled_rest)
        queues = [recent, shuffled_rest, sorted(rest, key=when)]
        positions = [0, 0, 0]
        used: set = set()

        def draw(q):
            queue = queues[q]
            while positions[q] < len(queue):
                record = queue[positions[q]]
                positions[q] += 1
                if id(record) not in used:
                    used.add(id(record))
                    return record
            return None

        ordered: list = []
        while len(ordered) < len(records):
            for q in self.SMART_PATTERN:
                record = draw(q) or draw(0) or draw(1) or draw(2)
                if record is None:
                    return ordered
                ordered.append(record)
        return ordered

    # ── Triggering ───────────────────────────────────────────────────────────

    def _series_lookup(self, agent, cfg, candidates) -> dict[int, str]:
        """Fetch titles only for series the wanted list did not name."""
        lookup: dict[int, str] = {}
        if cfg["type"] != "sonarr":
            return lookup
        needed = {
            r.get("seriesId") for r in candidates
            if r.get("seriesId")
            and not (r.get("series") or {}).get("title", "")
            and not r.get("seriesTitle", "")
        }
        for series_id in needed:
            try:
                lookup[series_id] = agent.http_get(f"/api/v3/series/{series_id}").get("title", "")
            except Exception:
                pass
        return lookup

    def _series_title(self, record: dict, series_lookup: dict) -> str:
        series_id = record.get("seriesId")
        title = (record.get("series") or {}).get("title", "") or record.get("seriesTitle", "")
        if not title and series_id and series_lookup:
            title = series_lookup.get(series_id, f"Series #{series_id}")
        return title

    def _label(self, arr_type: str, record: dict, series_lookup: dict) -> str:
        if arr_type == "radarr":
            title = record.get("title") or f"Movie #{record.get('id')}"
            year = record.get("year", "")
            return f"{title} ({year})" if year else title
        series_title = self._series_title(record, series_lookup)
        episode_title = record.get("title", "")
        if series_title:
            return (f"{series_title} S{(record.get('seasonNumber') or 0):02d}"
                    f"E{(record.get('episodeNumber') or 0):02d} – {episode_title}")
        return episode_title or f"Episode #{record.get('id')}"

    def _trigger_search(self, agent, cfg, record, mode, series_lookup) -> SearchResult:
        arr_type = cfg["type"]
        try:
            if arr_type == "sonarr":
                return self._sonarr_search(agent, record, mode, series_lookup or {})
            return self._radarr_search(agent, record)
        except Exception as exc:
            return SearchResult(
                False, self._label(arr_type, record, series_lookup or {}),
                "movie" if arr_type == "radarr" else "episode", "", record.get("id"), None, str(exc),
            )

    def _episodes(self, agent, series_id, season_number=None):
        params = {"seriesId": series_id, "includeImages": "false"}
        if season_number is not None:
            params["seasonNumber"] = season_number
        try:
            episodes = agent.http_get("/api/v3/episode", params=params)
        except Exception as exc:
            agent.log("debug", self.name, f"Episode list for series {series_id} failed, using EpisodeSearch: {exc}")
            return None
        return episodes if isinstance(episodes, list) else None

    @staticmethod
    def _density(episodes, season=None, exclude_specials=False) -> tuple[int, int]:
        """(missing, relevant) over monitored episodes that have already aired.
        Unmonitored and future episodes say nothing about whether a pack is
        worth searching (A8)."""
        now = datetime.now(timezone.utc)
        relevant = []
        for e in episodes or []:
            if not e.get("monitored"):
                continue
            if season is not None and e.get("seasonNumber") != season:
                continue
            if exclude_specials and e.get("seasonNumber") == 0:
                continue
            aired = parse_arr_date(e.get("airDateUtc"))
            if aired is None or aired > now:
                continue
            relevant.append(e)
        missing = sum(1 for e in relevant if not e.get("hasFile"))
        return missing, len(relevant)

    @staticmethod
    def _dense(missing: int, total: int) -> bool:
        return total > 0 and missing / total >= RATIO_THRESHOLD

    def _sonarr_search(self, agent, record: dict, mode: str, series_lookup: dict) -> SearchResult:
        """cache_key and arr_id describe the command actually sent, not the
        mode's intent, so later runs check at the right level."""
        episode_id = record.get("id")
        series_id = record.get("seriesId")
        season_number = record.get("seasonNumber")
        series_title = self._series_title(record, series_lookup)
        label = self._label("sonarr", record, series_lookup)

        if not episode_id:
            return SearchResult(False, label, "episode", "", None, None, "record has no episode id")

        def fire_episode() -> SearchResult:
            resp = agent.http_post("/api/v3/command", {"name": "EpisodeSearch", "episodeIds": [episode_id]})
            agent.log("debug", self.name, f"EpisodeSearch: {label}")
            return SearchResult(True, label, "episode", f"ep:{episode_id}", episode_id, resp.get("id"))

        def fire_season() -> SearchResult:
            resp = agent.http_post(
                "/api/v3/command",
                {"name": "SeasonSearch", "seriesId": series_id, "seasonNumber": season_number},
            )
            title = f"{series_title} Season {season_number}"
            agent.log("debug", self.name, f"SeasonSearch: {title}")
            return SearchResult(True, title, "season", f"sea:{series_id}:{season_number}", series_id, resp.get("id"))

        def fire_series() -> SearchResult:
            resp = agent.http_post("/api/v3/command", {"name": "SeriesSearch", "seriesId": series_id})
            agent.log("debug", self.name, f"SeriesSearch: {series_title}")
            return SearchResult(True, series_title, "series", f"ser:{series_id}", series_id, resp.get("id"))

        if mode in ("season_packs", "smart") and series_id is not None and season_number is not None:
            missing, total = self._density(self._episodes(agent, series_id, season_number), season=season_number)
            agent.log("debug", self.name,
                      f"{mode}: {missing}/{total} aired monitored episodes of season {season_number} missing")
            return fire_season() if self._dense(missing, total) else fire_episode()

        if mode == "show_batch" and series_id is not None:
            episodes = self._episodes(agent, series_id)
            series_missing, series_total = self._density(episodes, exclude_specials=True)
            if self._dense(series_missing, series_total):
                return fire_series()
            if season_number is not None:
                season_missing, season_total = self._density(episodes, season=season_number)
                if self._dense(season_missing, season_total):
                    return fire_season()
            return fire_episode()

        return fire_episode()

    def _radarr_search(self, agent, record: dict) -> SearchResult:
        movie_id = record.get("id")
        label = self._label("radarr", record, {})
        if not movie_id:
            return SearchResult(False, label, "movie", "", None, None, "record has no movie id")
        resp = agent.http_post("/api/v3/command", {"name": "MoviesSearch", "movieIds": [movie_id]})
        agent.log("debug", self.name, f"MoviesSearch: {label}")
        return SearchResult(True, label, "movie", f"mov:{movie_id}", movie_id, resp.get("id"))
```

- [ ] **Step 4: Tests grün**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p2_search_missing.py -q -p no:cacheprovider && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/skills/search_missing.py tests/test_p2_search_missing.py
git commit -m "fix: order missing searches over the whole wanted list and bound random paging"
```

### Task P2.3: Cache-Regel mit Ausstrahlung, Dichte nur über überwachte ausgestrahlte Folgen, Radarr-Freigabe

**Files:**
- Test: `tests/test_p2_cache_rules.py` (die Implementierung steht schon in P2.2; dieser Task sichert die Regeln aus A8, A9, B6 und A-L2 mit eigenen Tests ab und korrigiert, falls ein Test rot ist)

**Interfaces:**
- Consumes: `SearchMissingSkill` (P2.2), `db.searched.add`, `local_to_utc` (P3)
- Produces: nichts Neues

- [ ] **Step 1: Tests schreiben**

`tests/test_p2_cache_rules.py` (Bausteine von oben einfügen, dazu):

```python
from backend.skills.search_missing import SearchMissingSkill


def run_missing(agent, force=False):
    SearchMissingSkill().execute(agent, force=force)


def cached_days_ago(instance_id, key, days):
    cache_all(instance_id, [key], f"-{days} days")


def rule_since_days_ago(days):
    """Pretend 0.8.0 first started `days` ago (init_db wrote the marker now)."""
    with database.get_db() as conn:
        conn.execute("UPDATE app_settings SET value=datetime('now','localtime', ?) "
                     "WHERE key='ancestor_rule_since'", (f"-{days} days",))


def test_season_search_blocks_only_episodes_that_aired_before_it(db_path):
    rule_since_days_ago(30)
    inst = make_instance()
    cached_days_ago(inst["id"], "sea:1:1", 2)
    agent = agent_for(inst, missing=[episode(1, aired=5), episode(2, aired=1)])
    run_missing(agent)
    assert episode_ids(agent) == [2]


def test_series_search_still_blocks_after_a_mode_switch(db_path):
    rule_since_days_ago(30)
    inst = make_instance(missing_mode="episode")
    cached_days_ago(inst["id"], "ser:1", 2)
    agent = agent_for(inst, missing=[episode(1, aired=5), episode(2, aired=6)])
    run_missing(agent)
    assert agent.posts == []


def test_series_keys_from_before_the_update_do_not_block(db_path):
    # Live on 30.09.2026: Sonarr switched to `episode` because old show_batch
    # `ser:` rows locked 118 of 139 series. 0.8.0 must not lock them again.
    inst = make_instance(missing_mode="episode")
    cached_days_ago(inst["id"], "ser:1", 2)
    cached_days_ago(inst["id"], "sea:1:1", 2)
    agent = agent_for(inst, missing=[episode(1, aired=5), episode(2, aired=6)])
    run_missing(agent)
    assert sorted(episode_ids(agent)) == [1, 2]


def test_season_search_inside_the_release_window_does_not_block(db_path):
    # Episode aired 26 h ago; the season search ran 2 h after the air date,
    # before hours_after_release=9 made the episode a candidate.
    rule_since_days_ago(30)
    inst = make_instance(hours_after_release=9)
    cache_all(inst["id"], ["sea:1:1"], "-24 hours")
    agent = agent_for(inst, missing=[episode(1, aired=26 / 24)])
    run_missing(agent)
    assert episode_ids(agent) == [1]
    cache_all(inst["id"], ["sea:1:2"], "-1 hours")
    later = agent_for(inst, missing=[episode(5, season=2, aired=26 / 24)])
    run_missing(later)
    assert later.posts == [], "a season search after the window does block"


def test_own_key_always_blocks(db_path):
    inst = make_instance()
    cached_days_ago(inst["id"], "ep:3", 400)
    agent = agent_for(inst, missing=[episode(3, aired=1)])
    run_missing(agent)
    assert agent.posts == []


def test_force_ignores_the_cache(db_path):
    inst = make_instance()
    cached_days_ago(inst["id"], "ep:3", 1)
    agent = agent_for(inst, missing=[episode(3, aired=5)])
    run_missing(agent, force=True)
    assert episode_ids(agent) == [3]


def test_unaired_and_unmonitored_episodes_do_not_trigger_a_season_pack(db_path):
    inst = make_instance(missing_mode="season_packs")
    season = [episode(1, aired=3)]
    season += [episode(i, aired=10, has_file=True) for i in (2, 3, 4)]
    season += [episode(i, aired=-7) for i in range(5, 11)]
    season += [episode(i, aired=20, monitored=False) for i in (11, 12, 13)]
    agent = agent_for(inst, missing=[season[0]], episodes=season)
    run_missing(agent)
    assert [p["name"] for p in agent.posts] == ["EpisodeSearch"]


def test_many_missing_aired_episodes_still_trigger_a_season_pack(db_path):
    inst = make_instance(missing_mode="smart")
    season = [episode(i, aired=10) for i in (1, 2, 3, 4)]
    season += [episode(i, aired=10, has_file=True, monitored=False) for i in range(5, 11)]
    agent = agent_for(inst, missing=[season[0]], episodes=season)
    run_missing(agent)
    assert agent.posts == [{"name": "SeasonSearch", "seriesId": 1, "seasonNumber": 1}]
    assert cache_keys() == ["sea:1:1"]


def test_specials_do_not_count_for_a_series_search(db_path):
    inst = make_instance(missing_mode="show_batch")
    specials = [episode(i, season=0, aired=30) for i in range(1, 11)]
    season_one = [episode(101, aired=5)] + [episode(i, aired=40, has_file=True) for i in range(102, 111)]
    agent = agent_for(inst, missing=[season_one[0]], episodes=specials + season_one)
    run_missing(agent)
    assert [p["name"] for p in agent.posts] == ["EpisodeSearch"]


def test_unreadable_episode_list_falls_back_to_an_episode_search(db_path):
    inst = make_instance(missing_mode="season_packs")
    agent = agent_for(inst, missing=[episode(1, aired=3)],
                      get_errors={"/api/v3/episode": requests.exceptions.ConnectionError("refused")})
    run_missing(agent)
    assert [p["name"] for p in agent.posts] == ["EpisodeSearch"]


def test_one_aired_missing_episode_with_only_future_ones_is_a_season_pack(db_path):
    # Only aired, monitored episodes count: 1 of 1 is missing. The season key
    # this writes does not block the future episodes later (air-date rule).
    rule_since_days_ago(30)  # so the moved-back season key is not simply pre-0.8.0
    inst = make_instance(missing_mode="season_packs")
    season = [episode(1, aired=1)] + [episode(i, aired=-3) for i in range(2, 8)]
    agent = agent_for(inst, missing=[season[0]], episodes=season)
    run_missing(agent)
    assert [p["name"] for p in agent.posts] == ["SeasonSearch"]
    with database.get_db() as conn:
        conn.execute("UPDATE searched_items SET searched_at=datetime('now','localtime','-2 days') "
                     "WHERE cache_key='sea:1:1'")
    later = agent_for(inst, missing=[episode(2, aired=1)], episodes=season)
    run_missing(later)
    assert later.posts, "an episode that aired after the season search must not be blocked"


def test_digital_release_counts_for_the_release_window(db_path):
    inst = make_instance(name="Radarr", type="radarr", hours_after_release=9)
    out_digitally = {"id": 1, "title": "Out", "hasFile": False, "inCinemas": iso(90),
                     "digitalRelease": iso(20), "physicalRelease": iso(-60)}
    digital_later = {"id": 2, "title": "Soon", "hasFile": False, "inCinemas": iso(30),
                     "digitalRelease": iso(-10)}
    agent = agent_for(inst, missing=[out_digitally, digital_later])
    run_missing(agent)
    assert [p["movieIds"][0] for p in agent.posts] == [1]


def test_radarr_newest_first_uses_the_home_release(db_path):
    inst = make_instance(name="Radarr", type="radarr", search_order="newest_first")
    movies = [
        {"id": 3, "title": "C", "hasFile": False, "physicalRelease": iso(30)},
        {"id": 1, "title": "A", "hasFile": False, "digitalRelease": iso(3)},
        {"id": 2, "title": "B", "hasFile": False, "physicalRelease": iso(10)},
    ]
    agent = agent_for(inst, missing=movies)
    run_missing(agent)
    assert [p["movieIds"][0] for p in agent.posts] == [1, 2, 3]
```

- [ ] **Step 2: Tests laufen lassen**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p2_cache_rules.py -q -p no:cacheprovider`
Expected: PASS mit der P2.2-Implementierung. Gegenprobe gegen den alten Stand: `git stash` ist hier nicht nötig — dieselben Tests auf 1c05737 schlagen fehl, weil dort `_check_keys` im Modus `episode` nur `ep:` prüft und `digitalRelease` nicht vorkommt. `test_series_keys_from_before_the_update_do_not_block` ist der Regressionstest für den Live-Stand vom 30.09.2026 (Modus `episode`, alte `ser:`-Zeilen); er muss auch dann grün bleiben, wenn jemand `_blocked` später vereinfacht. Schlägt ein Test fehl, liegt der Fehler in `_blocked`, `_density` oder `release_date`; dort korrigieren, nicht im Test.

- [ ] **Step 3: Gesamtsuite**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_p2_cache_rules.py
git commit -m "test: pin air-date aware cache blocking, density over aired monitored episodes, Radarr release date"
```

### Task P2.4: Upgrade-Suche — alle Seiten erreichbar, Quellenfehler ehrlich

**Files:**
- Modify: `backend/skills/search_upgrades.py` (ganze Datei)
- Test: `tests/test_p2_search_upgrades.py`

**Interfaces:**
- Consumes: P2.1, P3 `lookup_many`
- Produces: `SearchUpgradesSkill` mit `UPGRADE_PAGE_BUDGET=20`.

- [ ] **Step 1: Failing test schreiben**

`tests/test_p2_search_upgrades.py` (Bausteine von oben einfügen, dazu):

```python
from backend.skills import search_upgrades
from backend.skills.search_upgrades import SearchUpgradesSkill


def run_upgrades(agent, force=False):
    SearchUpgradesSkill().execute(agent, force=force)


def cutoff_episode(i):
    return {"id": i, "seriesId": i, "seasonNumber": 1, "episodeNumber": 1, "title": f"E{i}",
            "series": {"title": f"Show {i}"}}


def test_pages_beyond_ten_are_reached_and_cached_pages_are_topped_up(db_path, monkeypatch):
    inst = make_instance(search_upgrades_enabled=True, upgrades_per_run=1)
    monkeypatch.setattr(search_upgrades.random, "shuffle", lambda seq: None)
    cache_all(inst["id"], [f"upg:sea:{i}:1" for i in range(1, 701)])  # pages 1-14 of 20
    agent = agent_for(inst, cutoff=[cutoff_episode(i) for i in range(1, 1001)])
    run_upgrades(agent)
    assert agent.posts == [{"name": "SeasonSearch", "seriesId": 701, "seasonNumber": 1}]


def test_all_sources_failing_is_an_error_not_an_empty_result(db_path):
    inst = make_instance(name="Radarr", type="radarr", search_upgrades_enabled=True,
                         upgrade_source="both")
    down = requests.exceptions.ConnectionError("refused")
    agent = agent_for(inst, get_errors={CUTOFF: down, MOVIES: down})
    run_upgrades(agent)
    run = last_run()
    assert run["status"] == "error"
    assert "cutoff list" in run["error_message"] and "monitored movies" in run["error_message"]
    assert agent.state["last_sync"] is None


def test_sonarr_cutoff_list_failing_is_an_error(db_path):
    inst = make_instance(search_upgrades_enabled=True)
    agent = agent_for(inst, get_errors={CUTOFF: requests.exceptions.ReadTimeout("slow")})
    run_upgrades(agent)
    assert last_run()["status"] == "error"


def test_one_failing_source_is_named_but_the_other_is_used(db_path):
    inst = make_instance(name="Radarr", type="radarr", search_upgrades_enabled=True,
                         upgrade_source="both")
    movies = [{"id": 5, "title": "Kept", "year": 2020, "hasFile": True, "monitored": True}]
    agent = agent_for(inst, movies=movies,
                      get_errors={CUTOFF: requests.exceptions.ConnectionError("refused")})
    run_upgrades(agent)
    assert agent.posts == [{"name": "MoviesSearch", "movieIds": [5]}]
    assert "cutoff list" in last_run()["error_message"]


def test_unmonitored_movies_are_not_upgrade_candidates(db_path):
    inst = make_instance(name="Radarr", type="radarr", search_upgrades_enabled=True)
    movies = [{"id": 1, "title": "Off", "hasFile": True, "monitored": False},
              {"id": 2, "title": "On", "hasFile": True, "monitored": True}]
    agent = agent_for(inst, movies=movies)
    run_upgrades(agent)
    assert agent.posts == [{"name": "MoviesSearch", "movieIds": [2]}]


def test_failed_upgrade_post_is_recorded_and_the_run_is_an_error(db_path):
    inst = make_instance(name="Radarr", type="radarr", search_upgrades_enabled=True)
    movies = [{"id": 2, "title": "On", "hasFile": True, "monitored": True}]
    agent = agent_for(inst, movies=movies, all_posts_fail=True)
    run_upgrades(agent)
    run = last_run()
    assert run["status"] == "error"
    assert history.get_item_statuses(run["id"]) == ["failed"]
    assert cache_keys() == []


def test_zero_per_run_does_nothing(db_path):
    inst = make_instance(search_upgrades_enabled=False, upgrades_per_run=0)
    agent = agent_for(inst, cutoff=[cutoff_episode(1)])
    run_upgrades(agent)
    assert agent.gets == [] and agent.posts == []
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p2_search_upgrades.py -q -p no:cacheprovider`
Expected: FAIL (Seite 15 wird nie gelesen, Ausfall ergibt `success`).

- [ ] **Step 3: Implementierung**

`backend/skills/search_upgrades.py` komplett:

```python
import math
import random

from backend import db
from backend.skills.base import BaseSkill, SearchResult, SubmitOutcome, finish_search_run, submit_candidates

CUTOFF_PATH = "/api/v3/wanted/cutoff"
MOVIES_PATH = "/api/v3/movie"

# Random pages read per run and source. No ten-page ceiling: every page of the
# cutoff list is reachable, and a page whose entries are all cached is simply
# followed by the next unvisited one (A5).
UPGRADE_PAGE_BUDGET = 20


class SearchUpgradesSkill(BaseSkill):
    name = "search_upgrades"

    SOURCES = {
        "wanted_list_only": ("cutoff",),
        "monitored_items_only": ("monitored",),
        "both": ("cutoff", "monitored"),
    }
    SOURCE_LABELS = {"cutoff": "cutoff list", "monitored": "monitored movies"}

    def execute(self, agent, force: bool = False) -> None:
        cfg = agent.config
        run_id = db.history.start_run(cfg["id"], cfg["name"], self.name)
        wanted_count = 0
        outcome = SubmitOutcome()

        try:
            per_run = int(cfg.get("upgrades_per_run", 1) or 0)
            if per_run <= 0:
                agent.log("info", self.name, "Upgrades per run is 0 — nothing to do")
                finish_search_run(self.name, agent, run_id, 0, outcome)
                return

            agent.log("info", self.name, "Searching for upgrade candidates...")
            candidates, failures, notes, requested = self._collect_candidates(agent, cfg, per_run, force)
            for failure in failures:
                agent.log("warn", self.name, f"Could not load {failure}")
            if failures and len(failures) == requested:
                # Every source failed: an error, not "no candidates" (A7).
                # last_sync stays untouched.
                db.history.finish_run(run_id, 0, 0, "error", "; ".join(failures))
                return

            notes = failures + notes
            wanted_count = len(candidates)
            if not candidates:
                agent.log("info", self.name, "No upgrade candidates found")
                finish_search_run(self.name, agent, run_id, 0, outcome, notes)
                return

            delay = cfg.get("seconds_between_actions", 2) or 0
            outcome = submit_candidates(
                self.name, agent, run_id, candidates,
                lambda item: self._fire_upgrade(agent, cfg["type"], item),
                delay,
            )
            agent.log("info", self.name,
                      f"Done — candidates: {wanted_count}, triggered: {outcome.triggered}, "
                      f"failed: {len(outcome.errors)}")
            finish_search_run(self.name, agent, run_id, wanted_count, outcome, notes)

        except Exception as exc:
            agent.log("error", self.name, f"Upgrade search failed: {exc}")
            db.history.finish_run(run_id, wanted_count, outcome.triggered, "error", str(exc))

    def _cache_key(self, arr_type: str, item: dict) -> str:
        if arr_type == "radarr":
            return f"upg:{item['id']}"
        # Sonarr: season level when known (SeasonSearch deduplication)
        series_id = item.get("series_id")
        season_number = item.get("season_number")
        if series_id is not None and season_number is not None:
            return f"upg:sea:{series_id}:{season_number}"
        return f"upg:{item['id']}"

    def _trigger_upgrade(self, agent, arr_type: str, item: dict) -> SearchResult:
        """Fire the upgrade search and report what was actually addressed.
        Reuses _cache_key so the pre-filter and the stored key never disagree."""
        label = item.get("label") or item.get("title") or f"#{item['id']}"
        cache_key = self._cache_key(arr_type, item)

        if arr_type == "radarr":
            movie_id = item["id"]
            resp = agent.http_post("/api/v3/command", {"name": "MoviesSearch", "movieIds": [movie_id]})
            return SearchResult(True, label, "movie", cache_key, movie_id, resp.get("id"))

        series_id = item.get("series_id")
        season_number = item.get("season_number")
        if series_id is not None and season_number is not None:
            resp = agent.http_post(
                "/api/v3/command",
                {"name": "SeasonSearch", "seriesId": series_id, "seasonNumber": season_number},
            )
            return SearchResult(True, label, "season", cache_key, series_id, resp.get("id"))

        episode_id = item["id"]
        resp = agent.http_post("/api/v3/command", {"name": "EpisodeSearch", "episodeIds": [episode_id]})
        return SearchResult(True, label, "episode", cache_key, episode_id, resp.get("id"))

    def _fire_upgrade(self, agent, arr_type: str, item: dict) -> SearchResult:
        try:
            result = self._trigger_upgrade(agent, arr_type, item)
            agent.log("debug", self.name, f"Upgrade search: {result.title}")
            return result
        except Exception as exc:
            if arr_type == "radarr":
                item_type = "movie"
            elif item.get("series_id") is not None and item.get("season_number") is not None:
                item_type = "season"
            else:
                item_type = "episode"
            return SearchResult(False, item.get("label") or f"#{item['id']}", item_type, "",
                                item.get("id"), None, str(exc))

    # ── Candidates ───────────────────────────────────────────────────────────

    def _collect_candidates(self, agent, cfg, per_run, force):
        """Returns (candidates, failed sources, notes, number of sources asked)."""
        if cfg["type"] == "radarr":
            sources = self.SOURCES.get(cfg.get("upgrade_source", "monitored_items_only"), ("monitored",))
        else:
            sources = ("cutoff",)

        found: list = []
        seen: set = set()
        failures: list = []
        notes: list = []
        for source in sources:
            try:
                if source == "cutoff":
                    self._collect_cutoff(agent, cfg, per_run, force, found, seen, notes)
                else:
                    self._collect_monitored(agent, cfg, per_run, force, found, seen)
            except Exception as exc:
                failures.append(f"{self.SOURCE_LABELS[source]}: {exc}")

        random.shuffle(found)
        return found[:per_run], failures, notes, len(sources)

    def _keep_uncached(self, cfg, items, force, found, seen, limit) -> None:
        keyed = [(self._cache_key(cfg["type"], item), item) for item in items]
        hits = {} if force else db.searched.lookup_many(
            cfg["id"], [key for key, _ in keyed], int(cfg.get("retry_hours", 0) or 0)
        )
        for key, item in keyed:
            if len(found) >= limit:
                return
            if key in hits or key in seen:
                continue
            seen.add(key)
            found.append(item)

    def _collect_cutoff(self, agent, cfg, per_run, force, found, seen, notes) -> None:
        arr_type = cfg["type"]
        limit = len(found) + per_run
        pool = max(per_run * 5, 50)
        probe = agent.http_get(CUTOFF_PATH, params={"pageSize": 1, "page": 1, "monitored": "true"})
        total = int(probe.get("totalRecords", 0) or 0)
        pages = list(range(1, max(1, math.ceil(total / pool)) + 1))
        random.shuffle(pages)
        budget = max(UPGRADE_PAGE_BUDGET, math.ceil(per_run * 4 / pool))

        loaded = 0
        for page in pages[:budget]:
            if len(found) >= limit or agent.stop_requested():
                return
            try:
                resp = agent.http_get(CUTOFF_PATH, params={"pageSize": pool, "page": page, "monitored": "true"})
            except Exception as exc:
                if loaded == 0:
                    raise
                notes.append(f"cutoff page {page} could not be loaded: {exc}")
                return
            loaded += 1
            items = [self._cutoff_item(arr_type, r) for r in resp.get("records") or []]
            items = [item for item in items if item is not None]
            random.shuffle(items)
            self._keep_uncached(cfg, items, force, found, seen, limit)

    @staticmethod
    def _cutoff_item(arr_type: str, record: dict):
        if "id" not in record:
            return None
        if arr_type == "radarr":
            if not record.get("hasFile"):
                return None
            year = record.get("year", "")
            title = record.get("title") or f"Movie #{record['id']}"
            return {"id": record["id"], "label": f"{title} ({year})" if year else title}
        series = record.get("series") or {}
        series_title = series.get("title") or record.get("seriesTitle", "") or f"Series #{record.get('seriesId', '?')}"
        season_number = record.get("seasonNumber")
        episode_title = record.get("title", "")
        if season_number is not None:
            label = f"{series_title} S{(season_number or 0):02d}E{(record.get('episodeNumber') or 0):02d}"
            if episode_title:
                label += f" – {episode_title}"
        else:
            label = episode_title or f"Episode #{record['id']}"
        return {"id": record["id"], "label": label,
                "series_id": record.get("seriesId"), "season_number": season_number}

    def _collect_monitored(self, agent, cfg, per_run, force, found, seen) -> None:
        limit = len(found) + per_run
        movies = agent.http_get(MOVIES_PATH, params={"monitored": "true"})
        items = []
        for movie in movies if isinstance(movies, list) else []:
            # Checked here as well: the monitored filter of /movie is not
            # guaranteed on every Radarr version.
            if "id" not in movie or not movie.get("hasFile") or not movie.get("monitored", True):
                continue
            year = movie.get("year", "")
            title = movie.get("title") or f"Movie #{movie['id']}"
            items.append({"id": movie["id"], "label": f"{title} ({year})" if year else title})
        random.shuffle(items)
        self._keep_uncached(cfg, items, force, found, seen, limit)
```

- [ ] **Step 4: Tests grün**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p2_search_upgrades.py -q -p no:cacheprovider && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/skills/search_upgrades.py tests/test_p2_search_upgrades.py
git commit -m "fix: reach every page of the cutoff list and report failed upgrade sources"
```

### Task P2.5: Health-Check erkennt falschen Schlüssel und Weiterleitungen

**Files:**
- Modify: `backend/skills/health_check.py` (HTTPError-Zweig)
- Test: `tests/test_p2_health.py`

**Interfaces:**
- Consumes: P1.5 (3xx als `HTTPError` mit `.response`)
- Produces: `connection_status` `error` bei 401/403.

- [ ] **Step 1: Failing test schreiben**

`tests/test_p2_health.py` (Fixture `db_path`, `make_instance` von oben, dazu):

```python
from backend.skills.health_check import HealthCheckSkill


class StatusAgent(BaseAgent):
    def __init__(self, config, status):
        super().__init__(config)
        self.status = status

    def build_skills(self):
        return []

    def http_get(self, path, params=None):
        response = requests.Response()
        response.status_code = self.status
        raise requests.exceptions.HTTPError(f"{self.status} error", response=response)


def messages():
    return [r["message"] for r in db.activity.query(include_debug=True, limit=50)]


@pytest.mark.parametrize("status,expected,text", [
    (401, "error", "Invalid API key"),
    (403, "error", "Invalid API key"),
    (500, "offline", "HTTP 500"),
    (302, "offline", "redirects are not followed"),
])
def test_http_errors_keep_their_status_code(db_path, status, expected, text):
    inst = make_instance()
    agent = StatusAgent(db.instances.get_by_id(inst["id"]), status)
    HealthCheckSkill().execute(agent)
    assert agent.state["connection_status"] == expected
    assert db.instances.get_by_id(inst["id"])["connection_status"] == expected
    assert any(text in m for m in messages())
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p2_health.py -q -p no:cacheprovider`
Expected: FAIL (`offline` statt `error`, Log „HTTP 0“).

- [ ] **Step 3: Implementierung**

In `backend/skills/health_check.py` den `HTTPError`-Zweig ersetzen:

```python
        except requests.exceptions.HTTPError as exc:
            # Not `if exc.response`: Response.__bool__ is .ok, so every 4xx/5xx
            # read as "no response" and a wrong API key showed up as HTTP 0 (A-L1).
            status_code = exc.response.status_code if exc.response is not None else 0
            if status_code in (401, 403):
                agent.log("error", self.name, "Invalid API key")
                db.instances.update_status(cfg["id"], "error")
                agent.state["connection_status"] = "error"
            elif 300 <= status_code < 400:
                agent.log("warn", self.name,
                          f"HTTP {status_code} from *arr API — redirects are not followed, check the URL")
                db.instances.update_status(cfg["id"], "offline")
                agent.state["connection_status"] = "offline"
            else:
                agent.log("warn", self.name, f"HTTP {status_code} from *arr API")
                db.instances.update_status(cfg["id"], "offline")
                agent.state["connection_status"] = "offline"
```

- [ ] **Step 4: Tests grün**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p2_health.py -q -p no:cacheprovider && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/skills/health_check.py tests/test_p2_health.py
git commit -m "fix: health check reports a wrong API key and redirects instead of HTTP 0"
```

---
## Paket P4 — Web, Anmeldung, Schlüssel (Welle 2)

Voraussetzung: Welle 1 komplett (P1-Orchestrator-API und Trigger-Konstanten, P3 `config.py`-Felder, `db.app_settings`, `close_interrupted_runs`, `FIELD_BOUNDS` aus P1). Reihenfolge innerhalb von P4: P4.1 zuerst (danach ist `import backend.main` gefahrlos), dann P4.2 bis P4.7 der Reihe nach (alle ändern `main.py` oder `auth.py`).

Abnahme P4: `tests/test_p4_*.py` grün, Gesamtsuite grün; `grep -rn "passlib" backend/` leer; `grep -n "^init_db()\|get_or_create_secret_key()" backend/main.py` leer; der curl-Ablauf aus dem Vault (Test `test_documented_curl_flow_still_works`) ist grün; kein API-Endpunkt und keine Seite enthält den Klartext-Schlüssel (Tests in P4.5/P4.6).

Gemeinsame Test-Bausteine für P4-Dateien mit HTTP-Client (in jede Datei kopieren, die sie braucht). Wichtig: `backend.main` wird erst **in** der Fixture importiert, nachdem `settings.database_url` umgebogen ist.

```python
import pytest
from fastapi.testclient import TestClient

from backend import database, db
from backend.config import settings

PASSWORD = "correct horse battery"
HOST = "missingarr.test"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_url", str(tmp_path / "missingarr.db"))
    monkeypatch.setattr(settings, "auth_password", PASSWORD)
    monkeypatch.setattr(settings, "secret_key", "")
    monkeypatch.setattr(settings, "cookie_secure", False)
    monkeypatch.setattr(database, "_cached_secret_key", None)
    from backend import crypto, main
    crypto._reset_cache()
    main.app.middleware_stack = None
    with TestClient(main.app, base_url=f"http://{HOST}") as test_client:
        yield test_client
    crypto._reset_cache()
    main.app.middleware_stack = None


def login(client, remember=False, next_path="/"):
    data = {"username": "admin", "password": PASSWORD, "next": next_path}
    if remember:
        data["remember"] = "true"
    return client.post("/login", data=data, follow_redirects=False)


def make_instance(**fields):
    data = {"name": "Sonarr", "type": "sonarr", "url": "http://127.0.0.1:9",
            "api_key": "SUPERSECRETKEY1234567890", "enabled": False}
    data.update(fields)
    return db.instances.create(data)
```

### Task P4.1: `import backend.main` öffnet keine Datenbank mehr

**Files:**
- Modify: `backend/main.py` (Modulende: `init_db()`/`get_or_create_secret_key()` weg, Middleware-Registrierung)
- Modify: `backend/auth.py` (neu `LazySessionMiddleware`)
- Modify: `backend/crypto.py` (neu `get_session_secret`, `_reset_cache`)
- Test: `tests/test_p4_startup.py`

**Interfaces:**
- Consumes: `database.get_or_create_secret_key` (unverändert)
- Produces: `LazySessionMiddleware(app, secret_provider, **options)`, `crypto.get_session_secret()`, `crypto._reset_cache()`. Ab hier dürfen Tests `backend.main` in Fixtures importieren.

- [ ] **Step 1: Failing test schreiben**

`tests/test_p4_startup.py` (Bausteine von oben einfügen, dazu):

```python
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_importing_main_does_not_touch_any_database(tmp_path):
    target = tmp_path / "never.db"
    env = {**os.environ, "DATABASE_URL": str(target)}
    subprocess.run([sys.executable, "-c", "import backend.main"], cwd=REPO, env=env, check=True)
    assert not target.exists()


def test_login_works_with_the_lazily_read_session_key(client):
    assert login(client).status_code == 302
    assert client.get("/api/instances").status_code == 200
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p4_startup.py -q -p no:cacheprovider`
Expected: FAIL — `never.db` wird beim Import angelegt. (Der Subprozess schreibt nur in `tmp_path`, weil `DATABASE_URL` gesetzt ist.)

- [ ] **Step 3: Implementierung**

`backend/crypto.py` ergänzen:

```python
from backend.database import get_db, get_or_create_secret_key

_session_secret: str | None = None


def get_session_secret() -> str:
    """Key that signs session cookies and remember-me tokens."""
    global _session_secret
    if _session_secret is None:
        _session_secret = get_or_create_secret_key()
    return _session_secret


def _reset_cache() -> None:
    """Tests switch databases; forget the cached keys."""
    global _fernet, _session_secret
    _fernet = None
    _session_secret = None
```

`backend/auth.py` ergänzen (Import `from starlette.middleware.sessions import SessionMiddleware`):

```python
class LazySessionMiddleware(SessionMiddleware):
    """SessionMiddleware that reads its signing key and cookie flags on the
    first HTTP request instead of at import time.

    The key lives in the database (or is derived from SECRET_KEY), and
    importing backend.main must not open a database: tests and tools import it
    without the real data directory. Lifespan messages pass straight through,
    so the key is only read after the lifespan has run init_db().
    """

    def __init__(self, app, secret_provider, **options):
        self.app = app
        self._secret_provider = secret_provider
        self._options = options
        self._ready = False

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        if not self._ready:
            SessionMiddleware.__init__(
                self, self.app,
                secret_key=self._secret_provider(),
                https_only=settings.cookie_secure,
                **self._options,
            )
            self._ready = True
        await SessionMiddleware.__call__(self, scope, receive, send)
```

`backend/main.py`: die Zeilen

```python
# Init DB early so we can read the persisted secret key before middleware is wired.
init_db()
_session_secret = get_or_create_secret_key()
```

entfernen, Import `from backend.database import init_db` (ohne `get_or_create_secret_key`), `from backend.crypto import get_session_secret`, und die Middleware-Zeilen ersetzen durch:

```python
# Middleware order: last added = outermost = runs first.
# Session must wrap Auth so the session is available when Auth checks it.
app.add_middleware(AuthMiddleware)
app.add_middleware(
    LazySessionMiddleware,
    secret_provider=get_session_secret,
    session_cookie="ma_session",
    same_site="lax",
)
```

`LazySessionMiddleware` in den Import aus `backend.auth` aufnehmen.

- [ ] **Step 4: Tests grün**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p4_startup.py -q -p no:cacheprovider && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/main.py backend/auth.py backend/crypto.py tests/test_p4_startup.py
git commit -m "fix: read the session key lazily so importing the app opens no database"
```

### Task P4.2: `SECRET_KEY` opt-in mit einmaliger Umschlüsselung

**Files:**
- Modify: `backend/crypto.py` (ganze Datei)
- Modify: `backend/main.py` (Lifespan: `init_crypto()` nach `init_db()`)
- Test: `tests/test_p4_crypto.py`

**Interfaces:**
- Consumes: `settings.secret_key` (P3.1), `database.get_db`, `database.get_or_create_secret_key`
- Produces: `init_crypto()`, `get_session_secret()`, `encrypt()`, `decrypt()`, Konstanten `KEY_SOURCE_SETTING`, `KEY_CHECK_SETTING`, `FERNET_INFO`, `SESSION_INFO`, `CHECK_INFO`, `_derive(secret, info) -> bytes`.

- [ ] **Step 1: Failing test schreiben**

`tests/test_p4_crypto.py`:

```python
import base64

import pytest
from cryptography.fernet import Fernet

from backend import crypto, database, db
from backend.config import settings


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "missingarr.db"
    monkeypatch.setattr(settings, "database_url", str(path))
    monkeypatch.setattr(settings, "secret_key", "")
    monkeypatch.setattr(database, "_cached_secret_key", None)
    crypto._reset_cache()
    database.init_db()
    yield path
    crypto._reset_cache()


def make_instance(name, api_key):
    return db.instances.create({"name": name, "type": "sonarr", "url": "http://s:8989", "api_key": api_key})


def raw_keys():
    with database.get_db() as conn:
        return [r[0] for r in conn.execute("SELECT api_key FROM instances ORDER BY id")]


def stored_settings():
    with database.get_db() as conn:
        return dict(conn.execute("SELECT key, value FROM app_settings").fetchall())


def switch_to(monkeypatch, secret):
    monkeypatch.setattr(settings, "secret_key", secret)
    crypto._reset_cache()
    crypto.init_crypto()


def test_without_secret_key_the_keys_stay_in_the_database(db_path):
    crypto.init_crypto()
    inst = make_instance("Sonarr", "plain-key-123")
    assert db.instances.get_by_id(inst["id"])["api_key"] == "plain-key-123"
    rows = stored_settings()
    assert "encryption_key" in rows and "key_source" not in rows
    assert crypto.get_session_secret() == rows["secret_key"]


def test_secret_key_reencrypts_once_and_removes_the_stored_keys(db_path, monkeypatch):
    crypto.init_crypto()
    first = make_instance("Sonarr", "first-key")
    with database.get_db() as conn:
        conn.execute("INSERT INTO instances (name, type, url, api_key) "
                     "VALUES ('Legacy', 'radarr', 'http://r:7878', 'legacy-plain')")
    old_session = crypto.get_session_secret()

    switch_to(monkeypatch, "s3cret-value")

    rows = stored_settings()
    assert rows["key_source"] == "env"
    assert "encryption_key" not in rows and "secret_key" not in rows
    derived = Fernet(base64.urlsafe_b64encode(crypto._derive("s3cret-value", crypto.FERNET_INFO)))
    assert [derived.decrypt(v[4:].encode()).decode() for v in raw_keys()] == ["first-key", "legacy-plain"]
    assert db.instances.get_by_id(first["id"])["api_key"] == "first-key"
    assert crypto.get_session_secret() != old_session

    before = raw_keys()
    switch_to(monkeypatch, "s3cret-value")
    assert raw_keys() == before


def test_missing_secret_key_after_the_switch_stops_the_start(db_path, monkeypatch):
    crypto.init_crypto()
    make_instance("Sonarr", "first-key")
    switch_to(monkeypatch, "s3cret-value")
    with pytest.raises(RuntimeError, match="SECRET_KEY is not set"):
        switch_to(monkeypatch, "")


def test_a_different_secret_key_stops_the_start(db_path, monkeypatch):
    crypto.init_crypto()
    switch_to(monkeypatch, "s3cret-value")
    with pytest.raises(RuntimeError, match="does not match"):
        switch_to(monkeypatch, "another-value")


def test_encrypt_works_without_an_explicit_init(db_path):
    crypto._reset_cache()
    assert crypto.decrypt(crypto.encrypt("abc")) == "abc"


def test_lost_secret_key_recovery_path_works(db_path, monkeypatch):
    # The way out documented in README and Risiken: without the old value the
    # stored API keys are lost, so they are cleared and entered again. Only
    # deleting the marker rows is not enough: a fresh Fernet key would then
    # fail on every enc: row and stop the start in orchestrator.start_all().
    crypto.init_crypto()
    make_instance("Sonarr", "first-key")
    switch_to(monkeypatch, "s3cret-value")
    with database.get_db() as conn:
        conn.execute("DELETE FROM app_settings WHERE key IN ('key_source', 'secret_key_check')")
        conn.execute("UPDATE instances SET api_key=''")
    switch_to(monkeypatch, "")
    assert [inst["api_key"] for inst in db.instances.get_all()] == [""]
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p4_crypto.py -q -p no:cacheprovider`
Expected: FAIL mit `AttributeError: module 'backend.crypto' has no attribute 'init_crypto'`.

- [ ] **Step 3: Implementierung**

`backend/crypto.py` komplett (kein Import von `backend.db` — das würde einen Import-Kreis über `backend.db.instances` bilden; deshalb direktes SQL):

```python
"""
Encryption of the *arr API keys stored in the database, and the session key.

Without SECRET_KEY (the default, as before 0.8.0) a random Fernet key and a
random session key are generated once and stored in app_settings. That
survives restarts, but whoever has the database file has the keys too.

With SECRET_KEY both keys are derived from it (HKDF-SHA256) and never stored.
On the first start with SECRET_KEY the stored API keys are re-encrypted and
the stored keys are deleted (C5). From then on the database is marked
key_source=env and the same SECRET_KEY is required to start.

Stored values carry the prefix 'enc:'. Values without it are legacy plain
text and are returned as they are.
"""
import base64
import hashlib
import hmac
import logging
import sqlite3

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from backend.config import settings
from backend.database import get_db, get_or_create_secret_key

logger = logging.getLogger("missingarr.crypto")

_ENC_PREFIX = "enc:"
_KEY_SETTING = "encryption_key"
KEY_SOURCE_SETTING = "key_source"
KEY_CHECK_SETTING = "secret_key_check"
FERNET_INFO = b"missingarr fernet v1"
SESSION_INFO = b"missingarr session v1"
CHECK_INFO = b"missingarr check v1"

_fernet: Fernet | None = None
_session_secret: str | None = None


def _derive(secret: str, info: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=b"missingarr", info=info).derive(secret.encode())


def _setting(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def _stored_fernet_key(create: bool) -> bytes | None:
    with get_db() as conn:
        value = _setting(conn, _KEY_SETTING)
        if value:
            return value.encode()
        if not create:
            return None
        key = Fernet.generate_key()
        conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?)", (_KEY_SETTING, key.decode()))
        logger.info("Generated new encryption key and stored in DB")
        return key


def init_crypto() -> None:
    """Decide where the keys come from. Called at startup; raises when the
    database needs SECRET_KEY and it is missing or wrong."""
    global _fernet, _session_secret
    secret = (settings.secret_key or "").strip()
    with get_db() as conn:
        source = _setting(conn, KEY_SOURCE_SETTING) or "db"
        stored_check = _setting(conn, KEY_CHECK_SETTING)

    if source == "env" and not secret:
        raise RuntimeError(
            "SECRET_KEY is not set, but this database was encrypted with a key derived from "
            "SECRET_KEY. Set the same SECRET_KEY again — it cannot be recovered."
        )

    if not secret:
        _fernet = Fernet(_stored_fernet_key(create=True))
        _session_secret = get_or_create_secret_key()
        return

    fernet_key = base64.urlsafe_b64encode(_derive(secret, FERNET_INFO))
    check = hmac.new(_derive(secret, CHECK_INFO), b"missingarr", hashlib.sha256).hexdigest()
    if source == "env":
        if not hmac.compare_digest(check, stored_check or ""):
            raise RuntimeError(
                "SECRET_KEY does not match the key this database was encrypted with. "
                "Restore the previous SECRET_KEY."
            )
    else:
        _migrate_to_secret_key(Fernet(fernet_key), check)

    _fernet = Fernet(fernet_key)
    _session_secret = _derive(secret, SESSION_INFO).hex()


def _migrate_to_secret_key(new: Fernet, check: str) -> None:
    old_key = _stored_fernet_key(create=False)
    old = Fernet(old_key) if old_key else None
    with get_db() as conn:
        rows = conn.execute("SELECT id, api_key FROM instances").fetchall()
        for row in rows:
            value = row["api_key"] or ""
            if not value:
                continue
            if value.startswith(_ENC_PREFIX):
                if old is None:
                    raise RuntimeError("Encrypted API keys found but no stored encryption key — cannot re-encrypt")
                plain = old.decrypt(value[len(_ENC_PREFIX):].encode()).decode()
            else:
                plain = value
            conn.execute(
                "UPDATE instances SET api_key=? WHERE id=?",
                (_ENC_PREFIX + new.encrypt(plain.encode()).decode(), row["id"]),
            )
        conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, 'env')", (KEY_SOURCE_SETTING,))
        conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", (KEY_CHECK_SETTING, check))
        conn.execute("DELETE FROM app_settings WHERE key IN (?, 'secret_key')", (_KEY_SETTING,))
    try:
        # The deleted keys would otherwise linger in free pages of the file.
        conn = sqlite3.connect(settings.database_url)
        conn.execute("VACUUM")
        conn.close()
    except sqlite3.Error as exc:
        logger.warning("VACUUM after re-encryption failed: %s", exc)
    logger.warning(
        "Re-encrypted %d API key(s) with a key derived from SECRET_KEY and removed the stored keys. "
        "Keep SECRET_KEY safe: without it the API keys cannot be decrypted.",
        len(rows),
    )


def _get_fernet() -> Fernet:
    if _fernet is None:
        init_crypto()
    return _fernet


def get_session_secret() -> str:
    """Key that signs session cookies and remember-me tokens."""
    if _session_secret is None:
        init_crypto()
    return _session_secret


def _reset_cache() -> None:
    """Tests switch databases; forget the cached keys."""
    global _fernet, _session_secret
    _fernet = None
    _session_secret = None


def encrypt(plain: str) -> str:
    """Encrypt a plain-text string. Returns 'enc:<ciphertext>'."""
    if not plain:
        return plain
    return f"{_ENC_PREFIX}{_get_fernet().encrypt(plain.encode()).decode()}"


def decrypt(value: str) -> str:
    """Decrypt a stored value. Plain-text (legacy) values are returned as-is."""
    if not value or not value.startswith(_ENC_PREFIX):
        return value
    return _get_fernet().decrypt(value[len(_ENC_PREFIX):].encode()).decode()
```

`backend/main.py`, Lifespan: nach `init_db()` die Zeile `init_crypto()` einfügen (Import `from backend.crypto import get_session_secret, init_crypto`). Ein `RuntimeError` dort beendet den Start mit der Meldung im Log — gewollt.

- [ ] **Step 4: Tests grün**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p4_crypto.py tests/test_p4_startup.py -q -p no:cacheprovider && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/crypto.py backend/main.py tests/test_p4_crypto.py
git commit -m "feat: opt-in SECRET_KEY derives the encryption and session keys and re-encrypts once"
```

### Task P4.3: 401 statt Umleitung für API und htmx, CSRF-Prüfung

**Files:**
- Modify: `backend/auth.py` (`AuthMiddleware`, neu `CSRFMiddleware`, `is_same_origin_request`, `unauthenticated_response`, `start_session`)
- Modify: `backend/main.py` (CSRF-Middleware registrieren)
- Test: `tests/test_p4_middleware.py`

**Interfaces:**
- Consumes: nichts Neues
- Produces: HTTP-Verhalten laut Vertrag (401/HX-Redirect/403), `is_same_origin_request(headers) -> bool`, `start_session(request) -> None`.

- [ ] **Step 1: Failing test schreiben**

`tests/test_p4_middleware.py` (Client-Bausteine einfügen, dazu):

```python
from backend.auth import is_same_origin_request


def test_api_without_session_gets_401_json(client):
    resp = client.get("/api/instances", follow_redirects=False)
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Not authenticated"}


def test_htmx_without_session_gets_401_with_hx_redirect(client):
    resp = client.get("/history", headers={"HX-Request": "true"}, follow_redirects=False)
    assert resp.status_code == 401
    assert resp.headers["HX-Redirect"] == "/login?next=/history"


def test_card_polling_without_session_is_sent_to_the_login_page(client):
    # The dashboard card polls this through htmx every 5 s.
    resp = client.get("/api/instances/1/status", follow_redirects=False,
                      headers={"HX-Request": "true", "HX-Current-URL": f"http://{HOST}/?view=all"})
    assert resp.status_code == 401
    assert resp.json() == {"detail": "Not authenticated"}
    assert resp.headers["HX-Redirect"] == "/login?next=/%3Fview%3Dall"


def test_odd_hx_current_url_falls_back_to_root(client):
    resp = client.get("/api/instances/1/status", follow_redirects=False,
                      headers={"HX-Request": "true", "HX-Current-URL": "javascript:alert(1)"})
    assert resp.headers["HX-Redirect"] == "/login?next=/"
    # Only the path is used; a foreign host in the header never becomes the target.
    resp = client.get("/api/instances/1/status", follow_redirects=False,
                      headers={"HX-Request": "true", "HX-Current-URL": "http://evil.example/x"})
    assert resp.headers["HX-Redirect"] == "/login?next=/x"


def test_page_without_session_is_redirected(client):
    resp = client.get("/history", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/login?next=/history"


def test_public_paths_stay_public(client):
    assert client.get("/api/health").status_code == 200
    assert client.get("/login").status_code == 200


@pytest.mark.parametrize("headers", [
    {"Origin": "http://evil.example"},
    {"Origin": "null"},
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},
    {"Sec-Fetch-Site": "same-site", "Origin": f"http://{HOST}"},
])
def test_cross_site_writes_are_blocked(client, headers):
    login(client)
    resp = client.post("/api/instances/1/trigger?skill=search_missing", headers=headers)
    assert resp.status_code == 403
    assert resp.json() == {"detail": "Cross-site request blocked"}


@pytest.mark.parametrize("headers", [
    {},
    {"Sec-Fetch-Site": "same-origin"},
    {"Sec-Fetch-Site": "none"},
    {"Origin": f"http://{HOST}"},
])
def test_same_origin_and_non_browser_writes_pass(client, headers):
    login(client)
    resp = client.post("/api/instances/999/trigger?skill=search_missing", headers=headers)
    assert resp.status_code == 404  # reached the route: no such instance


def test_reads_are_not_checked(client):
    login(client)
    assert client.get("/api/instances", headers={"Origin": "http://evil.example"}).status_code == 200


def test_login_from_another_site_is_blocked(client):
    resp = client.post("/login", data={"username": "admin", "password": PASSWORD},
                       headers={"Origin": "http://evil.example"}, follow_redirects=False)
    assert resp.status_code == 403


def test_origin_check_unit():
    assert is_same_origin_request({"host": "a:8000", "origin": "http://a:8000"})
    assert not is_same_origin_request({"host": "a:8000", "origin": "http://a:9000"})
    assert is_same_origin_request({"host": "a:8000"})
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p4_middleware.py -q -p no:cacheprovider`
Expected: FAIL (`ImportError: cannot import name 'is_same_origin_request'`).

- [ ] **Step 3: Implementierung**

`backend/auth.py` — Imports ergänzen: `from urllib.parse import quote, urlsplit`, `from fastapi.responses import JSONResponse, RedirectResponse, Response`. Neu bzw. ersetzt:

```python
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def is_same_origin_request(headers) -> bool:
    """True unless a browser tells us the request comes from another site (C3).

    Modern browsers send Sec-Fetch-Site on every request; 'same-site' is not
    enough, because every other service on the same host counts as same-site.
    Older browsers send Origin on unsafe requests. Clients that send neither
    (curl, scripts) are not browsers and cannot be forged by a web page.
    """
    site = headers.get("sec-fetch-site")
    if site is not None:
        return site in ("same-origin", "none")
    origin = headers.get("origin")
    if origin is None:
        return True
    if origin == "null":
        return False
    return urlsplit(origin).netloc.lower() == (headers.get("host") or "").lower()


class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method not in SAFE_METHODS and not is_same_origin_request(request.headers):
            logger.warning(
                "Blocked cross-site %s %s (Origin=%r, Sec-Fetch-Site=%r)",
                request.method, request.url.path,
                request.headers.get("origin"), request.headers.get("sec-fetch-site"),
            )
            return JSONResponse({"detail": "Cross-site request blocked"}, status_code=403)
        return await call_next(request)


def start_session(request: Request) -> None:
    request.session["user"] = settings.auth_username


def _login_target(wanted: str) -> str:
    return "/login?next=" + quote(wanted, safe="/")


def _current_page(request: Request) -> str:
    """The page an htmx call was made from (HX-Current-URL), as a local path.
    Anything odd falls back to '/'; /login runs safe_next() on it again."""
    current = urlsplit(request.headers.get("hx-current-url") or "")
    path = current.path or "/"
    if not path.startswith("/") or path.startswith("//"):
        return "/"
    return path + (f"?{current.query}" if current.query else "")


def unauthenticated_response(request: Request):
    """Pages get the login redirect; API calls and htmx get a 401 they can act
    on. A 302 made fetch() follow to the login page and report success (C-L6).

    htmx is checked first: the dashboard card polls /api/instances/{id}/status
    every 5 s through htmx. Its 401 carries HX-Redirect back to the page the
    card is on, otherwise htmx would never leave the dead page."""
    path = request.url.path
    is_api = path.startswith("/api/")
    if request.headers.get("hx-request") == "true":
        if is_api:
            return JSONResponse(
                {"detail": "Not authenticated"}, status_code=401,
                headers={"HX-Redirect": _login_target(_current_page(request))},
            )
        wanted = path + (f"?{request.url.query}" if request.url.query else "")
        return Response(status_code=401, headers={"HX-Redirect": _login_target(wanted)})
    if is_api:
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    wanted = path + (f"?{request.url.query}" if request.url.query else "")
    return RedirectResponse(_login_target(wanted), status_code=302)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path == "/login" or path.startswith(_PUBLIC_PREFIXES):
            return await call_next(request)
        if is_authenticated(request):
            return await call_next(request)
        token = request.cookies.get(_REMEMBER_COOKIE)
        if token and verify_remember_token(token) == settings.auth_username:
            start_session(request)
            return await call_next(request)
        return unauthenticated_response(request)
```

`backend/main.py`, nach der Session-Middleware (damit CSRF ganz außen läuft):

```python
app.add_middleware(CSRFMiddleware)
```

und `CSRFMiddleware` in den Import aus `backend.auth` aufnehmen.

- [ ] **Step 4: Tests grün**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p4_middleware.py -q -p no:cacheprovider && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/auth.py backend/main.py tests/test_p4_middleware.py
git commit -m "fix: answer API and htmx calls with 401 and reject cross-site writes"
```

### Task P4.4: Remember-Token v2, Abmelden widerruft alles, Login-Drossel, bcrypt, sichere `next`

**Files:**
- Modify: `backend/auth.py` (Token, `token_version`, Passwort-Fingerabdruck, `LoginThrottle`, `verify_password`, `is_authenticated`, `start_session`; `passlib` raus)
- Modify: `backend/main.py` (`login_page`, `login_submit`, `logout`, neu `safe_next`, `client_ip`, `_login_page`)
- Test: `tests/test_p4_auth.py`

**Interfaces:**
- Consumes: `db.app_settings` (P3.2), `crypto.get_session_secret` (P4.2), `settings.cookie_secure` (P3.1)
- Produces: `REMEMBER_COOKIE`, `REMEMBER_MAX_AGE`, `create_remember_token`, `verify_remember_token`, `current_token_version`, `revoke_all_tokens`, `LoginThrottle`, `login_throttle`, `safe_next`, `POST /logout`.

- [ ] **Step 1: Failing test schreiben**

`tests/test_p4_auth.py` (Client-Bausteine einfügen, dazu):

```python
import logging
import re
import time
from pathlib import Path

import bcrypt

from backend import auth


@pytest.fixture(autouse=True)
def clean_auth_state():
    auth.login_throttle.reset()
    auth._reset_token_version_cache()
    yield
    auth.login_throttle.reset()
    auth._reset_token_version_cache()


def test_remember_cookie_has_the_v2_format(client):
    resp = login(client, remember=True)
    assert re.fullmatch(r"v2\.\d+\.0\.[0-9a-f]{64}", resp.cookies["ma_remember"])


def test_remember_cookie_alone_restores_the_session(client):
    login(client, remember=True)
    client.cookies.delete("ma_session")
    assert client.get("/api/instances").status_code == 200


def tampered():
    token = auth.create_remember_token("admin")
    return token[:-1] + ("1" if token[-1] == "0" else "0")


@pytest.mark.parametrize("make_token", [
    tampered,
    lambda: "admin:" + "0" * 64,
    lambda: auth.create_remember_token("admin", now=int(time.time()) - 31 * 24 * 3600),
    lambda: auth.create_remember_token("admin", now=int(time.time()) + 3600),
])
def test_tampered_old_or_expired_tokens_are_rejected(client, make_token):
    client.get("/api/health")  # initialise the session key
    client.cookies.set("ma_remember", make_token(), domain=HOST)
    assert client.get("/api/instances").status_code == 401


def test_logout_revokes_every_session_and_token(client):
    login(client, remember=True)
    stolen = dict(client.cookies)
    resp = client.post("/logout", follow_redirects=False)
    assert (resp.status_code, resp.headers["location"]) == (303, "/login")
    client.cookies.clear()
    for name, value in stolen.items():
        client.cookies.set(name, value, domain=HOST)
    assert client.get("/api/instances").status_code == 401


def test_password_change_ends_sessions_and_tokens(client, monkeypatch):
    login(client, remember=True)
    monkeypatch.setattr(auth, "_active_password", "a new password")
    assert client.get("/api/instances").status_code == 401


def test_logout_needs_post(client):
    login(client)
    assert client.get("/logout", follow_redirects=False).status_code == 405


def test_bcrypt_hash_as_password(client, monkeypatch):
    hashed = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()
    monkeypatch.setattr(settings, "auth_password", hashed)
    auth.init_auth()
    assert login(client).status_code == 302
    assert client.post("/login", data={"username": "admin", "password": "wrong"},
                       follow_redirects=False).status_code == 401
    assert "passlib" not in Path(auth.__file__).read_text()


def test_broken_hash_is_logged(monkeypatch, caplog):
    monkeypatch.setattr(auth, "_active_password", "$2b$04$not-a-real-hash")
    with caplog.at_level(logging.ERROR, logger="missingarr.auth"):
        assert auth.verify_password("x") is False
    assert "bcrypt check failed" in caplog.text


def test_long_passwords_are_cut_like_older_bcrypt(monkeypatch, caplog):
    long_password = "p" * 100
    # A hash made the way htpasswd, passlib and bcrypt < 5 did: only 72 bytes count.
    hashed = bcrypt.hashpw(long_password.encode()[:72], bcrypt.gensalt(rounds=4)).decode()
    monkeypatch.setattr(auth, "_active_password", hashed)
    with caplog.at_level(logging.ERROR, logger="missingarr.auth"):
        assert auth.verify_password(long_password) is True
        assert auth.verify_password("q" * 100) is False
    assert "bcrypt check failed" not in caplog.text


def test_failed_logins_lock_the_address_and_are_logged(client, monkeypatch, caplog):
    now = [1000.0]
    monkeypatch.setattr(auth.login_throttle, "_clock", lambda: now[0])
    wrong = {"username": "admin", "password": "wrong", "next": "/"}
    with caplog.at_level(logging.WARNING, logger="missingarr"):
        for _ in range(5):
            assert client.post("/login", data=wrong, follow_redirects=False).status_code == 401
        locked = login(client)
    assert locked.status_code == 429
    assert locked.headers["Retry-After"] == "30"
    assert "Failed sign-in" in caplog.text and "testclient" in caplog.text
    now[0] += 31
    assert login(client).status_code == 302


def test_lock_doubles_up_to_fifteen_minutes():
    now = [0.0]
    throttle = auth.LoginThrottle(clock=lambda: now[0])
    waits = []
    for _ in range(12):
        throttle.record_failure("1.2.3.4")
        waits.append(throttle.retry_after("1.2.3.4"))
    assert waits[:4] == [0, 0, 0, 0]
    assert waits[4:8] == [30, 60, 120, 240]
    assert waits[-1] == 900
    throttle.record_success("1.2.3.4")
    assert throttle.retry_after("1.2.3.4") == 0


@pytest.mark.parametrize("next_value,expected", [
    ("//evil.example/x", "/"), ("https://evil.example", "/"), ("/\\evil.example", "/"),
    ("javascript:alert(1)", "/"), ("", "/"), ("/history?x=1", "/history?x=1"),
])
def test_next_only_allows_local_paths(client, next_value, expected):
    resp = login(client, next_path=next_value)
    assert resp.headers["location"] == expected
    assert client.get(f"/login?next={next_value}", follow_redirects=False).headers["location"] == expected


def test_cookie_secure_flag(client, monkeypatch):
    monkeypatch.setattr(settings, "cookie_secure", True)
    resp = login(client, remember=True)
    cookies = resp.headers.get_list("set-cookie")
    assert all("secure" in c.lower() for c in cookies)
    assert {c.split("=", 1)[0] for c in cookies} == {"ma_session", "ma_remember"}
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p4_auth.py -q -p no:cacheprovider`
Expected: FAIL (`AttributeError: … 'login_throttle'`).

- [ ] **Step 3: Implementierung `backend/auth.py`**

Kopf (Imports) ersetzen, `passlib` fällt weg:

```python
import hashlib
import hmac
import logging
import math
import secrets
import threading
import time
from urllib.parse import quote, urlsplit

import bcrypt
from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from backend import db
from backend.config import settings
from backend.crypto import get_session_secret

logger = logging.getLogger("missingarr.auth")

_PUBLIC_PREFIXES = ("/static/", "/api/health")
_active_password: str = ""

REMEMBER_COOKIE = "ma_remember"
REMEMBER_MAX_AGE = 30 * 24 * 60 * 60
_REMEMBER_COOKIE = REMEMBER_COOKIE          # old names, still imported by main.py until P4.4 is done
_REMEMBER_MAX_AGE = REMEMBER_MAX_AGE
TOKEN_VERSION_SETTING = "token_version"
_BCRYPT_PREFIXES = ("$2a$", "$2b$", "$2y$")
```

Token und Sitzung:

```python
_token_version_cache: int | None = None


def current_token_version() -> int:
    global _token_version_cache
    if _token_version_cache is None:
        _token_version_cache = int(db.app_settings.get_value(TOKEN_VERSION_SETTING) or 0)
    return _token_version_cache


def revoke_all_tokens() -> int:
    """Sign out everywhere: every remember-me token and every session carries
    the version they were issued with, and it no longer matches (C6)."""
    global _token_version_cache
    version = int(db.app_settings.get_value(TOKEN_VERSION_SETTING) or 0) + 1
    db.app_settings.set_value(TOKEN_VERSION_SETTING, str(version))
    _token_version_cache = version
    return version


def _reset_token_version_cache() -> None:
    global _token_version_cache
    _token_version_cache = None


def _password_fingerprint() -> str:
    return hashlib.sha256(b"missingarr-password|" + _active_password.encode()).hexdigest()


def _remember_signature(username: str, issued: int, version: int) -> str:
    message = f"v2|{username}|{issued}|{version}|{_password_fingerprint()}".encode()
    return hmac.new(get_session_secret().encode(), message, hashlib.sha256).hexdigest()


def create_remember_token(username: str, now: int | None = None) -> str:
    issued = int(time.time() if now is None else now)
    version = current_token_version()
    return f"v2.{issued}.{version}.{_remember_signature(username, issued, version)}"


def verify_remember_token(token: str, now: int | None = None) -> str | None:
    """Valid for 30 days from issue, until the next sign-out or password
    change. Tokens of 0.7.0 ("user:hmac") are not accepted any more."""
    parts = token.split(".")
    if len(parts) != 4 or parts[0] != "v2":
        return None
    try:
        issued, version = int(parts[1]), int(parts[2])
    except ValueError:
        return None
    current = int(time.time() if now is None else now)
    if issued > current + 300 or current - issued > REMEMBER_MAX_AGE:
        return None
    if version != current_token_version():
        return None
    expected = _remember_signature(settings.auth_username, issued, version)
    return settings.auth_username if hmac.compare_digest(parts[3], expected) else None


def is_authenticated(request: Request) -> bool:
    session = request.session
    return (
        session.get("user") == settings.auth_username
        and session.get("tv") == current_token_version()
        and session.get("pf") == _password_fingerprint()[:16]
    )


def start_session(request: Request) -> None:
    request.session["user"] = settings.auth_username
    request.session["tv"] = current_token_version()
    request.session["pf"] = _password_fingerprint()[:16]
```

Passwort:

```python
BCRYPT_MAX_BYTES = 72


def verify_password(plain: str) -> bool:
    """Plain text compared in constant time, or a bcrypt hash checked with
    bcrypt directly — passlib 1.7.4 cannot read bcrypt >= 4.1 and rejected
    every login with a hash (C-L3)."""
    if not _active_password:
        return False
    if _active_password.startswith(_BCRYPT_PREFIXES):
        # bcrypt only ever used the first 72 bytes. bcrypt >= 5 raises on
        # longer input instead of cutting it; cut it here like older bcrypt,
        # passlib and htpasswd did, so their hashes keep working and a long
        # password is a plain mismatch, not an error line (C-L3).
        candidate = plain.encode()[:BCRYPT_MAX_BYTES]
        try:
            return bcrypt.checkpw(candidate, _active_password.encode())
        except ValueError as exc:
            logger.error("bcrypt check failed (%s) — is AUTH_PASSWORD a complete bcrypt hash?", exc)
            return False
    return secrets.compare_digest(plain.encode(), _active_password.encode())
```

Drossel:

```python
class LoginThrottle:
    """Per client address, in memory (C7). The first five failures are free;
    from the fifth on the address waits 30 s, doubling up to 15 minutes. A
    success clears the address; an address quiet for an hour is forgotten."""

    FREE_ATTEMPTS = 5
    BASE_DELAY_SECONDS = 30
    MAX_DELAY_SECONDS = 15 * 60
    FORGET_AFTER_SECONDS = 60 * 60

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._state: dict[str, list] = {}  # address -> [failures, locked_until, last_failure]

    def _prune(self, now: float) -> None:
        stale = [a for a, (_, until, last) in self._state.items()
                 if now >= until and now - last > self.FORGET_AFTER_SECONDS]
        for address in stale:
            del self._state[address]

    def retry_after(self, address: str) -> int:
        with self._lock:
            state = self._state.get(address)
            if not state:
                return 0
            return max(0, math.ceil(state[1] - self._clock()))

    def record_failure(self, address: str) -> int:
        with self._lock:
            now = self._clock()
            self._prune(now)
            failures, locked_until, _ = self._state.get(address, [0, 0.0, now])
            failures += 1
            if failures >= self.FREE_ATTEMPTS:
                steps = min(failures - self.FREE_ATTEMPTS, 10)
                locked_until = now + min(self.MAX_DELAY_SECONDS, self.BASE_DELAY_SECONDS * 2 ** steps)
            self._state[address] = [failures, locked_until, now]
            return failures

    def record_success(self, address: str) -> None:
        with self._lock:
            self._state.pop(address, None)

    def reset(self) -> None:
        with self._lock:
            self._state.clear()


login_throttle = LoginThrottle()
```

In `AuthMiddleware.dispatch` `_REMEMBER_COOKIE` durch `REMEMBER_COOKIE` ersetzen. Die Aliase `_REMEMBER_COOKIE`/`_REMEMBER_MAX_AGE` am Ende von Step 4 löschen, sobald `main.py` umgestellt ist.

- [ ] **Step 4: Implementierung `backend/main.py`**

Imports: `import math`, `from urllib.parse import urlsplit`; aus `backend.auth` jetzt `AuthMiddleware, CSRFMiddleware, LazySessionMiddleware, REMEMBER_COOKIE, REMEMBER_MAX_AGE, auth_enabled, create_remember_token, init_auth, is_authenticated, login_throttle, revoke_all_tokens, start_session, verify_password`.

Die drei Auth-Routen ersetzen:

```python
def safe_next(value: str | None) -> str:
    """Only local paths. '//host' and '/\\host' are treated by browsers as
    another host (C10)."""
    if not value or not value.startswith("/") or value.startswith(("//", "/\\")):
        return "/"
    parts = urlsplit(value)
    if parts.scheme or parts.netloc or any(ord(ch) < 32 for ch in value):
        return "/"
    return value


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _login_page(request: Request, next_path: str, error: str, status: int, headers: dict | None = None):
    return templates.TemplateResponse(
        request,
        "login.html",
        {"request": request, "app_name": settings.app_name, "next": next_path, "error": error},
        status_code=status,
        headers=headers,
    )


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/", error: str = ""):
    target = safe_next(next)
    if not auth_enabled() or is_authenticated(request):
        return RedirectResponse(target, status_code=302)
    return _login_page(request, target, error, 200)


@app.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form(default="/"),
    remember: bool = Form(default=False),
):
    target = safe_next(next)
    if not auth_enabled():
        return RedirectResponse(target, status_code=302)

    address = client_ip(request)
    wait = login_throttle.retry_after(address)
    if wait:
        logger.warning("Sign-in from %s refused — locked for another %ds", address, wait)
        return _login_page(
            request, target,
            f"Too many failed sign-in attempts. Try again in {math.ceil(wait / 60)} minute(s).",
            429, {"Retry-After": str(wait)},
        )

    if username == settings.auth_username and verify_password(password):
        login_throttle.record_success(address)
        start_session(request)
        response = RedirectResponse(target, status_code=302)
        if remember:
            response.set_cookie(
                REMEMBER_COOKIE, create_remember_token(username),
                max_age=REMEMBER_MAX_AGE, httponly=True, samesite="lax",
                secure=settings.cookie_secure,
            )
        return response

    failures = login_throttle.record_failure(address)
    logger.warning("Failed sign-in for user %r from %s (%d in a row)", username[:64], address, failures)
    return _login_page(request, target, "Invalid username or password.", 401)


@app.post("/logout")
async def logout(request: Request):
    revoke_all_tokens()
    request.session.clear()
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(REMEMBER_COOKIE, httponly=True, samesite="lax", secure=settings.cookie_secure)
    return response
```

Danach die Aliase `_REMEMBER_COOKIE`/`_REMEMBER_MAX_AGE` in `auth.py` löschen.

- [ ] **Step 5: Tests grün**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p4_auth.py tests/test_p4_middleware.py tests/test_p4_startup.py -q -p no:cacheprovider && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/auth.py backend/main.py tests/test_p4_auth.py
git commit -m "fix: expiring revocable remember tokens, POST logout, login throttling, bcrypt without passlib, local next only"
```

### Task P4.5: Instanz-API — Schlüssel maskiert, 409 bei laufendem Skill, Schalter ohne Neustart

**Files:**
- Modify: `backend/api/instances.py` (ganze Datei)
- Test: `tests/test_p4_instances_api.py`

**Interfaces:**
- Consumes: P1 `TRIGGER_BUSY`, `TRIGGER_UNKNOWN_SKILL`, `TRIGGER_NOT_FOUND`, `Orchestrator.refresh_config/forget_instance/stop_agent`; P1 Modellgrenzen
- Produces: `API_KEY_MASK`, `public_instance()`, HTTP-Vertrag für `/api/instances*`.

- [ ] **Step 1: Failing test schreiben**

`tests/test_p4_instances_api.py` (Client-Bausteine einfügen, dazu):

```python
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SECRET = "SUPERSECRETKEY1234567890"


class FakeOrchestrator:
    def __init__(self):
        self.calls = []
        self.trigger_result = "started"

    def get_agent_state(self, instance_id):
        return {}

    def start_agent(self, instance_id):
        self.calls.append(("start", instance_id))

    def stop_agent(self, instance_id, abort_running=True, wait_seconds=0.0):
        self.calls.append(("stop", instance_id, abort_running))

    def reload_agent(self, instance_id):
        self.calls.append(("reload", instance_id))

    def refresh_config(self, instance_id):
        self.calls.append(("refresh", instance_id))

    def forget_instance(self, instance_id, wait_seconds=15.0):
        self.calls.append(("forget", instance_id, db.instances.get_by_id(instance_id) is not None))

    def trigger(self, instance_id, skill_name, force=True):
        self.calls.append(("trigger", instance_id, skill_name, force))
        return self.trigger_result

    def stop_all(self):
        pass


@pytest.fixture
def api(client):
    client.app.state.orchestrator.stop_all()
    fake = FakeOrchestrator()
    client.app.state.orchestrator = fake
    login(client)
    return client, fake


def body(**fields):
    data = {"name": "Sonarr", "type": "sonarr", "url": "http://sonarr:8989", "api_key": SECRET,
            "enabled": False}
    data.update(fields)
    return data


def test_api_key_never_leaves_the_server(api):
    client, _ = api
    created = client.post("/api/instances", json=body())
    assert created.status_code == 201
    instance_id = created.json()["id"]
    for resp in (created, client.get("/api/instances"), client.get(f"/api/instances/{instance_id}"),
                 client.put(f"/api/instances/{instance_id}", json=body(api_key=""))):
        assert SECRET not in resp.text
    one = client.get(f"/api/instances/{instance_id}").json()
    assert (one["api_key"], one["api_key_set"]) == ("********", True)


@pytest.mark.parametrize("key", ["", "********"])
def test_empty_or_masked_key_keeps_the_stored_one(api, key):
    client, fake = api
    instance_id = client.post("/api/instances", json=body()).json()["id"]
    assert client.put(f"/api/instances/{instance_id}", json=body(api_key=key)).status_code == 200
    assert db.instances.get_by_id(instance_id)["api_key"] == SECRET
    assert ("reload", instance_id) in fake.calls


def test_changed_url_needs_the_key_again(api):
    client, _ = api
    instance_id = client.post("/api/instances", json=body()).json()["id"]
    resp = client.put(f"/api/instances/{instance_id}", json=body(url="http://elsewhere:8989", api_key=""))
    assert resp.status_code == 400
    assert db.instances.get_by_id(instance_id)["url"] == "http://sonarr:8989"
    resp = client.put(f"/api/instances/{instance_id}",
                      json=body(url="http://elsewhere:8989", api_key="NEWKEY"))
    assert resp.status_code == 200
    assert db.instances.get_by_id(instance_id)["api_key"] == "NEWKEY"


def test_out_of_range_settings_are_rejected(api):
    client, _ = api
    assert client.post("/api/instances", json=body(interval_minutes=0)).status_code == 422


@pytest.mark.parametrize("result,status", [("busy", 409), ("not_found", 404), ("unknown_skill", 400)])
def test_trigger_reports_why_nothing_started(api, result, status):
    client, fake = api
    instance_id = make_instance()["id"]
    fake.trigger_result = result
    resp = client.post(f"/api/instances/{instance_id}/trigger?skill=search_missing&force=true")
    assert resp.status_code == status
    if status == 409:
        assert "already running" in resp.json()["detail"]


def test_unknown_skill_name_is_rejected(api):
    client, _ = api
    instance_id = make_instance()["id"]
    assert client.post(f"/api/instances/{instance_id}/trigger?skill=bogus").status_code == 400


def test_documented_curl_flow_still_works(client):
    client.app.state.orchestrator.stop_all()
    fake = FakeOrchestrator()
    client.app.state.orchestrator = fake
    instance_id = make_instance()["id"]
    assert login(client).status_code == 302
    resp = client.post(f"/api/instances/{instance_id}/trigger?skill=search_missing&force=false")
    assert resp.status_code == 200
    assert resp.json() == {"status": "triggered", "skill": "search_missing"}
    assert fake.calls[-1] == ("trigger", instance_id, "search_missing", False)


def test_skill_switch_refreshes_the_agent_and_checks_per_run(api):
    client, fake = api
    instance_id = make_instance(search_upgrades_enabled=False, upgrades_per_run=0)["id"]
    resp = client.post(f"/api/instances/{instance_id}/toggle-skill?skill=upgrades&enabled=true")
    assert resp.status_code == 409
    with database.get_db() as conn:
        conn.execute("UPDATE instances SET upgrades_per_run=1 WHERE id=?", (instance_id,))
    resp = client.post(f"/api/instances/{instance_id}/toggle-skill?skill=upgrades&enabled=true")
    assert resp.status_code == 200
    assert db.instances.get_by_id(instance_id)["search_upgrades_enabled"] == 1
    assert ("refresh", instance_id) in fake.calls
    assert not any(call[0] == "reload" for call in fake.calls)


def test_disabling_aborts_and_deleting_waits_for_the_agent(api):
    client, fake = api
    instance_id = make_instance(enabled=True)["id"]
    client.post(f"/api/instances/{instance_id}/toggle?enabled=false")
    assert ("stop", instance_id, True) in fake.calls
    assert client.delete(f"/api/instances/{instance_id}").status_code == 204
    assert ("forget", instance_id, True) in fake.calls
    assert db.instances.get_by_id(instance_id) is None


class Handler(BaseHTTPRequestHandler):
    status = 401
    hits = []

    def do_GET(self):
        Handler.hits.append(self.path)
        self.send_response(Handler.status)
        if Handler.status == 302:
            self.send_header("Location", "http://localhost:1/elsewhere")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def arr_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    Handler.hits = []
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.mark.parametrize("status,expected,detail", [
    (401, 401, "Invalid API key"), (403, 401, "Invalid API key"),
    (500, 502, "HTTP 500 from instance"), (302, 502, "redirect"),
])
def test_connection_test_keeps_the_real_status(api, arr_server, status, expected, detail):
    client, _ = api
    Handler.status = status
    instance_id = make_instance(url=arr_server)["id"]
    resp = client.get(f"/api/instances/{instance_id}/test")
    assert resp.status_code == expected
    assert detail in resp.json()["detail"]
    assert Handler.hits == ["/api/v3/system/status"]
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p4_instances_api.py -q -p no:cacheprovider`
Expected: FAIL (Klartext-Schlüssel in der Antwort, `trigger` antwortet immer 200, `HTTP 0`).

- [ ] **Step 3: Implementierung**

`backend/api/instances.py` komplett:

```python
import requests
from fastapi import APIRouter, HTTPException, Request

from backend import db
from backend.agents.base import TRIGGER_BUSY, TRIGGER_UNKNOWN_SKILL
from backend.agents.orchestrator import TRIGGER_NOT_FOUND
from backend.models.instance import InstanceCreate, InstanceUpdate

router = APIRouter(prefix="/instances")

API_KEY_MASK = "********"
TRIGGERABLE_SKILLS = ("search_missing", "search_upgrades", "health_check", "verify_commands")
DELETE_WAIT_SECONDS = 15.0


def _get_orchestrator(request: Request):
    return request.app.state.orchestrator


def public_instance(inst: dict) -> dict:
    """The instance as a browser may see it — never the API key (C1)."""
    out = {key: value for key, value in inst.items() if key != "api_key"}
    out["api_key_set"] = bool(inst.get("api_key"))
    out["api_key"] = API_KEY_MASK if out["api_key_set"] else ""
    return out


def _normalized_url(url: str | None) -> str:
    return (url or "").strip().rstrip("/")


@router.get("")
def list_instances(request: Request):
    orchestrator = _get_orchestrator(request)
    return [
        {**public_instance(inst), "agent_state": orchestrator.get_agent_state(inst["id"]) or {}}
        for inst in db.instances.get_all()
    ]


@router.get("/{instance_id}")
def get_instance(instance_id: int, request: Request):
    inst = db.instances.get_by_id(instance_id)
    if not inst:
        raise HTTPException(404, "Instance not found")
    state = _get_orchestrator(request).get_agent_state(instance_id) or {}
    return {**public_instance(inst), "agent_state": state}


@router.post("", status_code=201)
def create_instance(data: InstanceCreate, request: Request):
    inst = db.instances.create(data.model_dump())
    if inst.get("enabled"):
        _get_orchestrator(request).start_agent(inst["id"])
    return public_instance(inst)


@router.put("/{instance_id}")
def update_instance(instance_id: int, data: InstanceUpdate, request: Request):
    existing = db.instances.get_by_id(instance_id)
    if not existing:
        raise HTTPException(404, "Instance not found")
    payload = data.model_dump()
    new_key = (payload.get("api_key") or "").strip()
    if new_key == API_KEY_MASK:
        new_key = ""
    payload["api_key"] = new_key or None
    if not new_key and _normalized_url(payload["url"]) != _normalized_url(existing["url"]):
        # Otherwise the stored key could be sent to any new address (C1/C4).
        raise HTTPException(400, "The URL changed — enter the API key again so it is not sent to a new address.")
    inst = db.instances.update(instance_id, payload)
    _get_orchestrator(request).reload_agent(instance_id)
    return public_instance(inst)


@router.delete("/{instance_id}", status_code=204)
def delete_instance(instance_id: int, request: Request):
    if not db.instances.get_by_id(instance_id):
        raise HTTPException(404, "Instance not found")
    # Abort a running search and wait for it before the row (and its foreign
    # keys) disappear (A-L5).
    _get_orchestrator(request).forget_instance(instance_id, wait_seconds=DELETE_WAIT_SECONDS)
    db.instances.delete(instance_id)


@router.post("/{instance_id}/toggle-skill")
def toggle_skill(instance_id: int, request: Request, skill: str, enabled: bool):
    if skill not in ("missing", "upgrades"):
        raise HTTPException(400, "skill must be 'missing' or 'upgrades'")
    inst = db.instances.get_by_id(instance_id)
    if not inst:
        raise HTTPException(404, "Instance not found")
    per_run_field = "missing_per_run" if skill == "missing" else "upgrades_per_run"
    if enabled and int(inst.get(per_run_field) or 0) < 1:
        raise HTTPException(409, f"Set '{per_run_field}' to at least 1 before enabling this skill.")
    db.instances.toggle_skill(instance_id, skill, enabled)
    # The job exists already; the agent only needs the new flag (A2).
    _get_orchestrator(request).refresh_config(instance_id)
    return {"status": "ok", "skill": skill, "enabled": enabled}


@router.post("/{instance_id}/trigger")
def trigger_instance(instance_id: int, request: Request, skill: str = "search_missing", force: bool = True):
    if skill not in TRIGGERABLE_SKILLS:
        raise HTTPException(400, f"Unknown skill '{skill}'")
    if not db.instances.get_by_id(instance_id):
        raise HTTPException(404, "Instance not found")
    result = _get_orchestrator(request).trigger(instance_id, skill, force=force)
    if result == TRIGGER_BUSY:
        raise HTTPException(409, f"{skill} is already running — try again when it has finished")
    if result == TRIGGER_NOT_FOUND:
        raise HTTPException(404, "Instance not found")
    if result == TRIGGER_UNKNOWN_SKILL:
        raise HTTPException(400, f"Skill '{skill}' is not available for this instance")
    return {"status": "triggered", "skill": skill}


@router.get("/{instance_id}/status")
def instance_status(instance_id: int, request: Request):
    state = _get_orchestrator(request).get_agent_state(instance_id)
    if state is not None:
        return {
            "connection_status": state.get("connection_status", "unknown"),
            "last_seen_at": state.get("last_seen_at"),
            "agent_state": state,
        }
    inst = db.instances.get_by_id(instance_id)
    if not inst:
        raise HTTPException(404, "Instance not found")
    return {
        "connection_status": inst.get("connection_status", "unknown"),
        "last_seen_at": inst.get("last_seen_at"),
        "agent_state": {},
    }


@router.get("/{instance_id}/test")
def test_connection(instance_id: int):
    inst = db.instances.get_by_id(instance_id)
    if not inst:
        raise HTTPException(404, "Instance not found")

    url = inst["url"].rstrip("/") + "/api/v3/system/status"
    try:
        resp = requests.get(url, headers={"X-Api-Key": inst["api_key"]}, timeout=10, allow_redirects=False)
        if 300 <= resp.status_code < 400:
            db.instances.update_status(instance_id, "offline")
            raise HTTPException(502, f"Instance answered with a redirect (HTTP {resp.status_code}) — check the URL")
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.Timeout:
        db.instances.update_status(instance_id, "offline")
        raise HTTPException(504, "Connection timed out")
    except requests.exceptions.ConnectionError:
        db.instances.update_status(instance_id, "offline")
        raise HTTPException(503, "Cannot connect to instance")
    except requests.exceptions.HTTPError as exc:
        # Not `if exc.response`: Response.__bool__ is .ok and False for every
        # 4xx/5xx, which turned every error into "HTTP 0" (A-L1).
        code = exc.response.status_code if exc.response is not None else 0
        if code in (401, 403):
            db.instances.update_status(instance_id, "error")
            raise HTTPException(401, "Invalid API key")
        db.instances.update_status(instance_id, "offline")
        raise HTTPException(502, f"HTTP {code} from instance")
    except ValueError:
        db.instances.update_status(instance_id, "offline")
        raise HTTPException(502, "Instance did not answer with JSON — is the URL correct?")

    db.instances.update_status(instance_id, "online")
    return {"status": "online", "version": data.get("version"), "appName": data.get("appName")}


@router.post("/{instance_id}/toggle")
def toggle_instance(instance_id: int, enabled: bool, request: Request):
    inst = db.instances.toggle_enabled(instance_id, enabled)
    if not inst:
        raise HTTPException(404, "Instance not found")
    orchestrator = _get_orchestrator(request)
    if enabled:
        orchestrator.start_agent(instance_id)
    else:
        orchestrator.stop_agent(instance_id, abort_running=True)
    return {"enabled": enabled}
```

- [ ] **Step 4: Tests grün**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p4_instances_api.py -q -p no:cacheprovider && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/api/instances.py tests/test_p4_instances_api.py
git commit -m "fix: mask API keys, 409 for a running skill, switch skills without restart, report real test status"
```

### Task P4.6: Seiten ohne Schlüssel, Grenzen fürs Formular, Start-Aufräumen, Shutdown-Signal

**Files:**
- Modify: `backend/main.py` (Lifespan, alle UI-Routen, neu `install_shutdown_signal_hook`)
- Test: `tests/test_p4_pages.py`

**Interfaces:**
- Consumes: `public_instance` (P4.5), `FIELD_BOUNDS` (P1), `db.history.close_interrupted_runs` (P3), `LogBroadcaster.request_shutdown` (P4.7 — hier nur aufgerufen; bis P4.7 existiert, liefert P4.6 die Methode schon mit, siehe Step 3)
- Produces: Template-Kontext laut Vertrag, `install_shutdown_signal_hook(broadcaster)`.

- [ ] **Step 1: Failing test schreiben**

`tests/test_p4_pages.py` (Client-Bausteine einfügen, dazu):

```python
import signal

from backend.db import history
from backend.models.instance import FIELD_BOUNDS

SECRET = "SUPERSECRETKEY1234567890"


def test_no_page_contains_the_api_key(client):
    login(client)
    instance_id = make_instance(api_key=SECRET)["id"]
    for path in ("/", "/instances", f"/instances/{instance_id}/edit", f"/instances/{instance_id}/card",
                 "/instances/new", "/history", "/logs", "/searched"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert SECRET not in resp.text, path


def test_form_gets_the_bounds_and_a_masked_instance(client):
    login(client)
    instance_id = make_instance(api_key=SECRET)["id"]
    resp = client.get(f"/instances/{instance_id}/edit")
    assert resp.context["bounds"] == FIELD_BOUNDS
    assert resp.context["instance"]["api_key"] == "********"
    assert resp.context["instance"]["api_key_set"] is True
    assert client.get("/instances/new").context["bounds"] == FIELD_BOUNDS


def test_logs_page_gets_recent_entries_and_instances(client):
    login(client)
    make_instance()
    resp = client.get("/logs")
    assert isinstance(resp.context["recent"], list)
    assert [i["name"] for i in resp.context["instances"]] == ["Sonarr"]


def test_interrupted_runs_are_closed_on_start(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_url", str(tmp_path / "missingarr.db"))
    monkeypatch.setattr(settings, "auth_password", PASSWORD)
    monkeypatch.setattr(settings, "secret_key", "")
    monkeypatch.setattr(database, "_cached_secret_key", None)
    database.init_db()
    inst = make_instance()
    run = history.start_run(inst["id"], inst["name"], "search_missing")
    from backend import crypto, main
    crypto._reset_cache()
    main.app.middleware_stack = None
    with TestClient(main.app, base_url=f"http://{HOST}"):
        pass
    row = history.query(limit=1)[0]
    assert (row["id"], row["status"], row["error_message"]) == (run, "error", "Interrupted by restart")
    crypto._reset_cache()
    main.app.middleware_stack = None


def test_missing_secret_key_stops_the_start(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_url", str(tmp_path / "missingarr.db"))
    monkeypatch.setattr(settings, "secret_key", "")
    database.init_db()
    with database.get_db() as conn:
        conn.execute("INSERT INTO app_settings (key, value) VALUES ('key_source', 'env')")
    from backend import crypto, main
    crypto._reset_cache()
    main.app.middleware_stack = None
    with pytest.raises(BaseException) as caught:
        with TestClient(main.app):
            pass
    crypto._reset_cache()
    main.app.middleware_stack = None

    def messages_of(exc):
        found = [str(exc)]
        for inner in getattr(exc, "exceptions", ()):  # anyio may wrap it in an ExceptionGroup
            found += messages_of(inner)
        if exc.__cause__ is not None:
            found += messages_of(exc.__cause__)
        return found

    assert any("SECRET_KEY is not set" in m for m in messages_of(caught.value))


def test_signal_hook_wakes_streams_and_chains(monkeypatch):
    from backend import main
    calls = []

    class FakeBroadcaster:
        def request_shutdown(self):
            calls.append("shutdown")

    def previous(signum, frame):
        calls.append(("previous", signum))

    saved = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        signal.signal(signal.SIGTERM, previous)
        main.install_shutdown_signal_hook(FakeBroadcaster())
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
    finally:
        for sig, handler in saved.items():
            signal.signal(sig, handler)
    assert calls == ["shutdown", ("previous", signal.SIGTERM)]
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p4_pages.py -q -p no:cacheprovider`
Expected: FAIL (`KeyError: 'bounds'`, Lauf bleibt `running`, `install_shutdown_signal_hook` fehlt).

- [ ] **Step 3: Implementierung**

`backend/main.py` — Imports: `import signal`, `import threading`, `from backend import db`, `from backend.api.instances import public_instance`, `from backend.models.instance import FIELD_BOUNDS`. Die `from backend import db`-Zeilen in den Routen entfallen.

```python
def install_shutdown_signal_hook(broadcaster) -> None:
    """Wake open log streams as soon as SIGTERM/SIGINT arrives (C-L7).

    uvicorn waits for running responses before it runs the lifespan shutdown,
    and a log stream never ends by itself, so a single open browser tab kept
    the process alive until Docker's SIGKILL. The previous handler (uvicorn's)
    still runs afterwards.
    """
    if threading.current_thread() is not threading.main_thread():
        return  # signal handlers can only be set from the main thread (e.g. not under TestClient)
    for signum in (signal.SIGTERM, signal.SIGINT):
        previous = signal.getsignal(signum)

        def handler(received, frame, previous=previous):
            broadcaster.request_shutdown()
            if callable(previous):
                previous(received, frame)
            elif previous == signal.SIG_DFL:
                signal.signal(received, signal.SIG_DFL)
                signal.raise_signal(received)

        signal.signal(signum, handler)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting {settings.app_name} v{settings.version}")
    init_auth()
    init_db()
    interrupted = db.history.close_interrupted_runs()
    if interrupted:
        logger.warning("Closed %d search run(s) interrupted by the last shutdown", interrupted)
    init_crypto()

    broadcaster.set_loop(asyncio.get_running_loop())
    install_shutdown_signal_hook(broadcaster)
    app.state.broadcaster = broadcaster

    orchestrator = Orchestrator(broadcaster=broadcaster)
    app.state.orchestrator = orchestrator
    orchestrator.start_all()
    logger.info("Orchestrator started")

    yield

    logger.info("Shutting down orchestrator...")
    broadcaster.request_shutdown()
    orchestrator.stop_all()
    logger.info("Shutdown complete")
```

Bis P4.7 `request_shutdown` in `log_broadcaster.py` einführt, gehört dieser Teil von P4.7 schon hierher — also in diesem Task zusätzlich `backend/log_broadcaster.py` wie in P4.7 Step 3 beschrieben ändern (gleiches Paket, gleiche Datei-Zuständigkeit). P4.7 prüft ihn dann mit eigenen Tests.

UI-Routen: überall die Instanzen durch `public_instance(...)` reichen:

```python
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    orchestrator = request.app.state.orchestrator
    cards = []
    for inst in db.instances.get_all():
        cards.append({
            "instance": public_instance(inst),
            "state": orchestrator.get_agent_state(inst["id"]) or {},
            "recent": db.history.get_last_for_instance(inst["id"]),
        })
    return templates.TemplateResponse(request, "dashboard.html", template_ctx(request, cards=cards))


@app.get("/instances", response_class=HTMLResponse)
async def instances_list(request: Request):
    instances = [public_instance(i) for i in db.instances.get_all()]
    return templates.TemplateResponse(request, "instances/list.html", template_ctx(request, instances=instances))


@app.get("/instances/new", response_class=HTMLResponse)
async def instance_new(request: Request):
    return templates.TemplateResponse(
        request, "instances/form.html",
        template_ctx(request, instance=None, action="/api/instances", method="POST", bounds=FIELD_BOUNDS),
    )


@app.get("/instances/{instance_id}/card", response_class=HTMLResponse)
async def instance_card(instance_id: int, request: Request):
    inst = db.instances.get_by_id(instance_id)
    if not inst:
        raise HTTPException(404)
    state = request.app.state.orchestrator.get_agent_state(instance_id) or {}
    recent = db.history.get_last_for_instance(instance_id)
    return templates.TemplateResponse(
        request, "instances/card.html",
        template_ctx(request, inst=public_instance(inst), state=state, recent=recent,
                     conn=inst["connection_status"]),
    )


@app.get("/instances/{instance_id}/edit", response_class=HTMLResponse)
async def instance_edit(instance_id: int, request: Request):
    inst = db.instances.get_by_id(instance_id)
    if not inst:
        return RedirectResponse("/instances")
    return templates.TemplateResponse(
        request, "instances/form.html",
        template_ctx(request, instance=public_instance(inst), action=f"/api/instances/{instance_id}",
                     method="PUT", bounds=FIELD_BOUNDS),
    )


@app.get("/history", response_class=HTMLResponse)
async def history_page(request: Request):
    instances = [public_instance(i) for i in db.instances.get_all()]
    return templates.TemplateResponse(request, "history.html", template_ctx(request, instances=instances))


@app.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request):
    recent = db.activity.query(limit=100, include_debug=False)
    instances = [public_instance(i) for i in db.instances.get_all()]
    return templates.TemplateResponse(request, "logs.html", template_ctx(request, recent=recent, instances=instances))


@app.get("/searched", response_class=HTMLResponse)
async def searched_page(request: Request):
    instances = [public_instance(i) for i in db.instances.get_all()]
    return templates.TemplateResponse(
        request, "searched.html", template_ctx(request, instances=instances, counts=db.searched.count()),
    )
```

(`from fastapi import HTTPException` oben importieren.)

- [ ] **Step 4: Tests grün**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p4_pages.py -q -p no:cacheprovider && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/main.py backend/log_broadcaster.py tests/test_p4_pages.py
git commit -m "fix: keep API keys out of pages, pass form bounds, close interrupted runs on start, wake streams on SIGTERM"
```

### Task P4.7: Log-Stream endet beim Herunterfahren, Grenzen für `/api/activity`

**Files:**
- Modify: `backend/log_broadcaster.py` (`set_loop`, `shutdown_event`, `request_shutdown` — falls in P4.6 schon angelegt, nur prüfen)
- Modify: `backend/api/activity.py` (ganze Datei)
- Test: `tests/test_p4_activity.py`

**Interfaces:**
- Consumes: nichts Neues
- Produces: `LogBroadcaster.request_shutdown()`, `LogBroadcaster.shutdown_event`; `/api/activity` mit `Query`-Grenzen.

- [ ] **Step 1: Failing test schreiben**

`tests/test_p4_activity.py` (Client-Bausteine einfügen, dazu):

```python
import asyncio
from types import SimpleNamespace

from backend.api import activity
from backend.log_broadcaster import LogBroadcaster


@pytest.mark.parametrize("query", ["limit=-1", "limit=501", "offset=-1", "level=verbose"])
def test_activity_parameters_are_validated(client, query):
    login(client)
    assert client.get(f"/api/activity?{query}").status_code == 422


class FakeRequest:
    def __init__(self, broadcaster):
        self.app = SimpleNamespace(state=SimpleNamespace(broadcaster=broadcaster))

    async def is_disconnected(self):
        return False


def test_stream_delivers_entries_and_ends_on_shutdown():
    async def scenario():
        broadcaster = LogBroadcaster()
        broadcaster.set_loop(asyncio.get_running_loop())
        response = await activity.stream_activity(FakeRequest(broadcaster), debug=False)
        stream = response.body_iterator
        broadcaster.broadcast({"level": "info", "message": "hello"})
        first = await asyncio.wait_for(stream.__anext__(), 1)
        assert '"hello"' in first
        waiting = asyncio.ensure_future(stream.__anext__())
        await asyncio.sleep(0.05)
        broadcaster.request_shutdown()
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(waiting, 1)
        assert broadcaster._queues == []

    asyncio.run(scenario())


def test_request_shutdown_before_start_is_harmless():
    LogBroadcaster().request_shutdown()
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p4_activity.py -q -p no:cacheprovider`
Expected: FAIL (negative Limits → 200, Stream endet nicht → `TimeoutError`).

- [ ] **Step 3: Implementierung**

`backend/log_broadcaster.py` — `__init__`, `set_loop` ersetzen und ergänzen:

```python
    def __init__(self):
        self._queues: list[asyncio.Queue] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._shutdown: Optional[asyncio.Event] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop
        # A fresh event per loop: asyncio.Event binds to the loop that first waits on it.
        self._shutdown = asyncio.Event()

    @property
    def shutdown_event(self) -> Optional[asyncio.Event]:
        return self._shutdown

    def request_shutdown(self) -> None:
        """Thread- and signal-safe: wakes every open stream so it can end."""
        loop, event = self._loop, self._shutdown
        if loop is None or event is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(event.set)
```

`backend/api/activity.py` komplett:

```python
import asyncio
import json
from typing import Literal, Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from backend import db

router = APIRouter(prefix="/activity")

KEEPALIVE_SECONDS = 30


@router.get("")
def list_activity(
    instance_id: Optional[int] = None,
    level: Optional[Literal["info", "warn", "error", "debug"]] = None,
    debug: bool = False,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    return db.activity.query(
        instance_id=instance_id, level=level, include_debug=debug, limit=limit, offset=offset,
    )


@router.delete("")
def clear_activity():
    db.activity.clear()
    return {"status": "cleared"}


@router.get("/stream")
async def stream_activity(request: Request, debug: bool = False):
    broadcaster = request.app.state.broadcaster
    queue = broadcaster.subscribe()
    shutdown = broadcaster.shutdown_event

    async def event_generator():
        try:
            while shutdown is None or not shutdown.is_set():
                if await request.is_disconnected():
                    break
                getter = asyncio.ensure_future(queue.get())
                waiters = {getter}
                stopper = None
                if shutdown is not None:
                    stopper = asyncio.ensure_future(shutdown.wait())
                    waiters.add(stopper)
                done, pending = await asyncio.wait(
                    waiters, timeout=KEEPALIVE_SECONDS, return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()
                if stopper is not None and stopper in done:
                    break  # server is shutting down (C-L7)
                if getter in done:
                    payload = getter.result()
                    if not debug and json.loads(payload).get("level") == "debug":
                        continue
                    yield f"data: {payload}\n\n"
                else:
                    yield ": keep-alive\n\n"
        finally:
            broadcaster.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

`backend/api/health.py` bleibt unverändert (geprüft: keine Befunde, kein Klartext).

- [ ] **Step 4: Tests grün**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p4_activity.py -q -p no:cacheprovider && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/log_broadcaster.py backend/api/activity.py tests/test_p4_activity.py
git commit -m "fix: end log streams on shutdown and validate activity list limits"
```

---
## Paket P5 — Oberfläche (Welle 3)

Voraussetzung: Welle 2 komplett (P4-Template-Kontext mit `public_instance`/`bounds`, 401/409-Verhalten, `POST /logout`; P3 `X-Total-Count`; P6 `static/vendor`).

Abnahme P5: `tests/test_p5_*.py` grün, Gesamtsuite grün; im gerenderten HTML aller Seiten steht ein Instanzname nie in einem `on*`-, `@*`- oder `x-*`-Attribut; `grep -rn "cdn.jsdelivr\|unpkg.com\|__x\|data-iname\|href=\"/logout\"" templates/ static/js/` findet nichts; Handprobe im Browser (Task Z) ohne Konsolenfehler.

Regeln für alle P5-Dateien:

- `static/js/app.js` und alle Inline-Skripte nutzen auf oberster Ebene nur `function`- und `var`-Deklarationen, keine `class`/`let`/`const`. Grund: `hx-boost` tauscht den `<body>` aus und führt `app.js` erneut aus; eine zweite `class`-Deklaration derselben Klasse wäre ein `SyntaxError` und würde das ganze Skript abbrechen.
- Inline-`<script>`-Blöcke, die eine Alpine-Komponente definieren, stehen **vor** dem Markup, das sie mit `x-data` benutzt.
- Serverdaten kommen nie in JavaScript-Quelltext. Namen stehen in `data-*`-Attributen (Jinja escapt sie) und werden über `$el.dataset`/`this.dataset` gelesen; Listen kommen als `<script type="application/json">{{ … |tojson }}</script>`.
- Jeder `fetch` auf `/api/*` läuft über `apiFetch()`; gefangene Fehler zeigen nur dann einen Toast, wenn `isSessionExpired(err)` falsch ist.

Test-Bausteine für Seiten-Tests (Client-Fixture wie in P4, in jede Datei kopieren):

```python
import html.parser

import pytest
from fastapi.testclient import TestClient

from backend import database, db
from backend.config import settings

PASSWORD = "correct horse battery"
HOST = "missingarr.test"
HOSTILE = "x'); alert(1);//\"><img src=x onerror=alert(2)>"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_url", str(tmp_path / "missingarr.db"))
    monkeypatch.setattr(settings, "auth_password", PASSWORD)
    monkeypatch.setattr(settings, "secret_key", "")
    monkeypatch.setattr(settings, "cookie_secure", False)
    monkeypatch.setattr(database, "_cached_secret_key", None)
    from backend import crypto, main
    crypto._reset_cache()
    main.app.middleware_stack = None
    with TestClient(main.app, base_url=f"http://{HOST}") as test_client:
        test_client.post("/login", data={"username": "admin", "password": PASSWORD, "next": "/"})
        yield test_client
    crypto._reset_cache()
    main.app.middleware_stack = None


def make_instance(**fields):
    data = {"name": "Sonarr", "type": "sonarr", "url": "http://127.0.0.1:9",
            "api_key": "SUPERSECRETKEY1234567890", "enabled": False}
    data.update(fields)
    return db.instances.create(data)


class AttributeCollector(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.attributes = []   # (tag, name, value)
        self.tags = []         # (tag, attrs dict)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        for name, value in attrs:
            self.attributes.append((tag, name, value or ""))


def parse(text):
    collector = AttributeCollector()
    collector.feed(text)
    return collector


def script_attributes(text):
    """Attributes whose value is executed as JavaScript by the browser or by Alpine."""
    return [(tag, name, value) for tag, name, value in parse(text).attributes
            if name.startswith(("on", "@", "x-", ":"))]
```

Test-Bausteine für JavaScript-Tests (Node, in jede Datei kopieren, die sie braucht):

```python
import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

PRELUDE = r"""
const fs = require('fs');
const vm = require('vm');
const timers = [];
globalThis.setTimeout = (fn, ms) => { const t = { fn, ms, id: timers.length + 1, cleared: false }; timers.push(t); return t.id; };
globalThis.clearTimeout = (id) => { const t = timers.find(x => x.id === id); if (t) t.cleared = true; };
globalThis.setInterval = () => 0;
globalThis.clearInterval = () => {};
const activeTimers = () => timers.filter(t => !t.cleared).length;
const sources = [];
function FakeEventSource(url) { this.url = url; this.readyState = 0; sources.push(this); }
FakeEventSource.prototype.close = function () { this.readyState = 2; };
FakeEventSource.CONNECTING = 0; FakeEventSource.OPEN = 1; FakeEventSource.CLOSED = 2;
globalThis.EventSource = FakeEventSource;
globalThis.window = globalThis;
globalThis.location = { pathname: '/logs', search: '?x=1', href: 'http://t/logs?x=1' };
const listeners = {};
globalThis.document = { addEventListener: (name, fn) => { listeners[name] = fn; }, getElementById: () => null };
const stores = {};
const toasts = [];
globalThis.Alpine = {
  store: (name, value) => { if (value !== undefined) stores[name] = value; return stores[name]; },
  $data: () => ({}),
};
const responses = [];
globalThis.fetch = async (url, options) => responses.shift() || { status: 200, ok: true, redirected: false, url, json: async () => ({}) };
vm.runInThisContext(fs.readFileSync('static/js/app.js', 'utf8'));
listeners['alpine:init']();
stores.toasts.add = (message, type) => toasts.push([message, type]);
const out = {};
"""


def run_js(tmp_path, body):
    script = tmp_path / "case.js"
    script.write_text(PRELUDE + "(async () => {\n" + body +
                      "\nconsole.log(JSON.stringify(out));\n})().catch(e => { console.error(e); process.exit(1); });\n")
    result = subprocess.run([NODE, str(script)], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])
```

### Task P5.1: `base.html` und `app.js` — lokale Bibliotheken, Logout-Formular, `apiFetch`, SSE-Wiederverbindung

**Files:**
- Modify: `templates/base.html`
- Modify: `static/js/app.js` (ganze Datei)
- Test: `tests/test_p5_base.py`, `tests/test_p5_js.py`

**Interfaces:**
- Consumes: `/static/vendor/*.js` (P6.1), `POST /logout`, 401-Verhalten (P4.3/P4.4), 409 von `trigger` (P4.5)
- Produces: `apiFetch(url, options)`, `sessionExpiredError()`, `isSessionExpired(err)`, `toast(message, type)`, `createLogStore()` mit `seed(rows)`.

- [ ] **Step 1: Failing tests schreiben**

`tests/test_p5_base.py` (Seiten-Bausteine einfügen, dazu):

```python
def test_libraries_are_served_locally(client):
    page = client.get("/").text
    assert 'src="/static/vendor/alpinejs-3.14.1.min.js"' in page
    assert 'src="/static/vendor/htmx-2.0.4.min.js"' in page
    assert "cdn.jsdelivr.net" not in page and "unpkg.com" not in page
    assert client.get("/static/vendor/htmx-2.0.4.min.js").status_code == 200


def test_logout_is_a_post_form(client):
    page = client.get("/").text
    forms = [attrs for tag, attrs in parse(page).tags if tag == "form"]
    assert {"method": "post", "action": "/logout", "hx-boost": "false"}.items() <= forms[0].items()
    assert 'href="/logout"' not in page
```

`tests/test_p5_js.py` (JavaScript-Bausteine einfügen, dazu):

```python
def test_sse_retries_only_for_a_closed_current_source(tmp_path):
    out = run_js(tmp_path, """
const store = stores.logs;
store.init();
const first = sources[0];
first.readyState = 0; first.onerror();
out.afterConnecting = activeTimers();
first.readyState = 2; first.onerror(); first.onerror();
out.afterClosedTwice = activeTimers();
store.connect();
out.afterReconnect = activeTimers();
first.onerror();
out.afterStaleError = activeTimers();
out.sources = sources.length;
""")
    assert out == {"afterConnecting": 0, "afterClosedTwice": 1, "afterReconnect": 0,
                   "afterStaleError": 0, "sources": 2}


def test_seed_merges_without_duplicates(tmp_path):
    out = run_js(tmp_path, """
const store = stores.logs;
store.entries = [{ created_at: '2026-09-30 12:00:02', instance_name: 'S', level: 'info', message: 'live', _id: 99 }];
store.seed([
  { created_at: '2026-09-30 12:00:02', instance_name: 'S', level: 'info', message: 'live' },
  { created_at: '2026-09-30 12:00:01', instance_name: 'S', level: 'info', message: 'older' },
]);
out.messages = store.entries.map(e => e.message);
""")
    assert out["messages"] == ["live", "older"]


def test_seed_tolerates_a_second_between_live_and_stored_time(tmp_path):
    out = run_js(tmp_path, """
const store = stores.logs;
store.entries = [{ created_at: '2026-09-30 12:00:03', instance_name: 'S', level: 'info', message: 'tick', _id: 99 }];
store.seed([
  { created_at: '2026-09-30 12:00:02', instance_name: 'S', level: 'info', message: 'tick' },
  { created_at: '2026-09-30 11:59:00', instance_name: 'S', level: 'info', message: 'tick' },
]);
out.times = store.entries.map(e => e.created_at);
""")
    assert out["times"] == ["2026-09-30 12:00:03", "2026-09-30 11:59:00"]


def test_api_fetch_sends_a_lost_session_to_the_login_page(tmp_path):
    out = run_js(tmp_path, """
responses.push({ status: 401, ok: false, redirected: false, url: '/api/x' });
try { await apiFetch('/api/x'); out.thrown = false; } catch (err) { out.thrown = isSessionExpired(err); }
out.href = location.href;
location.href = 'http://t/logs?x=1';
responses.push({ status: 200, ok: true, redirected: true, url: 'http://t/login?next=/x' });
try { await apiFetch('/api/y'); out.redirectThrown = false; } catch (err) { out.redirectThrown = isSessionExpired(err); }
""")
    assert out == {"thrown": True, "href": "/login?next=%2Flogs%3Fx%3D1", "redirectThrown": True}


def test_force_run_explains_a_running_search(tmp_path):
    out = run_js(tmp_path, """
responses.push({ status: 409, ok: false, redirected: false, url: '/api', json: async () => ({ detail: 'busy' }) });
await forceRun(1);
out.toasts = toasts;
""")
    assert out["toasts"] == [["Already running — wait for the current run to finish", "info"]]
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag bestätigen**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p5_base.py tests/test_p5_js.py -q -p no:cacheprovider`
Expected: FAIL (CDN-Links, `href="/logout"`, `apiFetch is not defined`).

- [ ] **Step 3: `templates/base.html` ändern**

Im `<head>` die beiden CDN-Skripte ersetzen:

```html
    <!-- Alpine.js / htmx — vendored, verified against the npm integrity (scripts/vendor_assets.py) -->
    <script defer src="/static/vendor/alpinejs-3.14.1.min.js"></script>
    <script src="/static/vendor/htmx-2.0.4.min.js"></script>
```

Den Logout-Link ersetzen:

```html
        <form method="post" action="/logout" hx-boost="false" style="display:inline;margin:0;">
            <button type="submit" class="btn btn-secondary btn-sm" title="Sign out">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="vertical-align:middle;">
                    <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>
                    <polyline points="16 17 21 12 16 7"/>
                    <line x1="21" y1="12" x2="9" y2="12"/>
                </svg>
            </button>
        </form>
```

- [ ] **Step 4: `static/js/app.js` komplett**

```js
// Missingarr — Alpine.js stores, API helper & SSE setup.
// Top level holds function declarations only: hx-boost re-runs this file on
// every navigation, and a second `class`/`let` declaration would be a SyntaxError.

function sessionExpiredError() {
    const err = new Error('Session expired');
    err.name = 'SessionExpiredError';
    return err;
}

function isSessionExpired(err) {
    return !!err && err.name === 'SessionExpiredError';
}

// fetch() that notices a lost session. The server answers 401 for /api/*; a
// redirect to /login counts the same. Go to the login page instead of
// reporting a success that did not happen (C-L6).
async function apiFetch(url, options = {}) {
    const resp = await fetch(url, Object.assign({ credentials: 'same-origin' }, options));
    const toLogin = resp.redirected && new URL(resp.url, location.href).pathname === '/login';
    if (resp.status === 401 || toLogin) {
        location.href = `/login?next=${encodeURIComponent(location.pathname + location.search)}`;
        throw sessionExpiredError();
    }
    return resp;
}

function toast(message, type = 'info') {
    Alpine.store('toasts').add(message, type);
}

// Same line = same instance, level and message within one second. The live
// entry's created_at comes from Python's clock, the stored one from SQLite's
// datetime('now'); both are taken separately and may straddle a second.
function logEntryKey(entry) {
    return `${entry.instance_name}|${entry.level}|${entry.message}`;
}

function logEntrySeconds(entry) {
    const t = Date.parse(String(entry.created_at || '').replace(' ', 'T'));
    return Number.isNaN(t) ? null : t / 1000;
}

function createLogStore() {
    return {
        enabled: true,
        debug: false,
        entries: [],
        maxEntries: 500,
        retryDelay: 5000,
        _evtSource: null,
        _retryTimer: null,
        _nextId: 0,
        _buffer: [],
        _flushTimer: null,

        init() {
            this.connect();
        },

        // Rows rendered into the logs page (newest first). Merged with what the
        // live stream already delivered, without duplicates (C11).
        seed(rows) {
            if (!Array.isArray(rows)) return;
            const known = new Map();
            for (const entry of this.entries) {
                const key = logEntryKey(entry);
                if (!known.has(key)) known.set(key, []);
                known.get(key).push(logEntrySeconds(entry));
            }
            const isKnown = (row) => {
                const seconds = known.get(logEntryKey(row));
                if (!seconds) return false;
                const t = logEntrySeconds(row);
                return seconds.some(s => s === null || t === null || Math.abs(s - t) <= 1);
            };
            const older = rows
                .filter(row => !isKnown(row))
                .map(row => Object.assign({}, row, { _id: this._nextId++ }));
            this.entries = this.entries
                .concat(older)
                .sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''))
                .slice(0, this.maxEntries);
        },

        connect() {
            clearTimeout(this._retryTimer);
            this._retryTimer = null;
            if (this._evtSource) this._evtSource.close();
            const source = new EventSource(`/api/activity/stream?debug=${this.debug ? 1 : 0}`);
            this._evtSource = source;
            source.onmessage = (e) => {
                if (!this.enabled) return;
                try {
                    const entry = JSON.parse(e.data);
                    entry._id = this._nextId++;
                    this._buffer.push(entry);
                    if (!this._flushTimer) {
                        this._flushTimer = setTimeout(() => this._flush(), 150);
                    }
                } catch (err) {}
            };
            // While CONNECTING the browser reconnects by itself. Only a CLOSED
            // source (a non-200 answer such as 401) needs us — once, and only
            // for the source that is still current (C12).
            source.onerror = () => {
                if (source !== this._evtSource || !this.enabled) return;
                if (source.readyState !== EventSource.CLOSED || this._retryTimer) return;
                this._retryTimer = setTimeout(() => this._reconnect(), this.retryDelay);
            };
        },

        async _reconnect() {
            this._retryTimer = null;
            try {
                await apiFetch('/api/activity?limit=1');
            } catch (err) {
                if (isSessionExpired(err)) return;
            }
            if (this.enabled) this.connect();
        },

        _flush() {
            this._flushTimer = null;
            if (!this._buffer.length) return;
            // Reverse so the newest message ends up at index 0 after unshift
            const toAdd = this._buffer.splice(0).reverse();
            this.entries.unshift(...toAdd);
            if (this.entries.length > this.maxEntries) {
                this.entries.splice(this.maxEntries);
            }
        },

        toggleDebug() {
            this.debug = !this.debug;
            this.connect();
        },

        toggleEnabled() {
            this.enabled = !this.enabled;
            clearTimeout(this._retryTimer);
            this._retryTimer = null;
            if (!this.enabled) {
                if (this._evtSource) {
                    this._evtSource.close();
                    this._evtSource = null;
                }
                clearTimeout(this._flushTimer);
                this._flushTimer = null;
                this._buffer = [];
            } else {
                this.connect();
            }
        },

        clear() {
            this.entries = [];
            this._buffer = [];
            clearTimeout(this._flushTimer);
            this._flushTimer = null;
        },

        levelClass(level) {
            return `level-${level}`;
        },

        formatTime(ts) {
            if (!ts) return '-';
            return ts.replace('T', ' ').substring(0, 19);
        }
    };
}

document.addEventListener('alpine:init', () => {
    Alpine.store('toasts', {
        items: [],
        _id: 0,
        add(message, type = 'info', duration = 4000) {
            const id = ++this._id;
            this.items.push({ id, message, type });
            setTimeout(() => this.remove(id), duration);
        },
        remove(id) {
            this.items = this.items.filter(t => t.id !== id);
        }
    });
    Alpine.store('logs', createLogStore());
});

// ── Countdown helper ──────────────────────────────────────────────────────────
function countdownComponent(nextRunIso, status) {
    return {
        nextRun: nextRunIso ? new Date(nextRunIso) : null,
        status: status || 'unknown',
        display: '--:--',
        _timer: null,

        init() {
            this.update();
            this._timer = setInterval(() => this.update(), 1000);
        },
        destroy() {
            clearInterval(this._timer);
        },
        update() {
            if (!this.nextRun || this.status === 'off' || this.status === 'running' || this.status === 'error') {
                this.display = this.status === 'running' ? 'Running...' : '--:--';
                return;
            }
            const diff = Math.max(0, Math.floor((this.nextRun - Date.now()) / 1000));
            const h = Math.floor(diff / 3600);
            const m = Math.floor((diff % 3600) / 60);
            const s = diff % 60;
            this.display = h > 0
                ? `${h}h ${String(m).padStart(2, '0')}m`
                : `${String(m).padStart(2, '0')}m ${String(s).padStart(2, '0')}s`;
        }
    };
}

// ── Card live-update (called by htmx after every /status poll) ────────────────
var STATUS_BADGES = {
    running: ['badge badge-running', 'RUNNING'],
    quiet: ['badge badge-unknown', 'QUIET'],
    off: ['badge badge-unknown', 'OFF'],
    error: ['badge badge-offline', 'ERROR'],
};

function updateCardState(instanceId, responseText) {
    try {
        const data = JSON.parse(responseText);
        const state = data.agent_state || {};
        const card = document.getElementById(`icard-${instanceId}`);
        if (!card) return;

        const alpineData = Alpine.$data(card);
        if (alpineData) {
            alpineData.nextRun = state.next_run_at ? new Date(state.next_run_at) : null;
            alpineData.status = state.status || 'unknown';
        }

        const badgeEl = card.querySelector('[data-status-badge]');
        if (badgeEl) {
            const [cls, text] = STATUS_BADGES[state.status || 'off'] || ['badge badge-scheduled', 'WAIT'];
            badgeEl.className = cls;
            badgeEl.textContent = text;
        }

        const connEl = card.querySelector('[data-conn-badge]');
        if (connEl) {
            const c = data.connection_status || 'unknown';
            const cls = c === 'online' ? 'badge-online' : c === 'offline' ? 'badge-offline' : c === 'error' ? 'badge-error' : 'badge-unknown';
            connEl.className = `badge ${cls}`;
            connEl.textContent = c;
        }

        const rateCap = state.rate_cap || 1;
        const rateUsed = state.rate_used || 0;
        const ratePct = Math.min(100, Math.round((rateUsed / rateCap) * 100));
        const rateBar = card.querySelector('[data-rate-bar]');
        if (rateBar) {
            rateBar.style.width = ratePct + '%';
            rateBar.classList.toggle('danger', ratePct >= 80);
        }
        const rateUsedEl = card.querySelector('[data-rate-used]');
        if (rateUsedEl) rateUsedEl.textContent = `${rateUsed} / ${rateCap}`;

        card.querySelectorAll('[data-stat]').forEach(el => {
            const key = el.dataset.stat;
            if (key === 'last_wanted') el.textContent = state.last_wanted ?? '-';
            else if (key === 'last_triggered') el.textContent = state.last_triggered ?? '-';
            else if (key === 'last_verified') el.textContent = state.last_verified ?? '-';
            else if (key === 'last_sync') el.textContent = state.last_sync || '-';
        });
    } catch (_) {}
}

// ── Actions ───────────────────────────────────────────────────────────────────
async function errorDetail(resp, fallback) {
    const data = await resp.json().catch(() => ({}));
    return typeof data.detail === 'string' ? data.detail : fallback;
}

async function forceRun(instanceId, skill = 'search_missing') {
    try {
        const resp = await apiFetch(
            `/api/instances/${instanceId}/trigger?skill=${encodeURIComponent(skill)}&force=true`,
            { method: 'POST' }
        );
        if (resp.ok) {
            toast('Run triggered!', 'success');
        } else if (resp.status === 409) {
            toast('Already running — wait for the current run to finish', 'info');
        } else {
            toast(await errorDetail(resp, 'Failed to trigger run'), 'error');
        }
    } catch (err) {
        if (!isSessionExpired(err)) toast('Network error', 'error');
    }
}

async function testCardConnection(instanceId, btn) {
    const orig = btn.innerHTML;
    btn.disabled = true;
    btn.textContent = '…';
    try {
        const resp = await apiFetch(`/api/instances/${instanceId}/test`);
        if (resp.ok) {
            const data = await resp.json();
            toast(`Online — ${data.appName} v${data.version}`, 'success');
        } else {
            toast(await errorDetail(resp, 'Connection failed'), 'error');
        }
    } catch (err) {
        if (!isSessionExpired(err)) toast('Connection test failed', 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = orig;
    }
}

async function toggleSkill(instanceId, skill, currentlyEnabled, btn) {
    const newEnabled = !currentlyEnabled;
    try {
        const resp = await apiFetch(
            `/api/instances/${instanceId}/toggle-skill?skill=${skill}&enabled=${newEnabled}`,
            { method: 'POST' }
        );
        if (resp.ok) {
            btn.className = btn.className.replace(
                newEnabled ? 'btn-toggle-off' : 'btn-toggle-on',
                newEnabled ? 'btn-toggle-on' : 'btn-toggle-off'
            );
            btn.setAttribute('onclick', `toggleSkill(${instanceId}, '${skill}', ${newEnabled}, this)`);
            const name = skill.charAt(0).toUpperCase() + skill.slice(1);
            toast(`${name} ${newEnabled ? 'enabled' : 'disabled'}`, 'info');
        } else {
            toast(await errorDetail(resp, 'Failed to toggle skill'), 'error');
        }
    } catch (err) {
        if (!isSessionExpired(err)) toast('Failed to toggle skill', 'error');
    }
}

async function toggleInstance(instanceId, enabled) {
    try {
        const resp = await apiFetch(`/api/instances/${instanceId}/toggle?enabled=${enabled}`, { method: 'POST' });
        if (!resp.ok) {
            toast(await errorDetail(resp, 'Failed to toggle instance'), 'error');
            return;
        }
        toast(enabled ? 'Instance enabled' : 'Instance disabled', 'info');
        htmx.ajax('GET', `/instances/${instanceId}/card`, { target: `#icard-${instanceId}`, swap: 'outerHTML' });
    } catch (err) {
        if (!isSessionExpired(err)) toast('Failed to toggle instance', 'error');
    }
}
```

(`toggleSkill` setzt in `onclick` nur Zahlen und die festen Wörter `missing`/`upgrades` ein, keine Nutzerdaten. Die doppelte `instanceForm`-Funktion aus dem alten `app.js` entfällt; `form.html` definiert ihre eigene.)

- [ ] **Step 5: Tests grün**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p5_base.py tests/test_p5_js.py -q -p no:cacheprovider && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add templates/base.html static/js/app.js tests/test_p5_base.py tests/test_p5_js.py
git commit -m "fix: local libraries, POST logout, session-aware fetch, single SSE retry for closed streams"
```

### Task P5.2: Logs-, History- und Instanzliste — vorbefüllt, serverseitig gefiltert, Namen nur in `data-*`

**Files:**
- Modify: `templates/logs.html`, `templates/history.html`, `templates/instances/list.html`
- Test: `tests/test_p5_lists.py`

**Interfaces:**
- Consumes: `recent`, `instances` (P4.6), `GET /api/history/items` mit `X-Total-Count` (P3.6), `DELETE /api/history` → `{deleted, kept_open}`, `createLogStore().seed`, `apiFetch`
- Produces: nichts für andere Pakete

- [ ] **Step 1: Failing test schreiben**

`tests/test_p5_lists.py` (Seiten-Bausteine einfügen, dazu):

```python
import json


def test_hostile_names_never_reach_script_attributes(client):
    make_instance(name=HOSTILE)
    for path in ("/", "/instances", "/history", "/logs"):
        page = client.get(path).text
        for tag, name, value in script_attributes(page):
            assert "alert(" not in value, (path, tag, name, value)


def test_delete_button_reads_the_name_from_data_attributes(client):
    make_instance(name=HOSTILE)
    buttons = [attrs for tag, attrs in parse(client.get("/instances").text).tags
               if tag == "button" and "data-name" in attrs]
    assert buttons[0]["data-name"] == HOSTILE
    assert buttons[0]["onclick"] == "deleteInstance(this.dataset.id, this.dataset.name)"


def test_logs_page_is_seeded_with_stored_entries(client):
    inst = make_instance(name="Radarr")
    db.activity.insert(inst["id"], "Radarr", "info", "stored before the page opened", "system")
    page = client.get("/logs").text
    seeds = [attrs for tag, attrs in parse(page).tags if tag == "script" and attrs.get("id") == "logs-seed"]
    assert seeds and seeds[0].get("type") == "application/json"
    start = page.index('id="logs-seed">') + len('id="logs-seed">')
    rows = json.loads(page[start:page.index("</script>", start)])
    assert any(r["message"] == "stored before the page opened" for r in rows)
    assert '<option value="Radarr">Radarr</option>' in page
    # Alpine calls init() of an x-data component by itself; x-init would run it twice.
    roots = [attrs for tag, attrs in parse(page).tags if attrs.get("x-data") == "logsTable()"]
    assert roots and "x-init" not in roots[0]


def test_history_filters_are_server_side(client):
    inst = make_instance()
    page = client.get("/history").text
    assert 'value="upgrade"' not in page
    assert '<option value="search_upgrades">Upgrade</option>' in page
    assert f'<option value="{inst["id"]}">Sonarr</option>' in page
    assert "/api/history/items?" in page and "X-Total-Count" in page
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p5_lists.py -q -p no:cacheprovider`
Expected: FAIL (Name im `onclick`, kein Seed, Option `upgrade`).

- [ ] **Step 3: `templates/instances/list.html`**

Den Löschen-Knopf ersetzen:

```html
                    <button class="btn btn-danger btn-sm"
                            data-id="{{ inst.id }}" data-name="{{ inst.name }}"
                            onclick="deleteInstance(this.dataset.id, this.dataset.name)">
                        Delete
                    </button>
```

Das Skript am Ende ersetzen:

```html
<script>
async function deleteInstance(id, name) {
    if (!confirm(`Delete instance "${name}"? This will also remove all logs and history for this instance.`)) return;
    try {
        const resp = await apiFetch(`/api/instances/${id}`, { method: 'DELETE' });
        if (resp.ok || resp.status === 204) {
            toast(`Instance "${name}" deleted`, 'info');
            setTimeout(() => location.reload(), 800);
        } else {
            toast('Failed to delete instance', 'error');
        }
    } catch (err) {
        if (!isSessionExpired(err)) toast('Failed to delete instance', 'error');
    }
}
</script>
```

- [ ] **Step 4: `templates/logs.html`**

1. Den `<script>`-Block (Funktionen `logsTable`, `clearAllLogs`) vor das `<div x-data="logsTable()" …>` verschieben und so ersetzen:

```html
<script type="application/json" id="logs-seed">{{ recent|tojson }}</script>
<script>
function logsTable() {
    return {
        page: 0,
        perPage: 20,
        filterInstance: '',
        filterLevel: '',
        _filtered: [],

        init() {
            // Stored entries from the server, so a fresh load is not empty (C11).
            const seed = document.getElementById('logs-seed');
            if (seed) this.$store.logs.seed(JSON.parse(seed.textContent));
            this._refilter();
            this.$watch('$store.logs.entries', () => this._refilter());
            this.$watch('filterInstance', () => { this.page = 0; this._refilter(); });
            this.$watch('filterLevel',    () => { this.page = 0; this._refilter(); });
        },

        _refilter() {
            let entries = this.$store.logs.entries;
            if (this.filterInstance) entries = entries.filter(e => e.instance_name === this.filterInstance);
            if (this.filterLevel)    entries = entries.filter(e => e.level === this.filterLevel);
            this._filtered = entries;
        },

        pageEntries() {
            const start = this.page * this.perPage;
            return this._filtered.slice(start, start + this.perPage);
        },

        pageCount() {
            return Math.max(1, Math.ceil(this._filtered.length / this.perPage));
        },

        paginationLabel() {
            const total = this._filtered.length;
            const start = this.page * this.perPage + 1;
            const end = Math.min(start + this.perPage - 1, total);
            return total === 0 ? '0 entries' : `${start}–${end} of ${total}`;
        },

        fmt(ts) {
            if (!ts) return '—';
            return ts.replace('T', ' ').substring(0, 19);
        }
    };
}

async function clearAllLogs() {
    if (!confirm('Clear all logs from database?')) return;
    try {
        const resp = await apiFetch('/api/activity', { method: 'DELETE' });
        if (resp.ok) {
            toast('Logs cleared', 'info');
            Alpine.store('logs').clear();
        }
    } catch (err) {
        if (!isSessionExpired(err)) toast('Could not clear logs', 'error');
    }
}
</script>
```

2. Im Instanzfilter `{% for inst in all_instances %}` durch `{% for inst in instances %}` ersetzen.

3. `<div x-data="logsTable()" x-init="init()">` durch `<div x-data="logsTable()">` ersetzen. Alpine ruft `init()` einer `x-data`-Komponente selbst auf; mit `x-init="init()"` liefe es doppelt (zweimal `seed`, doppelte `$watch`-Registrierungen).

- [ ] **Step 5: `templates/history.html` komplett**

```html
{% extends "base.html" %}
{% block title %}History — {{ app_name }}{% endblock %}

{% block content %}
<script>
function historyPage() {
    return {
        search: '',
        instance: '',
        type: '',
        skill: '',
        perPage: 50,
        page: 1,
        rows: [],
        total: 0,
        loading: true,
        _request: 0,

        get totalPages() { return Math.max(1, Math.ceil(this.total / this.perPage)); },
        filtersActive() { return !!(this.search || this.instance || this.type || this.skill); },
        reload() { this.page = 1; return this.load(); },
        goTo(page) {
            this.page = Math.min(Math.max(1, page), this.totalPages);
            return this.load();
        },

        // Filters and paging run on the server, so every item is reachable and
        // "N items" counts all matches, not the first 1000 (B-L5).
        async load() {
            const request = ++this._request;
            this.loading = true;
            const params = new URLSearchParams({ limit: this.perPage, offset: (this.page - 1) * this.perPage });
            if (this.search.trim()) params.set('q', this.search.trim());
            if (this.instance) params.set('instance_id', this.instance);
            if (this.type) params.set('item_type', this.type);
            if (this.skill) params.set('skill', this.skill);
            try {
                const resp = await apiFetch(`/api/history/items?${params}`);
                if (request !== this._request) return;  // a newer filter change won
                this.rows = resp.ok ? await resp.json() : [];
                this.total = resp.ok ? Number(resp.headers.get('X-Total-Count') || this.rows.length) : 0;
            } catch (err) {
                if (!isSessionExpired(err)) toast('Could not load history', 'error');
            } finally {
                if (request === this._request) this.loading = false;
            }
        },

        async clearHistory() {
            if (!confirm('Clear finished search history? Runs still being verified are kept.')) return;
            try {
                const resp = await apiFetch('/api/history', { method: 'DELETE' });
                if (resp.ok) {
                    const data = await resp.json();
                    toast(`Removed ${data.deleted} run(s), kept ${data.kept_open} still open`, 'info');
                    this.reload();
                }
            } catch (err) {
                if (!isSessionExpired(err)) toast('Could not clear history', 'error');
            }
        },

        fmtTime(s) {
            if (!s) return '—';
            return s.replace('T', ' ').substring(0, 16);
        },
        skillLabel(s) {
            return s === 'search_upgrades' ? 'upgrade' : 'missing';
        },
        runLabel(s) {
            return {
                success: 'bestätigt',
                pending: 'offen',
                partial: 'teilweise',
                failed: 'gescheitert',
                unverified: 'nicht prüfbar',
                error: 'Fehler',
                running: 'läuft'
            }[s] || s;
        },
        itemLabel(s) {
            return {
                completed: 'durchgelaufen',
                failed: 'gescheitert',
                submitted: 'offen',
                expired: 'nicht prüfbar'
            }[s] || s;
        }
    };
}
</script>

<div x-data="historyPage()" x-init="load()">

<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:1.5rem;flex-wrap:wrap;gap:0.75rem;">
    <h1 style="margin:0;font-size:1.4rem;font-weight:700;">Search History</h1>
    <button class="btn btn-danger btn-sm" @click="clearHistory()">Clear History</button>
</div>

<div style="display:flex;gap:0.75rem;flex-wrap:wrap;margin-bottom:1rem;align-items:center;">
    <input type="text" class="form-input" placeholder="Search title or instance…"
           x-model="search" @input.debounce.300ms="reload()" style="width:220px;">

    <select class="form-input" x-model="instance" @change="reload()">
        <option value="">All instances</option>
        {% for inst in instances %}
        <option value="{{ inst.id }}">{{ inst.name }}</option>
        {% endfor %}
    </select>

    <select class="form-input" x-model="type" @change="reload()">
        <option value="">All types</option>
        <option value="episode">Episode</option>
        <option value="season">Season</option>
        <option value="series">Series</option>
        <option value="movie">Movie</option>
    </select>

    <select class="form-input" x-model="skill" @change="reload()">
        <option value="">All searches</option>
        <option value="search_missing">Missing</option>
        <option value="search_upgrades">Upgrade</option>
    </select>

    <select class="form-input" x-model.number="perPage" @change="reload()" style="width:110px;">
        <option value="25">25 / page</option>
        <option value="50" selected>50 / page</option>
        <option value="100">100 / page</option>
    </select>

    <span style="font-size:0.82rem;color:var(--text-muted);" x-text="total + ' items'"></span>
</div>

<div class="card" style="overflow:auto;">
    <template x-if="loading && rows.length === 0">
        <div style="text-align:center;padding:3rem 1rem;color:var(--text-muted);">Loading…</div>
    </template>

    <template x-if="rows.length > 0">
        <table class="table">
            <thead>
                <tr>
                    <th style="white-space:nowrap;">Time</th>
                    <th>Type</th>
                    <th>Title</th>
                    <th>Instance</th>
                    <th>*arr</th>
                    <th>Search</th>
                    <th>Status</th>
                    <th>Verified</th>
                </tr>
            </thead>
            <tbody>
                <template x-for="row in rows" :key="row.item_id">
                    <tr>
                        <td style="color:var(--text-muted);font-size:0.78rem;white-space:nowrap;" x-text="fmtTime(row.started_at)"></td>
                        <td>
                            <span class="badge"
                                :class="{
                                    'badge-scheduled': row.item_type === 'episode',
                                    'badge-running': row.item_type === 'season',
                                    'badge-online': row.item_type === 'movie',
                                    'badge-unknown': row.item_type === 'series'
                                }"
                                x-text="row.item_type">
                            </span>
                        </td>
                        <td style="max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"
                            :title="row.title" x-text="row.title"></td>
                        <td style="font-size:0.85rem;white-space:nowrap;" x-text="row.instance_name"></td>
                        <td>
                            <span class="badge"
                                :class="row.arr_type === 'sonarr' ? 'badge-scheduled' : 'badge-online'"
                                x-text="row.arr_type || '—'">
                            </span>
                        </td>
                        <td>
                            <span style="font-size:0.78rem;color:var(--text-muted);" x-text="skillLabel(row.skill)"></span>
                        </td>
                        <td>
                            <span class="badge"
                                :class="{
                                    'badge-online': row.status === 'success',
                                    'badge-offline': row.status === 'error' || row.status === 'failed',
                                    'badge-error': row.status === 'partial',
                                    'badge-scheduled': row.status === 'pending',
                                    'badge-unknown': row.status === 'unverified',
                                    'badge-running': row.status === 'running'
                                }"
                                :title="row.error_message || ''"
                                x-text="runLabel(row.status)">
                            </span>
                        </td>
                        <td>
                            <template x-if="row.command_status === 'legacy'">
                                <span style="color:var(--text-muted);"
                                      title="Vor der Einführung der Belegpflicht angelegt">—</span>
                            </template>
                            <template x-if="row.command_status !== 'legacy'">
                                <span class="badge"
                                    :class="{
                                        'badge-online': row.command_status === 'completed',
                                        'badge-offline': row.command_status === 'failed',
                                        'badge-scheduled': row.command_status === 'submitted',
                                        'badge-unknown': row.command_status === 'expired'
                                    }"
                                    :title="row.command_id ? 'Befehl #' + row.command_id : (row.command_status === 'failed' ? 'Nicht angenommen' : '')"
                                    x-text="itemLabel(row.command_status)">
                                </span>
                            </template>
                        </td>
                    </tr>
                </template>
            </tbody>
        </table>
    </template>

    <template x-if="!loading && rows.length === 0">
        <div style="text-align:center;padding:3rem 1rem;color:var(--text-muted);">
            <span x-text="filtersActive() ? 'No items match your filters.' : 'No search history yet. History is recorded after the first run.'"></span>
        </div>
    </template>
</div>

<template x-if="totalPages > 1">
    <div style="display:flex;align-items:center;justify-content:center;gap:0.5rem;margin-top:1rem;">
        <button class="btn btn-secondary btn-sm" @click="goTo(page - 1)" :disabled="page <= 1">&laquo;</button>
        <span style="font-size:0.85rem;color:var(--text-muted);">
            Page <span x-text="page"></span> of <span x-text="totalPages"></span>
        </span>
        <button class="btn btn-secondary btn-sm" @click="goTo(page + 1)" :disabled="page >= totalPages">&raquo;</button>
    </div>
</template>

</div>
{% endblock %}
```

- [ ] **Step 6: Tests grün**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p5_lists.py -q -p no:cacheprovider && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add templates/logs.html templates/history.html templates/instances/list.html tests/test_p5_lists.py
git commit -m "fix: seed the logs page, filter history on the server, keep instance names out of inline JS"
```

### Task P5.3: Dashboard-Karte — Fehlerzustand, FORCE gesperrt während eines Laufs, „Next search“

**Files:**
- Modify: `templates/instances/card.html`
- Test: `tests/test_p5_card.py`

**Interfaces:**
- Consumes: `state["status"] == "error"`, `next_run_at` für beide Such-Jobs (P1.2), `updateCardState` (P5.1)
- Produces: nichts

- [ ] **Step 1: Failing test schreiben**

`tests/test_p5_card.py` (Seiten-Bausteine **und** JavaScript-Bausteine einfügen; `pytestmark` der JS-Bausteine nur an die JS-Tests hängen, d. h. dort statt `pytestmark` den Dekorator `@pytest.mark.skipif(NODE is None, reason="node is not installed")` verwenden):

```python
def card_html(client, **fields):
    inst = make_instance(**fields)
    return client.get(f"/instances/{inst['id']}/card").text


def test_force_button_is_locked_while_running(client):
    page = card_html(client, enabled=True)
    force = [attrs for tag, attrs in parse(page).tags if tag == "button" and "btn-force" in attrs.get("class", "")]
    assert force[0][":disabled"] == "false || status === 'running'"


def test_status_poll_only_reads_successful_answers(client):
    # A 401 carries HX-Redirect (P4.3); its JSON body must not be read as a
    # state and turn the card into WAIT/unknown (C-L6).
    page = card_html(client, enabled=True)
    polls = [attrs for tag, attrs in parse(page).tags if "hx-get" in attrs]
    assert polls[0]["hx-on::after-request"] == (
        "if (event.detail.successful) updateCardState("
        + polls[0]["hx-get"].split("/")[3] + ", event.detail.xhr.responseText)"
    )


def test_label_says_why_there_is_no_countdown(client):
    # No agent runs for rows created after startup, so next_run is empty here.
    idle = card_html(client, enabled=True, search_missing_enabled=False, search_upgrades_enabled=False)
    assert "No search skill enabled" in idle
    starting = card_html(client, name="Radarr", enabled=True)
    assert "Starting up..." in starting
    assert "Next run · " not in idle + starting


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_error_status_shows_an_error_badge(tmp_path):
    out = run_js(tmp_path, """
const badge = { className: '', textContent: '' };
const card = { querySelector: (sel) => sel === '[data-status-badge]' ? badge : null, querySelectorAll: () => [] };
document.getElementById = () => card;
updateCardState(1, JSON.stringify({ agent_state: { status: 'error' } }));
out.badge = [badge.className, badge.textContent];
""")
    assert out["badge"] == ["badge badge-offline", "ERROR"]
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p5_card.py -q -p no:cacheprovider`
Expected: FAIL (kein `:disabled`, Beschriftung „Next run · “, `updateCardState` ohne Erfolgsprüfung).

- [ ] **Step 3: `templates/instances/card.html` ändern**

Status-Abfrage (Attribut am Element mit `hx-get="/api/instances/{{ inst.id }}/status"`) ersetzen:

```html
     hx-on::after-request="if (event.detail.successful) updateCardState({{ inst.id }}, event.detail.xhr.responseText)">
```

(Bei einer abgelaufenen Sitzung antwortet der Server `401` mit `HX-Redirect` (P4.3); htmx folgt dem Header, und die Karte liest die Fehlerantwort nicht als Zustand.)

Status-Badge (vor `{% else %}` einfügen):

```html
            {% elif agent_status == 'error' %}
                <span class="badge badge-offline" data-status-badge title="The scheduler could not start — see Logs">ERROR</span>
```

FORCE-Knopf ersetzen:

```html
            <button class="btn btn-force"
                    onclick="forceRun({{ inst.id }}, 'search_missing')"
                    {% if not is_enabled %}disabled{% endif %}
                    :disabled="{{ 'true' if not is_enabled else 'false' }} || status === 'running'"
                    title="Force run now (bypasses quiet hours)">
                FORCE
            </button>
```

Countdown-Beschriftung ersetzen:

```html
        <div class="countdown-label">
            {% if next_run %}
                Next search run
            {% elif agent_status == 'error' %}
                Scheduler error — see Logs
            {% elif not inst.search_missing_enabled and not inst.search_upgrades_enabled %}
                No search skill enabled
            {% else %}
                Starting up...
            {% endif %}
        </div>
```

- [ ] **Step 4: Tests grün**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p5_card.py -q -p no:cacheprovider && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add templates/instances/card.html tests/test_p5_card.py
git commit -m "fix: card shows scheduler errors, locks FORCE while a run is active, labels the next search run"
```

### Task P5.4: Progressed-Seite — Alpine-Methoden statt `__x`, serverseitiges Blättern

**Files:**
- Modify: `templates/searched.html` (ganze Datei)
- Test: `tests/test_p5_searched.py`

**Interfaces:**
- Consumes: `GET /api/searched` mit `X-Total-Count` (P3.6), `DELETE /api/searched[/{id}]`, `apiFetch`
- Produces: nichts

- [ ] **Step 1: Failing test schreiben**

`tests/test_p5_searched.py` (Seiten-Bausteine **und** JavaScript-Bausteine einfügen; wie in P5.3 statt `pytestmark` den Dekorator `@pytest.mark.skipif(NODE is None, reason="node is not installed")` nur an den JS-Test hängen), dazu:

```python
def component_script(page, marker):
    """The inline <script> that defines `marker`, as the browser gets it."""
    at = page.index(marker)
    start = page.rindex("<script>", 0, at) + len("<script>")
    return page[start:page.index("</script>", at)]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_reset_and_heading_behave_in_the_component(client, tmp_path):
    # C13 is about behaviour: after a reset the open table must be empty and
    # the heading must name the instance. Run the real component in Node.
    make_instance(name="Daniel's Sonarr")
    script = component_script(client.get("/searched").text, "function searchedPage()")
    out = run_js(tmp_path, f"""
vm.runInThisContext({json.dumps(script)});
globalThis.confirm = () => true;
document.querySelectorAll = () => [];
const listed = (rows, total) => ({{ status: 200, ok: true, redirected: false, url: '/api/searched',
  headers: {{ get: () => String(total) }}, json: async () => rows }});
const deleted = (n) => ({{ status: 200, ok: true, redirected: false, url: '/api/searched',
  json: async () => ({{ deleted: n }}) }});
const page = searchedPage();
responses.push(listed([{{ id: 1, title: 'A' }}, {{ id: 2, title: 'B' }}], 2));
await page.loadInstance(1, "Daniel's Sonarr");
out.heading = page.activeInstanceName;
out.loaded = page.items.length;
responses.push(deleted(5));
await page.reset(2, 'Other');
out.afterOtherReset = page.items.length;
responses.push(deleted(2));
await page.reset(1, "Daniel's Sonarr");
out.afterOwnReset = [page.items.length, page.total];
responses.push(listed([{{ id: 3, title: 'C' }}], 1));
await page.fetchItems();
responses.push(deleted(1));
await page.resetAll();
out.afterResetAll = [page.items.length, page.total];
""")
    assert out == {"heading": "Daniel's Sonarr", "loaded": 2, "afterOtherReset": 2,
                   "afterOwnReset": [0, 0], "afterResetAll": [0, 0]}


def test_searched_page_uses_component_methods_and_data_attributes(client):
    make_instance(name=HOSTILE)
    page = client.get("/searched").text
    for tag, name, value in script_attributes(page):
        assert "alert(" not in value, (tag, name, value)
    assert "__x" not in page and "data-iname" not in page
    assert '@click="reset(Number($el.dataset.id), $el.dataset.name)"' in page
    assert '@click="loadInstance(Number($el.dataset.id), $el.dataset.name)"' in page
    assert '@click="resetAll()"' in page
    assert "X-Total-Count" in page
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p5_searched.py -q -p no:cacheprovider`
Expected: FAIL.

- [ ] **Step 3: `templates/searched.html` komplett**

```html
{% extends "base.html" %}
{% block title %}Searched Items — {{ app_name }}{% endblock %}

{% block content %}
<script>
function searchedPage() {
    return {
        activeInstance: null,
        activeInstanceName: '',
        items: [],
        total: 0,
        loading: false,
        filterType: '',
        tablePage: 0,
        perPage: 20,

        async loadInstance(id, name) {
            if (this.activeInstance === id) {
                this.activeInstance = null;
                return;
            }
            this.activeInstance = id;
            this.activeInstanceName = name;
            this.tablePage = 0;
            this.filterType = '';
            await this.fetchItems();
        },

        // Paged on the server: every cached key is reachable, not just 500 (C-L5).
        async fetchItems() {
            if (this.activeInstance === null) return;
            this.loading = true;
            const params = new URLSearchParams({
                instance_id: this.activeInstance,
                limit: this.perPage,
                offset: this.tablePage * this.perPage,
            });
            if (this.filterType) params.set('item_type', this.filterType);
            try {
                const resp = await apiFetch(`/api/searched?${params}`);
                this.items = resp.ok ? await resp.json() : [];
                this.total = resp.ok ? Number(resp.headers.get('X-Total-Count') || this.items.length) : 0;
            } catch (err) {
                if (!isSessionExpired(err)) toast('Could not load items', 'error');
            } finally {
                this.loading = false;
            }
        },

        goTo(page) {
            this.tablePage = Math.min(Math.max(0, page), this.pageCount() - 1);
            return this.fetchItems();
        },

        pageCount() {
            return Math.max(1, Math.ceil(this.total / this.perPage));
        },

        paginationLabel() {
            if (!this.total) return '0 items';
            const start = this.tablePage * this.perPage + 1;
            const end = Math.min(start + this.perPage - 1, this.total);
            return `${start}–${end} of ${this.total}`;
        },

        _setCount(id, value) {
            const counter = document.getElementById(`count-${id}`);
            if (counter) counter.textContent = value;
        },

        async reset(id, name) {
            if (!confirm(`Reset searched cache for "${name}"? It will be re-searched on the next run.`)) return;
            try {
                const resp = await apiFetch(`/api/searched/${id}`, { method: 'DELETE' });
                if (!resp.ok) {
                    toast('Reset failed', 'error');
                    return;
                }
                const data = await resp.json();
                this._setCount(id, '0');
                toast(`Reset ${data.deleted} items for ${name}`, 'info');
                if (this.activeInstance === id) {
                    this.items = [];
                    this.total = 0;
                    this.tablePage = 0;
                }
            } catch (err) {
                if (!isSessionExpired(err)) toast('Reset failed', 'error');
            }
        },

        async resetAll() {
            if (!confirm('Reset ALL searched items? All instances will re-search everything on the next run.')) return;
            try {
                const resp = await apiFetch('/api/searched', { method: 'DELETE' });
                if (!resp.ok) {
                    toast('Reset failed', 'error');
                    return;
                }
                const data = await resp.json();
                document.querySelectorAll('[id^="count-"]').forEach(el => { el.textContent = '0'; });
                toast(`Reset ${data.deleted} items total`, 'info');
                this.items = [];
                this.total = 0;
                this.tablePage = 0;
            } catch (err) {
                if (!isSessionExpired(err)) toast('Reset failed', 'error');
            }
        },
    };
}
</script>

<div x-data="searchedPage()">

    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:1.5rem;flex-wrap:wrap;gap:0.75rem;">
        <div>
            <h1 style="margin:0;font-size:1.4rem;font-weight:700;">Progressed Items</h1>
            <p style="margin:0.25rem 0 0;color:var(--text-muted);font-size:0.85rem;">
                Items that have already been searched — they will be skipped in future runs until reset.
            </p>
        </div>
        <div style="display:flex;gap:0.5rem;flex-wrap:wrap;">
            <button class="btn btn-danger btn-sm" @click="resetAll()">Reset All</button>
        </div>
    </div>

    <div class="instance-grid" style="margin-bottom:1.5rem;">
        {% for inst in instances %}
        {% set inst_count = counts | selectattr('instance_id', 'equalto', inst.id) | list %}
        {% set total = inst_count[0].total if inst_count else 0 %}
        <div class="card" style="padding:1rem;">
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:0.75rem;">
                <div style="display:flex;align-items:center;gap:0.5rem;">
                    <span class="type-badge type-{{ inst.type }}">{{ inst.type }}</span>
                    <span style="font-weight:600;font-size:0.9rem;">{{ inst.name }}</span>
                </div>
                <button class="btn btn-secondary btn-sm"
                        data-id="{{ inst.id }}" data-name="{{ inst.name }}"
                        @click="reset(Number($el.dataset.id), $el.dataset.name)">
                    Reset
                </button>
            </div>
            <div style="font-size:2rem;font-weight:700;color:var(--text-primary);" id="count-{{ inst.id }}">{{ total }}</div>
            <div style="font-size:0.78rem;color:var(--text-muted);margin-top:0.15rem;">items in cache</div>
            <div style="margin-top:0.75rem;">
                <button class="btn btn-secondary btn-sm"
                        data-id="{{ inst.id }}" data-name="{{ inst.name }}"
                        @click="loadInstance(Number($el.dataset.id), $el.dataset.name)"
                        x-text="activeInstance === {{ inst.id }} ? 'Hide' : 'Show items'">
                    Show items
                </button>
            </div>
        </div>
        {% endfor %}

        {% if not instances %}
        <div class="card" style="text-align:center;padding:2rem;grid-column:1/-1;">
            <p style="color:var(--text-muted);">No instances configured yet.</p>
        </div>
        {% endif %}
    </div>

    <template x-if="activeInstance !== null">
        <div class="card" style="overflow:hidden;">
            <div style="display:flex;align-items:center;justify-content:space-between;padding:0.75rem 1rem;border-bottom:1px solid var(--border);">
                <div style="display:flex;align-items:center;gap:0.75rem;">
                    <span style="font-weight:600;font-size:0.9rem;" x-text="activeInstanceName"></span>
                    <span style="font-size:0.8rem;color:var(--text-muted);" x-text="`${total} items`"></span>
                </div>
                <div style="display:flex;gap:0.5rem;align-items:center;">
                    <select class="form-select" style="font-size:0.82rem;padding:0.3rem 0.6rem;width:auto;"
                            x-model="filterType" @change="goTo(0)">
                        <option value="">All types</option>
                        <option value="episode">Episode</option>
                        <option value="season">Season</option>
                        <option value="series">Series</option>
                        <option value="movie">Movie</option>
                    </select>
                    <select class="form-select" style="font-size:0.82rem;padding:0.3rem 0.6rem;width:auto;"
                            x-model.number="perPage" @change="goTo(0)">
                        <option value="20">20 / page</option>
                        <option value="50">50 / page</option>
                        <option value="100">100 / page</option>
                    </select>
                </div>
            </div>

            <div style="overflow-x:auto;">
                <table class="table" style="table-layout:fixed;width:100%;">
                    <colgroup>
                        <col style="width:155px;">
                        <col style="width:80px;">
                        <col>
                    </colgroup>
                    <thead>
                        <tr>
                            <th>Searched At</th>
                            <th>Type</th>
                            <th>Title</th>
                        </tr>
                    </thead>
                    <tbody>
                        <template x-for="item in items" :key="item.id">
                            <tr>
                                <td style="font-size:0.78rem;color:var(--text-muted);font-family:'JetBrains Mono',monospace;white-space:nowrap;"
                                    x-text="item.searched_at ? item.searched_at.replace('T',' ').substring(0,19) : '—'"></td>
                                <td>
                                    <span class="badge badge-scheduled" style="font-size:0.72rem;" x-text="item.item_type"></span>
                                </td>
                                <td style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"
                                    x-text="item.title" :title="item.title"></td>
                            </tr>
                        </template>
                        <template x-if="items.length === 0 && !loading">
                            <tr>
                                <td colspan="3" style="text-align:center;color:var(--text-muted);padding:2rem;">
                                    No searched items yet.
                                </td>
                            </tr>
                        </template>
                        <template x-if="loading">
                            <tr>
                                <td colspan="3" style="text-align:center;color:var(--text-muted);padding:2rem;">
                                    Loading…
                                </td>
                            </tr>
                        </template>
                    </tbody>
                </table>
            </div>

            <div style="display:flex;align-items:center;justify-content:space-between;padding:0.6rem 1rem;border-top:1px solid var(--border);font-size:0.82rem;color:var(--text-muted);">
                <span x-text="paginationLabel()"></span>
                <div style="display:flex;gap:0.4rem;">
                    <button class="btn btn-secondary btn-sm" @click="goTo(tablePage - 1)" :disabled="tablePage === 0">← Prev</button>
                    <template x-for="p in pageCount()" :key="p">
                        <button class="btn btn-sm"
                                :class="tablePage === p - 1 ? 'btn-primary' : 'btn-secondary'"
                                @click="goTo(p - 1)"
                                x-text="p"
                                x-show="Math.abs(tablePage - (p-1)) <= 2">
                        </button>
                    </template>
                    <button class="btn btn-secondary btn-sm" @click="goTo(tablePage + 1)" :disabled="tablePage >= pageCount() - 1">Next →</button>
                </div>
            </div>
        </div>
    </template>
</div>
{% endblock %}
```

- [ ] **Step 4: Tests grün**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p5_searched.py -q -p no:cacheprovider && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add templates/searched.html tests/test_p5_searched.py
git commit -m "fix: progressed page resets through Alpine methods, shows the instance name, pages on the server"
```

### Task P5.5: Formular — Grenzen, Schlüssel-Hinweis, URL-Wechsel; Tooltips und Hilfe

**Files:**
- Modify: `templates/instances/form.html`, `templates/help.html`, `backend/tooltips.py`
- Test: `tests/test_p5_form.py`

**Interfaces:**
- Consumes: `bounds` und maskierte `instance` aus dem Kontext (P4.6), `400` bei URL-Wechsel ohne Schlüssel (P4.5)
- Produces: nichts

- [ ] **Step 1: Failing test schreiben**

`tests/test_p5_form.py` (Seiten-Bausteine einfügen, dazu):

```python
from backend.models.instance import FIELD_BOUNDS
from backend.tooltips import TOOLTIPS


def inputs(page):
    return {attrs["id"]: attrs for tag, attrs in parse(page).tags if tag == "input" and "id" in attrs}


@pytest.mark.parametrize("path_kind", ["new", "edit"])
def test_number_fields_carry_the_model_bounds(client, path_kind):
    path = "/instances/new" if path_kind == "new" else f"/instances/{make_instance()['id']}/edit"
    fields = inputs(client.get(path).text)
    for name, (low, high) in FIELD_BOUNDS.items():
        assert fields[name]["min"] == str(low), name
        assert fields[name]["max"] == str(high), name


def test_edit_form_explains_the_stored_key_and_keeps_it_secret(client):
    inst = make_instance()
    page = client.get(f"/instances/{inst['id']}/edit").text
    assert "SUPERSECRETKEY" not in page
    assert "A key is stored" in page
    forms = [attrs for tag, attrs in parse(page).tags if tag == "form" and attrs.get("id") == "instance-form"]
    assert forms[0]["data-original-url"] == "http://127.0.0.1:9"


def test_tooltips_name_the_limits():
    assert "10080" in TOOLTIPS["interval_minutes"]
    assert "at least 1" in TOOLTIPS["missing_per_run"]
    assert "Radarr only" not in TOOLTIPS["search_upgrades_enabled"]
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag bestätigen**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p5_form.py -q -p no:cacheprovider`
Expected: FAIL.

- [ ] **Step 3: `templates/instances/form.html` ändern**

1. `<form id="instance-form">` ersetzen durch `<form id="instance-form" data-original-url="{{ instance.url if instance else '' }}">`.

2. Jedes der acht Zahlenfelder bekommt `min`, `max` und `step="1"` aus `bounds`, z. B.:

```html
                        <input class="form-control" type="number" id="interval_minutes" name="interval_minutes"
                               min="{{ bounds.interval_minutes[0] }}" max="{{ bounds.interval_minutes[1] }}" step="1"
                               value="{{ instance.interval_minutes if instance else 15 }}">
```

Genauso für `retry_hours`, `rate_window_minutes`, `rate_cap`, `hours_after_release`, `seconds_between_actions`, `missing_per_run`, `upgrades_per_run` (jeweils `bounds.<feld>[0]` und `bounds.<feld>[1]`).

3. Den Hinweis unter dem API-Key-Feld ersetzen:

```html
                    {% if instance and instance.api_key_set %}
                    <div class="form-hint">A key is stored. Leave the field blank to keep it — if you change the URL, enter the key again.</div>
                    {% elif instance %}
                    <div class="form-hint">No key stored yet.</div>
                    {% endif %}
```

4. Im Submit-Handler vor `try` einfügen und `fetch` durch `apiFetch` ersetzen:

```js
    const originalUrl = form.dataset.originalUrl || '';
    const urlChanged = originalUrl && data.url.replace(/\/+$/, '') !== originalUrl.replace(/\/+$/, '');
    if (method === 'PUT' && urlChanged && !apiKey) {
        toast('The URL changed — enter the API key again', 'error');
        btn.disabled = false;
        btn.textContent = 'Save Instance';
        return;
    }
```

und im `catch` des Submit-Handlers: `if (!isSessionExpired(err)) toast('Network error', 'error');` (plus Knopf wieder freigeben). Die Fehlermeldung aus der Antwort bleibt `result.detail?.[0]?.msg ?? result.detail ?? 'Save failed'` (deckt 400 mit Text und 422 mit Liste ab).

5. `testExistingConnection` auf `apiFetch` umstellen:

```js
async function testExistingConnection(id) {
    const btn = event.target;
    btn.disabled = true;
    btn.textContent = 'Testing...';
    try {
        const resp = await apiFetch(`/api/instances/${id}/test`);
        const data = await resp.json().catch(() => ({}));
        if (resp.ok) {
            toast(`Connected — ${data.appName} v${data.version}`, 'success');
        } else {
            toast(data.detail || 'Connection failed', 'error');
        }
    } catch (err) {
        if (!isSessionExpired(err)) toast('Connection test failed', 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Test Connection';
    }
}
```

6. Nur `function instanceForm(…)` in einen eigenen `<script>` **vor** `<div class="card" x-data="instanceForm(…)">` verschieben. Submit-Handler und `testExistingConnection` bleiben im Skript **nach** dem Formular: der Handler holt sich das Formular mit `document.getElementById('instance-form')`, das muss dann schon im DOM stehen.

`backend/tooltips.py` — diese Einträge ersetzen:

```python
    "search_upgrades_enabled": "Automatically searches for better releases of titles that already have a file (Sonarr: cutoff-unmet list, Radarr: see Upgrade Source).",
    "interval_minutes": "How often (in minutes) a search runs. Between 1 and 10080 (one week). Recommended: 15–60 minutes. Upgrade searches run every 4× this interval.",
    "retry_hours": "How long a searched title stays in the Searched cache. 0 = never search it again automatically (recommended — use the cache reset to retry). A season or series search only blocks episodes that were already out (air date plus hours after release) when it ran.",
    "rate_cap": "Maximum number of search actions within the rate window (1 to 1,000,000,000). Prevents API overload.",
    "missing_per_run": "Maximum number of missing titles searched per run. Must be at least 1 while missing search is enabled.",
    "upgrades_per_run": "Maximum number of upgrade candidates searched per run. Must be at least 1 while upgrade search is enabled.",
```

`templates/help.html` — im FAQ-Block nach „Instance shows "error"“ einfügen:

```html
        <div style="margin-bottom:1rem;">
            <strong style="color:var(--text-primary);">Card shows ERROR instead of a countdown</strong>
            <p style="color:var(--text-secondary);margin:0.25rem 0 0;">The scheduler of this instance could not start. The Logs page names the reason. Fix the setting and save the instance again.</p>
        </div>
        <div style="margin-bottom:1rem;">
            <strong style="color:var(--text-primary);">FORCE says "Already running"</strong>
            <p style="color:var(--text-secondary);margin:0.25rem 0 0;">A search of this kind is still running. Wait until it has finished; a second run would only repeat the same searches.</p>
        </div>
```

- [ ] **Step 4: Tests grün**

Run: `cd /root/missingarr && .venv/bin/python -m pytest tests/test_p5_form.py -q -p no:cacheprovider && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add templates/instances/form.html templates/help.html backend/tooltips.py tests/test_p5_form.py
git commit -m "fix: form enforces the model bounds, explains the stored key and asks for it on URL changes"
```

---
## Task Z: Aufräumen, Abnahme, Push (nach Welle 3, allein)

**Files:**
- Modify (nur Entfernen toter Übergangsfunktionen): `backend/agents/base.py`, `backend/db/searched.py`, `backend/db/history.py`
- Keine neuen Tests; die vorhandenen müssen grün bleiben.

**Interfaces:**
- Consumes: alles
- Produces: Branch `fix/codex-review` auf GitHub

- [ ] **Step 1: Übergangsfunktionen ohne Aufrufer finden**

Run: `cd /root/missingarr && grep -rn "check_rate_cap\|record_action\|exists_any\|searched\.exists(\|\.insert_item(\|set_item_status(" backend/ tests/`
Expected: nur noch die Definitionen (und ggf. Tests, die genau diese Funktionen prüfen). Was außer der Definition keinen Treffer hat, wird entfernt: `BaseAgent.check_rate_cap`, `BaseAgent.record_action`, `db.searched.exists`, `db.searched.exists_any`, `db.history.insert_item`, `db.history.set_item_status`. Hat eine Funktion noch einen Aufrufer, bleibt sie und der Aufrufer wird im Abschlussbericht genannt.

- [ ] **Step 2: `passlib` aus der `.venv` entfernen und alles testen**

Run: `cd /root/missingarr && .venv/bin/pip uninstall -y passlib && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: alle Tests grün (22 alte + alle neuen), kein `ModuleNotFoundError: passlib`.

- [ ] **Step 3: Lokaler Probelauf mit Scratch-Datenbank (nicht Live)**

Run:

```bash
SCRATCH=/tmp/claude-0/-root/b5f0b5e7-3436-4d16-9f25-cd6f007f969c/scratchpad
test -d "$SCRATCH" || { echo "SCRATCH fehlt: $SCRATCH" >&2; exit 1; }
PROBE="$SCRATCH/probe"
rm -rf "$PROBE" && mkdir -p "$PROBE"
cd /root/missingarr
DATABASE_URL="$PROBE/missingarr.db" AUTH_PASSWORD=probe-pass \
  .venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 18766 --timeout-graceful-shutdown 5 \
  > "$PROBE/uvicorn.log" 2>&1 &
echo $! > "$PROBE/pid"
timeout 30 sh -c 'until curl -fsS http://127.0.0.1:18766/api/health >/dev/null; do sleep 1; done'

# documented curl flow (no Origin header)
curl -s -c "$PROBE/jar" -o /dev/null -w '%{http_code}\n' -d 'username=admin&password=probe-pass&next=/' http://127.0.0.1:18766/login
curl -s -b "$PROBE/jar" -H 'Content-Type: application/json' \
  -d '{"name":"Probe","type":"sonarr","url":"http://127.0.0.1:9","api_key":"probe-key-123","enabled":false}' \
  http://127.0.0.1:18766/api/instances
curl -s -b "$PROBE/jar" -X POST 'http://127.0.0.1:18766/api/instances/1/trigger?skill=search_missing&force=false'
curl -s -b "$PROBE/jar" http://127.0.0.1:18766/api/instances | grep -c probe-key-123 || true
curl -s -o /dev/null -w '%{http_code}\n' -b "$PROBE/jar" -H 'Origin: http://evil.example' -X POST 'http://127.0.0.1:18766/api/instances/1/trigger?skill=search_missing'
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18766/api/instances
curl -s -o /dev/null -w '%{http_code}\n' -b "$PROBE/jar" http://127.0.0.1:18766/logout

# shutdown with an open log stream
curl -s -N -b "$PROBE/jar" http://127.0.0.1:18766/api/activity/stream > /dev/null &
sleep 1
start=$(date +%s); kill -TERM "$(cat "$PROBE/pid")"
timeout 15 sh -c "while kill -0 $(cat "$PROBE/pid") 2>/dev/null; do sleep 0.2; done"
echo "stopped after $(( $(date +%s) - start ))s"; grep -c "Shutdown complete" "$PROBE/uvicorn.log"
```

Expected der Reihe nach: `302`; JSON mit `"api_key":"********"`; `{"status":"triggered","skill":"search_missing"}`; `0` (Klartext kommt nicht vor); `403`; `401`; `405`; `stopped after 0s` oder `1s`; `1`. (`sleep` hier ist ein Shell-Befehl im Probeskript; wer das über ein Agenten-Werkzeug ausführt, nutzt dessen Warte-Mechanismus.)

- [ ] **Step 4: Probe-Container: ohne root, private Daten, Stopp mit offenem Stream (nicht der Live-Container)**

Hier, am fertigen Branch, steht der Container-Lauf, den P6.3 bewusst ausgelassen hat (in Welle 1 fehlte `passlib` im Image, `auth.py` brauchte es noch). Geprüft wird mit `PUID/PGID=568` wie für den Live-Stack vorgesehen, dazu eine alte Sicherungskopie mit `644` und ein root-eigener Datenordner wie auf hetzner2.

Run:

```bash
SCRATCH=/tmp/claude-0/-root/b5f0b5e7-3436-4d16-9f25-cd6f007f969c/scratchpad
test -d "$SCRATCH" || { echo "SCRATCH fehlt: $SCRATCH" >&2; exit 1; }
cd /root/missingarr
docker build -t missingarr:codex-review-test .
rm -rf "$SCRATCH/smoke-data" && mkdir -p "$SCRATCH/smoke-data"
chmod 755 "$SCRATCH/smoke-data"
echo old > "$SCRATCH/smoke-data/missingarr.db.bak-20260817-000513" && chmod 644 "$SCRATCH/smoke-data/missingarr.db.bak-20260817-000513"
docker run -d --name missingarr-smoke -p 127.0.0.1:18765:8000 -e AUTH_PASSWORD=smoke \
  -e PUID=568 -e PGID=568 -v "$SCRATCH/smoke-data:/data" missingarr:codex-review-test
timeout 60 sh -c 'until curl -fsS http://127.0.0.1:18765/api/health >/dev/null; do sleep 1; done'
docker top missingarr-smoke -o pid,uid,cmd | grep uvicorn
stat -c '%u:%g %a %n' "$SCRATCH/smoke-data" "$SCRATCH/smoke-data"/*
curl -s -c "$SCRATCH/smoke-jar" -o /dev/null -d 'username=admin&password=smoke' http://127.0.0.1:18765/login
curl -s -N -b "$SCRATCH/smoke-jar" http://127.0.0.1:18765/api/activity/stream > /dev/null &
sleep 1
time docker stop -t 10 missingarr-smoke
docker logs missingarr-smoke 2>&1 | grep -c "Shutdown complete"
docker rm missingarr-smoke
```

Expected: `/api/health` antwortet; `docker top` zeigt UID `568` für uvicorn; Datenordner `568:568 700`, `missingarr.db` (und ggf. `-wal`/`-shm`) sowie die `.bak`-Kopie `568:568 600`; `docker stop` dauert unter 7 s (nicht die vollen 10 s + SIGKILL), `Shutdown complete` erscheint einmal.

- [ ] **Step 5: Handprobe im Browser gegen den Probelauf aus Step 3 (neu starten)**

Checkliste, jeweils ohne Fehler in der Browser-Konsole:
1. Login mit „Remember me“, Dashboard lädt, Alpine und htmx kommen von `/static/vendor/`.
2. Karte: Missing/Upgrades-Schalter, Toast erscheint, `Next search run` bzw. `No search skill enabled` stimmt.
3. FORCE zweimal schnell: der zweite Klick zeigt „Already running …“ (bei einer Instanz, deren URL nicht antwortet, läuft der Lauf kurz genug zum Nachstellen; sonst per `curl` gegen `trigger` prüfen, dass 409 kommt).
4. Logs-Seite direkt neu laden: ältere Einträge sind da, Instanzfilter enthält die Instanz.
5. History: Filter „Upgrade“, Instanz, Suche, Blättern; Zahl „N items“ ändert sich mit dem Filter.
6. Progressed: „Show items“ zeigt den Namen in der Überschrift; „Reset“ leert Tabelle und Zähler; keine Konsolenfehler.
7. Formular: Intervall 0 → Browser lehnt ab; URL ändern ohne Schlüssel → Toast; Test Connection.
8. Abmelden über den Knopf → Login-Seite; zurück-Navigation zeigt keine Daten (401/Redirect).
Den Probelauf dafür mit dem Block aus Step 3 bis einschließlich `timeout 30 …/api/health` neu starten (Anmeldung dann im Browser). Danach beenden, im selben Bash-Aufruf mit gesetztem Pfad: `PROBE=/tmp/claude-0/-root/b5f0b5e7-3436-4d16-9f25-cd6f007f969c/scratchpad/probe; kill -TERM "$(cat "$PROBE/pid")"`.

- [ ] **Step 6: Commit und Push des Branches (kein Tag, kein Merge)**

```bash
cd /root/missingarr
git add backend/
git commit -m "chore: drop transition helpers nothing calls any more"
.venv/bin/python -m pytest tests/ -q -p no:cacheprovider
git push -u origin fix/codex-review
```

Expected: Tests grün, Push des Branches `fix/codex-review`. Kein `git tag`, kein Merge nach `main`: ein Tag `v*` startet `.github/workflows/docker-publish.yml` und veröffentlicht `gomaaz/missingarr:latest`. Das Deployment auf den Live-Container ist ein eigener Schritt mit Daniels Freigabe.

---

## Verhaltensänderungen für die Release-Notiz (0.8.0)

Was der Live-Betrieb spürt, nach Wirkung sortiert:

1. **Einmal neu anmelden.** Das Remember-Cookie hat ein neues Format (v2), alte Cookies und Sitzungen gelten nicht mehr. Das gilt auch für Skripte, die ein altes Session-Cookie aufbewahren.
2. **Abmelden meldet überall ab.** `POST /logout` widerruft alle Sitzungen und Remember-Cookies aller Geräte und Skripte. Auch ein Wechsel von `AUTH_PASSWORD` meldet alle ab.
3. **Abmelden nur per Knopf.** `GET /logout` antwortet `405`; Lesezeichen auf `/logout` funktionieren nicht mehr.
4. **Rechte von `/data`.** Beim ersten Start übergibt der Container `/root/docker/missingarr/data` an `PUID:PGID` (nur Dateien, die noch nicht passen, Symlinks nie), nimmt Gruppe und Anderen alle Rechte (Ordner `700`, Dateien `600`, auch `missingarr.db.bak-20260817-000513`) und läuft danach ohne root. **Auf hetzner2 ist UID/GID 1000 der Login-Benutzer `codeuser`** (`getent passwd 1000`, geprüft 30.09.2026); mit dem Standard könnte `codeuser` die DB samt Fernet-Schlüssel lesen und schreiben (heute nur lesen, `root:root 644`, `/root` hat `755`). Deshalb im Live-Stack vor dem Update `PUID=568` und `PGID=568` setzen (auf dem Host frei, geprüft 30.09.2026) — Änderung am Live-Stack, nur mit Daniels Freigabe beim Deployment. Danach lesen nur root und 568 die Daten.
5. **`SECRET_KEY`.** Ist im Live-Stack **kein** Wert gesetzt: keine Änderung. Ist dort ein Wert gesetzt (der alte Compose-Kommentar lud dazu ein), werden beim ersten Start alle API-Schlüssel umgeschlüsselt, die alten Schlüssel aus der DB gelöscht, und ab dann startet missingarr nur noch mit genau diesem Wert. Vorher `data/` sichern. Sicherungskopien in `data/` (z. B. `missingarr.db.bak-…`) enthalten danach weiter die alten Schlüssel samt Fernet-Schlüssel: löschen oder wie ein Geheimnis aufbewahren. Geht der Wert verloren: Rettungsweg in „Risiken“ (Schlüssel leeren, neu eingeben).
6. **Erstes Aufräumen.** Etwa 30 s nach dem Start löscht die Hauspflege abgeschlossene Läufe älter als 365 Tage (`HISTORY_RETENTION_DAYS`), danach stündlich. Bei `retry_hours=0` (live) bleibt der Cache unangetastet.
7. **Hängende Läufe werden geschlossen.** Läufe, die seit einem früheren Neustart auf „läuft“ stehen, werden beim Start auf „offen“ (mit Items) bzw. „Fehler: Interrupted by restart“ gesetzt.
8. **API-Schlüssel nicht mehr lesbar.** `GET /api/instances` liefert `"api_key": "********"` und `api_key_set`. Speichern mit leerem oder maskiertem Schlüssel behält den gespeicherten; wer die URL ändert, muss den Schlüssel neu eingeben (sonst `400`).
9. **`trigger` sagt „läuft schon“.** Läuft der Skill bereits, antwortet `POST /api/instances/<id>/trigger` mit `409` statt `200` (auch bei `force=false`; früher wurde der Aufruf mit `200` angenommen und still verworfen). Force wartet nicht mehr bis zu 90 s. Der dokumentierte curl-Ablauf bleibt sonst gleich; mit `curl -f` endet ein 409 mit Exit-Code 22.
10. **401 statt Login-Seite.** `/api/*` ohne Sitzung antwortet `401 {"detail": "Not authenticated"}` statt mit einer Umleitung auf das Login-HTML. htmx-Aufrufe (auch der 5-s-Status der Dashboard-Karten) bekommen zusätzlich `HX-Redirect`, der Browser landet dann auf der Login-Seite und kommt danach auf die Seite zurück.
11. **Fremde Seiten können nichts mehr auslösen.** POST/PUT/DELETE mit fremdem `Origin` oder `Sec-Fetch-Site` außer `same-origin`/`none` → `403`. Das trifft auch andere Dienste auf demselben Host (same-site). curl ohne diese Header ist nicht betroffen.
12. **Strengere Listen-Parameter.** `limit` über dem Maximum oder negativ → `422` statt stiller Kappung (`/api/history` ≤ 200, `/api/history/items` ≤ 1000, `/api/searched` ≤ 500, `/api/activity` ≤ 500). `DELETE /api/history` antwortet mit `deleted`/`kept_open` und löscht offene Läufe nicht mehr.
13. **Login-Sperre.** Nach 5 Fehlversuchen von einer Adresse 30 s Sperre, verdoppelt bis 15 min (`429`). Hinter einem Reverse-Proxy sehen alle Clients die Proxy-Adresse, siehe `FORWARDED_ALLOW_IPS`.
14. **Keine Weiterleitungen zu *arr.** Antwortet Sonarr/Radarr (oder ein Proxy davor) mit 3xx, gilt die Instanz als offline mit Hinweis „redirect“ — dann die URL korrigieren.
15. **Reihenfolgen `newest_first`/`oldest_first`/`smart` wirken jetzt über den ganzen Rückstand.** Dafür wird die ganze Wanted-Liste in Seiten zu 1000 gelesen (bis 100 Seiten). Bei großen Listen mehr GETs pro Lauf als bisher.
16. **`random` liest höchstens 10 Seiten pro Lauf.** Bei weitgehend abgearbeitetem Rückstand findet ein Lauf die letzten offenen Einträge unter Umständen erst in einem späteren Lauf; dafür nicht mehr die ganze Liste bei jedem Intervall.
17. **Cache-Regel für Sonarr.** Jeder Modus prüft jetzt Folge, Staffel und Serie. Eine Staffel- oder Seriensuche sperrt eine Folge nur, wenn sie nach Ausstrahlung **plus** `hours_after_release` lief **und** nach dem ersten Start von 0.8.0 (`ancestor_rule_since`). Folgen: Die rund 6.700 `ser:`- und 730 `sea:`-Zeilen aus früherem `show_batch`/`smart` sperren im Live-Modus `episode` weiterhin nichts; Sonarr sucht den am 30.09.2026 wieder freigegebenen Rückstand weiter (Regressionstest `test_series_keys_from_before_the_update_do_not_block`). Wechselt jemand künftig von `show_batch`/`season_packs` nach `episode`, sperren die dann neuen Staffel-/Seriensuchen die Folgen, die sie abgedeckt haben. Eine Staffelsuche wenige Stunden nach der Ausstrahlung sperrt die neue Folge nicht mehr dauerhaft. Offene Frage 7.
18. **Weniger Staffel-/Serienpakete.** Die Dichte zählt nur überwachte, bereits ausgestrahlte Folgen, die Serie ohne Staffel 0. Bei laufenden Staffeln gibt es öfter EpisodeSearch.
19. **Radarr-Freigabedatum.** Ein Film gilt ab dem früheren von digitaler und physischer VÖ als erschienen, sonst ab Kinostart. Digital erschienene Filme werden früher gesucht; Filme, deren digitale VÖ noch kommt, später. `newest_first`/`oldest_first` sortieren danach.
20. **Upgrades.** Alle Seiten der Cutoff-Liste sind erreichbar. Fällt jede Quelle aus, ist der Lauf `error` statt „keine Kandidaten“.
21. **Ehrliche Laufstatus.** Scheitert jedes Einreichen, ist der Lauf `error`; scheitern einige, nennt der Lauf Zahl und ersten Fehler, die History zeigt diese Titel als „gescheitert“ ohne Befehls-ID, und die Verifikation ergibt höchstens „teilweise“.
22. **Verifikation in der Ruhezeit.** `verify_commands` fragt auch während der Ruhezeit den Befehlsstatus ab (keine Suchen). `orphaned` zählt als gescheitert und gibt den Cache frei. Nach 24 h läuft ein Item erst ab, wenn *arr selbst geantwortet hat; 502/503/504 eines Proxys vor einem ausgefallenen *arr, 401/403 und 3xx zählen nicht als Antwort.
23. **Abbrechen.** Deaktivieren oder Löschen einer Instanz bricht einen laufenden Suchlauf sofort ab (Radarr mit 600 Items endet nicht mehr erst nach ~20 min). Speichern im Formular bricht nicht ab.
24. **Rate-Fenster bleibt erhalten.** Speichern oder Aus/An setzt den Zähler nicht mehr auf 0 (ein Container-Neustart weiterhin).
25. **Skill-Schalter auf der Karte wirken sofort**, ohne Neustart.
26. **Intervall geklemmt.** Gespeicherte Intervalle außerhalb 1…10080 min werden mit Warnung im Log auf die Grenze gesetzt.
27. **Formular prüft Grenzen.** Speichern mit Werten außerhalb der Grenzen, `*_per_run = 0` bei eingeschaltetem Skill, Namen über 100 Zeichen oder mit Steuerzeichen, URLs mit `?`, `#` oder Benutzerdaten scheitert mit Meldung. Bereits gespeicherte Werte laufen weiter, bis jemand speichert.
28. **Oberfläche.** Logs-Seite zeigt beim Öffnen die letzten 100 Einträge; History filtert serverseitig (auch „Upgrade“); Progressed blättert über alle Einträge; Karte zeigt `ERROR`, wenn der Scheduler nicht startet; FORCE ist während eines Laufs gesperrt.
29. **`AUTH_PASSWORD` als bcrypt-Hash** funktioniert jetzt. Wie bei bcrypt üblich zählen nur die ersten 72 Byte des Passworts.
30. **Container.** Basisimage und Pakete sind festgenagelt; ein offener Browser-Tab verzögert `docker stop` nicht mehr auf 10 s.

---

## Risiken

| Risiko | Wirkung | Gegenmaßnahme im Plan |
|---|---|---|
| Große Wanted-Liste bei `newest_first`/`oldest_first`/`smart` | bis 100 GETs à 1000 Einträge pro Lauf, Last auf *arr, Lauf dauert länger | Obergrenze `ORDERED_MAX_PAGES`, Hinweis am Lauf bei Kappung; `random` (Standard) bleibt günstig. Welche Reihenfolge live läuft, ist unbekannt (offene Frage). |
| Live-URL leitet um | Instanz nach Update „offline“ | klare Meldung „redirect … check the URL“; Abnahme nach Deployment: „Test“ auf beiden Karten |
| `SECRET_KEY` im Live-Stack schon gesetzt | automatische Umschlüsselung beim ersten Start, danach Pflichtwert | Release-Notiz Punkt 5, offene Frage 1, Backup vor Update. Wiederherstellung bei verlorenem Wert (Container gestoppt, mit einem SQLite-Werkzeug auf dem Host): `DELETE FROM app_settings WHERE key IN ('key_source','secret_key_check'); UPDATE instances SET api_key='';`, dann ohne `SECRET_KEY` (oder mit neuem Wert) starten und alle API-Schlüssel im Formular neu eingeben. Ohne das `UPDATE` scheitert schon `orchestrator.start_all()` an `InvalidToken` für jede `enc:`-Zeile. Test `test_lost_secret_key_recovery_path_works` (P4.2), README-Hinweis (P6.4). |
| Login-Sperre hinter Proxy | alle Clients teilen eine Adresse, 5 Fehlversuche sperren alle bis 15 min | Tailnet-Zugang, README nennt `FORWARDED_ALLOW_IPS` |
| `LazySessionMiddleware` ruft `SessionMiddleware.__init__` später auf | ein Starlette-Update könnte das brechen | Test `test_login_works_with_the_lazily_read_session_key`; Versionen stehen in `requirements.lock` |
| `token_version` im Speicher | mehrere Worker sähen verschiedene Stände | Dockerfile erzwingt `--workers 1` (Agenten brauchen das ohnehin) |
| chown von `/data` auf `PUID:PGID` | Standard 1000 = `codeuser` auf hetzner2 bekäme Lese- und Schreibzugriff auf DB und Fernet-Schlüssel; Host-Werkzeuge, die als anderer Benutzer lesen, brauchen root | Live-Stack mit `PUID/PGID=568` (Release-Notiz 4, offene Frage 6); `go-rwx` auch auf Sicherungskopien; Symlinks übersprungen; `PUID=0` als Ausweg; Task Z Step 4 prüft 568/700/600 am Probe-Container |
| Vorfahren-Regel gegen den Live-Stand | ohne Marker sperrten die alten `ser:`/`sea:`-Zeilen (Seriensuchen 2026, Folgen älter) im Live-Modus `episode` wieder den Großteil des Rückstands — genau das, was die Umstellung am 30.09.2026 behoben hat | Marker `ancestor_rule_since` (P3.1), `_blocked` ignoriert ältere Vorfahren-Schlüssel (P2.2), Regressionstest in P2.3; Entscheidung vor P2.2 bei Daniel (offene Frage 7) |
| Staffelsuche kurz nach Ausstrahlung | eine SeasonSearch wenige Stunden nach der Folge sperrte sie bei `retry_hours=0` für immer | Vorfahren-Schlüssel zählen erst ab Ausstrahlung + `hours_after_release` (P2.2, Test in P2.3) |
| Cache-Regel mit alten Zeitstempeln | Zeilen vor 1c1dc83 (März 2026) stehen in UTC statt Ortszeit | Abweichung 1–2 h, nur für Vorfahren-Schlüssel relevant; durch den Marker betrifft es nur noch Zeilen ab 0.8.0 (Ortszeit); hingenommen |
| Worktree-Merge am Wellenende | ein Paket ändert entgegen der Zuständigkeit eine fremde Datei → Merge-Konflikt | Wellen-Abnahme merged nacheinander und stoppt beim ersten Konflikt; der Vertrag nennt jede Datei genau einmal |
| Items ohne Antwort von *arr | bei dauerhaft unerreichbarem *arr bleiben Items `submitted`, Läufe `pending` | gewollt: kein Urteil ohne Antwort; Rotation hält die übrigen Prüfungen am Laufen |
| Hash-gepinnte Abhängigkeiten, SHA-gepinnte Actions | Updates kommen nicht mehr von selbst | README beschreibt das Erneuern (pip-compile, `scripts/vendor_assets.py`, Digest-Befehl in P6.3) |
| `409` bei `force=false` | Skripte mit `curl -f` melden einen Fehler, wenn gerade ein Lauf läuft | Release-Notiz Punkt 9, offene Frage 5 |
| Tests unter root | Entrypoint-Tests brauchen root und `setpriv` | werden sonst übersprungen, nicht rot |
| Release-Kette im Test braucht Git-Tags | in einem flachen Checkout (CI) fehlen `v0.5.2`…`v0.7.0` | Test überspringt sich dann; auf hetzner2 sind alle Tags da |

## Bewusst nicht gemacht

- **`sortKey`/`sortDirection` an *arr:** 8a1a912 und c68178e haben sie entfernt, weil *arr damit Einträge ohne Datum verschluckt. Sortiert wird lokal über die ganze Liste.
- **Sperre privater Netze für Instanz-URLs:** Sonarr/Radarr im Docker-Netz und LAN sind der Zweck der App. Gegen SSRF wirken Weiterleitungsverbot, URL-Prüfung, Schlüssel-Pflicht bei URL-Wechsel und CSRF-Schutz.
- **Content-Security-Policy:** Alpine-Ausdrücke und Inline-Handler bräuchten `unsafe-eval`/`unsafe-inline` oder den CSP-Build von Alpine mit Umbau aller Komponenten. Stattdessen: lokale Bibliotheken, keine Serverdaten in JavaScript, CSRF-Prüfung.
- **CSRF-Token:** Die Prüfung von `Sec-Fetch-Site`/`Origin` deckt den Einsatz ab und lässt curl-Skripte unverändert; ein Token hätte jeden Aufruf in Formularen und `fetch` geändert.
- **Einzelne Cache-Schlüssel in der Oberfläche löschen (Nebenvorschlag zu B6):** Mit der Ausstrahlungs-Regel sperren breite Schlüssel keine neuen Folgen mehr; Leeren pro Instanz reicht.
- **Absicht-Eintrag vor dem POST (B2):** Laut Prüfer für den Heimserver nicht nötig; eine Transaktion plus 30 s `busy_timeout` plus fehlertolerantes Log genügen.
- **Rate-Zähler aus der Datenbank ableiten (A-L4, Alternative):** Die Runtime im Orchestrator deckt Speichern, Aus/An und Wegwerf-Agenten ab. Ein Container-Neustart setzt den Zähler weiter zurück (dokumentiert).
- **Upgrade-Start versetzen (A10, optional):** Die atomare Reservierung verhindert die Überschreitung schon.
- **Eigener Status `aborted`:** Der CHECK-Constraint kennt ihn nicht; ein Tabellenumbau nur dafür lohnt nicht. Abbrüche stehen als `error` bzw. mit Text am Lauf.
- **Leere Läufe gar nicht speichern (B7, Nebenvorschlag):** Sie zeigen auf der Karte, dass gesucht wurde; die Aufbewahrungsfrist begrenzt sie.
- **`cap_drop`, `no-new-privileges`, `read_only` im Compose (C9, optional):** Der Entrypoint braucht beim Start `CHOWN`, `SETUID`, `SETGID`. Das ist eine Entscheidung für den Live-Stack (Portainer) und gehört nicht in diesen Branch.
- **Dependabot/Renovate:** würde Pull-Requests im öffentlichen Repo erzeugen; Daniels Entscheidung.
- **Deployment, Tag, Merge, Änderungen am Live-Stack:** außerhalb dieses Plans; nur der Branch wird gepusht.

## Offene Fragen

1. Ist im Live-Stack (Portainer, Stack der missingarr) `SECRET_KEY` gesetzt? Wenn ja: gewollt, dass 0.8.0 beim ersten Start umschlüsselt? (Nur in der Stack-Definition nachsehen, nicht im laufenden Container.)
2. Läuft der Browserzugriff über NPMplus/einen Reverse-Proxy? Dann `FORWARDED_ALLOW_IPS` auf dessen Adresse setzen, damit die Login-Sperre echte Adressen sieht.
3. Welche `search_order` nutzen Live-Sonarr und Live-Radarr? Bei `newest_first`/`oldest_first`/`smart` steigt die Zahl der GETs pro Lauf (Punkt 15).
4. Leiten die Live-URLs irgendwo um (z. B. http → https, fehlender Basispfad)?
5. Stört `409` bei `force=false` ein Vault-Skript mit `curl -f`?
6. `PUID`/`PGID` für den Live-Stack: 1000 ist auf hetzner2 `codeuser` (Login-Benutzer), das wäre ein Rückschritt. Vorschlag: `PUID=568`, `PGID=568` (frei, geprüft 30.09.2026), beim Deployment in der Stack-Definition setzen. Einverstanden, oder soll ein anderer (System-)Benutzer die Daten besitzen?
7. **Vor P2.2:** Alte `ser:`/`sea:`-Zeilen (rund 6.700 und 730) sollen nach Richtung 7 „immer geprüft“ werden, würden im Live-Modus `episode` aber den Großteil des Rückstands wieder sperren. Plan-Vorschlag: Marker `ancestor_rule_since` beim ersten Start von 0.8.0; ältere Vorfahren-Schlüssel sperren nie, neue nach der Ausstrahlungs-Regel (nichts wird gelöscht, künftige Moduswechsel sind abgedeckt). Alternative: einmalig `ser:%`/`sea:%` der Sonarr-Instanz löschen (Deploy-Schritt mit Freigabe, dann ohne Marker). Welche Variante?

## Kritik geprüft (Review des Plans, 30.09.2026)

Jeder Punkt wurde am Code bzw. am Host nachgeprüft. Alle 17 Punkte stimmen und sind übernommen; wo der Plan einen anderen als den vorgeschlagenen Weg nimmt, steht der Grund dabei.

| # | Punkt | Befund am Code | Umsetzung |
|---|---|---|---|
| 1 | Probe-Container in Welle 1 kann nicht starten | `backend/auth.py:12` importiert `passlib.context` auf Modulebene; P6.2 nimmt `passlib` aus dem Image; `_restrict_file_permissions` kommt erst mit P3.1 | P6.3 Step 6 nur `docker build` + `image inspect`; Container-, UID- und Rechte-Prüfung in Task Z Step 4; Abnahme P6 angepasst |
| 2 | Karten-Polling ohne Sitzung bekommt kein `HX-Redirect` | `card.html` fragt per `hx-get` `/api/instances/{id}/status` alle 5 s; `/api/` wurde vor `HX-Request` geprüft; `hx-on::after-request` ruft `updateCardState` ohne Erfolgsprüfung | `unauthenticated_response` prüft htmx zuerst, `/api/*` + htmx → JSON-401 mit `HX-Redirect` auf die Seite aus `HX-Current-URL` (nur Pfad, sonst `/`); Karte ruft `updateCardState` nur bei `event.detail.successful`; Tests in P4.3 und P5.3 |
| 3 | 5xx/401/3xx zählen als Antwort in `verify_commands` | `map_command_status` liefert für jeden Nicht-200-Status außer 404 `submitted`, `http_status != 0` hakte ihn trotzdem als geprüft ab | nur `http_status == 200 and isinstance(payload, dict)` zählt; Test parametrisiert mit 0/502/503/401/302 |
| 4 | `.env` aus `.env.example` bricht den Import | nachgestellt: pydantic-settings meldet `puid Extra inputs are not permitted` | `model_config["extra"] = "ignore"` in P3.1, Test `test_env_file_copied_from_the_example_is_accepted` |
| 5 | Rettungsweg bei verlorenem `SECRET_KEY` greift nicht | nach Löschen der Marker erzeugt `init_crypto` einen neuen Fernet-Schlüssel, `row_to_dict` → `decrypt` wirft `InvalidToken`, `start_all()` scheitert | Rettungsweg um `UPDATE instances SET api_key=''` ergänzt (Risiken, README), Test in P4.2. Nicht gewählt: tolerantes `decrypt`, weil ein falscher Schlüssel dann still zu leeren API-Keys würde statt zu einem klaren Startfehler |
| 6 | Staffelsuche kurz nach Ausstrahlung sperrt die Folge dauerhaft | `_blocked` verglich nur `searched_at >= aired` | Vorfahren-Schlüssel erst ab `aired + hours_after_release`; Test in P2.3 |
| 7 | nur ein Wegwerf-Agent je Instanz gemerkt | `_adhoc[instance_id] = agent` überschrieb, nie ausgetragen, `forget_instance` brach nur den letzten ab | `_adhoc: dict[int, set]`, Abbruch an alle, Austragen per `on_done` am Ende des Trigger-Threads; zwei Tests in P1.4 |
| 8 | Ruhezeit-Überspringen lässt `next_run_at` stehen | `_run_skill` kehrt vor dem `finally` zurück | `_update_next_run()` vor jedem vorzeitigen `return` (Ruhezeit, Deaktiviert); Test in `test_p1_control.py` (dort liegen schon die Ruhezeit-Tests). APScheduler hält beim Einreichen `_jobstores_lock`, `get_job` im Job-Thread sieht also schon die neue Zeit |
| 9 | `init()` doppelt, Dedup an Sekundengrenzen | Alpine ruft `init()` einer `x-data`-Komponente selbst; `created_at` für Broadcast und DB getrennt gebildet | `x-init` entfernt (P5.2, Test); `seed()` vergleicht Instanz/Level/Text und Zeit mit ±1 s (P5.1, Test). Nicht gewählt: Zeitstempel einmal bilden und an `db.activity.insert` geben — das hätte P1.3 (Welle 1) von einer Signaturänderung in P3 abhängig gemacht |
| 10 | bcrypt 5 wirft bei > 72 Byte | nachgestellt: `ValueError: password cannot be longer than 72 bytes` | auf 72 Byte kürzen wie ältere bcrypt/passlib/htpasswd, `ValueError`-Zweig nur noch für kaputte Hashes; Test in P4.4 |
| 11 | Vorfahren-Regel macht die Live-Umstellung rückgängig | Vault „missingarr sucht fehlende Folgen nie ein zweites Mal“: 118 von 139 Serien durch alte `ser:`-Zeilen gesperrt; 0.7.0 prüft in `episode` nur `ep:` | Marker `ancestor_rule_since` (P3.1), `_blocked` ignoriert ältere Vorfahren-Schlüssel (P2.2), Regressionstest; Risiko, Release-Notiz 17, offene Frage 7 |
| 12 | Standard-UID 1000 = `codeuser` | `getent passwd 1000` → `codeuser`; `/root` 755; Daten heute `root:root 644`, `.bak` daneben | Live-Vorschlag `PUID/PGID=568` (frei), Entrypoint `go-rwx` + `umask 077`, Tests; Release-Notiz 4 und 5, README, offene Frage 6. Der Image-Standard bleibt 1000 (Richtungsentscheidung 12) |
| 13 | parallele Wellen im selben Arbeitsbaum | gemeinsamer Index und gemeinsame Suite | je Paket ein Worktree und Zweig, Merge am Wellenende; P3.4/P3.5 als P3b in Welle 2 (brauchen P1.4) |
| 14 | `$SCRATCH` nirgends gesetzt | stimmt, nur ein Hinweistext | jeder Block setzt den absoluten Pfad und prüft `test -d`; `PROBE` ebenso |
| 15 | Alt-DB nur als handgeschriebene DDL | Tags `v0.5.2`…`v0.7.0` liegen im Repo | zusätzlicher Test mit echter Release-Kette per `git show` (im Scratchpad nachgestellt: Kette läuft, Items A/B `legacy`, C `submitted`) |
| 16 | C13 nur statisch geprüft | stimmt | Node-Test führt `searchedPage()` aus der gerenderten Seite aus: Überschrift, Reset eigener/fremder Instanz, Reset all |
| 17 | `chown` folgt Symlinks | `find … -exec chown` ohne `-h` ändert das Ziel | `! -type l` + `chown -h`, Test mit Symlink auf Datei außerhalb (im Scratchpad nachgestellt) |

## Selbstprüfung

- Abdeckung: jede der 53 Befund-IDs steht in der Tabelle; jede nicht-doppelte ID hat mindestens einen Task mit Test. B10 ist über die Tests aller Pakete abgedeckt, die Mutanten aus dem Prüfbericht (fehlende Cache-Freigabe, fehlendes Ablaufen, Umbau mit Fremdschlüsseln) fängt `test_p3_verify.py`/`test_p3_database.py` gezielt.
- Dateizuständigkeit: jede geänderte Datei gehört genau einem Paket (Tabelle „Wellen und Dateizuständigkeit“); parallele Pakete arbeiten in eigenen Worktrees; Task 0, Welle 3 und Task Z laufen allein im Haupt-Arbeitsbaum.
- Nach dem Review geänderte Stellen sind nur teilweise gegen die Wegwerf-Kopie gelaufen: nachgestellt wurden die Release-Kette (Punkt 15), der Entrypoint mit Symlink, `go-rwx` und `umask` (Punkte 12, 17), `extra="ignore"` (Punkt 4) und bcrypt > 72 Byte (Punkt 10). Die übrigen neuen Tests (P1.4 Wegwerf-Agenten und Ruhezeit, P2.3 Marker und Freigabefenster, P3.4 Nicht-Antworten, P4.2 Rettungsweg, P4.3 htmx, P5.1/P5.2/P5.3/P5.4) laufen erst beim Ausführen; schlägt einer fehl, zuerst den Test gegen diese Tabelle prüfen.
- Namen: `reserve_action`/`release_action`, `stop_requested`/`wait_or_stop`/`request_abort`, `record_submission`/`record_failed_submission`, `lookup_many`, `public_instance`, `apiFetch`/`isSessionExpired` sind im Vertrag und in den Tasks gleich geschrieben.
- Probe bei Planerstellung (30.09.2026, nur in einer Wegwerf-Kopie im Scratchpad, Repo und Live-System unberührt): Der Code aller Pakete wurde so angewendet, wie die Tasks ihn beschreiben, und die Tests aus diesem Plan liefen dagegen: 292 Tests aus P1–P5 plus die alten 22 grün (dreimal hintereinander, keine Wackler), P6 mit 13 Tests grün, `pip-compile` mit Hashes und `pip install --dry-run --require-hashes` ohne Fehler, `scripts/vendor_assets.py` bestätigt beide npm-Integrity-Werte. Das Image baute mit Digest und `--require-hashes`; der Probe-Container lief als UID 1000, `missingarr.db` mit `600`, curl-Ablauf 302/201/200, fremder `Origin` 403, ohne Sitzung 401, `docker stop` mit offenem Log-Stream in 0 s mit „Shutdown complete“. In headless Chromium luden `/`, `/instances`, `/history`, `/logs`, `/searched`, `/instances/1/edit`, `/help` ohne JavaScript-Fehler, auch mit dem Instanznamen `Daniel's "Sonarr" <4K>`. Dabei gefundene Planfehler sind eingearbeitet: `httpx2` statt `httpx` (Starlette 1.6), Startstatus `starting` und Log-vor-Status (Wettlauf in den Scheduler-Tests), Migrationsfehler-Test mit `PRIMARY KEY`, Hauspflege-Test mit offenem Item, `docker top -o pid,uid,cmd`.
