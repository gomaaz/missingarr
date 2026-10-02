# Geprüfte Suche (missingarr 0.9.0) — Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** missingarr 0.9.0 sucht mit „Checked search“ nicht mehr per Such-Befehl, sondern holt die Suchergebnisse selbst, prüft jeden angenommenen Treffer mit dem Vorfilter (Radarr V6, Sonarr S1–S4) und lädt nur den ersten sauberen, mit festgelegtem Ziel (`shouldOverride`); ein Probelauf zeigt die Entscheidungen vorher auf der Seite „Pre-filter“ und als CSV. Dazu (Spec-Nachtrag „Profiländerungen erkennen“) merkt sich missingarr zu jedem gesuchten Titel einen Fingerabdruck seines Qualitätsprofils: Ändert sich das Profil, darf der Titel wieder gesucht werden, und der Probelauf prüft ihn erneut.

**Architecture:** Neues Paket `backend/checked_search/` mit reinen Regeln (Normalisieren, Einstellungen, Radarr- und Sonarr-Regeln, Profil-Fingerabdruck, ohne Netz und Datenbank) und einem Runner, der pro Titel über die HTTP-Methoden des Agenten sucht, prüft und lädt. Die beiden Such-Skills lesen zu Laufbeginn die Profile (`backend/skills/profiles.py`) und rufen bei `checked_search != 'off'` den Runner statt des Befehls auf. Ein neues Protokoll `checked_search_log` hält jede Entscheidung; Verlauf und Such-Cache bekommen die Status `grabbed` und `no_hit`, die ohne Befehlsprüfung sofort als erledigt gelten; Such-Cache und Protokoll tragen den Profil-Fingerabdruck. Fünf Pakete mit fester Dateizuständigkeit laufen in drei Wellen (Welle 1: G1 Regeln ‖ G2 Daten; Welle 2: G3 Runner ‖ G4 Modell/API; Welle 3: G5 Oberfläche), danach Task Z (Version, README, Probe, Review).

**Tech Stack:** Python 3.12, FastAPI 0.141 / Starlette 1.6, Pydantic 2.13, APScheduler 3.11, SQLite 3.45 (stdlib `sqlite3`), `requests`, Jinja2, Alpine.js 3.14.1, htmx 2.0.4. Tests: `pytest`, `fastapi.testclient` (`httpx2`), Node.js v24 für zwei JavaScript-Tests (G5).

## Global Constraints

- Gearbeitet wird auf Branch `feat/checked-search` (von `main` 5693201 = live 0.8.0, plus Spec-Commit 73410b4, Spec-Nachtrag e5712cf „Profiländerungen erkennen“ und 9631d35, das nur ältere Pläne bereinigt). Ausgangsstand für alle Zählungen ist 9631d35 (Testzahlen wie e5712cf). Die überarbeitete Spec liegt noch uncommittet im Arbeitsbaum; sie kommt mit Daniels Freigabe vor Welle 1 als eigener `docs:`-Commit auf den Branch (die Worktrees zweigen vom Branch ab und sähen sie sonst nicht). Am Ende steht `0.9.0` in `VERSION`.
- Spec ist verbindlich: `docs/superpowers/specs/2026-10-01-checked-search-design.md`, einschließlich des Abschnitts „Profiländerungen erkennen (Nachtrag)“.
- **Diese Plandatei wird nicht committet.** Sie enthält Host-Pfade, Live-Einstellungen und Hinweise auf den Messkorpus und bleibt untracked im Arbeitsbaum (`git status --short docs/superpowers/plans/2026-10-01-checked-search.md` → `??`, geprüft in Task Z vor dem Push). Kein `git add docs/`, kein `git add -A`.
- Die Live-Datenbanken (Datenordner des Live-Stacks und `<repo>/data`), der laufende Container `missingarr`, Radarr, Sonarr, Prowlarr und Seerr werden **nie** geöffnet, gelesen, abgefragt oder verändert. Jede Probe läuft gegen eine Scratch-Datenbank und ein nachgebildetes *arr auf `127.0.0.1`.
- Jeder Test benutzt eine eigene SQLite-Datei unter `tmp_path`. Kein Test importiert `backend.main` auf Modulebene (nur in einer Fixture nach dem Umbiegen von `settings.database_url`). In Tests nie `monkeypatch.undo()` aufrufen.
- Tests nur in neuen Dateien `tests/test_g<paket>_*.py` bzw. `tests/test_z_*.py`, jede Datei mit eigenen Fixtures, keine `conftest.py`. Vorhandene Testdateien bleiben unverändert — **einzige Ausnahme**: Task Z ändert in `tests/test_p6_docs.py` die Versionsprüfung von `0.8.0` auf `0.9.0` (der Test pinnt die Version; er muss mitgehen).
- Testbefehl für alles: `cd <repo> && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`. Vor Beginn (9631d35): `353 passed`.
- **Worktrees in Welle 1 und 2.** Zu Beginn jeder Welle im Haupt-Arbeitsbaum, für jedes Paket `<p>` der Welle (Welle 1: `g1`, `g2`; Welle 2: `g3`, `g4`):
  `git -C <repo> worktree add <repo>-wt/<p> -b feat/checked-search-<p> feat/checked-search`
  Im Worktree heißt jedes `cd <repo>` in den Tasks `cd <repo>-wt/<p>`, und jedes `.venv/bin/…` heißt `<repo>/.venv/bin/…` (eine gemeinsame `.venv`, niemand installiert etwas). Commits nur im eigenen Worktree.
- **Wellen-Abnahme.** Am Ende einer Welle im Haupt-Arbeitsbaum `<repo>` (Branch `feat/checked-search`) die Paketzweige nacheinander mergen (`git merge --no-ff feat/checked-search-<p>`; wegen der Dateizuständigkeit ohne Konflikte), dann die Gesamtsuite. Erst wenn sie grün ist: `git worktree remove <repo>-wt/<p>` und `git branch -d feat/checked-search-<p>`. Welle 3 (G5) und Task Z laufen direkt im Haupt-Arbeitsbaum.
- **Scratchpad.** Befehle mit Wegwerf-Dateien setzen am Anfang jedes Codeblocks `SCRATCH=<scratchpad>` und prüfen `test -d "$SCRATCH"`. Wer den Plan ausführt, ersetzt `<scratchpad>` durch den absoluten Pfad des eigenen Scratchpads.
- Dateien schreiben nur mit dem Write- bzw. Edit-Werkzeug, nie per Heredoc (`cat <<EOF`), auch nicht im Scratchpad.
- Das Repo ist öffentlich. In Repo-Dateien stehen keine Host-Details (Hostnamen, Host-Pfade, IPs außer `127.0.0.1`, Benutzernamen) und keine Daten aus Daniels Bibliothek. Als Testdaten erlaubt sind **nur** die öffentlichen Beispiele der Spec („The Thing“, „Halloween“, „John Carter“, „Noah“, „Bāhubali 2“, „The Guest“, „A Better Place“, Release-Namen nur so weit, wie die Spec sie nennt) und erfundene Titel („Example Film“, „Some Film“, „Other Show“, Gruppe `-GRP`). Keine weiteren echten Film- oder Serientitel, keine Release-Gruppen aus dem Messmaterial. Den Pfad zum Messkorpus bekommt nur der lokale Test über die Umgebungsvariable `MISSINGARR_VORFILTER_KORPUS` (der Vorgabe-Ordner mit `korpus/` und `referenz/` oder sein Unterordner `korpus/`; Runde 4 liest ihre Metadaten aus `referenz/r4/` oder aus `MISSINGARR_VORFILTER_R4_META`).
- **Nie geloggt oder gespeichert:** `downloadUrl`, `infoUrl`, `magnetUrl`, `guid` (außer im Speicher für den einen `POST /api/v3/release`), Header, `apikey`. Der Runner übernimmt aus einem Treffer nur Titel, Indexer, Indexer-ID, Punkte, Größe, Qualitätsname, `movieTitles`, `publishDate`, `fullSeason` und nur im Speicher (nicht in `repr`, nie gespeichert) `guid` sowie die Zuordnung (`mappedMovieId`, `mappedSeriesId`, Folgen-IDs aus `mappedEpisodeInfo`), `quality` und `languages` für den POST.
- Modi wörtlich: `checked_search` ∈ `off`, `dry_run`, `active`. Protokoll-Ergebnisse wörtlich: `grabbed`, `would_grab`, `no_clean_hit`, `no_results`, `error`, `grab_failed`, `grab_uncertain`. Neue Item-Status: `grabbed`, `no_hit`.
- Voreinstellungen und Grenzen wörtlich aus der Spec: Release search timeout 120 s (10–600), Time budget per run 25 min (1–1440), Dry run: max releases checked 100 (1–1000), Search again if still missing after (days) 7 (1–365; Nachtrag Daniel 01.10.2026); Radarr: Year tolerance 1, Count release dates as years an, Veto when /parse names another movie an, Prefix match an, Prefix minimum length 6, Word match an, Word match minimum core words 2, Releases without year need an exact title an, Skip the release of the existing file an; Sonarr: Veto when /parse names another series an, Check country/year suffix an, Country codes `AU, US, UK, GB, DE, CO, CA, NZ, FR, ES, IT, NL, SE, DK, NO, JP, KR, MX, BR, AR, IN`, Suffix year tolerance 1, Reject … days before air date 365, Note … days before air date 14. Grenzen ohne Vorgabe in der Spec legt dieser Plan fest (Year tolerance 0–10, Prefix minimum length 1–50, core words 1–10, Suffix year tolerance 0–10, Tage 0–36500). „Search again after profile changes“: an (Spalte `instances.search_again_after_profile_change`, **nicht** in `checked_search_settings`, weil sie auch für den alten Suchweg gilt).
- Profil-Fingerabdruck nach Spec-Nachtrag in der Fassung vom 01.10.2026 (Daniel): SHA-256 über kanonisches JSON (`sort_keys=True`) einer **Auswahl der bewertungsrelevanten Felder** aus Profil, allen Custom Formats und allen Release-Profilen (ohne Namen, Beschriftungen, Hilfetexte, Auswahllisten; ungeordnete Mengen sortiert, die Rangfolge der Qualitäten bleibt), die ersten 16 Hex-Zeichen. Endpunkte `GET /api/v3/qualityprofile`, `/api/v3/customformat`, `/api/v3/releaseprofile` (Radarr 6.4 und Sonarr 4.0 haben alle drei; keine Indexer-Last). Nachtrag 02.10.2026: dazu die globalen Größengrenzen `GET /api/v3/qualitydefinition` (`minSize`/`maxSize` je Qualitäts-ID) und `GET /api/v3/config/indexer` (`minimumAge`, `maximumSize`, `retention`, bei Radarr `allowHardcodedSubs`/`whitelistedHardcodedSubs`); sie fließen in den Fingerabdruck jedes Profils ein (Auswahl und Auslassungen in G1.6).
- API-Pfade wörtlich: `GET /api/checked-search` (Liste, Header `X-Total-Count`), `GET /api/checked-search.csv`, `POST /api/instances/<id>/checked-search/reset-dry-run`.
- Oberflächentexte und Log-Meldungen englisch wie im Bestand. Die History-Plaketten bleiben deutsch und bekommen „geladen“ (`grabbed`) und „kein sauberer Treffer“ (`no_hit`). Der Menüpunkt, den die Spec „Vorfilter“ nennt, heißt in der englischen Oberfläche **„Pre-filter“** (Pfad `/checked-search`).
- Zeitstempel in der DB sind Ortszeit. Neu: `checked_search_log.created_at` und `instances.dry_run_round_started_at` tragen Millisekunden (`strftime('%Y-%m-%d %H:%M:%f','now','localtime')`, Anzeige und Reihenfolge). Die Probelauf-Runde selbst ist ein **Zähler** (`instances.dry_run_round`, je Protokollzeile `dry_run_round`), kein Zeitvergleich: So landet die Arbeit eines Laufs, der vor einem Reset begann, nicht in der neuen Runde, und die Zeitumstellung stört nicht.
- Bestehende Instanzen bekommen `checked_search='off'`: ohne Umschalten verhält sich 0.9.0 wie 0.8.0. Die Live-Werte (Sonarr `missing_mode=episode`, `missing_per_run=4`, `rate_cap=300`; Radarr `missing_per_run=600`, `rate_cap=999999999`) bleiben gültig.
- Kein `sortKey`/`sortDirection` an Sonarr/Radarr senden (8a1a912, c68178e).
- Commit-Nachrichten englisch im Stil des Repos (`feat: …`, `fix: …`, `test: …`, `docs: …`), Trailer nach der Vorgabe der ausführenden Sitzung.
- Nichts wird getaggt, nichts nach `main` gemergt. Push des Branches nur nach Daniels Freigabe (Task Z). Ein Tag `v*` würde über `.github/workflows/docker-publish.yml` ein Image veröffentlichen.

---

## Wellen und Dateizuständigkeit

| Schritt | Paket | Dateien (nur dieses Paket ändert sie) |
|---|---|---|
| Welle 1 | **G1 Regeln** | neu `backend/checked_search/__init__.py`, `normalize.py`, `verdict.py`, `settings.py`, `radarr_rules.py`, `sonarr_rules.py`, `fingerprint.py`; `tests/test_g1_*.py` |
| Welle 1 | **G2 Daten** | `backend/database.py`, `backend/verification.py`, `backend/db/__init__.py`, neu `backend/db/checked_search_log.py`, `backend/db/history.py`, `backend/db/instances.py`, `backend/db/searched.py`, `backend/skills/verify_commands.py`; `tests/test_g2_*.py` |
| Welle 2 | **G3 Runner** | neu `backend/checked_search/runner.py`, neu `backend/skills/profiles.py`, `backend/skills/search_missing.py`, `backend/skills/search_upgrades.py`, `backend/skills/base.py`, `backend/agents/base.py`; `tests/test_g3_*.py` |
| Welle 2 | **G4 Modell und API** | `backend/models/instance.py`, `backend/api/instances.py`, neu `backend/api/checked_search.py`, `backend/main.py` (nur Router einbinden); `tests/test_g4_*.py` |
| Welle 3 | **G5 Oberfläche** | `backend/tooltips.py`, `backend/main.py` (Formular-Kontext, Seite), neu `templates/checked_search.html`, `templates/base.html`, `templates/instances/form.html`, `templates/instances/card.html`, `templates/history.html`, `templates/help.html`; `tests/test_g5_*.py` |
| danach, allein | **Task Z** | `VERSION`, `README.md`, `tests/test_p6_docs.py` (eine Zeile), neu `tests/test_z_release.py`; Probe, Review, Push nach Freigabe |

Welle 2 startet erst, wenn G1 und G2 gemergt sind und die Gesamtsuite auf `feat/checked-search` grün ist; Welle 3 genauso nach Welle 2.

**Abweichungen von der vorgeschlagenen Aufteilung und warum:**

1. **„Modell/Einstellungen“ ist kein eigenes Paket in Welle 1.** Das Pydantic-Modell der Einstellungen (`CheckedSearchSettings`) ist die Eingabe der Regeln; G1 braucht es für seine eigenen Tests. Läge es in einem dritten parallelen Paket, sähe G1 es erst nach dem Merge. Lösung: **G1 besitzt `settings.py`** (rein, ohne DB). Das Instanzmodell (`backend/models/instance.py`) bettet `CheckedSearchSettings` ein und kommt deshalb in **Welle 2 (G4)**. So gibt es genau eine Quelle für Voreinstellungen und Grenzen.
2. **`backend/db/instances.py` gehört zu G2 (Daten), nicht zum Modell.** Der Runner (G3) und die API (G4) brauchen in Welle 2 beide schon Instanzen mit `checked_search`, und ihre Tests legen sie über `db.instances.create` an. `db/instances.py` speichert die Einstellungen als JSON, ohne das Pydantic-Modell zu importieren (Prüfung erst in G4 bzw. beim Lesen mit `from_stored`).
3. **`backend/skills/verify_commands.py` gehört zu G2.** Es braucht nur `count_verified` aus `verification.py` und `purge_old` aus dem Protokoll (beides G2). Die Befehlsprüfung überspringt `grabbed`/`no_hit` von selbst, weil `get_pending_items` nur `submitted` liest; G2 belegt das mit einem Test.
4. **`backend/agents/base.py` gehört zu G3.** Die einzige Änderung (eigener `timeout` für `http_get`/`http_post`) braucht nur der Runner.
5. **`backend/main.py` ändern zwei Pakete, aber in verschiedenen Wellen:** G4 bindet den Router ein (zwei Zeilen), G5 ergänzt Formular-Kontext und Seite. Nie parallel.
6. **`backend/tooltips.py` gehört zu G5** wie im letzten Plan (Texte der Oberfläche).
7. **Runde 4 als zweiter Abgleich** läuft mit ihren eigenen Metadaten, die seit 01.10.2026 unter `referenz/r4/` im Vorgabe-Ordner liegen (siehe G1.5).
8. **Profiländerungen (Spec-Nachtrag) verteilen sich auf alle Pakete:** G1 die reine Funktion `fingerprint` (`backend/checked_search/fingerprint.py`, ohne Netz); G2 alle Spalten, `db/searched.py` (Fingerabdruck schreiben, `lookup_many` mit Fingerabdruck-Vergleich), `record_submission`/`record_checked`, `dry_run_keys` mit Fingerabdrücken, `store_profile_fingerprints`; G3 der Abruf zu Laufbeginn (`backend/skills/profiles.py`, neu) und die Zuordnung Titel → Profil in beiden Skills; G4 die Einstellung in Modell und API; G5 Formular-Kästchen, Tooltip und Plakette „profile changed“.

---

## Spec → Paket → Task

| Spec-Punkt | Umsetzung | Task |
|---|---|---|
| Ablauf pro Titel 1–5 (Daten, `/release` mit eigener Wartezeit, nur `approved`, Zuordnung zum Titel, `/parse`, Laden per `POST /release` mit `shouldOverride`, Protokoll) | `runner._TitleCheck.load/search/mapped_here/verdict/grab/run` | G3.1 |
| Ziel-Festlegung beim Laden (Spec Ablauf 4, Codex K1): Treffer nur, wenn `GET /release` ihn genau diesem Titel zugeordnet hat; POST mit `shouldOverride`, `movieId` bzw. `seriesId`/`episodeIds`, `quality`, `languages` wie gemeldet | `_Release.mapped_*`, `quality_raw`, `languages_raw`, `REASON_TARGET`, `_TitleCheck.grab` | G1.1, G3.1 |
| Fehler: `/movie`, `/episode`, `/series` → „Fehler“, aktiv Verlaufseintrag `failed` ohne Cache (Codex K4); `/release` → `failed`; `/parse` → Kandidat Urteil `error` mit „parse error“ (nie geladen, keine Regel-Ablehnung), besteht keiner → „Fehler“, `failed` ohne Cache (Codex-Prüfung 0.9.0); kein sauberer Treffer oder keine Treffer, während der Health-Check von \*arr einen Indexer, den die Suche für den Titel fragt („Interactive Search“ an, ohne Tags oder mit einem Tag des Films bzw. der Serie), als gestört nennt (oder Health/Indexer-Liste nicht lesbar) → „Fehler“, `failed` ohne Cache (Codex-Prüfung 0.9.0, Indexer-Ausfall); `POST` abgelehnt → `grab_failed`, `failed` ohne Cache; `POST` unklar → `grab_uncertain`, `failed` **mit** Cache (Codex K2); kein zweiter Kandidat; Fehler zählen nicht zur Probelauf-Runde („Titel nicht gemerkt“) | `runner._TitleCheck.run`, `_TitleCheck.indexer_failure`, `_refused`, `run_checked`, `record_checked(cache=)`, `dry_run_keys` (`outcome != 'error'`) | G2.2, G2.3, G3.1 |
| Grab gespeichert gescheitert → im Speicher halten, sperrt den Titel, nächster Lauf speichert nach (Codex K2) | `skills.base.UnsavedCheckedGrab`, `store_unsaved_submissions`, `unsaved_cache_keys` | G3.1 |
| Zeitbudget ab Start des Laufs (Codex K9), Abbruch zwischen Titeln, nach der Release-Suche, vor jedem `/parse` und vor dem `POST` | `run_checked(clock=…, started=…)`, `_Stopped` | G3.1 |
| Rate-Limit: eine Aktion pro Titel | `agent.reserve_action()` je Titel; Rückgabe nur, wenn keine Indexer-Suche lief (`load` gescheitert, `/release` nachweislich nicht gesendet oder 3xx/4xx; Codex K5) | G3.1 |
| Indexer-Schalter: weicht „Automatic Search“ von „Interactive Search“ ab oder ist die Liste nicht lesbar → Lauf setzt aus, Status `success` mit Hinweis, Warnung im Aktivitätslog, `last_sync` bleibt (Entscheidungen Daniel 01.10.2026); geprüft in jedem geprüften Lauf vor dem Sammeln, auch wenn danach nichts zu suchen ist (Codex-Runde 3, G4) | `runner.indexer_pause`, `SubmitOutcome.paused`, `finish_search_run`, beide Skills vor dem Sammeln | G3.1, G3.2 |
| Sonarr: Treffer mit mehr als einer Folge in `mappedEpisodeInfo` → „multi-episode release“, Grab nennt genau die gesuchte Folge (Entscheidung Daniel 01.10.2026, Codex-Runde 2 F4) | `REASON_MULTI_EPISODE`, `_TitleCheck.verdict/grab` | G1.1, G3.1 |
| Erneut suchen, wenn noch fehlend nach N Tagen (Entscheidung Daniel 01.10.2026), unabhängig von `retry_hours` auch in Cache-Abfrage und Hauspflege (Codex-Runde 2 F1) | `CheckedSearchSettings.search_again_after_days`, `searched_items.grabbed_at`, `lookup_many(grab_release_days=)`, `purge_expired(keep_grab_days=)`, beide Skills (nur Wanted-Listen) | G1.2, G2.1, G2.3, G2.5, G3.1, G3.2, G5.1 |
| Gescheiterter Befehl gibt nur den eigenen Cache-Eintrag frei, nie die Sperre eines neueren Grabs (Codex-Runde 2 F3), auch nicht nach dem Leeren oder Kürzen des Verlaufs (Codex-Runde 3, G1: der Eintrag nennt das Item, das ihn geschrieben hat) | `searched_items.history_item_id`, `_UPSERT_SEARCHED`, `db.history.resolve_item` | G2.1, G2.3 |
| Hauspflege löscht nie Zeilen der laufenden Probelauf-Runde (Entscheidung Daniel 01.10.2026, Codex-Runde 2 F6) | `checked_search_log.purge_old` | G2.2 |
| Sonarr-Upgrades: alter Staffel-Schlüssel sperrt im geprüften Modus, Folgen-Schlüssel auf dem Befehlsweg (Spec „Das Update allein gibt nichts frei“, Codex-Runde 2 F5) | `SearchUpgradesSkill._keep_uncached` | G3.2 |
| Sonarr-Upgrades auf dem Befehlsweg (Off): Die Staffelsuche wartet, solange eine Folge der Staffel nach einem Grab der geprüften Suche gesperrt ist (`grabbed`/`grab_uncertain`, „Search again if still missing after (days)“); keine Einzelbefehle als Ersatz (Codex-Runde 3, G3, Entscheidung Daniel 02.10.2026) | Staffel-Halter `upg:sea-hold:<serie>:<staffel>` (`record_checked(hold_key=)`, `CheckedTask.hold_key`, `UnsavedCheckedGrab.hold_key`), `SearchUpgradesSkill._hold_key/_keep_uncached` | G2.3, G3.1, G3.2 |
| Ein gescheitertes erstes Speichern der Grundlinie gibt nichts frei (Codex-Runde 3, G2) | `profiles.refresh` (Grundlinie dieses Laufs = gelesener Stand) | G3.1 |
| Probelauf-Runde als Zähler, gilt auch beim Force Run, Reset während eines Laufs (Codex K8, Entscheidung Daniel) | `instances.dry_run_round`, `checked_search_log.dry_run_round`, `dry_run_keys(…, dry_run_round)`, `reset_dry_run`, `run_checked(config=)` | G2.1, G2.2, G2.4, G3.1, G3.2 |
| Regel-Einstellungen geändert → Probelauf prüft neu, Plakette „settings changed“ (Entscheidung Daniel) | `CheckedSearchSettings.rules_fingerprint`, `checked_search_log.settings_fingerprint`, `ProfileState.round_blocks`, API `settings_changed` | G1.2, G2.1, G2.2, G3.1, G4.2, G5.2 |
| Profiländerungen: Fingerabdruck (SHA-256 über die bewertungsrelevante Auswahl, 16 Hex; Codex K6, Entscheidung Daniel) | `fingerprint.fingerprint/fingerprints/changes` | G1.6 |
| Profiländerungen: Größengrenzen (`/qualitydefinition`) und Indexer-Einstellungen (`/config/indexer`) im Fingerabdruck jedes Profils, Abruf mit den anderen, Fehler gibt nichts frei (Nachtrag 02.10.2026) | `fingerprint._quality_definitions/_indexer_config`, `profiles.PROFILE_PATHS` | G1.6, G3.1 |
| Profiländerungen: Abruf zu Laufbeginn in beiden Skills und beiden Suchwegen, Zuordnung Titel → Profil (Radarr `qualityProfileId`, Sonarr über die Serienliste, sonst die einzelne Serie; geprüfte Suche aus dem geladenen Film bzw. der Serie, Codex K3), Abruf-Fehler → gespeicherter Stand, nichts neu freigegeben, Aktivitätslog je geändertem Profil | `skills/profiles.refresh`, `ProfileState.stored_fingerprint`, `_TitleCheck.fingerprint` | G3.1, G3.2 |
| Profiländerungen: `instances.profile_fingerprints`, `profile_fingerprints_baseline` (einmal gesetzt), `searched_items.profile_fingerprint`, `checked_search_log.profile_fingerprint` | `database.py`, `db.instances.store_profile_fingerprints` | G2.1, G2.4 |
| Profiländerungen: Cache sperrt nur mit aktuellem Fingerabdruck, Alt-Einträge über die Grundlinie, beide Suchwege schreiben den Fingerabdruck, ein vorhandener wird nie durch `NULL` ersetzt (Codex K3) | `db.searched.lookup_many(fingerprints=…)`, `add`, `record_submission`, `record_checked`, `_UPSERT_SEARCHED` (`COALESCE`), `submit_candidates(fingerprint_of=…)` | G2.3, G2.5, G3.1 |
| Profiländerungen: Probelauf prüft nach Änderung erneut | `dry_run_keys` liefert Fingerabdrücke je Titel | G2.2, G3.1 |
| Profiländerungen: Einstellung „Search again after profile changes“ (an), Info-Symbol | Spalte `search_again_after_profile_change`, Modell, Formular | G2.1, G2.4, G4.1, G5.1 |
| Profiländerungen: Plakette „profile changed“, verglichen mit dem Profil der Zeile (Codex-Runde 2 F7); auch wenn das letzte Profil gelöscht wurde (Codex-Runde 3, G6) | `checked_search_log.profile_id`, `query` → `profile_changed` (bekannt = Grundlinie gesetzt), Runner schreibt `profile_id`, Seite | G2.1, G2.2, G3.1, G5.2 |
| Profiländerungen: die sieben Tests des Nachtrags | `test_g1_fingerprint.py`, `test_g2_searched.py`, `test_g2_log.py`, `test_g3_runner.py` | G1.6, G2.2, G2.5, G3.1 |
| Regeln Radarr a, b, c, d, 1a mit Einstellungen | `radarr_rules.evaluate` | G1.3 |
| Normalisieren wie die Referenz V6 (Spec-Wortlaut seit 01.10.2026 an die Referenz angepasst: zwei Schreibweisen für exakt/Präfix, eine für Wörter) | `normalize.compact/variants/tokens` | G1.1 |
| Messung Runde 5 exakt (api-echt), Runde 4 als zweiter Abgleich (66 → 62, 0 fremd) | `tests/test_g1_corpus.py` | G1.5 |
| Regeln Sonarr S1–S4, im Probelauf alle greifenden Regeln | `sonarr_rules.evaluate` (sammelt immer alle) | G1.4 |
| Einstellungen pro Instanz, JSON-Spalte, Voreinstellungen, fehlende Schlüssel | `settings.CheckedSearchSettings` (+ `from_stored`), `instances.checked_search_settings` | G1.2, G2.4 |
| Einstellungen in der Oberfläche mit Info-Symbol, nur Regeln der App | Formularabschnitt „Checked search“, `TOOLTIPS["cs_<feld>"]` | G5.1 |
| Sonarr-Modussperre: Formular + 422 im Server | `checked_mode_conflict`, `packsLocked()` | G4.1, G4.2, G5.1 |
| Daten: drei Spalten, Tabelle `checked_search_log`, Indizes, Migration per `PRAGMA table_info` | `database.py` | G2.1 |
| Aufbewahrung wie History | `checked_search_log.purge_old` in der Hauspflege | G2.3 |
| Probelauf: kein Verlaufs-/Cache-Eintrag, Lauf `success`/`triggered_count` 0, Aktivitätslog, übergeht Cache, einmal pro Runde (auch beim Force Run), Reset | `run_checked` (`handled`), `dry_run_keys`, `reset_dry_run`, Endpunkt | G2.2, G2.4, G3.1, G4.2 |
| Aktiv geladen: `grabbed` + Cache (mit `grabbed_at`) in einer Transaktion; Befehlsprüfung lässt aus | `db.history.record_checked`, `get_pending_items` unverändert | G2.3 |
| Aktiv ohne sauberen Treffer/ohne Treffer: `no_hit` + Cache | `record_checked(ITEM_NO_HIT)` | G2.3, G3.1 |
| Laufstatus: `grabbed`/`no_hit` erledigt, keine Wartezeit; Fehler vor der Suche → `failed`-Item, gemischter Lauf → `partial` (Codex K4) | `aggregate_run_status`, `count_verified`, `finish_run` | G2.3, G3.1 |
| Upgrades über denselben Runner, Regel 1a/S4 | `search_upgrades` → `run_checked` | G3.2 |
| History-Plaketten „geladen“, „kein sauberer Treffer“ | `history.html` | G5.2 |
| Seite „Vorfilter“ (Pre-filter): Zähler, Tabelle, Filter, „only differences“, aufklappbar, Reset (danach Liste neu, Probelauf-Zähler der Instanz weg; Codex-Prüfung 0.9.0); Filter „current round only“ (voreingestellt an), Zähler der laufenden Runde; Plaketten „profile changed“, „settings changed“ | `checked_search.html`, `checked_search_log.query/count/summary(current_round=…)` | G2.2, G4.2, G5.2 |
| CSV mit Filter, eine Zeile pro Kandidat, aus einem Datenbankstand (Codex-Runde 3, G5) | `checked_search_log.iter_csv` (eine Lesetransaktion, `fetchmany`), `GET /api/checked-search.csv` | G2.2, G4.2 |
| Karte: Plakette „dry run“/„checked“ | `card.html` | G5.2 |
| Nie gespeichert/geloggt: `downloadUrl`, `guid`, `infoUrl` (dazu `magnetUrl`) | `runner._approved`, Test mit Schlüssel-Suche, auch in den Fehlerpfaden | G3.1 |
| Nicht-Ziel Staffelpakete (Sonarr) | Treffer mit `fullSeason` verworfen („season pack“), ohne `/parse` | G1.1, G3.1 |
| `GET /release` ist eine interaktive Suche (Indexer mit „Interactive Search“) | Lauf setzt bei abweichenden Schaltern aus (siehe oben); Risiken, README, Einführung | G3.1, Task Z |
| Version 0.9.0, README, Release-Notiz | `VERSION`, `README.md`, Abschnitt unten | Task Z |
| Codex-Review am Ende, Sitzungen auf Schlüssel prüfen | Task Z Step 6 (Muster-Suche, keine echten Schlüssel) | Task Z |

---

## Schnittstellenvertrag zwischen den Paketen

Was hier steht, ist verbindlich. Ein Paket darf von anderen Paketen nur diese Namen, Signaturen und Formate benutzen. Weicht ein Paket ab, passt es diesen Abschnitt im selben Commit an und nennt die betroffenen Pakete.

### G1 liefert (Welle 1)

`backend/checked_search/normalize.py`

```python
STOP_WORDS: frozenset[str]          # the a an der die das and und of le la les el il
def compact(text: str | None) -> str                 # klein, ohne Akzente, & → and, nur [a-z0-9]
def variants(text: str | None) -> set[str]           # compact() mit Umlauten als ae/oe/ue/ss und mit nur entferntem
                                                     # Akzent (ß entfällt dort), bei & zusätzlich mit „und“ (Referenz V6)
def tokens(text: str | None) -> list[str]            # Wörter, Umlaute als ae/oe/ue/ss, & → und (eine Schreibweise, Referenz V6)
def same_release(a: str | None, b: str | None) -> bool   # compact(a) == compact(b) != ""
def strip_extension(name: str | None) -> str         # ohne Ordner, ohne Endung (2–4 Zeichen)
def year_of(value) -> int                            # Jahr eines *arr-Datums, sonst 0
def parse_utc(value) -> datetime | None              # ISO (auch mit Z) → aware UTC
```

`backend/checked_search/verdict.py`

```python
REASON_YEAR = "year"; REASON_OTHER_MOVIE = "other movie"; REASON_TITLE = "title"
REASON_TITLE_WITHOUT_YEAR = "title without year"; REASON_EXISTING_FILE = "existing file"
REASON_OTHER_SERIES = "other series"; REASON_YEAR_SUFFIX = "year suffix"
REASON_COUNTRY_SUFFIX = "country suffix"; REASON_TOO_EARLY = "published too early"
REASON_PARSE_ERROR = "parse error"
REASON_SEASON_PACK = "season pack"; REASON_TARGET = "not mapped to this title"
REASON_MULTI_EPISODE = "multi-episode release"   # Sonarr: mappedEpisodeInfo mit mehr als einer Folge

@dataclass(frozen=True)
class Verdict:
    reasons: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    @property
    def ok(self) -> bool: ...          # not reasons
```

`backend/checked_search/settings.py`

```python
CheckedSearchMode = Literal["off", "dry_run", "active"]
CHECKED_SEARCH_MODES: tuple[str, ...] = ("off", "dry_run", "active")
DEFAULT_COUNTRY_CODES: tuple[str, ...]          # 21 Codes, Reihenfolge wie Spec
SETTING_BOUNDS: dict[str, tuple[int, int]]      # alle int-Felder
GENERAL_FIELDS, RADARR_FIELDS, SONARR_FIELDS: tuple[str, ...]   # skip_existing_file steht in RADARR_ und SONARR_FIELDS
FIELD_LABELS: dict[str, str]                    # englische Formular-Beschriftung je Feld

class CheckedSearchSettings(BaseModel):         # extra="ignore"
    release_timeout_seconds: int = 120; time_budget_minutes: int = 25; dry_run_max_releases: int = 100
    search_again_after_days: int = 7                # Nachtrag Daniel 01.10.2026, 1–365, in GENERAL_FIELDS
    year_tolerance: int = 1; count_release_dates: bool = True; veto_other_movie: bool = True
    prefix_match: bool = True; prefix_min_length: int = 6; word_match: bool = True
    word_min_core_words: int = 2; no_year_needs_exact: bool = True; skip_existing_file: bool = True
    veto_other_series: bool = True; check_suffix: bool = True; country_codes: list[str] = [21 Codes]
    suffix_year_tolerance: int = 1; reject_days_before_air: int = 365; note_days_before_air: int = 14
    @classmethod
    def from_stored(cls, value) -> "CheckedSearchSettings"   # dict/JSON/None/Müll; ungültiger Schlüssel → Voreinstellung
    def rules_fingerprint(self, arr_type: str) -> str         # 16 Hex über die Regeln der App + dry_run_max_releases
                                                              # (Listen sortiert); Wartezeit, Budget, Tage zählen nicht
```

Grenzverletzung beim Speichern: `ValueError("<feld> must be between <min> and <max>; …")` (Pydantic-Meldung beginnt mit `Value error, `).

`backend/checked_search/radarr_rules.py`

```python
@dataclass(frozen=True)
class MovieInfo:
    movie_id: int
    names: tuple[str, ...]                  # title, originalTitle, alternateTitles[].title
    year: int = 0
    secondary_year: int = 0
    release_years: tuple[int, ...] = ()     # Jahre von inCinemas, digitalRelease, physicalRelease
    existing_releases: tuple[str, ...] = () # movieFile.sceneName, relativePath/originalFilePath ohne Endung

@dataclass(frozen=True)
class MovieParse:
    titles: tuple[str, ...] = ()            # parsedMovieInfo.movieTitles (sonst movieTitles des Treffers)
    year: int = 0
    movie_id: int | None = None             # movie.id aus /parse

def movie_from_resource(movie: dict) -> MovieInfo                       # GET /api/v3/movie/{id}
def parse_from_resource(parse: dict | None, fallback_titles=()) -> MovieParse   # GET /api/v3/parse?title=
def evaluate(movie: MovieInfo, release_title: str, parse: MovieParse,
             settings: CheckedSearchSettings) -> Verdict              # alle greifenden Gründe, Reihenfolge a, b, d/c, 1a
```

`backend/checked_search/sonarr_rules.py`

```python
@dataclass(frozen=True)
class EpisodeInfo:
    series_id: int
    series_titles: tuple[str, ...]          # series.title + alternateTitles[].title
    series_year: int = 0
    air_date_utc: datetime | None = None
    existing_releases: tuple[str, ...] = () # episodeFile.sceneName, relativePath ohne Endung

@dataclass(frozen=True)
class EpisodeParse:
    series_id: int | None = None            # series.id aus /parse
    series_title: str = ""                  # parsedEpisodeInfo.seriesTitle

def episode_from_resources(episode: dict, series: dict) -> EpisodeInfo  # GET /episode/{id}, GET /series/{id}
def parse_from_resource(parse: dict | None) -> EpisodeParse
def title_suffix(series_title: str, codes) -> tuple[int, str]          # (Jahr, Ländercode) am Ende, sonst (0, "")
def evaluate(info: EpisodeInfo, release_title: str, publish_date: datetime | None,
             parse: EpisodeParse, settings: CheckedSearchSettings) -> Verdict   # immer alle Gründe S1, S2, S3, S4
```

Hinweis-Text S3 wörtlich: `"published <n> days before air date"`.

`backend/checked_search/fingerprint.py` (Spec-Nachtrag, Fassung 01.10.2026: semantische Auswahl)

```python
def fingerprint(profile: dict, custom_formats: list, release_profiles: list,
                quality_definitions: list, indexer_config: dict) -> str   # 16 Hex über die bewertungs-
    # relevanten Felder: Qualitäten in Rangfolge, Cutoff, Punkte, Formatbedingungen, Release-Profil-Begriffe,
    # Größengrenzen je Qualität, minimumAge/maximumSize/retention (Radarr: eingebrannte Untertitel)
def fingerprints(profiles: list, custom_formats: list, release_profiles: list,
                 quality_definitions: list, indexer_config: dict) -> dict[str, str]
    # str(profile id) -> fingerprint; Einträge ohne ganzzahlige id fallen weg; ValueError, wenn nicht
    # vier Listen und ein Objekt
def changes(old: dict, new: dict) -> list[tuple[str, str, str]]   # (profile id, alt, neu), nur in beiden und verschieden
def short(value: str | None) -> str                               # erste 8 Zeichen, "—" ohne Wert
```

### G2 liefert (Welle 1)

`backend/verification.py`

```python
ITEM_GRABBED = "grabbed"
ITEM_NO_HIT = "no_hit"
DONE_STATUSES = frozenset({ITEM_COMPLETED, ITEM_GRABBED, ITEM_NO_HIT})
def aggregate_run_status(item_statuses: list[str]) -> str   # alle erledigt → success, einige → partial
def count_verified(item_statuses: list[str]) -> int          # Anzahl in DONE_STATUSES
```

`backend/db/checked_search_log.py` (in `backend/db/__init__.py` exportiert als `db.checked_search_log`)

```python
MODES = ("dry_run", "active")
OUTCOMES = ("grabbed", "would_grab", "no_clean_hit", "no_results", "error", "grab_failed", "grab_uncertain")
CSV_HEADER = ["time", "instance", "mode", "title", "outcome", "release", "indexer", "score", "size",
              "quality", "verdict", "reasons", "notes", "chosen", "arr_would_grab"]
def insert_with(conn: sqlite3.Connection, entry: dict) -> int
def insert(entry: dict) -> int
    # entry: instance_id, run_id (None erlaubt), mode, skill, arr_id, cache_key, title, outcome,
    #        arr_pick (str|None), pick ({title, indexer, score, size, quality}|None),
    #        candidates (list[dict]), error_message (str|None), profile_fingerprint (str|None),
    #        profile_id (int|None; Profil, unter dem geprüft wurde), dry_run_round (int|None; Runde,
    #        in der der Lauf begann), settings_fingerprint (str|None)
def dry_run_keys(instance_id: int, dry_run_round: int) -> dict[str, set]
    # cache_key -> {(profile_fingerprint, settings_fingerprint)} der dry_run-Zeilen dieser Runde;
    # outcome 'error' zählt nicht
def query(instance_id=None, mode=None, outcome=None, search=None, only_differences=False,
          current_round=False, limit=50, offset=0) -> list[dict]
    # + instance_name, arr_type, candidates (list), rejected_count, profile_changed (bool);
    #   settings_fingerprint roh (settings_changed setzt die API, G4.2)
def count(instance_id=None, mode=None, outcome=None, search=None, only_differences=False,
          current_round=False) -> int
def summary(current_round=False) -> list[dict]   # {instance_id, instance_name, mode, outcome, n}
def purge_old(instance_id: int, days: int) -> int   # 0 bei days <= 0; Probelauf-Zeilen der laufenden
                                                    # Runde bleiben bis zum nächsten Reset (Entscheidung Daniel)
def iter_csv(instance_id=None, mode=None, outcome=None, search=None, only_differences=False,
             current_round=False) -> Iterator[str]
```

`current_round`: Probelauf-Zeilen nur mit `dry_run_round = instances.dry_run_round`; aktive Zeilen immer. `profile_changed`: die Zeile trägt einen Fingerabdruck, die Instanz hat ihre Profile schon einmal gelesen (Grundlinie gesetzt; auch eine leere Profilliste zählt, Codex-Runde 3, G6), und der aktuelle Fingerabdruck ihres Profils (`profile_id`) ist ein anderer (gelöschtes Profil: geändert, auch das letzte); Zeilen ohne `profile_id`: keiner der aktuellen ist der der Zeile. `iter_csv` liest alles aus einer Lesetransaktion (ein Datenbankstand, kein `OFFSET`; Codex-Runde 3, G5).

Kandidat im JSON `candidates` (vom Runner geschrieben): `{"title", "indexer", "score", "size", "quality", "verdict": "pass"|"reject"|"unchecked"|"error", "reasons": [..], "notes": [..], "chosen": bool, "arr_choice": bool}`. `error`: `/parse` scheiterte (Grund `parse error`), zählt nicht zu `rejected_count`.
„only differences“: `arr_pick IS NOT NULL AND (pick IS NULL OR pick != arr_pick)`.

`backend/db/history.py`

```python
def record_checked(run_id: int, instance_id: int, title: str, arr_id: int | None, item_type: str,
                   cache_key: str, status: str, log_entry: dict | None = None,
                   profile_fingerprint: str | None = None, cache: bool | None = None,
                   hold_key: str | None = None) -> int
    # eine Transaktion: Item (command_id NULL, verified_at jetzt) + Cache-Upsert (mit Fingerabdruck und
    # Item-ID) + Protokollzeile (log_entry). cache None: Cache bei grabbed/no_hit, nicht bei failed;
    # cache=True bei failed = Grab mit unklarer Antwort. grabbed_at gesetzt bei grabbed und bei failed mit
    # Cache. hold_key: zweiter Schlüssel, den ein Grab (auch ein unklarer) mit derselben Sperre schreibt
    # (Sonarr-Upgrade: die Staffel für den Befehlsweg, G3.2); bei no_hit/failed ohne Cache nichts.
    # status ∉ {grabbed, no_hit, failed} → ValueError
def record_submission(run_id, instance_id, title, arr_id, item_type, cache_key, command_id,
                      profile_fingerprint: str | None = None) -> int     # neu: letzter Parameter
def finish_run(run_id, wanted_count, triggered_count, status="success", error_message=None)
    # geändert: bei "success" mit Items → aggregate_run_status + verified_count sofort (pending nur mit submitted)
def resolve_item(item_id, status, instance_id, cache_key) -> bool
    # geändert: failed gibt den Cache-Eintrag nur frei, wenn er noch diesem Item gehört
    # (searched_items.history_item_id = item_id); ein Eintrag ohne Item-ID (vor 0.9.0 geschrieben) nur,
    # wenn kein neueres Item desselben Schlüssels (Befehl oder grabbed/no_hit/failed mit Schlüssel) existiert
```

Cache-Upsert überall (`searched.add`, `record_submission`, `record_checked`): `ON CONFLICT … DO UPDATE SET searched_at=…, profile_fingerprint=COALESCE(excluded.profile_fingerprint, searched_items.profile_fingerprint), grabbed_at=excluded.grabbed_at, history_item_id=excluded.history_item_id` (ein vorhandener Fingerabdruck wird nie durch `NULL` ersetzt; `grabbed_at` nur bei einem Grab der geprüften Suche, jede andere Suche leert es; `history_item_id` nennt das Item, das den Eintrag zuletzt geschrieben hat, `searched.add` schreibt `NULL`).

`backend/db/searched.py`

```python
def add(instance_id, cache_key, title, item_type, profile_fingerprint: str | None = None) -> None
def lookup_many(instance_id, keys, retry_hours=0, fingerprints: dict | None = None,
                grab_release_days: int = 0) -> dict[str, datetime]
    # fingerprints: cache_key -> (aktueller, Grundlinien-Fingerabdruck) des Profils des Titels.
    # None: jede Zeile sperrt (wie bisher; Einstellung aus). Sonst sperrt eine Zeile nur, wenn ihr
    # Fingerabdruck (Zeile ohne: der Grundlinien-Wert) dem aktuellen gleicht; aktueller unbekannt → sperrt.
    # grab_release_days > 0: eine Zeile mit grabbed_at sperrt genau so viele Tage, auch wenn retry_hours
    # kürzer ist, danach nicht mehr (nur für Kandidaten aus einer Wanted-Liste übergeben: der Titel
    # fehlt dann noch). Eine Profiländerung gibt sie wie jede Zeile früher frei.
def purge_expired(instance_id, retry_hours, keep_grab_days: int = 0) -> int
    # neu: keep_grab_days; eine Zeile mit grabbed_at jünger als so viele Tage bleibt stehen
```

`backend/db/instances.py`

```python
CHECKED_SEARCH_MODES = ("off", "dry_run", "active")
# create(data): checked_search (sonst/ungültig "off"), checked_search_settings (dict → JSON),
#               dry_run_round_started_at = jetzt bei 'dry_run'
# update(id, data): checked_search / checked_search_settings fehlend oder None → gespeicherte Werte bleiben;
#               Wechsel nach 'dry_run' von einem anderen Modus → neue Runde (dry_run_round + 1, Beginn jetzt)
# create/update: search_again_after_profile_change (create: Standard an; update: fehlend/None → bleibt)
# row_to_dict: checked_search_settings als dict (unlesbar → {}), profile_fingerprints als dict,
#              profile_fingerprints_baseline als dict oder None (noch nie gesetzt); dry_run_round (int)
def reset_dry_run(instance_id: int) -> str | None     # dry_run_round + 1, Beginn jetzt; gibt den Beginn zurück; None = unbekannt
def store_profile_fingerprints(instance_id: int, fingerprints: dict) -> dict | None
    # speichert den aktuellen Stand; die Grundlinie nur beim ersten Mal; gibt die Grundlinie zurück
```

`backend/skills/verify_commands.py`: Hauspflege ruft `db.searched.purge_expired(…, keep_grab_days=_grab_days(config))` („Search again if still missing after (days)“ roh aus `checked_search_settings`, Voreinstellung 7) und zusätzlich `db.checked_search_log.purge_old(instance_id, settings.history_retention_days)`; die Logzeile hängt `"; <n> pre-filter log row(s)"` an, wenn `n > 0`. Run-Urteil mit `count_verified`.

### G3 liefert (Welle 2)

`backend/agents/base.py`

```python
def http_get(self, path: str, params: dict | None = None, timeout: float = 10) -> dict
def http_post(self, path: str, body: dict, timeout: float = 10) -> dict
```

`backend/skills/base.py`: `SubmitOutcome.handled: int = 0` (Titel ohne Fehler, die nicht in `triggered` zählen) und `SubmitOutcome.paused: str = ""` (geprüfter Lauf ausgesetzt → Status `success`, Text vorn im Fehlertext, `last_sync` bleibt stehen; Entscheidung Daniel 01.10.2026). `finish_search_run` rechnet mit `triggered + handled` und liest `last_verified` aus dem gerade beendeten Lauf (`get_latest_run_verification`). `SearchResult.profile_fingerprint: str | None = None`; `submit_candidates(…, fingerprint_of=None)` hängt den Fingerabdruck des Kandidaten an jede angenommene Einreichung (auch an `UnsavedSubmission`). Neu `UnsavedCheckedGrab(run_id, instance_id, title, arr_id, item_type, cache_key, status, log_entry, profile_fingerprint, sent_at, hold_key=None)` in derselben Liste `runtime.unsaved_submissions`; `store_unsaved_submissions` speichert beide Arten (geprüfte per `record_checked(…, cache=True, hold_key=)`), `unsaved_cache_keys` sperrt beide (bei geprüften auch den `hold_key`).

`backend/skills/profiles.py` (neu)

```python
PROFILE_PATHS = ("/api/v3/qualityprofile", "/api/v3/customformat", "/api/v3/releaseprofile",
                 "/api/v3/qualitydefinition", "/api/v3/config/indexer")   # die letzten zwei: Nachtrag 02.10.2026
SERIES_PATH = "/api/v3/series"; PROFILE_TIMEOUT = 60

@dataclass
class ProfileState:
    current: dict; baseline: dict | None; series_profiles: dict; search_again: bool
    settings_fingerprint: str | None          # rules_fingerprint der Instanz (Probelauf-Runde)
    series_loader: Callable | None             # Sonarr bei nicht lesbarer Serienliste: GET /series/{id}
    loaded_series: dict
    def profile_of(self, record: dict) -> int | None     # qualityProfileId, series.qualityProfileId, Serienliste
    def fingerprint(self, profile_id) -> str | None
    def stored_fingerprint(self, record: dict) -> str | None   # wie fingerprint(profile_of(...)), sonst die einzelne Serie
    def expected(self, profile_id) -> tuple[str | None, str | None]   # (aktuell, Grundlinie)
    def cache_filter(self, keyed: list[tuple[str, dict]]) -> dict | None   # für lookup_many; None bei Einstellung aus
    def round_blocks(self, round_keys: dict, key: str, record: dict) -> bool   # Profil und Regel-Einstellungen

def refresh(skill_name: str, agent) -> ProfileState
```

Aktivitätslog bei Änderung wörtlich: `"Quality profile changed: <name> (<alt 8> → <neu 8>)"`. Abruf-Fehler: `"Could not read the quality profiles — the stored fingerprints stay in force: <fehler>"` (warn). Speichern gescheitert: `"Could not store the quality profile fingerprints: <fehler>"` (warn); fehlt die Grundlinie noch, gilt für diesen Lauf der gerade gelesene Stand als Grundlinie (Codex-Runde 3, G2). Serienliste nicht lesbar: `"Could not read the series list — the series of each searched episode is asked instead: <fehler>"` (warn).

`backend/checked_search/runner.py`

```python
MODE_DRY_RUN = "dry_run"; MODE_ACTIVE = "active"
OUTCOME_GRABBED, OUTCOME_WOULD_GRAB, OUTCOME_NO_CLEAN_HIT, OUTCOME_NO_RESULTS, OUTCOME_ERROR, OUTCOME_GRAB_FAILED,
OUTCOME_GRAB_UNCERTAIN
RELEASE_PATH = "/api/v3/release"; PARSE_PATH = "/api/v3/parse"; INDEXER_PATH = "/api/v3/indexer"
HEALTH_PATH = "/api/v3/health"
INDEXER_HEALTH_SOURCES = ("IndexerStatusCheck", "IndexerLongTermStatusCheck")
HEALTH_SETTLE_SECONDS = 6     # *arr wertet Health-Checks bis 5 s nach einer Indexer-Störung neu aus

@dataclass(frozen=True)
class CheckedTask:
    arr_id: int; title: str; item_type: str; cache_key: str; series_id: int | None = None
    profile_fingerprint: str | None = None
    hold_key: str | None = None        # Sonarr-Upgrade: upg:sea-hold:<serie>:<staffel> (G3.2)

@dataclass
class CheckedRunOutcome(SubmitOutcome):
    checked: int = 0; would_grab: int = 0; grabbed: int = 0; no_hit: int = 0
    budget_exhausted: bool = False; notes: list = []

def indexer_pause(agent) -> str        # "" oder der Hinweis „Checked search paused — …“
def run_checked(skill_name: str, agent, run_id: int, tasks: list[CheckedTask], mode: str,
                clock=time.monotonic, *, profiles: ProfileState | None = None, started: float | None = None,
                config: dict | None = None, check_indexers: bool = True) -> CheckedRunOutcome
    # dry_run: handled = checked, triggered = 0; active: triggered = grabbed + no_hit
    # started: clock()-Wert beim Start des Skills (Zeitbudget); config: Instanz-Stand beim Start
    # (Runde, Einstellungen), Vorgabe agent.config; profiles: Fingerabdruck aus dem geladenen Titel;
    # check_indexers=False: der Skill hat die Indexer-Liste schon gelesen (vor dem Sammeln, Codex-Runde 3, G4)
```

Indexer-Störung (Codex-Prüfung 0.9.0): Endet ein Titel ohne Treffer oder ohne sauberen Treffer, wartet der Runner `HEALTH_SETTLE_SECONDS` (`agent.wait_or_stop`, Abbruch → Lauf endet ohne Zeile) und liest `GET /api/v3/health`. Nennt ein Eintrag mit `source` in `INDEXER_HEALTH_SOURCES` einen Indexer, der nicht sicher ungefragt ist (alles außer Indexern mit `enableInteractiveSearch` aus laut `GET /api/v3/indexer`; Meldung ohne Namen = alle), wird der Titel `error` mit `"indexer failure during search — *arr reports: <meldung>"`; Health oder Indexer-Liste nicht lesbar → `error` mit `"could not read the indexer status, the miss may be an indexer failure: <fehler>"`. Sonst `no_results`/`no_clean_hit` wie bisher.

Aktivitätslog am Laufende wörtlich: `"Dry run: <n> title(s) checked, <m> would grab"` bzw. `"Checked search: <g> grabbed, <h> without a clean hit, <f> failed"`. Zeitbudget-Hinweis am Lauf: `"Time budget of <n> min used up — <k> title(s) left for the next run"`. Aussetzen (warn im Aktivitätslog und Hinweis am Lauf, Laufstatus `success`): `"Checked search paused — indexer <name> (automatic search on, interactive search off): …"` bzw. `"Checked search paused — could not read the indexer list: <fehler>"`.

Cache-Schlüssel der geprüften Suche: Radarr fehlend `mov:<id>`, Sonarr fehlend `ep:<id>`, Upgrades `upg:<id>` (Radarr: Film, Sonarr: Folge). Ein Sonarr-Upgrade-Grab schreibt zusätzlich den Staffel-Halter `upg:sea-hold:<serie>:<staffel>` (gleiche Sperre wie der Grab); nur der Befehlsweg fragt ihn ab (Entscheidung Daniel 02.10.2026, G3.2).

Indexer-Schalter: Beide Skills lesen bei `checked_search != 'off'` die Indexer-Liste nach dem Profilstand und **vor** dem Sammeln der Kandidaten (`indexer_pause`), auch wenn danach nichts zu suchen wäre; ausgesetzt → `finish_search_run(…, 0, SubmitOutcome(paused=…))`. Ein Lauf mit „per run“ = 0 prüft nicht (nichts zu tun ist kein geprüfter Lauf).

### G4 liefert (Welle 2)

`backend/models/instance.py`

```python
CHECKED_SEARCH_MODE_MESSAGE = ("Checked search works with single episodes only — set Missing Mode to Episode "
                               "or switch checked search off")
def checked_mode_conflict(arr_type: str, checked_search: str | None, missing_mode: str) -> str | None
# InstanceCreate: checked_search: CheckedSearchMode = "off"; checked_search_settings: CheckedSearchSettings = default
#                 search_again_after_profile_change: bool = True
# InstanceUpdate: checked_search: CheckedSearchMode | None = None; checked_search_settings: CheckedSearchSettings | None = None
#                 search_again_after_profile_change: bool | None = None
```

HTTP (G4):

| Route | Parameter | Antwort |
|---|---|---|
| `POST /api/instances` | wie bisher + `checked_search`, `checked_search_settings` | `201`, Sonarr mit Paket-Modus und Checked search ≠ off → `422` (`detail[0].msg` enthält `CHECKED_SEARCH_MODE_MESSAGE`) |
| `PUT /api/instances/{id}` | Felder weggelassen → gespeicherte Werte bleiben | Sonarr-Konflikt mit gespeichertem Modus → `422 {"detail": CHECKED_SEARCH_MODE_MESSAGE}` |
| alle Instanz-Antworten | — | zusätzlich `checked_search` und `checked_search_settings` **vollständig** (fehlende Schlüssel mit Voreinstellung), `dry_run_round`, `dry_run_round_started_at`, `search_again_after_profile_change` (bool) |
| `POST /api/instances/{id}/checked-search/reset-dry-run` | — | `200 {"status": "reset", "dry_run_round_started_at": "<Ortszeit mit ms>"}` · `404` |
| `DELETE /api/instances/{id}` | — | wie bisher; `409` bei geprüfter Suche mit Zusatz „A checked search waits for *arr's release search …“ |
| `GET /api/checked-search` | `instance_id`, `mode` ∈ dry_run/active, `outcome` ∈ 7 Werte, `q` (≤200), `only_differences` bool, `current_round` bool (false), `limit` 1..200 (50), `offset` ≥0 | Liste (Zeilen von `checked_search_log.query` plus `settings_changed`), Header `X-Total-Count` |
| `GET /api/checked-search.csv` | dieselben Filter ohne `limit`/`offset` | `text/csv; charset=utf-8`, `Content-Disposition: attachment; filename="checked-search.csv"` |
| ungültige Parameter | | `422` |

### G5 liefert (Welle 3)

`backend/main.py`: `checked_search_form() -> dict` (Schlüssel `general`, `radarr`, `sonarr`, `both`, `labels`, `bounds`, `defaults`, `kinds`), Kontext `cs` für `instances/form.html`; Seite `GET /checked-search` mit `instances`, `summary` (laufende Runde). `backend/tooltips.py`: `TOOLTIPS["checked_search"]`, `TOOLTIPS["cs_<feld>"]` für jedes Feld von `CheckedSearchSettings` (auch `cs_search_again_after_days`) und `TOOLTIPS["search_again_after_profile_change"]`.

---

## Schemaänderungen und Migrationen

Alle in G2, `backend/database.py`.

| Änderung | Art | Auf 0.8.0-DB |
|---|---|---|
| `instances.checked_search TEXT NOT NULL DEFAULT 'off' CHECK(checked_search IN ('off','dry_run','active'))` | in `_SCHEMA` und `_COLUMN_MIGRATIONS` | neue Spalte, alle Zeilen `off` |
| `instances.checked_search_settings TEXT NOT NULL DEFAULT '{}'` | ebenso | `{}` (= Voreinstellungen) |
| `instances.dry_run_round_started_at TEXT` (Anzeige) | ebenso | `NULL` |
| `instances.dry_run_round INTEGER NOT NULL DEFAULT 0` (Zähler der Probelauf-Runde) | ebenso | `0` |
| `instances.search_again_after_profile_change INTEGER NOT NULL DEFAULT 1` (Spec-Nachtrag) | ebenso | `1` (an) |
| `instances.profile_fingerprints TEXT NOT NULL DEFAULT '{}'` (Profil-ID → Fingerabdruck) | ebenso | `{}` (noch nichts gelesen) |
| `instances.profile_fingerprints_baseline TEXT` (einmal gesetzt beim ersten erfolgreichen Abruf) | ebenso | `NULL` |
| `searched_items.profile_fingerprint TEXT` | ebenso | `NULL` (= gesucht unter der Grundlinie) |
| `searched_items.grabbed_at TEXT` (Grab der geprüften Suche, „Search again if still missing“) | ebenso | `NULL` |
| `searched_items.history_item_id INTEGER` (Item, das den Eintrag zuletzt geschrieben hat; bewusst ohne Fremdschlüssel, damit das Leeren des Verlaufs ihn nicht auf `NULL` setzt) | ebenso | `NULL` (= vor 0.9.0 geschrieben) |
| Tabelle `checked_search_log` (Spalten wie Spec plus `profile_fingerprint`, `profile_id`, `dry_run_round`, `settings_fingerprint`, `instance_id` FK `ON DELETE CASCADE`, `run_id` FK auf `search_history` `ON DELETE SET NULL`, CHECK auf `mode`, `skill`, `outcome` (7 Werte mit `grab_uncertain`), `created_at` mit ms) | `CREATE TABLE IF NOT EXISTS` in `_SCHEMA` | neu, leer |
| Indizes `idx_cs_log_instance_created (instance_id, created_at)`, `idx_cs_log_instance_mode_key (instance_id, mode, cache_key)` | `CREATE INDEX IF NOT EXISTS` | neu |
| `search_history_items.command_status` neue Werte `grabbed`, `no_hit` | kein CHECK auf der Spalte | nichts zu tun |

Die Pflichtspalten-Prüfung `_assert_schema` deckt die zehn neuen Spalten automatisch ab (sie stehen in `_COLUMN_MIGRATIONS`). `checked_search_log.profile_fingerprint`, `profile_id`, `dry_run_round` und `settings_fingerprint` stehen nur in `CREATE TABLE` (die Tabelle ist neu in 0.9.0; ein Testlauf eines Zwischenstands dieses Branches gilt nicht als Bestand). Der Umbau von `search_history` (`_widen_history_status_check`, nur für Datenbanken vor 0.6.13) läuft mit `foreign_keys=OFF`; die neue Tabelle verweist per Namen auf `search_history` und bleibt danach gültig (Test über die Release-Kette in `test_p3_database.py` bleibt grün, neuer Test mit Tag `v0.8.0` in G2.1).

---

## Paket G1 — Regeln (Welle 1)

Abnahme G1: `tests/test_g1_*.py` grün (`79 passed, 2 skipped` ohne Korpus); mit `MISSINGARR_VORFILTER_KORPUS` grün mit den gemessenen Zahlen (G1.5, `81 passed`); Gesamtsuite im Worktree `432 passed, 2 skipped`; die Regel-Module sind rein:
`grep -n "import requests\|from backend import db\|backend.database" backend/checked_search/*.py` → keine Treffer.

### Task G1.1: Normalisieren und Urteil

**Files:**
- Create: `backend/checked_search/__init__.py`, `backend/checked_search/normalize.py`, `backend/checked_search/verdict.py`
- Test: `tests/test_g1_normalize.py`

**Interfaces:**
- Consumes: nichts
- Produces: `normalize.compact/variants/tokens/same_release/strip_extension/year_of/parse_utc`, `STOP_WORDS`; `verdict.Verdict` und die `REASON_*`-Konstanten (Vertrag G1).

Die Normalisierung ist eine wörtliche Portierung der Referenz-Simulation V6 (`basis.py`: `norm`, `normv`, `tokens`), und die Referenz ist verbindlich (Daniel, 01.10.2026); die Spec beschreibt seit dieser Überarbeitung genau diese Normalisierung. Für den exakten Vergleich und den Präfix zwei Schreibweisen: Umlaute als ae/oe/ue/ss und mit nur entferntem Akzent, wobei `ß` dort ganz wegfällt (`Straße` → `strasse`, `strae`); enthält der Text ein `&`, beide zusätzlich mit „und“ (sonst „and“). Der Wortvergleich kennt nur eine Schreibweise: Umlaute als ae/oe/ue/ss, `&` als „und“. Die Codex-Durchsicht (K7) schlug vor, die frühere Spec-Lesart (auch a/o/u/s und „and“ im Wortvergleich) umzusetzen; nachgemessen hätte das auf Runde 5 (2477 Kandidaten) und Runde 4 (453) kein einziges Urteil geändert, übernommen wird es trotzdem nicht: Es gilt die Referenz.

- [ ] **Step 1: Failing test schreiben**

`tests/test_g1_normalize.py`:

```python
from datetime import datetime, timezone

from backend.checked_search.normalize import (
    compact, parse_utc, same_release, strip_extension, tokens, variants, year_of,
)


def test_compact_keeps_only_letters_and_digits():
    assert compact("The Thing (1982)") == "thething1982"
    assert compact("Foo & Bar") == "fooandbar"
    assert compact("Exémple") == "exemple"
    assert compact(None) == ""


def test_variants_spell_umlauts_both_ways():
    assert variants("Schöne Tage") == {"schonetage", "schoenetage"}


def test_variants_spell_ampersand_as_and_and_und():
    assert variants("Foo & Bar") == {"fooandbar", "fooundbar"}


def test_sharp_s_follows_the_reference():
    # ss in the umlaut spelling, dropped in the plain one (reference V6)
    assert variants("Straße") == {"strasse", "strae"}


def test_variants_drop_empty_spellings():
    assert variants("…") == set()


def test_tokens_split_into_words():
    assert tokens("Example Day: Return") == ["example", "day", "return"]
    assert tokens("Foo & Bar") == ["foo", "und", "bar"]
    assert tokens("Die Schöne") == ["die", "schoene"]


def test_same_release_ignores_separators_and_case():
    assert same_release("Movie.2020.1080p.BluRay-GRP", "movie 2020 1080p bluray grp")
    assert not same_release("Movie.2020.1080p-GRP", "Movie.2020.720p-GRP")
    assert not same_release("", "")


def test_strip_extension_and_directory():
    assert strip_extension("Movie (2020)/Movie.2020.1080p-GRP.mkv") == "Movie.2020.1080p-GRP"
    assert strip_extension(None) == ""


def test_year_of_arr_dates():
    assert year_of("2016-06-23T00:00:00Z") == 2016
    assert year_of(None) == 0
    assert year_of("unknown") == 0


def test_parse_utc():
    assert parse_utc("2026-09-27T18:00:00Z") == datetime(2026, 9, 27, 18, tzinfo=timezone.utc)
    assert parse_utc("nonsense") is None
```

- [ ] **Step 2: Test laufen lassen, er muss scheitern**

Run: `cd <repo> && .venv/bin/python -m pytest tests/test_g1_normalize.py -q -p no:cacheprovider`
Expected: FAIL mit `ModuleNotFoundError: No module named 'backend.checked_search'`

- [ ] **Step 3: Implementieren**

`backend/checked_search/__init__.py`:

```python
"""Checked search: missingarr fetches the search results itself, checks every
approved release with a pre-filter and grabs only the first clean one.

normalize, verdict, settings, radarr_rules and sonarr_rules are pure: no
network, no database. runner.py ties them to an agent.
"""
```

`backend/checked_search/normalize.py`:

```python
"""Title normalisation for the pre-filter.

A byte-for-byte port of the reference simulation the rules were measured
with (V6). Changing anything here changes which releases pass; the corpus
test (tests/test_g1_corpus.py) holds it to the measured numbers.
"""

import re
import unicodedata
from datetime import datetime, timezone

_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"})
_NOT_ALNUM = re.compile(r"[^a-z0-9]+")

# Words that do not count as core words for the word match (rule c).
STOP_WORDS = frozenset({"the", "a", "an", "der", "die", "das", "and", "und", "of", "le", "la", "les", "el", "il"})


def _ascii_lower(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def compact(text: str | None) -> str:
    """Lower case, accents dropped, '&' as 'and', only letters and digits."""
    return _NOT_ALNUM.sub("", _ascii_lower(text or "").replace("&", "and"))


def variants(text: str | None) -> set[str]:
    """Every compact spelling of a title: umlauts as ae/oe/ue/ss and with the
    accent simply dropped, '&' as 'and' and as 'und'. Empty strings are left
    out. Like the reference, the second spelling drops 'ß' entirely."""
    text = text or ""
    folded = text.translate(_UMLAUTS)
    out = {compact(text), compact(folded)}
    if "&" in text:
        out |= {compact(text.replace("&", " und ")), compact(folded.replace("&", " und "))}
    return out - {""}


def tokens(text: str | None) -> list[str]:
    """Words of a title for the word match: umlauts as ae/oe/ue/ss, '&' as
    'und', split at everything that is not a letter or digit."""
    folded = _ascii_lower((text or "").translate(_UMLAUTS)).replace("&", " und ")
    return [word for word in _NOT_ALNUM.split(folded) if word]


def same_release(a: str | None, b: str | None) -> bool:
    """Two release names are the same release when their compact forms match
    (dots, spaces, dashes and case do not matter)."""
    left, right = compact(a), compact(b)
    return bool(left) and left == right


def strip_extension(name: str | None) -> str:
    """File name without directory and without a short extension
    ('Movie.2020.1080p.mkv' -> 'Movie.2020.1080p')."""
    base = re.split(r"[\\/]", name or "")[-1]
    return re.sub(r"\.[A-Za-z0-9]{2,4}$", "", base)


def year_of(value) -> int:
    """Year of an *arr date ('2016-06-23T00:00:00Z'), 0 when there is none."""
    if not value or not isinstance(value, str) or len(value) < 4 or not value[:4].isdigit():
        return 0
    return int(value[:4])


def parse_utc(value) -> datetime | None:
    """*arr timestamp (ISO, mostly with a trailing Z) as aware UTC."""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
```

`backend/checked_search/verdict.py`:

```python
from dataclasses import dataclass

# Reasons a release is rejected. Short English labels: they appear in the
# pre-filter log, on the page and in the CSV.
REASON_YEAR = "year"
REASON_OTHER_MOVIE = "other movie"
REASON_TITLE = "title"
REASON_TITLE_WITHOUT_YEAR = "title without year"
REASON_EXISTING_FILE = "existing file"
REASON_OTHER_SERIES = "other series"
REASON_YEAR_SUFFIX = "year suffix"
REASON_COUNTRY_SUFFIX = "country suffix"
REASON_TOO_EARLY = "published too early"
REASON_PARSE_ERROR = "parse error"
# Not rules of the pre-filter but of the checked search itself (runner.py):
# season packs are a non-goal, and a release GET /release did not map to this
# very title (or that came without quality and languages) is never grabbed —
# the grab names its target from that mapping.
REASON_SEASON_PACK = "season pack"
REASON_TARGET = "not mapped to this title"
# Sonarr: a release mapped to more than one episode. 0.9.0 grabs single
# episodes only; rules and cache would cover the searched episode alone.
REASON_MULTI_EPISODE = "multi-episode release"


@dataclass(frozen=True)
class Verdict:
    """Every rule that rejects the release (reasons) and every remark that
    does not (notes). A release passes when no rule rejects it."""

    reasons: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.reasons
```

- [ ] **Step 4: Test laufen lassen**

Run: `cd <repo> && .venv/bin/python -m pytest tests/test_g1_normalize.py -q -p no:cacheprovider`
Expected: `10 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/checked_search/__init__.py backend/checked_search/normalize.py backend/checked_search/verdict.py tests/test_g1_normalize.py
git commit -m "feat: add title normalisation for the checked-search pre-filter"
```

---

### Task G1.2: Einstellungen pro Instanz

**Files:**
- Create: `backend/checked_search/settings.py`
- Test: `tests/test_g1_settings.py`

**Interfaces:**
- Consumes: nichts
- Produces: `CheckedSearchSettings` (mit `from_stored`), `CHECKED_SEARCH_MODES`, `CheckedSearchMode`, `DEFAULT_COUNTRY_CODES`, `SETTING_BOUNDS`, `GENERAL_FIELDS`, `RADARR_FIELDS`, `SONARR_FIELDS`, `FIELD_LABELS` (Vertrag G1). G4 bettet das Modell in `InstanceCreate`/`InstanceUpdate` ein, G3 liest es mit `from_stored`, G5 baut daraus das Formular.

Grenzen gelten beim Speichern (Modell-Validator wie `_InstanceWrite`), nie beim Lesen: `from_stored` übernimmt jeden gespeicherten Schlüssel einzeln und fällt bei einem ungültigen Wert auf dessen Voreinstellung zurück.

- [ ] **Step 1: Failing test schreiben**

`tests/test_g1_settings.py`:

```python
import json

import pytest
from pydantic import ValidationError

from backend.checked_search.settings import (
    CHECKED_SEARCH_MODES, DEFAULT_COUNTRY_CODES, FIELD_LABELS, GENERAL_FIELDS, RADARR_FIELDS,
    SETTING_BOUNDS, SONARR_FIELDS, CheckedSearchSettings,
)


def test_defaults_match_the_spec():
    s = CheckedSearchSettings()
    assert (s.release_timeout_seconds, s.time_budget_minutes, s.dry_run_max_releases) == (120, 25, 100)
    assert s.search_again_after_days == 7
    assert (s.year_tolerance, s.prefix_min_length, s.word_min_core_words) == (1, 6, 2)
    assert s.count_release_dates and s.veto_other_movie and s.prefix_match and s.word_match
    assert s.no_year_needs_exact and s.skip_existing_file
    assert s.veto_other_series and s.check_suffix
    assert s.country_codes == list(DEFAULT_COUNTRY_CODES)
    assert (s.suffix_year_tolerance, s.reject_days_before_air, s.note_days_before_air) == (1, 365, 14)
    assert CHECKED_SEARCH_MODES == ("off", "dry_run", "active")


def test_every_field_belongs_to_a_group_and_has_a_label():
    grouped = set(GENERAL_FIELDS) | set(RADARR_FIELDS) | set(SONARR_FIELDS)
    assert grouped == set(CheckedSearchSettings.model_fields)
    assert set(FIELD_LABELS) == grouped
    assert set(SETTING_BOUNDS) <= grouped


def test_bounds_are_enforced_on_save():
    with pytest.raises(ValidationError) as info:
        CheckedSearchSettings(release_timeout_seconds=5, time_budget_minutes=2000, search_again_after_days=0)
    message = info.value.errors()[0]["msg"]
    assert "release_timeout_seconds must be between 10 and 600" in message
    assert "time_budget_minutes must be between 1 and 1440" in message
    assert "search_again_after_days must be between 1 and 365" in message


def test_rules_fingerprint_follows_only_what_decides_a_verdict():
    base = CheckedSearchSettings()
    radarr, sonarr = base.rules_fingerprint("radarr"), base.rules_fingerprint("sonarr")
    assert len(radarr) == 16 and radarr != sonarr
    # a rule of the app or the dry-run limit: another fingerprint
    assert CheckedSearchSettings(year_tolerance=2).rules_fingerprint("radarr") != radarr
    assert CheckedSearchSettings(skip_existing_file=False).rules_fingerprint("sonarr") != sonarr
    assert CheckedSearchSettings(dry_run_max_releases=5).rules_fingerprint("sonarr") != sonarr
    # a rule of the other app, timeouts, the budget, the days: the same
    assert CheckedSearchSettings(check_suffix=False).rules_fingerprint("radarr") == radarr
    assert CheckedSearchSettings(release_timeout_seconds=300, time_budget_minutes=5,
                                 search_again_after_days=30).rules_fingerprint("radarr") == radarr
    # the country codes are a set
    assert CheckedSearchSettings(country_codes=["US", "DE"]).rules_fingerprint("sonarr") == \
        CheckedSearchSettings(country_codes=["DE", "US"]).rules_fingerprint("sonarr")


def test_country_codes_accept_text_and_reject_garbage():
    assert CheckedSearchSettings(country_codes="au, us;de  us").country_codes == ["AU", "US", "DE"]
    with pytest.raises(ValidationError):
        CheckedSearchSettings(country_codes=["U.S."])


def test_from_stored_fills_missing_keys_with_defaults():
    s = CheckedSearchSettings.from_stored({"year_tolerance": 2})
    assert s.year_tolerance == 2
    assert s.release_timeout_seconds == 120


def test_from_stored_reads_json_text_and_ignores_unknown_keys():
    s = CheckedSearchSettings.from_stored(json.dumps({"prefix_match": False, "rule_from_the_future": 1}))
    assert s.prefix_match is False


def test_from_stored_never_fails():
    assert CheckedSearchSettings.from_stored("not json") == CheckedSearchSettings()
    assert CheckedSearchSettings.from_stored(None) == CheckedSearchSettings()
    assert CheckedSearchSettings.from_stored([1, 2]) == CheckedSearchSettings()
    # an out-of-bounds value falls back to its default, the rest is kept
    s = CheckedSearchSettings.from_stored({"release_timeout_seconds": 1, "year_tolerance": 3})
    assert (s.release_timeout_seconds, s.year_tolerance) == (120, 3)
```

- [ ] **Step 2: Test laufen lassen, er muss scheitern**

Run: `cd <repo> && .venv/bin/python -m pytest tests/test_g1_settings.py -q -p no:cacheprovider`
Expected: FAIL mit `ModuleNotFoundError: No module named 'backend.checked_search.settings'`

- [ ] **Step 3: Implementieren**

`backend/checked_search/settings.py`:

```python
"""Per-instance settings of the checked search.

Stored as one JSON column (instances.checked_search_settings). Keys that are
missing get their default, so a later rule can add a setting without a
migration. Bounds apply when a user saves; a stored value is never rejected
when it is read (from_stored falls back to the default for that key).
"""

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

CheckedSearchMode = Literal["off", "dry_run", "active"]
CHECKED_SEARCH_MODES: tuple[str, ...] = ("off", "dry_run", "active")

DEFAULT_COUNTRY_CODES: tuple[str, ...] = (
    "AU", "US", "UK", "GB", "DE", "CO", "CA", "NZ", "FR", "ES", "IT",
    "NL", "SE", "DK", "NO", "JP", "KR", "MX", "BR", "AR", "IN",
)

SETTING_BOUNDS: dict[str, tuple[int, int]] = {
    "release_timeout_seconds": (10, 600),
    "time_budget_minutes": (1, 1440),
    "dry_run_max_releases": (1, 1000),
    "search_again_after_days": (1, 365),
    "year_tolerance": (0, 10),
    "prefix_min_length": (1, 50),
    "word_min_core_words": (1, 10),
    "suffix_year_tolerance": (0, 10),
    "reject_days_before_air": (0, 36500),
    "note_days_before_air": (0, 36500),
}

GENERAL_FIELDS: tuple[str, ...] = (
    "release_timeout_seconds", "time_budget_minutes", "dry_run_max_releases", "search_again_after_days",
)
RADARR_FIELDS: tuple[str, ...] = (
    "year_tolerance", "count_release_dates", "veto_other_movie", "prefix_match", "prefix_min_length",
    "word_match", "word_min_core_words", "no_year_needs_exact", "skip_existing_file",
)
SONARR_FIELDS: tuple[str, ...] = (
    "veto_other_series", "check_suffix", "country_codes", "suffix_year_tolerance",
    "reject_days_before_air", "note_days_before_air", "skip_existing_file",
)

# Form labels (English like the rest of the UI). Tooltips: backend/tooltips.py, key "cs_<field>".
FIELD_LABELS: dict[str, str] = {
    "release_timeout_seconds": "Release search timeout (s)",
    "time_budget_minutes": "Time budget per run (min)",
    "dry_run_max_releases": "Dry run: max releases checked",
    "search_again_after_days": "Search again if still missing after (days)",
    "year_tolerance": "Year tolerance",
    "count_release_dates": "Count release dates as years",
    "veto_other_movie": "Veto when /parse names another movie",
    "prefix_match": "Prefix match",
    "prefix_min_length": "Prefix minimum length",
    "word_match": "Word match",
    "word_min_core_words": "Word match minimum core words",
    "no_year_needs_exact": "Releases without year need an exact title",
    "skip_existing_file": "Skip the release of the existing file",
    "veto_other_series": "Veto when /parse names another series",
    "check_suffix": "Check country/year suffix",
    "country_codes": "Country codes",
    "suffix_year_tolerance": "Suffix year tolerance",
    "reject_days_before_air": "Reject releases published this many days before air date",
    "note_days_before_air": "Note releases published this many days before air date",
}

_CODE = re.compile(r"^[A-Z]{2,3}$")


class CheckedSearchSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # General
    release_timeout_seconds: int = 120
    time_budget_minutes: int = 25
    dry_run_max_releases: int = 100
    # A title the checked search grabbed that is still in the wanted list after
    # this many days is searched again (takes the place of *arr's "Redownload
    # Failed from Interactive Search", which is switched off).
    search_again_after_days: int = 7
    # Radarr (V6)
    year_tolerance: int = 1
    count_release_dates: bool = True
    veto_other_movie: bool = True
    prefix_match: bool = True
    prefix_min_length: int = 6
    word_match: bool = True
    word_min_core_words: int = 2
    no_year_needs_exact: bool = True
    # Radarr rule 1a and Sonarr S4
    skip_existing_file: bool = True
    # Sonarr (S1-S3)
    veto_other_series: bool = True
    check_suffix: bool = True
    country_codes: list[str] = Field(default_factory=lambda: list(DEFAULT_COUNTRY_CODES))
    suffix_year_tolerance: int = 1
    reject_days_before_air: int = 365
    note_days_before_air: int = 14

    @field_validator("country_codes", mode="before")
    @classmethod
    def _codes(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            value = re.split(r"[\s,;]+", value)
        if not isinstance(value, (list, tuple)):
            raise ValueError("country_codes must be a list of two- or three-letter codes")
        codes: list[str] = []
        for item in value:
            code = str(item).strip().upper()
            if not code:
                continue
            if not _CODE.match(code):
                raise ValueError(f"country code '{code[:10]}' must be two or three letters")
            if code not in codes:
                codes.append(code)
        return codes

    @model_validator(mode="after")
    def _bounds(self):
        errors = [
            f"{name} must be between {low} and {high}"
            for name, (low, high) in SETTING_BOUNDS.items()
            if not low <= getattr(self, name) <= high
        ]
        if errors:
            raise ValueError("; ".join(errors))
        return self

    @classmethod
    def from_stored(cls, value) -> "CheckedSearchSettings":
        """Tolerant read of what the database holds: a dict, JSON text, None
        or garbage. Unknown keys are ignored; a key whose value no longer
        validates falls back to its default instead of failing the run."""
        if isinstance(value, str):
            try:
                value = json.loads(value) if value.strip() else {}
            except ValueError:
                value = {}
        if not isinstance(value, dict):
            value = {}
        accepted: dict = {}
        for key in cls.model_fields:
            if key not in value:
                continue
            try:
                cls.model_validate({**accepted, key: value[key]})
            except ValidationError:
                continue
            accepted[key] = value[key]
        return cls.model_validate(accepted)

    def rules_fingerprint(self, arr_type: str) -> str:
        """Fingerprint of everything that decides a dry-run verdict of this
        app: its rules and the dry-run limit (16 hex characters). A dry-run
        row counts for the round only under the fingerprint in force, so a
        changed rule has the titles checked again. Timeouts, the time budget
        and the days before a new search change no verdict."""
        names = ("dry_run_max_releases",) + (RADARR_FIELDS if arr_type == "radarr" else SONARR_FIELDS)
        payload = {}
        for name in names:
            value = getattr(self, name)
            payload[name] = sorted(value) if isinstance(value, list) else value
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
```

- [ ] **Step 4: Test laufen lassen**

Run: `cd <repo> && .venv/bin/python -m pytest tests/test_g1_settings.py -q -p no:cacheprovider`
Expected: `8 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/checked_search/settings.py tests/test_g1_settings.py
git commit -m "feat: add per-instance settings model for the checked search"
```

---

### Task G1.3: Radarr-Regeln (V6)

**Files:**
- Create: `backend/checked_search/radarr_rules.py`
- Test: `tests/test_g1_radarr_rules.py`

**Interfaces:**
- Consumes: G1.1 (`normalize`, `verdict`), G1.2 (`CheckedSearchSettings`)
- Produces: `MovieInfo`, `MovieParse`, `movie_from_resource`, `parse_from_resource`, `evaluate` (Vertrag G1).

`evaluate` sammelt alle Gründe statt beim ersten aufzuhören. Ein Release besteht genau dann, wenn es in der Referenz der Reihe nach (a → b → d bzw. c) durchkäme: (a) und (b) sind unabhängig, (d) ersetzt (c) bei Releases ohne Jahr, 1a kommt dazu. Feldnamen aus der Radarr-6.4-API (`Radarr.Api.V3`): `MovieResource.title` (Übersetzung in der Film-Info-Sprache, sonst Grundtitel), `originalTitle`, `alternateTitles[].title`, `year`, `secondaryYear`, `inCinemas`, `digitalRelease`, `physicalRelease`, `movieFile.sceneName/relativePath/originalFilePath`; `ParseResource.parsedMovieInfo.movieTitles/year`, `ParseResource.movie.id`. Ohne `parsedMovieInfo` (Radarr kann den Namen nicht zerlegen) nimmt `parse_from_resource` die `movieTitles` des Treffers, wie die Referenz.

- [ ] **Step 1: Failing test schreiben**

`tests/test_g1_radarr_rules.py`:

```python
from backend.checked_search import radarr_rules as rr
from backend.checked_search.settings import CheckedSearchSettings
from backend.checked_search.verdict import (
    REASON_EXISTING_FILE, REASON_OTHER_MOVIE, REASON_TITLE, REASON_TITLE_WITHOUT_YEAR, REASON_YEAR,
)

DEFAULT = CheckedSearchSettings()


def movie(names, year, movie_id=1, secondary=0, release_years=(), existing=()):
    return rr.MovieInfo(movie_id=movie_id, names=tuple(names), year=year, secondary_year=secondary,
                        release_years=tuple(release_years), existing_releases=tuple(existing))


def parsed(*titles, year=0, movie_id=None):
    return rr.MovieParse(titles=titles, year=year, movie_id=movie_id)


def check(m, title, p, **settings):
    return rr.evaluate(m, title, p, CheckedSearchSettings(**settings) if settings else DEFAULT)


THE_THING = movie(["Das Ding aus einer anderen Welt", "The Thing"], 1982, release_years=(1982,))
HALLOWEEN = movie(["Halloween - Die Nacht des Grauens", "Halloween"], 1978)
JOHN_CARTER = movie(["John Carter - Zwischen zwei Welten", "John Carter"], 2012)
# Made-up titles for every other case (public repository: no library data).
EXAMPLE_DAY = movie(["Beispieltag: Rückkehr", "Example Day: Return"], 2016)
SOME_FILM = movie(["Some Film"], 2005)


# ── Public examples from the spec ────────────────────────────────────────────

def test_the_thing_rejects_the_1951_film_by_year():
    v = check(THE_THING, "Das.Ding.aus.einer.anderen.Welt.1951.German.DL.1080p.BluRay.x265-GRP",
              parsed("Das Ding aus einer anderen Welt", year=1951))
    assert v.reasons == (REASON_YEAR,)


def test_the_thing_takes_the_right_film():
    v = check(THE_THING, "The.Thing.1982.German.DL.1080p.BluRay.x264-GRP", parsed("The Thing", year=1982, movie_id=1))
    assert v.ok


def test_halloween_2018_is_rejected_for_halloween_1978():
    v = check(HALLOWEEN, "Halloween.2018.German.DL.1080p.BluRay.x264-GRP",
              parsed("Halloween", year=2018, movie_id=2))
    assert v.reasons == (REASON_YEAR, REASON_OTHER_MOVIE)


def test_carter_2022_is_rejected_for_john_carter():
    v = check(JOHN_CARTER, "Carter.2022.German.DL.1080p.WEB.x264-GRP", parsed("Carter", year=2022))
    assert v.reasons == (REASON_YEAR, REASON_TITLE)


def test_umlauts_match_in_both_spellings():
    m = movie(["Schöne Tage", "Beautiful Example Days"], 1989)
    assert check(m, "Schoene.Tage.1989.German.1080p", parsed("Schoene Tage", year=1989)).ok
    assert check(m, "Schone.Tage.1989.German.1080p", parsed("Schone Tage", year=1989)).ok


def test_ampersand_matches_and_and_und():
    m = movie(["Foo & Bar"], 2009)
    assert check(m, "Foo.and.Bar.2009.1080p", parsed("Foo and Bar", year=2009)).ok
    assert check(m, "Foo.und.Bar.2009.1080p", parsed("Foo und Bar", year=2009)).ok


def test_release_without_year_needs_an_exact_title():
    assert check(EXAMPLE_DAY, "Example.Day.Return.German.1080p", parsed("Example Day Return")).ok
    v = check(EXAMPLE_DAY, "Beispieltag.Rueckkehr.Extended.German.1080p",
              parsed("Beispieltag Rueckkehr Extended"))
    assert v.reasons == (REASON_TITLE_WITHOUT_YEAR,)


def test_release_without_year_passes_when_parse_maps_this_movie():
    assert check(EXAMPLE_DAY, "ED2.Return.German.1080p", parsed("ED2 Return", movie_id=1)).ok


def test_veto_when_parse_names_another_movie():
    v = check(EXAMPLE_DAY, "Example.Day.Return.2016.1080p",
              parsed("Example Day Return", year=2016, movie_id=7))
    assert v.reasons == (REASON_OTHER_MOVIE,)


def test_prefix_match():
    v = check(SOME_FILM, "Some.Film.1.2005.German.1080p", parsed("Some Film 1", year=2005),
              word_match=False)
    assert v.ok


def test_word_match():
    v = check(EXAMPLE_DAY, "Example.Day.2.Return.2016.German.1080p",
              parsed("Example Day 2 Return", year=2016))
    assert v.ok


def test_noah_same_title_same_year_is_a_known_limit():
    m = movie(["Noah"], 2014)
    assert check(m, "Noah.2013.German.1080p.WEB-GRP", parsed("Noah", year=2013)).ok


def test_release_of_the_existing_file_is_skipped():
    m = movie(["Example Film"], 1995, existing=("Example.Film.1995.German.DL.1080p.BluRay.x264-GRP",))
    v = check(m, "Example.Film.1995.German.DL.1080p.BluRay.x264-GRP", parsed("Example Film", year=1995))
    assert v.reasons == (REASON_EXISTING_FILE,)


def test_movie_without_year_accepts_only_releases_without_year():
    m = movie(["Unknown Film"], 0)
    assert check(m, "Unknown.Film.German.1080p", parsed("Unknown Film")).ok
    assert check(m, "Unknown.Film.2020.German.1080p", parsed("Unknown Film", year=2020)).reasons == (REASON_YEAR,)


def test_secondary_year_counts():
    m = movie(["Example Film"], 1970, secondary=1971)
    assert check(m, "Example.Film.1972.1080p", parsed("Example Film", year=1972)).ok


# ── Every setting changes the verdict ────────────────────────────────────────

def test_year_tolerance():
    m = movie(["Example Film"], 2016)
    p = parsed("Example Film", year=2017)
    assert check(m, "Example.Film.2017", p).ok
    assert check(m, "Example.Film.2017", p, year_tolerance=0).reasons == (REASON_YEAR,)


def test_count_release_dates_as_years():
    m = movie(["Example Film"], 2019, release_years=(2021,))
    p = parsed("Example Film", year=2021)
    assert check(m, "Example.Film.2021", p).ok
    assert check(m, "Example.Film.2021", p, count_release_dates=False).reasons == (REASON_YEAR,)


def test_veto_can_be_switched_off():
    p = parsed("Example Day Return", year=2016, movie_id=7)
    assert check(EXAMPLE_DAY, "x", p, veto_other_movie=False).ok


def test_prefix_match_switch_and_length():
    p = parsed("Some Film 1", year=2005)
    assert check(SOME_FILM, "x", p, word_match=False, prefix_match=False).reasons == (REASON_TITLE,)
    assert check(SOME_FILM, "x", p, word_match=False, prefix_min_length=20).reasons == (REASON_TITLE,)


def test_word_match_switch_and_core_words():
    p = parsed("Example Day 2 Return", year=2016)
    assert check(EXAMPLE_DAY, "x", p, word_match=False).reasons == (REASON_TITLE,)
    assert check(EXAMPLE_DAY, "x", p, word_min_core_words=4).reasons == (REASON_TITLE,)


def test_releases_without_year_may_use_the_loose_title_rules():
    p = parsed("Beispieltag Rueckkehr Extended")
    assert check(EXAMPLE_DAY, "x", p, no_year_needs_exact=False).ok


def test_skip_existing_file_can_be_switched_off():
    m = movie(["Example Film"], 1995, existing=("Example.Film.1995.German.DL.1080p.BluRay.x264-GRP",))
    p = parsed("Example Film", year=1995)
    assert check(m, "Example.Film.1995.German.DL.1080p.BluRay.x264-GRP", p, skip_existing_file=False).ok


# ── Reading the API resources ────────────────────────────────────────────────

def test_movie_from_resource_reads_the_api_fields():
    m = rr.movie_from_resource({
        "id": 5, "title": "Das Ding aus einer anderen Welt", "originalTitle": "The Thing",
        "alternateTitles": [{"title": "John Carpenter's The Thing", "sourceType": "tmdb"}],
        "year": 1982, "secondaryYear": None,
        "inCinemas": "1982-06-25T00:00:00Z", "digitalRelease": None, "physicalRelease": "1998-05-01T00:00:00Z",
        "movieFile": {"sceneName": "The.Thing.1982.720p-OLD", "relativePath": "The Thing (1982).mkv",
                      "originalFilePath": "dl/The.Thing.1982.720p-OLD.mkv"},
    })
    assert m.movie_id == 5 and m.year == 1982 and m.secondary_year == 0
    assert m.names == ("Das Ding aus einer anderen Welt", "The Thing", "John Carpenter's The Thing")
    assert m.release_years == (1982, 1998)
    assert m.existing_releases == ("The.Thing.1982.720p-OLD", "The Thing (1982)", "The.Thing.1982.720p-OLD")


def test_parse_from_resource_and_fallback():
    p = rr.parse_from_resource({"parsedMovieInfo": {"movieTitles": ["The Thing"], "year": 1982}, "movie": {"id": 5}})
    assert p == rr.MovieParse(("The Thing",), 1982, 5)
    unparsed = rr.parse_from_resource({"title": "garbage"}, fallback_titles=["Garbage"])
    assert unparsed == rr.MovieParse(("Garbage",), 0, None)
```

- [ ] **Step 2: Test laufen lassen, er muss scheitern**

Run: `cd <repo> && .venv/bin/python -m pytest tests/test_g1_radarr_rules.py -q -p no:cacheprovider`
Expected: FAIL mit `ImportError: cannot import name 'radarr_rules' from 'backend.checked_search'`

- [ ] **Step 3: Implementieren**

`backend/checked_search/radarr_rules.py`:

```python
"""Pre-filter V6 for Radarr releases. Pure functions, no network.

Rules (all must hold, see docs/superpowers/specs/2026-10-01-checked-search-design.md):
  a  year      release year within the tolerance of a year of the movie
  b  veto      a release with a year that /parse maps to another movie
  c  title     exact name, /parse maps to this movie, prefix, or word match
  d  no year   a release without a year needs an exact name or the /parse match
  1a file      the release of the existing file is never grabbed again

evaluate() collects every rule that rejects, so the log names all of them.
A release passes exactly when the sequential reference lets it through.
"""

from dataclasses import dataclass

from backend.checked_search.normalize import (
    STOP_WORDS, same_release, strip_extension, tokens, variants, year_of,
)
from backend.checked_search.settings import CheckedSearchSettings
from backend.checked_search.verdict import (
    REASON_EXISTING_FILE, REASON_OTHER_MOVIE, REASON_TITLE, REASON_TITLE_WITHOUT_YEAR, REASON_YEAR, Verdict,
)


@dataclass(frozen=True)
class MovieInfo:
    movie_id: int
    names: tuple[str, ...]                 # title, originalTitle, alternateTitles[].title
    year: int = 0
    secondary_year: int = 0
    release_years: tuple[int, ...] = ()    # years of inCinemas, digitalRelease, physicalRelease
    existing_releases: tuple[str, ...] = ()  # sceneName and file names without extension


@dataclass(frozen=True)
class MovieParse:
    titles: tuple[str, ...] = ()           # parsedMovieInfo.movieTitles
    year: int = 0                          # parsedMovieInfo.year, 0 = none
    movie_id: int | None = None            # movie.id: the library movie /parse maps it to


def movie_from_resource(movie: dict) -> MovieInfo:
    """GET /api/v3/movie/{id}. `title` is already the translation in Radarr's
    movie info language (or the base title)."""
    names = [movie.get("title"), movie.get("originalTitle")]
    names += [alt.get("title") for alt in movie.get("alternateTitles") or [] if isinstance(alt, dict)]
    movie_file = movie.get("movieFile") or {}
    existing = [
        movie_file.get("sceneName"),
        strip_extension(movie_file.get("relativePath")),
        strip_extension(movie_file.get("originalFilePath")),
    ]
    release_years = [year_of(movie.get(key)) for key in ("inCinemas", "digitalRelease", "physicalRelease")]
    return MovieInfo(
        movie_id=int(movie.get("id") or 0),
        names=tuple(name for name in names if name),
        year=int(movie.get("year") or 0),
        secondary_year=int(movie.get("secondaryYear") or 0),
        release_years=tuple(year for year in release_years if year),
        existing_releases=tuple(name for name in existing if name),
    )


def parse_from_resource(parse: dict | None, fallback_titles=()) -> MovieParse:
    """GET /api/v3/parse?title=…  Without parsedMovieInfo (Radarr could not
    parse the name) the release's own movieTitles stand in, like in the
    reference."""
    parse = parse or {}
    info = parse.get("parsedMovieInfo") or {}
    movie = parse.get("movie") or {}
    titles = tuple(t for t in (info.get("movieTitles") or []) if t) or tuple(t for t in fallback_titles or () if t)
    movie_id = movie.get("id") if isinstance(movie, dict) else None
    return MovieParse(titles=titles, year=int(info.get("year") or 0), movie_id=int(movie_id) if movie_id else None)


def _movie_years(movie: MovieInfo, settings: CheckedSearchSettings) -> set[int]:
    years = {y for y in (movie.year, movie.secondary_year) if y}
    if settings.count_release_dates:
        years |= set(movie.release_years)
    return years


def _year_ok(movie: MovieInfo, parse: MovieParse, settings: CheckedSearchSettings) -> bool:
    if not movie.year and not movie.secondary_year:
        return parse.year == 0      # a movie without a year: only releases without one
    if parse.year == 0:
        return True                 # rule d decides
    return any(abs(parse.year - y) <= settings.year_tolerance for y in _movie_years(movie, settings))


def _exact(movie: MovieInfo, parse: MovieParse, names: set[str]) -> bool:
    if parse.movie_id is not None and parse.movie_id == movie.movie_id:
        return True
    return any(v in names for title in parse.titles for v in variants(title))


def _loose(movie: MovieInfo, parse: MovieParse, names: set[str], settings: CheckedSearchSettings) -> bool:
    if settings.prefix_match:
        size = settings.prefix_min_length
        for title in parse.titles:
            for x in variants(title):
                for n in names:
                    if len(x) >= size and len(n) >= size and (x.startswith(n) or n.startswith(x)):
                        return True
    if settings.word_match:
        words = {w for title in parse.titles for w in tokens(title)}
        for name in movie.names:
            name_words = tokens(name)
            core = [w for w in name_words if w not in STOP_WORDS]
            if name_words and set(name_words) <= words and len(core) >= settings.word_min_core_words:
                return True
    return False


def evaluate(movie: MovieInfo, release_title: str, parse: MovieParse,
             settings: CheckedSearchSettings) -> Verdict:
    reasons: list[str] = []
    if not _year_ok(movie, parse, settings):
        reasons.append(REASON_YEAR)
    if settings.veto_other_movie and parse.year and parse.movie_id is not None and parse.movie_id != movie.movie_id:
        reasons.append(REASON_OTHER_MOVIE)

    names = {v for name in movie.names for v in variants(name)}
    exact = _exact(movie, parse, names)
    if parse.year == 0 and settings.no_year_needs_exact:
        if not exact:
            reasons.append(REASON_TITLE_WITHOUT_YEAR)
    elif not exact and not _loose(movie, parse, names, settings):
        reasons.append(REASON_TITLE)

    if settings.skip_existing_file and any(same_release(release_title, name) for name in movie.existing_releases):
        reasons.append(REASON_EXISTING_FILE)
    return Verdict(tuple(reasons))
```

- [ ] **Step 4: Test laufen lassen**

Run: `cd <repo> && .venv/bin/python -m pytest tests/test_g1_radarr_rules.py -q -p no:cacheprovider`
Expected: `24 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/checked_search/radarr_rules.py tests/test_g1_radarr_rules.py
git commit -m "feat: add the Radarr pre-filter rules (V6)"
```

---

### Task G1.4: Sonarr-Regeln (S1–S4)

**Files:**
- Create: `backend/checked_search/sonarr_rules.py`
- Test: `tests/test_g1_sonarr_rules.py`

**Interfaces:**
- Consumes: G1.1, G1.2
- Produces: `EpisodeInfo`, `EpisodeParse`, `episode_from_resources`, `parse_from_resource`, `title_suffix`, `evaluate` (Vertrag G1).

Felder aus Sonarr 4.0 (`Sonarr.Api.V3`): `GET /episode/{id}` liefert `seriesId`, `airDateUtc` und (weil die Einzelabfrage `includeEpisodeFile` setzt) `episodeFile.sceneName/relativePath`; `GET /series/{id}` liefert `title`, `year`, `alternateTitles[].title` (nur die Einzelabfrage füllt die Alternativtitel, deshalb der zweite Aufruf); `ParseResource.parsedEpisodeInfo.seriesTitle`, `ParseResource.series.id`; Treffer `publishDate`.

**Abweichung von der Datenliste der Spec (S2):** Die Spec nennt `parsedEpisodeInfo.seriesTitleInfo.year`. Sonarr füllt dieses Jahr aber mit dem *ersten* vierstelligen Zahlblock im Titel (`YearInTitleRegex`), nicht mit einem Jahr am Ende. Die Regel der Spec spricht vom Ende („Steht im geparsten Serientitel am Ende ein Jahr oder ein Ländercode“). `title_suffix` liest deshalb Jahr und Ländercode selbst vom Ende von `seriesTitle` (höchstens eins von jedem, in beliebiger Reihenfolge; das erste Wort zählt nie, damit eine Serie, die nur aus einer Jahreszahl besteht, ihren Namen behält). Bei `The Guest CO 2025` ergibt das dasselbe wie `seriesTitleInfo.year`.

S3 vergleicht den genauen Abstand (`airDateUtc − publishDate` als Zeitspanne, nicht abgerundet; Codex-Runde 2, F8): verwerfen bei mehr als „Reject“-Tagen (365 Tage und 1 Stunde wird verworfen, genau 365 Tage nicht), Hinweis ab „Note“-Tagen (einschließlich, damit „A Better Place“ mit 14 Tagen und 3 Stunden einen Hinweis bekommt) und mindestens einem ganzen Tag. Nur der angezeigte Hinweis rundet auf ganze Tage ab (`published 14 days before air date`).

**Staffelpakete kommen bei der Suche nach einer Folge nicht als Kandidaten an.** Sonarr verwirft sie bei `GET /release?episodeId=` selbst („Full season pack“, `SingleEpisodeSearchMatchSpecification`, für normale Serien und Anime); nur bei Specials (Staffel 0, `SearchSpecial`) fehlt diese Ablehnung, dort verwirft der Runner Treffer mit `fullSeason` (G3.1). Das S3-Beispiel der Spec (`A.Better.Place.S01…`) ist deshalb im Test der Name einer einzelnen Folge (`A.Better.Place.S01E03…`): Die Regel selbst kennt keine Pakete, ein Paket-Name erreichte sie im Betrieb nie.

- [ ] **Step 1: Failing test schreiben**

`tests/test_g1_sonarr_rules.py`:

```python
from datetime import datetime, timedelta, timezone

from backend.checked_search import sonarr_rules as sr
from backend.checked_search.settings import CheckedSearchSettings
from backend.checked_search.verdict import (
    REASON_COUNTRY_SUFFIX, REASON_EXISTING_FILE, REASON_OTHER_SERIES, REASON_TOO_EARLY, REASON_YEAR_SUFFIX,
)

DEFAULT = CheckedSearchSettings()
AIRED = datetime(2026, 9, 27, 18, 0, tzinfo=timezone.utc)


def info(titles=("The Guest",), year=2018, series_id=10, aired=AIRED, existing=()):
    return sr.EpisodeInfo(series_id=series_id, series_titles=tuple(titles), series_year=year,
                          air_date_utc=aired, existing_releases=tuple(existing))


def check(i, title, published, p, **settings):
    return sr.evaluate(i, title, published, p, CheckedSearchSettings(**settings) if settings else DEFAULT)


GUEST = "The.Guest.CO.2025.S01E10.MULTI.1080p.WEB.X264-GRP"


def test_the_guest_co_2025_is_rejected_by_the_suffix():
    v = check(info(), GUEST, AIRED, sr.EpisodeParse(series_id=None, series_title="The Guest CO 2025"))
    assert v.reasons == (REASON_YEAR_SUFFIX, REASON_COUNTRY_SUFFIX)


def test_suffix_check_can_be_switched_off():
    v = check(info(), GUEST, AIRED, sr.EpisodeParse(series_title="The Guest CO 2025"), check_suffix=False)
    assert v.ok


def test_suffix_year_tolerance():
    i = info(year=2024)
    p = sr.EpisodeParse(series_title="The Guest 2025")
    assert check(i, "x", AIRED, p).ok
    assert check(i, "x", AIRED, p, suffix_year_tolerance=0).reasons == (REASON_YEAR_SUFFIX,)


def test_matching_country_suffix_passes():
    i = info(titles=("Example Show (US)",), year=2005)
    assert check(i, "Example.Show.US.S01E01.1080p", AIRED, sr.EpisodeParse(series_title="Example Show US")).ok


def test_country_code_list_is_a_setting():
    p = sr.EpisodeParse(series_title="The Guest CO")
    assert check(info(), "x", AIRED, p).reasons == (REASON_COUNTRY_SUFFIX,)
    assert check(info(), "x", AIRED, p, country_codes=["US"]).ok


def test_a_title_that_is_only_a_year_has_no_suffix():
    i = info(titles=("1901",), year=2022)
    assert check(i, "1901.S01E01.1080p", AIRED, sr.EpisodeParse(series_title="1901")).ok


def test_release_long_before_the_air_date_is_rejected():
    published = AIRED - timedelta(days=517)
    v = check(info(), "Show.S06E24.German.1080p.WEB.h264-GRP", published, sr.EpisodeParse())
    assert v.reasons == (REASON_TOO_EARLY,)


def test_a_better_place_14_days_early_only_gets_a_note():
    # A single episode: a season pack never reaches the rules in an episode
    # search (Sonarr rejects it as "Full season pack" itself).
    published = AIRED - timedelta(days=14, hours=3)
    v = check(info(titles=("A Better Place",), year=2026), "A.Better.Place.S01E03.German.1080p.WEB-DL.x264-GRP",
              published, sr.EpisodeParse(series_title="A Better Place"))
    assert v.ok
    assert v.notes == ("published 14 days before air date",)


def test_early_thresholds_are_settings():
    published = AIRED - timedelta(days=400)
    assert check(info(), "x", published, sr.EpisodeParse(), reject_days_before_air=500).notes == (
        "published 400 days before air date",)
    assert check(info(), "x", AIRED - timedelta(days=14), sr.EpisodeParse(), note_days_before_air=20).notes == ()


def test_the_reject_threshold_compares_the_exact_span():
    # "more than 365 days": one hour past the threshold is rejected, the
    # threshold itself only gets a note (no rounding down before comparing).
    over = check(info(), "x", AIRED - timedelta(days=365, hours=1), sr.EpisodeParse())
    assert over.reasons == (REASON_TOO_EARLY,)
    at = check(info(), "x", AIRED - timedelta(days=365), sr.EpisodeParse())
    assert at.ok
    assert at.notes == ("published 365 days before air date",)
    assert check(info(), "x", AIRED - timedelta(days=13, hours=23), sr.EpisodeParse()).notes == ()


def test_release_of_the_existing_file_is_skipped():
    i = info(existing=("The.Guest.S01E10.German.1080p.WEB.x264-GRP",))
    v = check(i, "The.Guest.S01E10.German.1080p.WEB.x264-GRP", AIRED, sr.EpisodeParse())
    assert v.reasons == (REASON_EXISTING_FILE,)
    assert check(i, "The.Guest.S01E10.German.1080p.WEB.x264-GRP", AIRED, sr.EpisodeParse(),
                 skip_existing_file=False).ok


def test_veto_when_parse_names_another_series():
    p = sr.EpisodeParse(series_id=99, series_title="Other Show")
    assert check(info(), "x", AIRED, p).reasons == (REASON_OTHER_SERIES,)
    assert check(info(), "x", AIRED, p, veto_other_series=False).ok


def test_every_rule_that_fires_is_reported():
    i = info(existing=(GUEST,))
    v = check(i, GUEST, AIRED - timedelta(days=400), sr.EpisodeParse(series_id=99, series_title="The Guest CO 2025"))
    assert v.reasons == (REASON_OTHER_SERIES, REASON_YEAR_SUFFIX, REASON_COUNTRY_SUFFIX,
                         REASON_TOO_EARLY, REASON_EXISTING_FILE)


def test_resources_are_read_from_the_api_fields():
    i = sr.episode_from_resources(
        {"id": 3, "seriesId": 10, "airDateUtc": "2026-09-27T18:00:00Z",
         "episodeFile": {"sceneName": "The.Guest.S01E10-GRP", "relativePath": "Season 1/The Guest - S01E10.mkv"}},
        {"id": 10, "title": "The Guest", "year": 2018, "alternateTitles": [{"title": "Example Guest", "seasonNumber": -1}]},
    )
    assert i == sr.EpisodeInfo(10, ("The Guest", "Example Guest"), 2018, AIRED,
                               ("The.Guest.S01E10-GRP", "The Guest - S01E10"))
    p = sr.parse_from_resource({"parsedEpisodeInfo": {"seriesTitle": "The Guest CO 2025",
                                                      "seriesTitleInfo": {"year": 2025}}, "series": {"id": 10}})
    assert p == sr.EpisodeParse(10, "The Guest CO 2025")
```

- [ ] **Step 2: Test laufen lassen, er muss scheitern**

Run: `cd <repo> && .venv/bin/python -m pytest tests/test_g1_sonarr_rules.py -q -p no:cacheprovider`
Expected: FAIL mit `ImportError: cannot import name 'sonarr_rules' from 'backend.checked_search'`

- [ ] **Step 3: Implementieren**

`backend/checked_search/sonarr_rules.py`:

```python
"""Pre-filter S1-S4 for Sonarr releases of single episodes. Pure functions.

  S1 veto    /parse maps the release to another series of the library
  S2 suffix  a year or country code at the end of the parsed series title
             must fit the series (year within tolerance, code also at the
             end of the series title or an alternate title)
  S3 early   published more than N days before the episode aired (between
             the note threshold and N only a note)
  S4 file    the release of the existing file is never grabbed again

The rules are not measured yet. evaluate() therefore always returns every
rule that fires, so a dry run shows each rule on its own.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from backend.checked_search.normalize import parse_utc, same_release, strip_extension, tokens
from backend.checked_search.settings import CheckedSearchSettings
from backend.checked_search.verdict import (
    REASON_COUNTRY_SUFFIX, REASON_EXISTING_FILE, REASON_OTHER_SERIES, REASON_TOO_EARLY, REASON_YEAR_SUFFIX, Verdict,
)

_YEAR = re.compile(r"^(18|19|20)\d\d$")


@dataclass(frozen=True)
class EpisodeInfo:
    series_id: int
    series_titles: tuple[str, ...]          # series title + alternateTitles[].title
    series_year: int = 0
    air_date_utc: datetime | None = None
    existing_releases: tuple[str, ...] = ()  # episodeFile.sceneName, file name without extension


@dataclass(frozen=True)
class EpisodeParse:
    series_id: int | None = None            # series.id from /parse
    series_title: str = ""                  # parsedEpisodeInfo.seriesTitle


def episode_from_resources(episode: dict, series: dict) -> EpisodeInfo:
    """GET /api/v3/episode/{id} (includes episodeFile) and GET /api/v3/series/{seriesId}
    (includes alternateTitles)."""
    titles = [series.get("title")]
    titles += [alt.get("title") for alt in series.get("alternateTitles") or [] if isinstance(alt, dict)]
    episode_file = episode.get("episodeFile") or {}
    existing = [episode_file.get("sceneName"), strip_extension(episode_file.get("relativePath"))]
    return EpisodeInfo(
        series_id=int(series.get("id") or episode.get("seriesId") or 0),
        series_titles=tuple(t for t in titles if t),
        series_year=int(series.get("year") or 0),
        air_date_utc=parse_utc(episode.get("airDateUtc")),
        existing_releases=tuple(name for name in existing if name),
    )


def parse_from_resource(parse: dict | None) -> EpisodeParse:
    parse = parse or {}
    info = parse.get("parsedEpisodeInfo") or {}
    series = parse.get("series") or {}
    series_id = series.get("id") if isinstance(series, dict) else None
    return EpisodeParse(series_id=int(series_id) if series_id else None, series_title=info.get("seriesTitle") or "")


def title_suffix(series_title: str, codes) -> tuple[int, str]:
    """(year, country code) at the end of a series title, 0/'' when absent.
    At most one of each, in either order; the first word never counts, so
    a series whose title is just a year keeps its name."""
    words = tokens(series_title)
    known = {c.upper() for c in codes}
    year, country = 0, ""
    for _ in range(2):
        if len(words) < 2:
            break
        last = words[-1]
        if not year and _YEAR.match(last):
            year = int(last)
        elif not country and last.upper() in known:
            country = last.upper()
        else:
            break
        words.pop()
    return year, country


def _country_fits(info: EpisodeInfo, country: str, codes) -> bool:
    for title in info.series_titles:
        _, own = title_suffix(title, codes)
        if own == country:
            return True
    return False


def evaluate(info: EpisodeInfo, release_title: str, publish_date: datetime | None,
             parse: EpisodeParse, settings: CheckedSearchSettings) -> Verdict:
    reasons: list[str] = []
    notes: list[str] = []

    if settings.veto_other_series and parse.series_id is not None and parse.series_id != info.series_id:
        reasons.append(REASON_OTHER_SERIES)

    if settings.check_suffix and parse.series_title:
        year, country = title_suffix(parse.series_title, settings.country_codes)
        if year and info.series_year and abs(year - info.series_year) > settings.suffix_year_tolerance:
            reasons.append(REASON_YEAR_SUFFIX)
        if country and not _country_fits(info, country, settings.country_codes):
            reasons.append(REASON_COUNTRY_SUFFIX)

    if publish_date is not None and info.air_date_utc is not None:
        # Exact span against the thresholds; only the note rounds down to whole days.
        early = info.air_date_utc - publish_date
        if early > timedelta(days=settings.reject_days_before_air):
            reasons.append(REASON_TOO_EARLY)
        elif early >= timedelta(days=settings.note_days_before_air) and early.days > 0:
            notes.append(f"published {early.days} days before air date")

    if settings.skip_existing_file and any(same_release(release_title, name) for name in info.existing_releases):
        reasons.append(REASON_EXISTING_FILE)
    return Verdict(tuple(reasons), tuple(notes))
```

- [ ] **Step 4: Test laufen lassen**

Run: `cd <repo> && .venv/bin/python -m pytest tests/test_g1_sonarr_rules.py -q -p no:cacheprovider`
Expected: `14 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/checked_search/sonarr_rules.py tests/test_g1_sonarr_rules.py
git commit -m "feat: add the Sonarr pre-filter rules S1-S4"
```

---

### Task G1.5: Abgleich mit dem Messkorpus (lokal)

**Files:**
- Test: `tests/test_g1_corpus.py`

**Interfaces:**
- Consumes: G1.2, G1.3
- Produces: nichts (Absicherung der Portierung)

Der Test liest das Korpus aus dem Ordner in `MISSINGARR_VORFILTER_KORPUS` (der Vorgabe-Ordner mit `korpus/runde4`, `korpus/runde5` und `referenz/` mit `meta.tsv`, `translations.tsv`, `alttitles.tsv`, `parse_cache.json`, `urteil.json` und `r4/`; die Variable darf auch auf den Unterordner `korpus/` selbst zeigen) und überspringt sich ohne ihn. Er baut die Filmdaten wie die API sie liefert (Variante „api-echt“: deutsche Übersetzung, sonst Grundtitel, dazu Originaltitel und Alternativtitel), wählt die Kandidaten wie die Referenz (`approved` oder nur durch die Speicher-Sperre „ignored terms“ abgelehnt; nicht „Wrong movie“/„Unknown Movie“) und schickt jeden durch `radarr_rules.evaluate` mit den Voreinstellungen. 1a greift im Korpus nicht (keine Dateinamen), wie in der Referenz. Für die 11 Treffer, deren `/parse` in der Messung scheiterte, nimmt der Test wie die Referenz die `movieTitles` des Treffers ohne Jahr; im Betrieb verwirft der Runner einen Kandidaten mit gescheitertem `/parse` (Grund „parse error“).

Erwartete Zahlen (bei Planerstellung nachgerechnet):
- Runde 5, 1.000 Filme, 416 mit Kandidaten: `anders_fremd 15, gleich_harmlos 33, gleich_richtig 347, weg_fremd 16, weg_harmlos 2, weg_richtig 1, weg_unklar 2` — genau die gemessenen api-echt-Zahlen (`weg_fremd` zählt „fremd“ und „wahrscheinlich fremd“).
- Runde 5 je Kandidat (nur Zahlen, keine Bibliotheksdaten): `candidates 2477, pass 2278, reject 199`, Gründe `year 148, other movie 69, title 88, title without year 9`. Die Ergebnisse je Film allein blieben grün, wenn das Veto (b) aus wäre, Regel d fehlte, die Jahrestoleranz 2 oder die Präfixlänge 5 bzw. 7 betrüge (nachgemessen: 4, 7, 2, 1 bzw. 2 Kandidaten-Urteile ändern sich, die Filmzahlen nicht). Die Zahlen je Kandidat fangen alle diese Abweichungen. Nicht gefangen wird `word_min_core_words=3` (ändert im Korpus kein einziges Urteil); das deckt der Regeltest in G1.3 ab (`word_min_core_words=4`). Je Kandidat stimmt die Portierung mit dem Nachbau „api-echt“ überein (2477 von 2477).
- Runde 4, 219 Filme, 66 mit Kandidaten: `gleich 61, anders 1, weg 4` (ohne Urteilsdatei); je Kandidat `candidates 453, pass 440, reject 13`, Gründe `year 9, other movie 7, title 7` (nachgerechnet am 01.10.2026). Mit den Urteilen der Verprobung (4 fremde Filme, sonst richtig; nur im Scratchpad nachgerechnet, die Film-IDs gehören nicht ins Repo) sind die 4 „weg“ genau die 4 fremden Filme und „anders“ ist ein richtiger: 66 → 62 Filme mit Pick, 0 fremd geladen, 0 richtige verloren, wie in der Vorgabe. Die Metadaten der Runde 4 liegen seit 01.10.2026 unter `referenz/r4/` im Vorgabe-Ordner (`referenz/meta.tsv` selbst deckt nur 5 der 66 Filme ab); `MISSINGARR_VORFILTER_R4_META` kann auf einen anderen Ordner zeigen. Fehlen sie, überspringt sich nur dieser Teil.

- [ ] **Step 1: Test schreiben**

`tests/test_g1_corpus.py`:

```python
"""Local check of the Radarr rules against the measuring corpus.

The corpus holds search results and library metadata and is not part of the
repository. Point MISSINGARR_VORFILTER_KORPUS at its folder (with korpus/
and referenz/, or at korpus/ itself); without it these tests skip. Round 4
additionally needs its own metadata: referenz/r4/ in that folder, or
MISSINGARR_VORFILTER_R4_META.

The expected numbers are the measurement with the names the Radarr API
delivers ('api-echt'): title = German translation or base title,
originalTitle, alternateTitles.
"""

import collections
import glob
import json
import os

import pytest

from backend.checked_search import radarr_rules
from backend.checked_search.settings import CheckedSearchSettings

KORPUS = os.environ.get("MISSINGARR_VORFILTER_KORPUS", "")

pytestmark = pytest.mark.skipif(
    not KORPUS or not os.path.isdir(KORPUS), reason="MISSINGARR_VORFILTER_KORPUS not set"
)


def _root(folder):
    """The folder with korpus/ and referenz/ — the variable may name korpus/ itself."""
    if folder and os.path.isdir(os.path.join(folder, "runde5")):
        return os.path.dirname(os.path.abspath(folder))
    return folder


ROOT = _root(KORPUS)

GERMAN = 4  # Radarr language id of the translation the API returns as `title`

EXPECTED_ROUND5 = {
    "anders_fremd": 15, "gleich_harmlos": 33, "gleich_richtig": 347,
    "weg_fremd": 16, "weg_harmlos": 2, "weg_richtig": 1, "weg_unklar": 2,
}
EXPECTED_ROUND4 = {"gleich": 61, "anders": 1, "weg": 4}
EXPECTED_ROUND4_CANDIDATES = {
    "candidates": 453, "pass": 440, "reject": 13, "year": 9, "other movie": 7, "title": 7,
}
# Per candidate, so a broken veto, rule d, year tolerance or prefix length
# shows even where the per-film numbers stay the same.
EXPECTED_ROUND5_CANDIDATES = {
    "candidates": 2477, "pass": 2278, "reject": 199,
    "year": 148, "other movie": 69, "title": 88, "title without year": 9,
}


def _films(folder):
    films = {}
    for path in sorted(glob.glob(os.path.join(folder, "radarr-13-*.json"))):
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        films[int(os.path.basename(path).split("-")[2].split(".")[0])] = data
    return films


def _meta(folder):
    meta = {}
    with open(os.path.join(folder, "meta.tsv"), encoding="utf-8") as handle:
        for line in handle:
            a = line.rstrip("\n").split("\t")
            meta[int(a[0])] = {
                "year": int(a[3] or 0), "sec": int(a[4]) if a[4] else 0,
                "dates": (a[5], a[6], a[7]), "title": a[10], "otitle": a[11], "trans": [], "alt": [],
            }
    with open(os.path.join(folder, "translations.tsv"), encoding="utf-8") as handle:
        for line in handle:
            a = line.rstrip("\n").split("\t")
            meta[int(a[0])]["trans"].append((int(a[1]), a[2]))
    with open(os.path.join(folder, "alttitles.tsv"), encoding="utf-8") as handle:
        for line in handle:
            a = line.rstrip("\n").split("\t")
            meta[int(a[0])]["alt"].append(a[2])
    return meta


def _accepted(release):
    """The reference's selection: approved, or rejected only by the storage
    lock's ignored terms (875 films of round 5 carry it), and not mapped to
    another movie by Radarr."""
    rejections = release.get("rejections") or []
    joined = " ".join(rejections)
    if "Wrong movie" in joined or "Unknown Movie" in joined:
        return False
    return bool(release.get("approved")) or (bool(rejections) and all("ignored terms" in r for r in rejections))


def _movie(movie_id, film, meta):
    german = [title for lang, title in meta["trans"] if lang == GERMAN]
    names = (german[:1] or [meta["title"]]) + [meta["otitle"], film.get("originaltitel")]
    names += meta["alt"] + list(film.get("alternativtitel") or [])
    return radarr_rules.MovieInfo(
        movie_id=movie_id,
        names=tuple(n for n in names if n),
        year=meta["year"],
        secondary_year=meta["sec"],
        release_years=tuple(int(d[:4]) for d in meta["dates"] if d),
    )


def _parse(release, cache):
    entry = cache.get(release["title"], {})
    titles = tuple(entry.get("titles") or release.get("movieTitles") or ())
    movie_id = entry.get("movie_id")
    return radarr_rules.MovieParse(titles=titles, year=entry.get("year") or 0,
                                   movie_id=int(movie_id) if movie_id else None)


def _run(films, meta, cache, judge):
    """(outcome per film, tally per candidate)."""
    settings = CheckedSearchSettings()
    stats = collections.Counter()
    tally = collections.Counter()
    for movie_id, film in films.items():
        accepted = [r for r in film["treffer"] if _accepted(r)]
        if not accepted:
            continue
        movie = _movie(movie_id, film, meta[movie_id])
        passing = []
        for release in accepted:
            verdict = radarr_rules.evaluate(movie, release["title"], _parse(release, cache), settings)
            tally["candidates"] += 1
            tally["pass" if verdict.ok else "reject"] += 1
            tally.update(verdict.reasons)
            if verdict.ok:
                passing.append(release)
        if not passing:
            outcome = "weg"
        elif passing[0] is accepted[0]:
            outcome = "gleich"
        else:
            outcome = "anders"
        stats[outcome + judge(movie_id)] += 1
    return dict(stats), dict(tally)


def test_round5_matches_the_measured_numbers():
    reference = os.path.join(ROOT, "referenz")
    with open(os.path.join(reference, "parse_cache.json"), encoding="utf-8") as handle:
        cache = json.load(handle)
    with open(os.path.join(reference, "urteil.json"), encoding="utf-8") as handle:
        verdicts = {row["mid"]: row["urteil"] for row in json.load(handle)}

    def judge(movie_id):
        verdict = verdicts[movie_id]
        return "_fremd" if verdict in ("fremd", "wahrscheinlich_fremd") else "_" + verdict

    stats, tally = _run(_films(os.path.join(ROOT, "korpus", "runde5")), _meta(reference), cache, judge)
    # weg_fremd counts both "foreign" verdicts, like the measurement
    assert stats == EXPECTED_ROUND5
    assert tally == EXPECTED_ROUND5_CANDIDATES


def test_round4_matches_the_measured_numbers():
    meta_folder = os.environ.get("MISSINGARR_VORFILTER_R4_META") or os.path.join(ROOT, "referenz", "r4")
    if not os.path.isfile(os.path.join(meta_folder, "meta.tsv")):
        pytest.skip("round 4 metadata not available")
    with open(os.path.join(ROOT, "referenz", "parse_cache.json"), encoding="utf-8") as handle:
        cache = json.load(handle)
    stats, tally = _run(_films(os.path.join(ROOT, "korpus", "runde4")), _meta(meta_folder), cache, lambda _: "")
    assert stats == EXPECTED_ROUND4
    assert tally == EXPECTED_ROUND4_CANDIDATES
```

- [ ] **Step 2: Ohne Korpus laufen lassen**

Run: `cd <repo> && .venv/bin/python -m pytest tests/test_g1_corpus.py -q -p no:cacheprovider -rs`
Expected: `2 skipped` mit `MISSINGARR_VORFILTER_KORPUS not set`

- [ ] **Step 3: Mit Korpus laufen lassen (nur lokal, Pfad nie ins Repo)**

Run: `cd <repo> && MISSINGARR_VORFILTER_KORPUS=<korpus> .venv/bin/python -m pytest tests/test_g1_corpus.py -v -p no:cacheprovider`
(`<korpus>` = Vorgabe-Ordner der geprüften Suche oder sein Unterordner `korpus/`, Pfad nie ins Repo; die Runde-4-Metadaten liegen in `referenz/r4/` daneben, ein anderer Ordner geht über `MISSINGARR_VORFILTER_R4_META=<ordner mit meta.tsv, translations.tsv, alttitles.tsv>`.)
Expected: `2 passed` (`test_round5_matches_the_measured_numbers PASSED`, `test_round4_matches_the_measured_numbers PASSED`).
Schlägt Runde 5 fehl, ist die Portierung falsch — nicht die Zahlen anpassen, sondern `normalize`/`radarr_rules` gegen `referenz/basis.py` und den Nachbau „api-echt“ prüfen.

- [ ] **Step 4: Commit**

```bash
git add tests/test_g1_corpus.py
git commit -m "test: hold the Radarr rules to the measured corpus numbers (local only)"
```

---

### Task G1.6: Profil-Fingerabdruck (Spec-Nachtrag)

**Files:**
- Create: `backend/checked_search/fingerprint.py`
- Test: `tests/test_g1_fingerprint.py`

**Interfaces:**
- Consumes: nichts
- Produces: `fingerprint`, `fingerprints`, `changes`, `short` (Vertrag G1). G3 (`backend/skills/profiles.py`) ruft sie mit den Antworten von `GET /api/v3/qualityprofile`, `/customformat`, `/releaseprofile`, `/qualitydefinition` und `/config/indexer` auf (die letzten beiden: Nachtrag 02.10.2026 am Ende dieses Tasks).

Rein, wie die Spec es seit 01.10.2026 beschreibt (Daniel, nach Codex K6): SHA-256 über das kanonische JSON (sortierte Schlüssel) einer **Auswahl der bewertungsrelevanten Felder**, gekürzt auf 16 Hex-Zeichen. Die ganzen Ressourcen taugen nicht: `GET /customformat` liefert je Spezifikation `implementationName`, `infoLink` und die Felder mit übersetzten `label`/`helpText`/`selectOptions` (Radarr `CustomFormatSpecificationSchema.cs`, `SchemaBuilder.cs:119–130`, Sprache aus `UILanguage`); die Sprach-Spezifikation listet alle Sprachen als Auswahl (`LanguageFieldConverter`), ein Update mit einer neuen Sprache änderte jeden Fingerabdruck; `tags` eines Release-Profils ist ein `HashSet`. Ausgewählt werden:
- Profil: `items` rekursiv als `{id, quality.id, allowed, items}` **in ihrer Reihenfolge** (Rangfolge der Qualitäten), `cutoff`, `upgradeAllowed`, `minFormatScore`, `cutoffFormatScore`, `minUpgradeFormatScore` (fehlt: `None`), `language.id` (nur Radarr), `formatItems` als `[format, score]` sortiert.
- Custom Formats: sortiert, je `{id, specifications}`; jede Spezifikation `{implementation, negate, required, fields: {name: value}}`, die Spezifikationen sortiert (\*arr wertet sie ohne Reihenfolge aus).
- Release-Profile: sortiert, je `{enabled, indexerId, required, ignored, tags}` (ohne `id`, wie in der Spec; Codex-Runde 2, F9: upstream entscheidet die ID nichts, `ReleaseProfileService.EnabledForTags` wählt nach Tags, `Enabled` und `IndexerId`, `ReleaseRestrictionsSpecification` liest nur `Required`/`Ignored`, kein Qualitätsprofil verweist auf eine Release-Profil-ID; ein gelöschtes und gleich neu angelegtes Release-Profil gibt so nichts frei); `required`/`ignored` als sortierte Menge (als Text: an Kommas getrennt wie upstream `ParseArray`), `tags` sortiert. Die ID eines Custom Formats bleibt dagegen drin: `formatItems.format` verweist auf sie.
- Weg fallen alle Namen, `implementationName`, `infoLink`, `presets`, `includeCustomFormatWhenRenaming` und bei Feldern alles außer `name`/`value`.

Bewusste Lücke: Ein Regel-Feld, das ein künftiges \*arr-Update neu einführt, zählt erst, wenn es in die Auswahl aufgenommen wird (Docstring, README). Antwortet \*arr nicht mit drei Listen, löst `fingerprints` `ValueError` aus — G3 behandelt das wie einen gescheiterten Abruf (gespeicherter Stand bleibt, nichts wird freigegeben).

- [ ] **Step 1: Failing test schreiben**

`tests/test_g1_fingerprint.py`:

```python
import copy

import pytest

from backend.checked_search.fingerprint import changes, fingerprint, fingerprints, short

# The shape of the API resources (Radarr 6.4 / Sonarr 4.0), display fields included.
PROFILE = {
    "id": 1, "name": "HD", "upgradeAllowed": True, "cutoff": 7, "minFormatScore": 0, "cutoffFormatScore": 100,
    "minUpgradeFormatScore": 1, "language": {"id": 1, "name": "English"},
    "items": [
        {"id": 0, "name": None, "quality": {"id": 3, "name": "WEBDL-1080p", "source": "webdl", "resolution": 1080},
         "items": [], "allowed": True},
        {"id": 1001, "name": "Bluray", "quality": None, "allowed": True, "items": [
            {"id": 0, "quality": {"id": 7, "name": "Bluray-1080p"}, "items": [], "allowed": True}]},
    ],
    "formatItems": [{"format": 3, "name": "German", "score": 100}, {"format": 4, "name": "Remux", "score": -10}],
}
FORMATS = [
    {"id": 3, "name": "German", "includeCustomFormatWhenRenaming": False, "specifications": [
        {"name": "German", "implementation": "LanguageSpecification", "implementationName": "Language",
         "infoLink": "https://wiki.example/custom-formats", "negate": False, "required": True,
         "fields": [{"order": 0, "name": "value", "label": "Language", "helpText": "", "value": 4,
                     "type": "select", "advanced": False,
                     "selectOptions": [{"value": 1, "name": "English"}, {"value": 4, "name": "German"}]}]},
        {"name": "Not English", "implementation": "LanguageSpecification", "implementationName": "Language",
         "negate": True, "required": False,
         "fields": [{"order": 0, "name": "value", "label": "Language", "value": 1, "type": "select"}]}]},
    {"id": 4, "name": "Remux", "specifications": [
        {"name": "Remux", "implementation": "QualityModifierSpecification", "implementationName": "Quality Modifier",
         "negate": False, "required": True, "fields": [{"name": "value", "label": "Quality Modifier", "value": 5}]}]},
]
RELEASE_PROFILES = [{"id": 1, "name": "German", "enabled": True, "required": ["German", "DL"], "ignored": [],
                     "indexerId": 0, "tags": [1, 2]}]


def base():
    return fingerprint(PROFILE, FORMATS, RELEASE_PROFILES)


def changed(mutate):
    """The fingerprint after mutate(profile, formats, release_profiles) on copies."""
    profile, formats, release_profiles = copy.deepcopy((PROFILE, FORMATS, RELEASE_PROFILES))
    mutate(profile, formats, release_profiles)
    return fingerprint(profile, formats, release_profiles)


def reorder(value):
    """The same content with every dict's keys in reverse order."""
    if isinstance(value, dict):
        return {key: reorder(value[key]) for key in reversed(list(value))}
    if isinstance(value, list):
        return [reorder(item) for item in value]
    return value


def test_fingerprint_is_16_hex_characters():
    value = base()
    assert len(value) == 16 and int(value, 16) >= 0


def test_same_content_in_another_key_order_gives_the_same_fingerprint():
    assert fingerprint(reorder(PROFILE), reorder(FORMATS), reorder(RELEASE_PROFILES)) == base()


def test_a_changed_score_changes_the_fingerprint():
    assert changed(lambda p, f, r: p["formatItems"][0].update(score=50)) != base()


@pytest.mark.parametrize("mutate", [
    lambda p, f, r: p.update(cutoffFormatScore=200),
    lambda p, f, r: p.update(minUpgradeFormatScore=5),
    lambda p, f, r: p.update(upgradeAllowed=False),
    lambda p, f, r: p.update(language={"id": 4, "name": "German"}),
    lambda p, f, r: p["items"][1]["items"][0].update(allowed=False),
    lambda p, f, r: f[0]["specifications"][1].update(negate=False),
    lambda p, f, r: f[1]["specifications"][0].update(required=False),
    lambda p, f, r: f[1]["specifications"][0]["fields"][0].update(value=6),
], ids=["cutoff score", "min upgrade score", "upgrades", "language", "allowed", "negate", "required", "field value"])
def test_every_rule_field_counts(mutate):
    assert changed(mutate) != base()


def test_the_quality_ranking_keeps_its_order():
    assert changed(lambda p, f, r: p["items"].reverse()) != base()


def test_display_texts_and_names_do_not_count():
    def relabel(p, f, r):
        p["name"] = "Renamed"
        p["formatItems"][0]["name"] = "Deutsch"
        p["items"][1]["name"] = "Disc"
        p["items"][0]["quality"]["name"] = "WEB 1080p"
        f[0]["name"] = "Deutsch"
        spec = f[0]["specifications"][0]
        spec.update(name="Deutsch", implementationName="Sprache", infoLink="https://wiki.example/other")
        spec["fields"][0].update(label="Sprache", helpText="Die Sprache", order=3,
                                 selectOptions=[{"value": 4, "name": "Deutsch"}, {"value": 99, "name": "Klingon"}])
        r[0]["name"] = "Deutsch"
    assert changed(relabel) == base()


def test_unordered_collections_do_not_count():
    def shuffle(p, f, r):
        p["formatItems"].reverse()
        f.reverse()
        f[1]["specifications"].reverse()        # the German format after f.reverse()
        r[0]["tags"].reverse()
        r[0]["required"].reverse()
    assert changed(shuffle) == base()
    other = {"id": 2, "enabled": True, "required": [], "ignored": ["CAM"], "indexerId": 0, "tags": []}
    assert fingerprint(PROFILE, FORMATS, [RELEASE_PROFILES[0], other]) == \
        fingerprint(PROFILE, FORMATS, [other, RELEASE_PROFILES[0]])


def test_terms_as_text_count_like_a_list():
    assert changed(lambda p, f, r: r[0].update(required="German, DL")) == base()


def test_a_recreated_release_profile_with_a_new_id_does_not_count():
    assert changed(lambda p, f, r: r[0].update(id=7)) == base()


def test_a_changed_custom_format_or_release_profile_changes_every_profile():
    other = {**PROFILE, "id": 2, "name": "UHD"}
    before = fingerprints([PROFILE, other], FORMATS, RELEASE_PROFILES)
    formats = copy.deepcopy(FORMATS)
    formats[0]["specifications"][0]["fields"][0]["value"] = 2
    after_format = fingerprints([PROFILE, other], formats, RELEASE_PROFILES)
    after_release = fingerprints([PROFILE, other], FORMATS, [{**RELEASE_PROFILES[0], "ignored": ["CAM"]}])
    for after in (after_format, after_release):
        assert after["1"] != before["1"] and after["2"] != before["2"]


def test_fingerprints_are_keyed_by_profile_id():
    other = {**PROFILE, "id": 2, "name": "UHD", "cutoff": 19}
    result = fingerprints([PROFILE, other, {"name": "no id"}, {"id": True}], FORMATS, [])
    assert set(result) == {"1", "2"}
    assert result["1"] != result["2"]
    # the id itself is no rule: two profiles with the same rules share a fingerprint
    assert fingerprints([PROFILE, {**PROFILE, "id": 3, "name": "Copy"}], FORMATS, [])["3"] == result["1"]


@pytest.mark.parametrize("answers", [({"id": 1}, [], []), ([PROFILE], None, []), ([PROFILE], [], "x")])
def test_unexpected_answers_are_refused(answers):
    with pytest.raises(ValueError):
        fingerprints(*answers)


def test_changes_names_only_profiles_that_existed_before():
    assert changes({"1": "aaa", "2": "bbb", "3": "ccc"}, {"1": "aaa", "2": "xxx", "4": "ddd"}) == [("2", "bbb", "xxx")]
    assert changes({}, {"1": "aaa"}) == []


def test_short():
    assert short("0123456789abcdef") == "01234567"
    assert short(None) == "—"
```

- [ ] **Step 2: Test laufen lassen, er muss scheitern**

Run: `cd <repo> && .venv/bin/python -m pytest tests/test_g1_fingerprint.py -q -p no:cacheprovider`
Expected: FAIL mit `ModuleNotFoundError: No module named 'backend.checked_search.fingerprint'`

- [ ] **Step 3: Implementieren**

`backend/checked_search/fingerprint.py`:

```python
"""Fingerprint of a quality profile (spec addendum: notice profile changes).

Radarr and Sonarr score every release with the profile in force when they
search; missingarr does not need to know the rules. It only notices that a
profile changed: a title searched under the old profile may be searched
again, and a dry-run row checked under it no longer counts for the round.

One fingerprint per quality profile: SHA-256 over canonical JSON (sorted
keys) of the fields that decide a release's score, cut to 16 hex characters.
Not the whole API resources: they carry translated labels, help texts and
select options that change with the UI language or an *arr update.
  profile          the quality ranking (order kept: it is the priority),
                   cutoff, upgradeAllowed, minFormatScore, cutoffFormatScore,
                   minUpgradeFormatScore, the format scores, language (Radarr)
  custom formats   per specification: implementation, negate, required and
                   the field values
  release profiles enabled, indexerId, required, ignored, tags
Names and display fields do not count; unordered collections are sorted.
Deliberately coarse: a changed custom format or release profile changes the
fingerprint of every profile. A rule field a future *arr update adds counts
only once it is added here. Pure: no network, no database.
"""

import hashlib
import json

LENGTH = 16
SHORT = 8


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def _sorted(values) -> list:
    return sorted(values, key=_canonical)


def _qualities(items) -> list:
    """The quality ranking with groups, in *arr's order."""
    out = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        quality = item.get("quality")
        out.append({
            "id": item.get("id"),
            "quality": quality.get("id") if isinstance(quality, dict) else None,
            "allowed": item.get("allowed"),
            "items": _qualities(item.get("items")),
        })
    return out


def _profile(profile: dict) -> dict:
    language = profile.get("language")
    return {
        "cutoff": profile.get("cutoff"),
        "upgradeAllowed": profile.get("upgradeAllowed"),
        "minFormatScore": profile.get("minFormatScore"),
        "cutoffFormatScore": profile.get("cutoffFormatScore"),
        "minUpgradeFormatScore": profile.get("minUpgradeFormatScore"),
        "language": language.get("id") if isinstance(language, dict) else None,
        "items": _qualities(profile.get("items")),
        "formatItems": _sorted([item.get("format"), item.get("score")]
                               for item in profile.get("formatItems") or [] if isinstance(item, dict)),
    }


def _specification(spec: dict) -> dict:
    fields = {field.get("name"): field.get("value") for field in spec.get("fields") or [] if isinstance(field, dict)}
    return {"implementation": spec.get("implementation"), "negate": spec.get("negate"),
            "required": spec.get("required"), "fields": fields}


def _custom_format(custom_format: dict) -> dict:
    specs = [_specification(s) for s in custom_format.get("specifications") or [] if isinstance(s, dict)]
    return {"id": custom_format.get("id"), "specifications": _sorted(specs)}


def _terms(value) -> list:
    """required / ignored: a list, or (as *arr accepts it) one text split at commas."""
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        return []
    return sorted({str(term).strip() for term in value if str(term).strip()})


def _release_profile(profile: dict) -> dict:
    """Without the id: *arr picks release profiles by tags, enabled and
    indexer, never by id, so a profile recreated with the same terms is no
    change."""
    tags = profile.get("tags")
    return {"enabled": profile.get("enabled"), "indexerId": profile.get("indexerId"),
            "required": _terms(profile.get("required")), "ignored": _terms(profile.get("ignored")),
            "tags": _sorted(tags) if isinstance(tags, list) else []}


def fingerprint(profile: dict, custom_formats: list, release_profiles: list) -> str:
    payload = {
        "profile": _profile(profile),
        "customFormats": _sorted(_custom_format(c) for c in custom_formats if isinstance(c, dict)),
        "releaseProfiles": _sorted(_release_profile(r) for r in release_profiles if isinstance(r, dict)),
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()[:LENGTH]


def fingerprints(profiles, custom_formats, release_profiles) -> dict[str, str]:
    """str(profile id) -> fingerprint for every profile with an integer id.
    Anything but three lists is not an answer to rely on: ValueError."""
    for name, value in (("quality profiles", profiles), ("custom formats", custom_formats),
                        ("release profiles", release_profiles)):
        if not isinstance(value, list):
            raise ValueError(f"unexpected answer for the {name}")
    out: dict[str, str] = {}
    for profile in profiles:
        if isinstance(profile, dict) and type(profile.get("id")) is int:
            out[str(profile["id"])] = fingerprint(profile, custom_formats, release_profiles)
    return out


def changes(old: dict, new: dict) -> list[tuple[str, str, str]]:
    """(profile id, old, new) for every profile known before and now with a
    different fingerprint. New or removed profiles are no change of a
    profile a title was searched under."""
    both = sorted(set(old) & set(new), key=lambda key: (len(key), key))
    return [(key, old[key], new[key]) for key in both if old[key] != new[key]]


def short(value: str | None) -> str:
    """The first characters, enough to tell two fingerprints apart in a log."""
    return value[:SHORT] if value else "—"
```

- [ ] **Step 4: Test laufen lassen**

Run: `cd <repo> && .venv/bin/python -m pytest tests/test_g1_fingerprint.py -q -p no:cacheprovider`
Expected: `23 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/checked_search/fingerprint.py tests/test_g1_fingerprint.py
git commit -m "feat: fingerprint the scoring rules of quality profiles to notice profile changes"
```

#### Nachtrag 02.10.2026: Größengrenzen und Indexer-Einstellungen

Anlass: Bei der Umstellung auf die Kompakt-Profile änderten sich die Größengrenzen der Quality Definitions. Sie gelten global für alle Profile und entscheiden in Radarr und Sonarr mit über `approved` („larger than maximum“, „smaller than minimum“); ein Titel, der unter den alten Grenzen nichts fand, blieb trotzdem gesperrt. Seitdem wirkt ihre Änderung wie eine Profiländerung. Die Codeblöcke der Steps 1 und 3 oben zeigen den Stand vor dem Nachtrag; verbindlich sind `backend/checked_search/fingerprint.py` und `tests/test_g1_fingerprint.py` im Repo.

Geprüft am Quellcode (Radarr und Sonarr, `NzbDrone.Core/DecisionEngine`): `GET /release` ist eine vom Nutzer ausgelöste interaktive Suche (`ReleaseController` → `MovieSearch(movieId, true, true)` bzw. `EpisodeSearch(episodeId, true, true)`). `DownloadDecisionMaker.GetDecisionForReport` wertet dabei alle `IDownloadDecisionEngineSpecification` aus, nach Priorität gruppiert, ohne Filter nach Suchart; `approved` ist `!Rejections.Any()`, also auch bei vorläufigen Ablehnungen `false`. Aufgenommen, weil sie bei der interaktiven Suche über `approved` entscheiden:

| Einstellung | Endpunkt, Feld | Spezifikation | Projektion |
|---|---|---|---|
| Größengrenzen je Qualität | `/qualitydefinition`: `quality.id`, `minSize`, `maxSize` | `AcceptableSizeSpecification` (MB je Minute Laufzeit; Sonarr überspringt Specials) | Liste `{quality, minSize, maxSize}`, sortiert nach Qualitäts-ID; Zahlen als float, `null` und `0` gleich (keine Grenze: `MinSize` 0 lehnt nichts ab, `MaxSize` `null`/0 heißt unbegrenzt) |
| Höchstgröße | `/config/indexer`: `maximumSize` | `MaximumSizeSpecification` (0 = aus) | Wert |
| Vorhaltezeit | `/config/indexer`: `retention` | `RetentionSpecification` (nur Usenet, 0 = aus) | Wert |
| Mindestalter | `/config/indexer`: `minimumAge` | `MinimumAgeSpecification` (nur Usenet; prüft die Suchart nicht, greift also auch bei der interaktiven Suche, als vorläufige Ablehnung) | Wert |
| Eingebrannte Untertitel (nur Radarr) | `/config/indexer`: `allowHardcodedSubs`, `whitelistedHardcodedSubs` | `HardcodeSubsSpecification` | `allowHardcodedSubs`; die Begriffe nur, solange es aus ist, als Menge in Kleinbuchstaben ohne leere (upstream `Split(',')`, Vergleich ohne Groß-/Kleinschreibung, Begriffe ungetrimmt) |

Weggelassen und warum:
- `preferredSize`: sortiert nur die freigegebenen Treffer (`DownloadDecisionComparer.CompareSize`), entscheidet nicht über `approved`. `weight`: von \*arr fest vorgegeben (`QualityDefinitionService` setzt ihn aus den Vorgaben, die Datenbank speichert ihn nicht). `title`, `quality.name`, die Zeilen-`id`: Beschriftung bzw. Zeile, nicht die Qualität.
- `rssSyncInterval`: nur der RSS-Takt. `preferIndexerFlags`: nur Sortierung (`CompareIndexerFlags`). `availabilityDelay` (Radarr): `AvailabilitySpecification` liegt unter `RssSync` und nimmt bei `UserInvokedSearch` alles an.
- Außerhalb dieser beiden Endpunkte, bewusst nicht im Nachtrag (eine Änderung gibt nichts frei, die vorsichtige Richtung): Delay-Profile (`/delayprofile`: `enableUsenet`/`enableTorrent`, `ProtocolSpecification`; die Verzögerung selbst überspringt die Suche), Einstellungen einzelner Indexer (Mindest-Seeder `TorrentSeedingSpecification`, Pflicht-Flags `RequiredIndexerFlagsSpecification` bei Radarr), `downloadPropersAndRepacks` aus `/config/mediamanagement` (`RepackSpecification`, `UpgradableSpecification`: nur Repacks und Propers gegenüber der vorhandenen Datei), freier Speicher (`FreeSpaceSpecification`, hängt am Datenträger) und `enableCompletedDownloadHandling` (`AlreadyImportedSpecification`, hängt am Verlauf). Wird eine davon gebraucht, kommt sie wie hier in die Auswahl.

Wie Custom Formats und Release-Profile fließen beide Projektionen in den Fingerabdruck **jedes** Profils ein (`_shared`, einmal je Lauf berechnet). Signaturen: `fingerprint(profile, custom_formats, release_profiles, quality_definitions, indexer_config)`, `fingerprints(…)` mit denselben fünf Antworten; ist `/qualitydefinition` keine Liste oder `/config/indexer` kein Objekt, `ValueError` (G3: gespeicherter Stand bleibt, nichts wird freigegeben).

Grundlinie: 0.9.0 ist noch nicht ausgerollt, es gibt keinen gespeicherten Fingerabdruck alter Form. Die Grundlinien-Logik bleibt unverändert (`store_profile_fingerprints` setzt sie einmal per `COALESCE`; Alt-Einträge ohne Fingerabdruck sperren bis zur ersten echten Änderung); `test_cache_entries_from_before_0_9_0_block_until_a_size_limit_changes` prüft das mit einer Größenänderung als erster Änderung.

Tests (`tests/test_g1_fingerprint.py`): Größenänderung (min, max, max aus unbegrenzt), `maximumSize`, `retention`, `minimumAge`, beide Untertitel-Felder ändern ihn; Titel, `quality.name`, `weight`, `preferredSize`, Zeilen-ID, `rssSyncInterval`, `preferIndexerFlags`, `availabilityDelay` nicht; umgeordnete Liste nicht; `null` und `0` gleich; Sonarr-Antwort ohne Untertitel-Felder; eine Größenänderung trifft jedes Profil; falsche Form → `ValueError`. Bestehende Tests rufen `fingerprint`/`fingerprints` mit den zwei neuen Antworten auf; feste Werte erwartete keiner.

---

## Paket G2 — Daten (Welle 1)

Abnahme G2: `tests/test_g2_*.py` grün (`66 passed`); Gesamtsuite im Worktree `419 passed`; die Befehlsprüfung liest weiter nur offene Befehle:
`grep -n "si.command_status = ?" backend/db/history.py` zeigt die Zeile in `get_pending_items` mit Parameter `ITEM_SUBMITTED` (unverändert). Keine Datei außerhalb der G2-Liste ist geändert (`git diff --stat feat/checked-search`).

### Task G2.1: Schema und Migration

**Files:**
- Modify: `backend/database.py` (`_SCHEMA`, `_COLUMN_MIGRATIONS`)
- Test: `tests/test_g2_database.py`

**Interfaces:**
- Consumes: nichts
- Produces: Spalten `instances.checked_search`, `instances.checked_search_settings`, `instances.dry_run_round`, `instances.dry_run_round_started_at`, `instances.search_again_after_profile_change`, `instances.profile_fingerprints`, `instances.profile_fingerprints_baseline`, `searched_items.profile_fingerprint`, `searched_items.grabbed_at`, `searched_items.history_item_id`; Tabelle `checked_search_log` (mit `profile_fingerprint`, `profile_id`, `dry_run_round`, `settings_fingerprint`, Ergebnis `grab_uncertain`) und Indizes (siehe „Schemaänderungen“).

- [ ] **Step 1: Failing test schreiben**

`tests/test_g2_database.py`:

```python
import sqlite3

import pytest

from backend import database
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


def columns(conn, table):
    return {row[1]: row for row in conn.execute(f"PRAGMA table_info({table})")}


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


def test_fresh_database_has_the_checked_search_schema(db_path):
    conn = sqlite3.connect(db_path)
    try:
        cols = columns(conn, "instances")
        assert {"checked_search", "checked_search_settings", "dry_run_round", "dry_run_round_started_at",
                "search_again_after_profile_change", "profile_fingerprints",
                "profile_fingerprints_baseline"} <= set(cols)
        assert {"profile_fingerprint", "grabbed_at", "history_item_id"} <= set(columns(conn, "searched_items"))
        assert set(columns(conn, "checked_search_log")) == {
            "id", "instance_id", "run_id", "mode", "skill", "arr_id", "cache_key", "title", "created_at",
            "outcome", "arr_pick", "pick", "pick_indexer", "pick_score", "pick_size", "pick_quality",
            "candidates", "error_message", "profile_fingerprint", "profile_id", "dry_run_round",
            "settings_fingerprint",
        }
        indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert {"idx_cs_log_instance_created", "idx_cs_log_instance_mode_key"} <= indexes
    finally:
        conn.close()


def test_0_8_0_database_gets_the_new_columns_with_defaults(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    monkeypatch.setattr(settings, "database_url", str(path))
    historic_database_module("v0.8.0").init_db()
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO instances (id, name, type, url, api_key) "
                 "VALUES (1, 'Radarr', 'radarr', 'http://radarr:7878', 'enc:abc')")
    conn.execute("INSERT INTO searched_items (instance_id, cache_key, title, item_type) "
                 "VALUES (1, 'mov:5', 'Old', 'movie')")
    conn.commit()
    conn.close()

    database.init_db()
    database.init_db()  # idempotent

    conn = sqlite3.connect(path)
    try:
        row = conn.execute("SELECT checked_search, checked_search_settings, dry_run_round, "
                           "dry_run_round_started_at, search_again_after_profile_change, profile_fingerprints, "
                           "profile_fingerprints_baseline FROM instances WHERE id=1").fetchone()
        assert row == ("off", "{}", 0, None, 1, "{}", None)
        # cached before 0.9.0: no fingerprint, counts as searched under the baseline; no checked grab;
        # no item named as its writer
        assert conn.execute("SELECT profile_fingerprint, grabbed_at, history_item_id FROM searched_items"
                            ).fetchall() == [(None, None, None)]
        assert conn.execute("SELECT COUNT(*) FROM checked_search_log").fetchone()[0] == 0
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def test_unknown_checked_search_mode_is_refused(db_path):
    sql("INSERT INTO instances (name, type, url, api_key) VALUES ('R', 'radarr', 'http://r', 'k')")
    with pytest.raises(sqlite3.IntegrityError):
        sql("UPDATE instances SET checked_search='maybe'")
```

- [ ] **Step 2: Test laufen lassen, er muss scheitern**

Run: `cd <repo> && .venv/bin/python -m pytest tests/test_g2_database.py -q -p no:cacheprovider`
Expected: `3 failed` (Spalten und Tabelle fehlen, `no such column: checked_search`)

- [ ] **Step 3: Implementieren**

In `backend/database.py`, `_SCHEMA`, Tabelle `instances` — Edit, old:

```
                upgrade_source           TEXT NOT NULL DEFAULT 'monitored_items_only'
                                         CHECK(upgrade_source IN ('wanted_list_only','monitored_items_only','both')),

                quiet_start              TEXT,
```

new:

```
                upgrade_source           TEXT NOT NULL DEFAULT 'monitored_items_only'
                                         CHECK(upgrade_source IN ('wanted_list_only','monitored_items_only','both')),

                checked_search           TEXT NOT NULL DEFAULT 'off'
                                         CHECK(checked_search IN ('off','dry_run','active')),
                checked_search_settings  TEXT NOT NULL DEFAULT '{}',
                dry_run_round            INTEGER NOT NULL DEFAULT 0,
                dry_run_round_started_at TEXT,
                search_again_after_profile_change INTEGER NOT NULL DEFAULT 1,
                profile_fingerprints     TEXT NOT NULL DEFAULT '{}',
                profile_fingerprints_baseline TEXT,

                quiet_start              TEXT,
```

`_SCHEMA`, Tabelle `searched_items` — Edit, old:

```
                searched_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                UNIQUE(instance_id, cache_key)
```

new:

```
                searched_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                profile_fingerprint TEXT,
                grabbed_at  TEXT,
                history_item_id INTEGER,
                UNIQUE(instance_id, cache_key)
```

Ende von `_SCHEMA` — Edit, old:

```
            CREATE TABLE IF NOT EXISTS app_settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
"""
```

new:

```
            CREATE TABLE IF NOT EXISTS app_settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS checked_search_log (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id   INTEGER NOT NULL REFERENCES instances(id) ON DELETE CASCADE,
                run_id        INTEGER REFERENCES search_history(id) ON DELETE SET NULL,
                mode          TEXT NOT NULL CHECK(mode IN ('dry_run','active')),
                skill         TEXT NOT NULL CHECK(skill IN ('search_missing','search_upgrades')),
                arr_id        INTEGER,
                cache_key     TEXT NOT NULL DEFAULT '',
                title         TEXT NOT NULL,
                created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f','now','localtime')),
                outcome       TEXT NOT NULL CHECK(outcome IN ('grabbed','would_grab','no_clean_hit',
                                                              'no_results','error','grab_failed',
                                                              'grab_uncertain')),
                arr_pick      TEXT,
                pick          TEXT,
                pick_indexer  TEXT,
                pick_score    INTEGER,
                pick_size     INTEGER,
                pick_quality  TEXT,
                candidates    TEXT NOT NULL DEFAULT '[]',
                error_message TEXT,
                profile_fingerprint TEXT,
                profile_id    INTEGER,
                dry_run_round INTEGER,
                settings_fingerprint TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_cs_log_instance_created ON checked_search_log(instance_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_cs_log_instance_mode_key ON checked_search_log(instance_id, mode, cache_key);
"""
```

`_COLUMN_MIGRATIONS` — Edit, old:

```
    # Fair rotation and "expire only after asking" in verify_commands (B4, B5).
    ("search_history_items", "last_checked_at", "TEXT"),
]
```

new:

```
    # Fair rotation and "expire only after asking" in verify_commands (B4, B5).
    ("search_history_items", "last_checked_at", "TEXT"),
    # Checked search (0.9.0).
    ("instances", "checked_search",
     "TEXT NOT NULL DEFAULT 'off' CHECK(checked_search IN ('off','dry_run','active'))"),
    ("instances", "checked_search_settings", "TEXT NOT NULL DEFAULT '{}'"),
    ("instances", "dry_run_round", "INTEGER NOT NULL DEFAULT 0"),
    ("instances", "dry_run_round_started_at", "TEXT"),
    # Notice profile changes (0.9.0): a cache entry blocks only under the
    # fingerprint of the profile it was searched with. Rows without one
    # were searched before 0.9.0 and count under the baseline.
    ("instances", "search_again_after_profile_change", "INTEGER NOT NULL DEFAULT 1"),
    ("instances", "profile_fingerprints", "TEXT NOT NULL DEFAULT '{}'"),
    ("instances", "profile_fingerprints_baseline", "TEXT"),
    ("searched_items", "profile_fingerprint", "TEXT"),
    # A grab of the checked search: "Search again if still missing" (0.9.0).
    ("searched_items", "grabbed_at", "TEXT"),
    # The history item that wrote the entry last: a failed command releases
    # only its own entry, even after the history was cleared (0.9.0). No
    # foreign key on purpose — clearing the history must not blank it.
    ("searched_items", "history_item_id", "INTEGER"),
]
```

- [ ] **Step 4: Tests laufen lassen**

Run: `cd <repo> && .venv/bin/python -m pytest tests/test_g2_database.py tests/test_p3_database.py tests/test_database_regressions.py -q -p no:cacheprovider`
Expected: alle grün (`3` neue plus die bestehenden Migrations-Tests, auch die echte Release-Kette)

- [ ] **Step 5: Commit**

```bash
git add backend/database.py tests/test_g2_database.py
git commit -m "feat: add checked-search columns and the pre-filter log table"
```

---

### Task G2.2: Vorfilter-Protokoll

**Files:**
- Create: `backend/db/checked_search_log.py`
- Modify: `backend/db/__init__.py`
- Test: `tests/test_g2_log.py`

**Interfaces:**
- Consumes: G2.1 (Tabelle)
- Produces: `db.checked_search_log.insert_with/insert/dry_run_keys/query/count/summary/purge_old/iter_csv`, `CSV_HEADER`, `MODES`, `OUTCOMES` (Vertrag G2).

CSV: eine Zeile pro Kandidat, ein Titel ohne Kandidaten (keine Treffer, Fehler) bekommt eine Zeile ohne Release; dort steht die Fehlermeldung in der Spalte `reasons`. Die Spalte `notes` (Hinweise wie bei S3) ist eine Ergänzung zur Spalten-Liste der Spec. Zellen, die mit `=`, `+`, `-`, `@`, Tab oder CR beginnen, bekommen ein `'` davor (Tabellen-Formeln). Gelesen wird seitenweise zu 500 Zeilen, und zwar aus **einer Lesetransaktion** (ein Datenbankstand, kein `OFFSET`; Codex-Runde 3, G5, mit Probe bestätigt): `iter_csv` öffnet eine eigene Verbindung, startet `BEGIN`, führt genau eine Abfrage mit Filter und Sortierung aus und liest sie mit `fetchmany` (unter WAL bleibt der Stand bis zum Ende; Schreiber werden nicht blockiert). Vorher las jede Seite neu mit `OFFSET`: Eine Zeile, die ein laufender Suchlauf während des Downloads schrieb, schob die Grenzzeile in die nächste Seite (doppelt), und ein „Reset dry run“ während des Downloads ließ mit `current_round` alle Zeilen der Runde weg, die beim Start lief. `try/finally` schließt die Verbindung auch bei einem abgebrochenen Download (`GeneratorExit`); `check_same_thread=False` ist gesetzt (Starlette ruft den Generator in wechselnden Threads auf). `query` und `iter_csv` teilen dieselbe Abfrage (`_SELECT`).

`dry_run_keys` zählt nur Zeilen, die einen Titel erledigen: `outcome = 'error'` (Laden oder Release-Suche gescheitert, etwa durch eine Zeitüberschreitung) zählt nicht, der Titel kommt im nächsten Probelauf wieder dran (Spec: „Fehler … Titel nicht gemerkt“). Statt einer Menge liefert es je Titel die Paare (Profil-Fingerabdruck, Einstellungs-Fingerabdruck) seiner Zeilen der Runde; G3 entscheidet damit, ob der Titel unter dem aktuellen Profil und den aktuellen Regel-Einstellungen schon geprüft ist (Spec-Nachtrag, Entscheidung Daniel 01.10.2026).

Die Runde ist ein **Zähler** (Codex K8): Jede Probelauf-Zeile trägt `dry_run_round`, die Runde, in der ihr Lauf begann (G3 liest sie beim Start). `dry_run_keys(instance_id, dry_run_round)` und `current_round` vergleichen nur diesen Zähler, nie Zeitstempel: Ein Lauf, der vor „Reset dry run“ begann und danach noch schreibt, landet in der alten Runde, und die Zeitumstellung im Herbst stört nicht. `current_round` filtert Probelauf-Zeilen auf `dry_run_round = instances.dry_run_round`, aktive Zeilen bleiben immer drin. Ohne diesen Filter stünden nach „Reset dry run“ alte und neue Urteile zum selben Titel gemischt in Liste, Zählern, „only differences“ und CSV. `profile_changed` setzt `query` je Zeile, und zwar gegen das Profil, unter dem die Zeile geprüft wurde (Spalte `profile_id`, Codex-Runde 2, F7): Die Zeile trägt einen Fingerabdruck, die Instanz hat ihre Profile schon einmal gelesen, und der aktuelle Fingerabdruck dieses Profils ist ein anderer (ein inzwischen gelöschtes Profil zählt als geändert). „Schon einmal gelesen“ heißt: `instances.profile_fingerprints_baseline` ist gesetzt (Codex-Runde 3, G6). Ein leeres `profile_fingerprints` allein sagt das nicht: Radarr und Sonarr lassen auch das letzte Profil löschen, solange kein Titel und keine Import-Liste es nutzt (`QualityProfileService.Delete`; neue Standardprofile legen sie erst beim nächsten Start an), und dann ist `{}` ein gelesener Stand, in dem jedes Profil der Zeilen fehlt. Ohne den Vergleich je Profil verdeckte ein zweites, unverändertes Profil mit denselben Regeln die Änderung. Zeilen ohne `profile_id` vergleichen gegen alle aktuellen Fingerabdrücke. (Ein Titel, der nur in ein anderes, unverändertes Profil verschoben wurde, bekommt die Plakette nicht; der Probelauf prüft ihn trotzdem erneut, weil sein aktueller Fingerabdruck ein anderer ist.)

Aufbewahrung (Entscheidung Daniel 01.10.2026, Codex-Runde 2, F6): `purge_old` löscht nie Probelauf-Zeilen der laufenden Runde einer Instanz (`mode = 'dry_run' AND dry_run_round = instances.dry_run_round`). Die Runde lebt nur in diesen Zeilen; löschte die Hauspflege sie, würden ihre Titel still wieder geprüft, ohne Reset und ohne Profil- oder Einstellungsänderung, und eine Runde, die länger dauert als die Aufbewahrung, würde nie fertig. Zeilen älterer Runden und aktive Zeilen löscht sie wie bisher nach `HISTORY_RETENTION_DAYS`.

- [ ] **Step 1: Failing test schreiben**

`tests/test_g2_log.py`:

```python
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
    # without a reset (decision Daniel 01.10.2026).
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
```

- [ ] **Step 2: Test laufen lassen, er muss scheitern**

Run: `cd <repo> && .venv/bin/python -m pytest tests/test_g2_log.py -q -p no:cacheprovider`
Expected: FAIL mit `ImportError: cannot import name 'checked_search_log' from 'backend.db'`

- [ ] **Step 3: Implementieren**

`backend/db/__init__.py` komplett:

```python
from backend.db import instances, activity, history, searched, app_settings, checked_search_log
```

`backend/db/checked_search_log.py`:

```python
"""Pre-filter log of the checked search: one row per title and run.

Never stored here: downloadUrl, infoUrl, guid, headers. The runner hands in
plain titles and numbers only (backend/checked_search/runner.py).
"""

import csv
import io
import json
import sqlite3
from typing import Iterator, Optional

from backend.database import get_connection, get_db

MODES = ("dry_run", "active")
OUTCOMES = ("grabbed", "would_grab", "no_clean_hit", "no_results", "error", "grab_failed", "grab_uncertain")

CSV_HEADER = ["time", "instance", "mode", "title", "outcome", "release", "indexer", "score", "size",
              "quality", "verdict", "reasons", "notes", "chosen", "arr_would_grab"]
_CSV_PAGE = 500

_INSERT = """
    INSERT INTO checked_search_log
        (instance_id, run_id, mode, skill, arr_id, cache_key, title, outcome,
         arr_pick, pick, pick_indexer, pick_score, pick_size, pick_quality, candidates, error_message,
         profile_fingerprint, profile_id, dry_run_round, settings_fingerprint)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

# A dry-run row belongs to the current round when it carries the instance's
# round counter (the round its run began in). Active rows always count.
_CURRENT_ROUND = "(l.mode != 'dry_run' OR l.dry_run_round = i.dry_run_round)"

# The rows of the page and of the CSV, newest first. fingerprints_known: the
# instance has read its profiles at least once (baseline set) — an empty
# profile list that was read is no "never read".
_SELECT = """
    SELECT l.*, COALESCE(i.name, '') AS instance_name, COALESCE(i.type, '') AS arr_type,
           i.profile_fingerprints AS current_fingerprints,
           i.profile_fingerprints_baseline IS NOT NULL AS fingerprints_known
    FROM checked_search_log l
    LEFT JOIN instances i ON i.id = l.instance_id
    {where}
    ORDER BY l.created_at DESC, l.id DESC
"""


def insert_with(conn: sqlite3.Connection, entry: dict) -> int:
    """Write one log row on an open connection (so a caller can put it in the
    same transaction as the history item and the cache entry).

    entry keys: instance_id, run_id, mode, skill, arr_id, cache_key, title,
    outcome, arr_pick, pick (dict with title/indexer/score/size/quality, or
    None), candidates (list of dicts), error_message, profile_fingerprint
    and profile_id (the title's quality profile when it was checked),
    dry_run_round (the round its run began in, dry run only),
    settings_fingerprint (the rule settings it was checked under)."""
    pick = entry.get("pick") or {}
    profile_id = entry.get("profile_id")
    cursor = conn.execute(_INSERT, (
        entry["instance_id"], entry.get("run_id"), entry["mode"], entry["skill"], entry.get("arr_id"),
        entry.get("cache_key") or "", entry["title"], entry["outcome"], entry.get("arr_pick"),
        pick.get("title"), pick.get("indexer"), pick.get("score"), pick.get("size"), pick.get("quality"),
        json.dumps(list(entry.get("candidates") or []), ensure_ascii=False), entry.get("error_message"),
        entry.get("profile_fingerprint"),
        profile_id if isinstance(profile_id, int) and not isinstance(profile_id, bool) else None,
        entry.get("dry_run_round"), entry.get("settings_fingerprint"),
    ))
    return cursor.lastrowid


def insert(entry: dict) -> int:
    with get_db() as conn:
        return insert_with(conn, entry)


def dry_run_keys(instance_id: int, dry_run_round: int) -> dict[str, set]:
    """cache_key -> {(profile fingerprint, settings fingerprint)} of its
    dry-run rows in the given round (the round counter the run began with).

    Errors do not count: a title whose load or release search failed was
    not checked and comes up again in the next dry run. The caller decides
    with the pairs whether the title was checked under its current quality
    profile and the current rule settings (a change means check again)."""
    keys: dict[str, set] = {}
    with get_db() as conn:
        rows = conn.execute(
            "SELECT cache_key, profile_fingerprint, settings_fingerprint FROM checked_search_log "
            "WHERE instance_id=? AND mode='dry_run' AND dry_run_round=? AND cache_key != '' "
            "AND outcome != 'error'",
            (instance_id, dry_run_round),
        )
        for cache_key, profile_fingerprint, settings_fingerprint in rows:
            keys.setdefault(cache_key, set()).add((profile_fingerprint, settings_fingerprint))
    return keys


def _json_dict(raw) -> dict:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _filter(instance_id, mode, outcome, search, only_differences, current_round=False) -> tuple[str, list]:
    conditions: list[str] = []
    params: list = []
    if instance_id is not None:
        conditions.append("l.instance_id=?")
        params.append(instance_id)
    if mode:
        conditions.append("l.mode=?")
        params.append(mode)
    if outcome:
        conditions.append("l.outcome=?")
        params.append(outcome)
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        conditions.append("(l.title LIKE ? ESCAPE '\\' OR i.name LIKE ? ESCAPE '\\')")
        params += [pattern, pattern]
    if only_differences:
        # The filter takes something else than *arr would have, or nothing.
        conditions.append("l.arr_pick IS NOT NULL AND (l.pick IS NULL OR l.pick != l.arr_pick)")
    if current_round:
        conditions.append(_CURRENT_ROUND)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    return where, params


def _row(row: sqlite3.Row) -> dict:
    out = dict(row)
    try:
        candidates = json.loads(out.get("candidates") or "[]")
    except ValueError:
        candidates = []
    out["candidates"] = candidates if isinstance(candidates, list) else []
    out["rejected_count"] = sum(1 for c in out["candidates"] if isinstance(c, dict) and c.get("verdict") == "reject")
    # Checked under a fingerprint its profile no longer has: the profile
    # changed since (spec addendum, badge "profile changed"). Compared with
    # the very profile the row was checked under, so an unchanged twin with
    # the same rules does not hide the change; a removed profile counts as
    # changed, the last one too. Rows without a profile id compare with
    # every profile. Unknown = never read (no baseline), not an empty list.
    current = _json_dict(out.pop("current_fingerprints", None))
    known = bool(out.pop("fingerprints_known", 0))
    fingerprint = out.get("profile_fingerprint")
    profile_id = out.get("profile_id")
    if not fingerprint or not known:
        changed = False
    elif profile_id is not None:
        changed = current.get(str(profile_id)) != fingerprint
    else:
        changed = fingerprint not in set(current.values())
    out["profile_changed"] = changed
    return out


def query(instance_id: Optional[int] = None, mode: Optional[str] = None, outcome: Optional[str] = None,
          search: Optional[str] = None, only_differences: bool = False, current_round: bool = False,
          limit: int = 50, offset: int = 0) -> list[dict]:
    where, params = _filter(instance_id, mode, outcome, search, only_differences, current_round)
    with get_db() as conn:
        rows = conn.execute(_SELECT.format(where=where) + " LIMIT ? OFFSET ?",
                            [*params, limit, offset]).fetchall()
        return [_row(r) for r in rows]


def count(instance_id: Optional[int] = None, mode: Optional[str] = None, outcome: Optional[str] = None,
          search: Optional[str] = None, only_differences: bool = False, current_round: bool = False) -> int:
    where, params = _filter(instance_id, mode, outcome, search, only_differences, current_round)
    with get_db() as conn:
        return conn.execute(
            f"SELECT COUNT(*) FROM checked_search_log l LEFT JOIN instances i ON i.id = l.instance_id {where}",
            params,
        ).fetchone()[0]


def summary(current_round: bool = False) -> list[dict]:
    """Counters for the page: rows per instance, mode and outcome
    (current_round: dry-run rows of the running round only)."""
    where = f"WHERE {_CURRENT_ROUND}" if current_round else ""
    with get_db() as conn:
        rows = conn.execute(
            f"""
            SELECT l.instance_id, COALESCE(i.name, '') AS instance_name, l.mode, l.outcome, COUNT(*) AS n
            FROM checked_search_log l
            LEFT JOIN instances i ON i.id = l.instance_id
            {where}
            GROUP BY l.instance_id, l.mode, l.outcome
            ORDER BY instance_name, l.mode, l.outcome
            """
        ).fetchall()
        return [dict(r) for r in rows]


def purge_old(instance_id: int, days: int) -> int:
    """Same retention as the history (HISTORY_RETENTION_DAYS); 0 keeps all.
    Dry-run rows of the running round are kept until the next reset: the
    round lives in them, and purging them would check their titles again
    without a reset (decision Daniel 01.10.2026)."""
    if days <= 0:
        return 0
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM checked_search_log WHERE instance_id=? "
            "AND created_at < datetime('now','localtime', ? || ' days') "
            "AND NOT (mode='dry_run' AND dry_run_round IS "
            "(SELECT dry_run_round FROM instances WHERE id=?))",
            (instance_id, f"-{days}", instance_id),
        )
        return cursor.rowcount


def _cell(value) -> str:
    """Spreadsheet-safe text: a leading = + - @ would start a formula."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _yes(value) -> str:
    return "yes" if value else "no"


def iter_csv(instance_id: Optional[int] = None, mode: Optional[str] = None, outcome: Optional[str] = None,
             search: Optional[str] = None, only_differences: bool = False,
             current_round: bool = False) -> Iterator[str]:
    """CSV text with the current filter: one line per checked candidate; a
    title without candidates (no results, error) gets one line without a
    release. Read in pages so a long log is never held in memory at once —
    all pages from one read transaction: under WAL the export sees one state
    of the database, so a row a running search writes meanwhile, a purge or
    a "Reset dry run" during the download neither repeats nor drops a row
    (pages with OFFSET did). Writers are not blocked."""
    where, params = _filter(instance_id, mode, outcome, search, only_differences, current_round)
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    def flush() -> str:
        text = buffer.getvalue()
        buffer.seek(0)
        buffer.truncate()
        return text

    writer.writerow(CSV_HEADER)
    yield flush()
    conn = get_connection()
    try:
        conn.execute("BEGIN")
        cursor = conn.execute(_SELECT.format(where=where), params)
        while True:
            rows = cursor.fetchmany(_CSV_PAGE)
            if not rows:
                return
            for row in map(_row, rows):
                base = [row["created_at"][:19], row["instance_name"], row["mode"], row["title"], row["outcome"]]
                candidates = [c for c in row["candidates"] if isinstance(c, dict)]
                if not candidates:
                    writer.writerow([_cell(v) for v in base + ["", "", "", "", "", "",
                                                               row.get("error_message") or "", "", "no", "no"]])
                for c in candidates:
                    writer.writerow([_cell(v) for v in base + [
                        c.get("title"), c.get("indexer"), c.get("score"), c.get("size"), c.get("quality"),
                        c.get("verdict"), "; ".join(c.get("reasons") or []), "; ".join(c.get("notes") or []),
                        _yes(c.get("chosen")), _yes(c.get("arr_choice")),
                    ]])
            yield flush()
    finally:
        # Also when the download is cancelled (GeneratorExit).
        conn.rollback()
        conn.close()
```

- [ ] **Step 4: Test laufen lassen**

Run: `cd <repo> && .venv/bin/python -m pytest tests/test_g2_log.py -q -p no:cacheprovider`
Expected: `21 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/db/__init__.py backend/db/checked_search_log.py tests/test_g2_log.py
git commit -m "feat: add the pre-filter log with filters, counters and CSV"
```

---

### Task G2.3: Verlauf, Verifikation, Hauspflege

**Files:**
- Modify: `backend/verification.py`, `backend/db/history.py` (dazu `resolve_item`), `backend/db/searched.py` (nur `purge_expired`), `backend/skills/verify_commands.py`
- Test: `tests/test_g2_history.py`

**Interfaces:**
- Consumes: G2.2 (`checked_search_log.insert_with`, `purge_old`)
- Produces: `ITEM_GRABBED`, `ITEM_NO_HIT`, `DONE_STATUSES`, `count_verified`, geändertes `aggregate_run_status`; `db.history.record_checked(…, cache=None, hold_key=None)`; `record_submission(…, profile_fingerprint=None)`; ein gemeinsamer Cache-Upsert `_UPSERT_SEARCHED`, der bei einer neuen Suche auch den Fingerabdruck übernimmt (nie `NULL` über einen vorhandenen), `grabbed_at` setzt bzw. leert und das schreibende Item festhält (`history_item_id`); `finish_run` setzt das Urteil sofort, wenn kein Item mehr `submitted` ist; `resolve_item` gibt bei einem gescheiterten Befehl den Cache-Eintrag nur frei, wenn er noch diesem Befehl gehört; `db.searched.purge_expired(instance_id, retry_hours, keep_grab_days=0)` (Vertrag G2).

Was sich für bestehende Läufe ändert: Ein Lauf, dessen Items alle schon ein Urteil haben (früher nur möglich, wenn *arr keine Befehls-ID lieferte und die Items als `expired` gespeichert wurden), endet jetzt sofort mit dem abgeleiteten Status statt zuerst `pending` und bis zu 2 Minuten später über `verify_commands`. Das Ergebnis ist dasselbe. `close_interrupted_runs()` und `expire_stale_items()` brauchen keine Änderung: sie schauen nur auf `submitted` bzw. `failed`. `clear()` und `purge_old_runs()` bleiben ebenfalls unverändert, löschen aber jetzt auch Läufe der geprüften Suche (sofort abgeschlossen, keine offenen Befehle) samt ihren Items. Deshalb hängt die Frage „wem gehört der Cache-Eintrag?“ nicht mehr am Verlauf, sondern am Eintrag selbst (nächster Absatz).

**Ein gescheiterter Befehl gibt nur seinen eigenen Cache-Eintrag frei** (Codex-Runde 2, F3, mit Probe bestätigt; Codex-Runde 3, G1, mit Probe bestätigt). `resolve_item` löschte bisher bei `failed` (auch `orphaned` nach einem Neustart von \*arr, `aborted`, `cancelled`) den Eintrag des Schlüssels, egal welche Suche ihn zuletzt geschrieben hat. Die Befehlsprüfung liest weiter Items `submitted` aus 0.8.0 oder vom Befehlsweg. Merkt die geprüfte Suche denselben Titel inzwischen als geladen (Force Run, Profiländerung, kurzes `retry_hours`; Radarr nutzt in beiden Wegen `mov:<id>`), löschte das späte `failed` des alten Befehls die Grab-Sperre, und der nächste Lauf lud ein zweites Mal (Probe: zweiter Grab). Die dritte Überarbeitung erkannte den Besitz noch an neueren Items im Verlauf; das hielt nicht: `clear()` löscht den sofort abgeschlossenen Grab-Lauf samt Items, behält aber den offenen alten Befehl, und `purge_old_runs()` tut dasselbe bei kurzer Aufbewahrung (Probe: nach „Clear history“ meldete die Befehlsprüfung „released“, die Sperre war weg). Neu merkt sich der Eintrag selbst, welches Item ihn zuletzt geschrieben hat (`searched_items.history_item_id`, gesetzt von `record_submission` und `record_checked` in derselben Transaktion; `searched.add` schreibt `NULL`). `resolve_item` löscht bei `failed` nur, wenn `history_item_id` das gescheiterte Item ist. Einträge ohne Item-ID (vor 0.9.0 geschrieben) behalten als Rückfall die bisherige Prüfung: gelöscht nur ohne `grabbed_at` und wenn für denselben Schlüssel der Instanz kein neueres Item existiert, das den Cache geschrieben hat (Vergleich über die Item-ID, weil `created_at` nur sekundengenau ist; zählt ein Item mit Befehls-ID oder mit `grabbed`/`no_hit`/`failed` und gesetztem Schlüssel). Die Spalte hat bewusst keinen Fremdschlüssel: `ON DELETE SET NULL` machte einen Eintrag nach dem Leeren des Verlaufs zum Alt-Eintrag. Item-IDs werden nicht wiederverwendet (`AUTOINCREMENT`), und gelöscht wird ohnehin nur unter dem Schlüssel des gescheiterten Items. Ein später nachgespeicherter `UnsavedCheckedGrab` schreibt seinen Eintrag mit der neuen Item-ID und schützt ebenso. Das schließt nebenbei denselben Wettlauf zwischen zwei Befehlen, der in 0.8.0 nur eine Suche zu früh auslöste. Das Item selbst bekommt sein Urteil wie bisher.

**Staffel-Halter (Codex-Runde 3, G3, Entscheidung Daniel 02.10.2026):** `record_checked(…, hold_key=…)` schreibt bei einem Grab (`grabbed` oder unklar mit Cache) in derselben Transaktion einen zweiten Cache-Eintrag unter `hold_key`, mit demselben Fingerabdruck, `grabbed_at` und derselben Item-ID (Typ `season`). G3.2 nutzt das für Sonarr-Upgrades: `upg:sea-hold:<serie>:<staffel>` hält die Staffelsuche des Befehlswegs zurück, solange der Grab sperrt. Bei `no_hit` oder einem abgelehnten Grab entsteht kein Halter. `resolve_item` berührt ihn nie (kein Befehl trägt diesen Schlüssel); die Hauspflege behandelt ihn wie jeden Grab-Eintrag (`keep_grab_days`).

**Hauspflege und „Search again if still missing“** (Codex-Runde 2, F1): `purge_expired` löschte jede Zeile außerhalb von `retry_hours`, also auch einen Grab, der laut Spec N Tage sperren soll. Neu bleibt eine Zeile mit `grabbed_at` jünger als „Search again if still missing after (days)“ stehen (`keep_grab_days`, aus `checked_search_settings` der Instanz, Voreinstellung 7, auch bei `checked_search = 'off'`). Danach löscht die Hauspflege sie nach dem normalen Fenster. `verify_commands` liest den Wert roh aus der Instanz (`_grab_days`): Das Einstellungsmodell gehört zu G1 und ist in Welle 1 noch nicht gemergt.

- [ ] **Step 1: Failing test schreiben**

`tests/test_g2_history.py`:

```python
import sqlite3

import pytest

from backend import database, db
from backend.config import settings
from backend.db import history
from backend.skills.verify_commands import VerifyCommandsSkill
from backend.verification import (
    ITEM_COMPLETED, ITEM_FAILED, ITEM_GRABBED, ITEM_NO_HIT, ITEM_SUBMITTED,
    aggregate_run_status, count_verified,
)


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "missingarr.db"
    monkeypatch.setattr(settings, "database_url", str(path))
    monkeypatch.setattr(database, "_cached_secret_key", None)
    database.init_db()
    return path


@pytest.fixture(autouse=True)
def fresh_housekeeping():
    VerifyCommandsSkill._last_housekeeping.clear()
    yield
    VerifyCommandsSkill._last_housekeeping.clear()


def sql(statement, params=()):
    with database.get_db() as conn:
        return [tuple(r) for r in conn.execute(statement, params).fetchall()]


def make_instance():
    return db.instances.create({"name": "Radarr", "type": "radarr", "url": "http://127.0.0.1:9",
                                "api_key": "k" * 32})


def log_entry(inst, run, outcome="grabbed"):
    return {"instance_id": inst["id"], "run_id": run, "mode": "active", "skill": "search_missing",
            "arr_id": 1, "cache_key": "mov:1", "title": "Movie (2020)", "outcome": outcome}


class FailOn:
    """Connection wrapper that fails on the first statement containing `needle`."""

    def __init__(self, conn, needle):
        self._conn, self._needle = conn, needle

    def execute(self, statement, *args):
        if self._needle in statement:
            raise sqlite3.OperationalError("injected failure")
        return self._conn.execute(statement, *args)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_grabbed_and_no_hit_settle_like_completed():
    assert aggregate_run_status([ITEM_GRABBED, ITEM_NO_HIT]) == "success"
    assert aggregate_run_status([ITEM_GRABBED, ITEM_FAILED]) == "partial"
    assert aggregate_run_status([ITEM_NO_HIT, ITEM_SUBMITTED]) == "pending"
    assert count_verified([ITEM_COMPLETED, ITEM_GRABBED, ITEM_NO_HIT, ITEM_FAILED]) == 3


def test_record_checked_writes_item_cache_and_log_together(db_path):
    inst = make_instance()
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    entry = {**log_entry(inst, run), "profile_fingerprint": "aaaa"}
    history.record_checked(run, inst["id"], "Movie (2020)", 1, "movie", "mov:1", ITEM_GRABBED, entry,
                           profile_fingerprint="aaaa")
    assert sql("SELECT command_status, command_id, cache_key FROM search_history_items") == [
        (ITEM_GRABBED, None, "mov:1")]
    assert sql("SELECT verified_at IS NOT NULL FROM search_history_items")[0][0] == 1
    assert sql("SELECT cache_key, profile_fingerprint, grabbed_at IS NOT NULL FROM searched_items") == [
        ("mov:1", "aaaa", 1)]
    assert sql("SELECT run_id, outcome, profile_fingerprint FROM checked_search_log") == [(run, "grabbed", "aaaa")]


def test_a_grab_without_a_clear_answer_is_a_failed_item_that_blocks(db_path):
    inst = make_instance()
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", ITEM_FAILED, cache=True,
                           profile_fingerprint="aaaa")
    assert sql("SELECT command_status, cache_key FROM search_history_items") == [(ITEM_FAILED, "mov:1")]
    assert sql("SELECT cache_key, profile_fingerprint, grabbed_at IS NOT NULL FROM searched_items") == [
        ("mov:1", "aaaa", 1)]


def test_only_a_grab_marks_the_cache_entry(db_path):
    inst = make_instance()
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", ITEM_GRABBED)
    history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", ITEM_NO_HIT)
    assert sql("SELECT grabbed_at FROM searched_items") == [(None,)]
    history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", ITEM_GRABBED)
    history.record_submission(run, inst["id"], "A", 1, "movie", "mov:1", 77)
    assert sql("SELECT grabbed_at FROM searched_items") == [(None,)]


def test_a_search_without_a_fingerprint_keeps_the_stored_one(db_path):
    # The title's profile was unknown this time: the old fingerprint is better than none.
    inst = make_instance()
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    history.record_submission(run, inst["id"], "A", 1, "movie", "mov:1", 77, profile_fingerprint="bbbb")
    history.record_submission(run, inst["id"], "A", 1, "movie", "mov:1", 78)
    history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", ITEM_NO_HIT)
    assert sql("SELECT profile_fingerprint FROM searched_items") == [("bbbb",)]


def test_a_new_search_stores_the_new_fingerprint(db_path):
    inst = make_instance()
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    history.record_submission(run, inst["id"], "A", 1, "movie", "mov:1", 77, profile_fingerprint="aaaa")
    assert sql("SELECT profile_fingerprint FROM searched_items") == [("aaaa",)]
    history.record_submission(run, inst["id"], "A", 1, "movie", "mov:1", 78, profile_fingerprint="bbbb")
    history.record_checked(run, inst["id"], "B", 2, "movie", "mov:2", ITEM_NO_HIT, profile_fingerprint="cccc")
    assert sql("SELECT cache_key, profile_fingerprint FROM searched_items ORDER BY cache_key") == [
        ("mov:1", "bbbb"), ("mov:2", "cccc")]


def test_no_hit_is_cached_failed_is_not(db_path):
    inst = make_instance()
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", ITEM_NO_HIT)
    history.record_checked(run, inst["id"], "B", 2, "movie", "mov:2", ITEM_FAILED)
    assert sql("SELECT cache_key FROM searched_items") == [("mov:1",)]
    assert history.get_item_statuses(run) == [ITEM_NO_HIT, ITEM_FAILED]


def test_unknown_status_is_refused(db_path):
    inst = make_instance()
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    with pytest.raises(ValueError):
        history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", ITEM_SUBMITTED)


def test_record_checked_is_one_transaction(db_path, monkeypatch):
    inst = make_instance()
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    real = database.get_connection
    monkeypatch.setattr(database, "get_connection", lambda: FailOn(real(), "INSERT INTO checked_search_log"))
    with pytest.raises(sqlite3.OperationalError):
        history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", ITEM_GRABBED, log_entry(inst, run))
    monkeypatch.setattr(database, "get_connection", real)
    assert sql("SELECT COUNT(*) FROM search_history_items")[0][0] == 0
    assert sql("SELECT COUNT(*) FROM searched_items")[0][0] == 0


def test_run_with_only_settled_items_finishes_without_waiting(db_path):
    inst = make_instance()
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", ITEM_GRABBED)
    history.record_checked(run, inst["id"], "B", 2, "movie", "mov:2", ITEM_NO_HIT)
    history.finish_run(run, 2, 2, "success")
    assert sql("SELECT status, verified_count FROM search_history WHERE id=?", (run,)) == [("success", 2)]


def test_run_with_a_submitted_command_still_waits(db_path):
    inst = make_instance()
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    history.record_submission(run, inst["id"], "A", 1, "movie", "mov:1", 77)
    history.finish_run(run, 1, 1, "success")
    assert sql("SELECT status FROM search_history WHERE id=?", (run,)) == [("pending",)]


def test_verification_never_asks_about_checked_items(db_path):
    inst = make_instance()
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", ITEM_GRABBED)
    assert history.get_pending_items(inst["id"]) == []
    assert history.expire_stale_items(inst["id"], 0) == 0
    assert history.get_item_statuses(run) == [ITEM_GRABBED]


def test_clear_and_purge_remove_settled_checked_runs(db_path):
    inst = make_instance()
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", ITEM_GRABBED)
    history.finish_run(run, 1, 1, "success")
    sql("UPDATE search_history SET started_at=datetime('now','localtime','-400 days')")
    assert history.purge_old_runs(inst["id"], 365) == 1
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    history.record_checked(run, inst["id"], "B", 2, "movie", "mov:2", ITEM_NO_HIT)
    history.finish_run(run, 1, 1, "success")
    assert history.clear() == {"deleted": 1, "kept_open": 0}


def test_housekeeping_purges_old_log_rows(db_path, monkeypatch):
    inst = make_instance()
    db.checked_search_log.insert({"instance_id": inst["id"], "mode": "dry_run", "skill": "search_missing",
                                  "title": "Old", "outcome": "would_grab", "cache_key": "mov:1"})
    sql("UPDATE checked_search_log SET created_at=datetime('now','localtime','-400 days')")
    monkeypatch.setattr(settings, "history_retention_days", 365)

    class Agent:
        config = db.instances.get_by_id(inst["id"])
        messages = []

        def log(self, level, skill, message):
            self.messages.append(message)

    agent = Agent()
    VerifyCommandsSkill().housekeeping(agent)
    assert sql("SELECT COUNT(*) FROM checked_search_log")[0][0] == 0
    assert agent.messages == ["Housekeeping: removed 0 run(s) older than 365 days and 0 expired cache entries; "
                              "1 pre-filter log row(s)"]


class HousekeepingAgent:
    def __init__(self, inst):
        self.config = db.instances.get_by_id(inst["id"])
        self.messages = []

    def log(self, level, skill, message):
        self.messages.append(message)


def test_housekeeping_keeps_a_grab_for_its_days_despite_a_short_retry_window(db_path):
    # "Search again if still missing after (days)" holds a grab N days,
    # also when retry_hours is shorter (Codex round 2, F1).
    inst = make_instance()
    sql("UPDATE instances SET retry_hours=24, checked_search_settings='{\"search_again_after_days\": 7}'")
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", ITEM_GRABBED)
    history.record_checked(run, inst["id"], "B", 2, "movie", "mov:2", ITEM_NO_HIT)
    sql("UPDATE searched_items SET searched_at=datetime('now','localtime','-2 days'), "
        "grabbed_at=CASE WHEN grabbed_at IS NOT NULL THEN datetime('now','localtime','-2 days') END")
    VerifyCommandsSkill().housekeeping(HousekeepingAgent(inst))
    assert sql("SELECT cache_key FROM searched_items") == [("mov:1",)]
    sql("UPDATE searched_items SET searched_at=datetime('now','localtime','-8 days'), "
        "grabbed_at=datetime('now','localtime','-8 days')")
    VerifyCommandsSkill._last_housekeeping.clear()
    VerifyCommandsSkill().housekeeping(HousekeepingAgent(inst))
    assert sql("SELECT cache_key FROM searched_items") == []


def test_purge_expired_keeps_young_grabs_only_when_asked(db_path):
    inst = make_instance()
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", ITEM_GRABBED)
    sql("UPDATE searched_items SET searched_at=datetime('now','localtime','-2 days'), "
        "grabbed_at=datetime('now','localtime','-2 days')")
    assert db.searched.purge_expired(inst["id"], 24, keep_grab_days=7) == 0
    assert db.searched.purge_expired(inst["id"], 24) == 1


class FakeCommands:
    """*arr's answer to GET /command/{id}: every command it is asked about failed."""

    def __init__(self, inst, answer):
        self.config = db.instances.get_by_id(inst["id"])
        self.answer = answer
        self.state = {}
        self.messages = []

    def stop_requested(self):
        return False

    def http_get_raw(self, path):
        return 200, {"status": self.answer}

    def log(self, level, skill, message):
        self.messages.append(message)


def test_a_failed_older_command_does_not_release_a_newer_grab(db_path):
    # Codex round 2, F3: an old command still awaiting its verdict, then a
    # checked grab of the same title; *arr reports the old command orphaned
    # (restart). The grab's cache entry must stay.
    inst = make_instance()
    old_run = history.start_run(inst["id"], "Radarr", "search_missing")
    old = history.record_submission(old_run, inst["id"], "A", 1, "movie", "mov:1", 42)
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", ITEM_GRABBED)
    assert history.resolve_item(old, ITEM_FAILED, inst["id"], "mov:1") is False
    assert sql("SELECT cache_key, grabbed_at IS NOT NULL FROM searched_items") == [("mov:1", 1)]
    assert sql("SELECT command_status FROM search_history_items WHERE id=?", (old,)) == [(ITEM_FAILED,)]


def test_verification_of_an_orphaned_command_keeps_the_newer_grab(db_path):
    inst = make_instance()
    old_run = history.start_run(inst["id"], "Radarr", "search_missing")
    history.record_submission(old_run, inst["id"], "A", 1, "movie", "mov:1", 42)
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", ITEM_GRABBED)
    agent = FakeCommands(inst, "orphaned")
    VerifyCommandsSkill().execute(agent)
    assert sql("SELECT cache_key, grabbed_at IS NOT NULL FROM searched_items") == [("mov:1", 1)]
    assert not any("released" in m for m in agent.messages)


def test_a_failed_command_still_releases_its_own_entry(db_path):
    inst = make_instance()
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    item = history.record_submission(run, inst["id"], "A", 1, "movie", "mov:1", 42)
    other = history.start_run(inst["id"], "Radarr", "search_missing")
    history.record_failed_submission(other, "A", 1, "movie")          # wrote no cache entry
    history.record_submission(other, inst["id"], "A", 1, "movie", "mov:1", None)   # no command id: no entry
    history.record_checked(other, inst["id"], "A", 1, "movie", "mov:1", ITEM_FAILED)   # refused grab: no entry
    assert history.resolve_item(item, ITEM_FAILED, inst["id"], "mov:1") is True
    assert sql("SELECT COUNT(*) FROM searched_items")[0][0] == 0


def test_a_cache_entry_names_the_item_that_wrote_it(db_path):
    inst = make_instance()
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    first = history.record_submission(run, inst["id"], "A", 1, "movie", "mov:1", 42)
    assert sql("SELECT history_item_id FROM searched_items") == [(first,)]
    second = history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", ITEM_GRABBED)
    assert sql("SELECT history_item_id FROM searched_items") == [(second,)]


def older_command_then_grab(inst):
    """Command A awaits its verdict (run pending); then a checked grab B of the
    same title in a run that is settled at once."""
    old_run = history.start_run(inst["id"], "Radarr", "search_missing")
    old = history.record_submission(old_run, inst["id"], "A", 1, "movie", "mov:1", 42)
    history.finish_run(old_run, 1, 1, "success")
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    history.record_checked(run, inst["id"], "A", 1, "movie", "mov:1", ITEM_GRABBED)
    history.finish_run(run, 1, 1, "success")
    return old, run


def test_clearing_the_history_keeps_a_newer_grab_after_a_late_failure(db_path):
    # Codex round 3, G1: clear() deletes the settled grab run with its items
    # and keeps the pending command; its late "orphaned" must not release the grab.
    inst = make_instance()
    older_command_then_grab(inst)
    assert history.clear() == {"deleted": 1, "kept_open": 1}
    agent = FakeCommands(inst, "orphaned")
    VerifyCommandsSkill().execute(agent)
    assert sql("SELECT cache_key, grabbed_at IS NOT NULL FROM searched_items") == [("mov:1", 1)]
    assert not any("released" in m for m in agent.messages)


def test_purging_old_runs_keeps_a_newer_grab_after_a_late_failure(db_path):
    inst = make_instance()
    old, run = older_command_then_grab(inst)
    sql("UPDATE search_history SET started_at=datetime('now','localtime','-2 days') WHERE id=?", (run,))
    assert history.purge_old_runs(inst["id"], 1) == 1
    assert history.resolve_item(old, ITEM_FAILED, inst["id"], "mov:1") is False
    assert sql("SELECT cache_key FROM searched_items") == [("mov:1",)]


def test_a_command_from_0_8_0_still_releases_its_entry(db_path):
    # Entries written before 0.9.0 name no item: the newer-search check decides.
    inst = make_instance()
    run = history.start_run(inst["id"], "Radarr", "search_missing")
    item = history.record_submission(run, inst["id"], "A", 1, "movie", "mov:1", 42)
    sql("UPDATE searched_items SET history_item_id=NULL")
    assert history.resolve_item(item, ITEM_FAILED, inst["id"], "mov:1") is True
    assert sql("SELECT COUNT(*) FROM searched_items")[0][0] == 0


def test_a_grab_also_writes_its_hold_key(db_path):
    # Codex round 3, G3: a Sonarr upgrade grab holds its season for the command path.
    inst = make_instance()
    run = history.start_run(inst["id"], "Sonarr", "search_upgrades")
    grabbed = history.record_checked(run, inst["id"], "E3", 3, "episode", "upg:3", ITEM_GRABBED,
                                     profile_fingerprint="aaaa", hold_key="upg:sea-hold:10:1")
    no_hit = history.record_checked(run, inst["id"], "E4", 4, "episode", "upg:4", ITEM_NO_HIT,
                                    profile_fingerprint="aaaa", hold_key="upg:sea-hold:10:2")
    refused = history.record_checked(run, inst["id"], "E5", 5, "episode", "upg:5", ITEM_FAILED,
                                     hold_key="upg:sea-hold:10:3")
    unclear = history.record_checked(run, inst["id"], "E6", 6, "episode", "upg:6", ITEM_FAILED, cache=True,
                                     profile_fingerprint="aaaa", hold_key="upg:sea-hold:10:4")
    assert refused
    assert sql("SELECT cache_key, item_type, profile_fingerprint, grabbed_at IS NOT NULL, history_item_id "
               "FROM searched_items ORDER BY cache_key") == [
        ("upg:3", "episode", "aaaa", 1, grabbed), ("upg:4", "episode", "aaaa", 0, no_hit),
        ("upg:6", "episode", "aaaa", 1, unclear),
        ("upg:sea-hold:10:1", "season", "aaaa", 1, grabbed), ("upg:sea-hold:10:4", "season", "aaaa", 1, unclear)]
```

- [ ] **Step 2: Test laufen lassen, er muss scheitern**

Run: `cd <repo> && .venv/bin/python -m pytest tests/test_g2_history.py -q -p no:cacheprovider`
Expected: FAIL mit `ImportError: cannot import name 'ITEM_GRABBED' from 'backend.verification'`

- [ ] **Step 3: Implementieren**

`backend/verification.py` — Edit, old:

```
ITEM_LEGACY = "legacy"
```

new:

```
ITEM_LEGACY = "legacy"
# Checked search (0.9.0): settled when written, no command to ask about.
ITEM_GRABBED = "grabbed"      # missingarr grabbed a release that passed the pre-filter
ITEM_NO_HIT = "no_hit"        # searched, but no approved release passed (or none came back)

# Items that count as done — confirmed by *arr or settled by the checked search.
DONE_STATUSES = frozenset({ITEM_COMPLETED, ITEM_GRABBED, ITEM_NO_HIT})
```

`backend/verification.py` — Edit, old:

```
    if all(s == ITEM_COMPLETED for s in relevant):
        return RUN_SUCCESS
    if ITEM_COMPLETED in relevant:
        return RUN_PARTIAL
    if ITEM_FAILED in relevant:
        return RUN_FAILED
    return RUN_UNVERIFIED
```

new:

```
    if all(s in DONE_STATUSES for s in relevant):
        return RUN_SUCCESS
    if any(s in DONE_STATUSES for s in relevant):
        return RUN_PARTIAL
    if ITEM_FAILED in relevant:
        return RUN_FAILED
    return RUN_UNVERIFIED


def count_verified(item_statuses: list[str]) -> int:
    """The run's "confirmed" number: completed commands plus titles the
    checked search settled (grabbed, no clean hit)."""
    return sum(1 for s in item_statuses if s in DONE_STATUSES)
```

`backend/db/history.py` — Edit, old:

```
from typing import Optional
from backend.database import get_db
from backend.verification import ITEM_SUBMITTED, ITEM_EXPIRED, ITEM_FAILED
```

new:

```
from typing import Optional
from backend.database import get_db
from backend.db import checked_search_log
from backend.verification import (
    ITEM_SUBMITTED, ITEM_EXPIRED, ITEM_FAILED, ITEM_GRABBED, ITEM_NO_HIT,
    aggregate_run_status, count_verified,
)
```

`backend/db/history.py`, `finish_run` — Edit, old:

```
    closed as 'pending'; verify_commands derives the final verdict. Only a run
    that sent nothing (or that threw) gets its verdict here.
    """
    with get_db() as conn:
        if status == "success":
            has_items = conn.execute(
                "SELECT 1 FROM search_history_items WHERE run_id=? LIMIT 1",
                (run_id,),
            ).fetchone()
            if has_items:
                status = "pending"

        conn.execute(
            """
            UPDATE search_history SET
                wanted_count=?, triggered_count=?,
                status=?, error_message=?,
                finished_at=datetime('now','localtime')
            WHERE id=?
            """,
            (wanted_count, triggered_count, status, error_message, run_id),
        )
```

new:

```
    closed as 'pending'; verify_commands derives the final verdict. A run
    whose items are all settled already (the checked search writes grabbed /
    no_hit, nothing to ask *arr about) gets its verdict right here, and so
    does a run that sent nothing (or that threw).
    """
    verified_count = None
    with get_db() as conn:
        if status == "success":
            statuses = [
                r[0] for r in conn.execute(
                    "SELECT command_status FROM search_history_items WHERE run_id=?", (run_id,)
                )
            ]
            if statuses:
                status = aggregate_run_status(statuses)
                verified_count = count_verified(statuses)

        conn.execute(
            """
            UPDATE search_history SET
                wanted_count=?, triggered_count=?,
                status=?, error_message=?,
                verified_count=COALESCE(?, verified_count),
                finished_at=datetime('now','localtime')
            WHERE id=?
            """,
            (wanted_count, triggered_count, status, error_message, verified_count, run_id),
        )
```

`backend/db/history.py` — neue Funktion direkt vor `def close_interrupted_runs() -> int:` einfügen (Edit, old: `def close_interrupted_runs() -> int:`, new: der folgende Block, gefolgt von `def close_interrupted_runs() -> int:`):

```python
def record_checked(
    run_id: int,
    instance_id: int,
    title: str,
    arr_id: Optional[int],
    item_type: str,
    cache_key: str,
    status: str,
    log_entry: Optional[dict] = None,
    profile_fingerprint: Optional[str] = None,
    cache: Optional[bool] = None,
    hold_key: Optional[str] = None,
) -> int:
    """Store what the checked search did with one title — history item,
    retry-cache entry and pre-filter log row in one transaction.

    status 'grabbed' or 'no_hit': item settled at once (no command id, POST
    /release answers synchronously) and the title is cached as searched,
    under the fingerprint of its quality profile.
    status 'failed': item only, no cache entry — the next run tries again.
    cache=True with 'failed': a grab whose answer got lost. It may be
    downloading, so the title is cached like after a grab.
    A grab (and such a failed one) also sets grabbed_at: "Search again if
    still missing" releases the title after the set days. hold_key: a second
    key such a grab holds the same way (a Sonarr upgrade holds its season,
    so the command path does not search the season meanwhile).
    The cache entry names this item as its writer (history_item_id).
    """
    if status not in (ITEM_GRABBED, ITEM_NO_HIT, ITEM_FAILED):
        raise ValueError(f"unexpected checked-search status {status!r}")
    if cache is None:
        cache = status != ITEM_FAILED
    grabbed = status == ITEM_GRABBED or (status == ITEM_FAILED and cache)
    with get_db() as conn:
        cursor = conn.execute(
            _INSERT_ITEM,
            (run_id, title, arr_id, item_type, cache_key if cache else "", None, status, "now"),
        )
        item_id = cursor.lastrowid
        if cache and cache_key:
            conn.execute(_UPSERT_SEARCHED,
                         (instance_id, cache_key, title, item_type, profile_fingerprint, int(grabbed), item_id))
            if grabbed and hold_key:
                conn.execute(_UPSERT_SEARCHED,
                             (instance_id, hold_key, title, "season", profile_fingerprint, 1, item_id))
        if log_entry is not None:
            checked_search_log.insert_with(conn, log_entry)
        return item_id


```

`backend/db/history.py` — Edit, old:

```
def record_submission(
    run_id: int,
    instance_id: int,
    title: str,
    arr_id: Optional[int],
    item_type: str,
    cache_key: str,
    command_id: Optional[int],
) -> int:
```

new:

```
# Retry-cache upsert of every search path. A new search of a cached title
# also takes over the fingerprint it was searched under (0.9.0) — but never
# replaces one with NULL (the profile was unknown this time). grabbed_at
# (1/0 parameter) marks a grab of the checked search; any other search
# clears it. history_item_id (last parameter): the item that wrote the
# entry last — a failed command releases only an entry it still owns.
_UPSERT_SEARCHED = """
    INSERT INTO searched_items
        (instance_id, cache_key, title, item_type, profile_fingerprint, grabbed_at, history_item_id)
    VALUES (?, ?, ?, ?, ?, CASE WHEN ? THEN datetime('now','localtime') END, ?)
    ON CONFLICT(instance_id, cache_key) DO UPDATE SET
        searched_at=datetime('now','localtime'),
        profile_fingerprint=COALESCE(excluded.profile_fingerprint, searched_items.profile_fingerprint),
        grabbed_at=excluded.grabbed_at,
        history_item_id=excluded.history_item_id
"""


def record_submission(
    run_id: int,
    instance_id: int,
    title: str,
    arr_id: Optional[int],
    item_type: str,
    cache_key: str,
    command_id: Optional[int],
    profile_fingerprint: Optional[str] = None,
) -> int:
```

Edit, old:

```
        if command_id is not None and cache_key:
            conn.execute(
                """
                INSERT INTO searched_items (instance_id, cache_key, title, item_type)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(instance_id, cache_key) DO UPDATE SET searched_at=datetime('now','localtime')
                """,
                (instance_id, cache_key, title, item_type),
            )
```

new:

```
        if command_id is not None and cache_key:
            conn.execute(_UPSERT_SEARCHED,
                         (instance_id, cache_key, title, item_type, profile_fingerprint, 0, cursor.lastrowid))
```

`backend/skills/verify_commands.py` — Edit, old:

```
    map_command_status,
    aggregate_run_status,
    ITEM_SUBMITTED,
    ITEM_COMPLETED,
)
```

new:

```
    map_command_status,
    aggregate_run_status,
    count_verified,
    ITEM_SUBMITTED,
)
```

Edit, old: `                statuses.count(ITEM_COMPLETED),` — new: `                count_verified(statuses),`

Edit, old:

```
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

new:

```
            # A checked-search grab blocks "Search again if still missing
            # after (days)", also past a shorter retry window: keep its row.
            cache = db.searched.purge_expired(instance_id, retry_hours, keep_grab_days=_grab_days(agent.config))
            runs = db.history.purge_old_runs(instance_id, settings.history_retention_days)
            # The pre-filter log keeps the history's retention (rows of the
            # running dry-run round stay, see checked_search_log.purge_old).
            log_rows = db.checked_search_log.purge_old(instance_id, settings.history_retention_days)
        except Exception as exc:
            agent.log("warn", self.name, f"Housekeeping failed: {exc}")
            return

        if cache or runs or log_rows:
            agent.log(
                "info",
                self.name,
                f"Housekeeping: removed {runs} run(s) older than "
                f"{settings.history_retention_days} days and {cache} expired cache entr"
                f"{'y' if cache == 1 else 'ies'}"
                + (f"; {log_rows} pre-filter log row(s)" if log_rows else ""),
            )
```

(Der Anfang der Meldung bleibt gleich; `tests/test_p3_housekeeping.py` prüft ihn mit `startswith`.)

`backend/skills/verify_commands.py` — Edit, old:

```
import threading
import time
```

new:

```
import json
import threading
import time
```

Edit, old:

```
class VerifyCommandsSkill(BaseSkill):
    """Resolve what *arr actually did with the commands we sent.
```

new:

```
GRAB_DAYS_DEFAULT = 7          # CheckedSearchSettings.search_again_after_days (1–365)


def _grab_days(config: dict) -> int:
    """'Search again if still missing after (days)' of an instance, read raw
    from its stored checked_search_settings (dict or JSON text): housekeeping
    must not depend on the settings model. Missing or invalid: the default."""
    raw = config.get("checked_search_settings")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except ValueError:
            raw = {}
    value = raw.get("search_again_after_days") if isinstance(raw, dict) else None
    if type(value) is int and 1 <= value <= 365:
        return value
    return GRAB_DAYS_DEFAULT


class VerifyCommandsSkill(BaseSkill):
    """Resolve what *arr actually did with the commands we sent.
```

`backend/db/searched.py`, `purge_expired` — Edit, old:

```
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
```

new:

```
def purge_expired(instance_id: int, retry_hours: int, keep_grab_days: int = 0) -> int:
    """Delete entries outside the retry window. They no longer block anything,
    they only pile up (B6). No window (0) means permanent — nothing expires.

    keep_grab_days (0.9.0): a grab of the checked search (grabbed_at) blocks
    that many days whatever retry_hours says ("Search again if still missing
    after"), so its row stays until then."""
    if retry_hours <= 0:
        return 0
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM searched_items WHERE instance_id=? "
            "AND searched_at <= datetime('now', 'localtime', ? || ' hours') "
            "AND NOT (grabbed_at IS NOT NULL AND grabbed_at > datetime('now', 'localtime', ? || ' days'))",
            (instance_id, f"-{retry_hours}", f"-{max(0, keep_grab_days)}"),
        )
        return cursor.rowcount
```

`backend/db/history.py`, `resolve_item` — Edit, old:

```
        if status == ITEM_FAILED and cache_key:
            cursor = conn.execute(
                "DELETE FROM searched_items WHERE instance_id=? AND cache_key=?",
                (instance_id, cache_key),
            )
            return cursor.rowcount > 0
    return False
```

new:

```
        if status == ITEM_FAILED and cache_key:
            # Only this command's own entry: a newer search of the same title
            # (a command, or a grab / no-hit of the checked search, which has
            # no command to verify) wrote the entry since and still holds it.
            # The entry names its writer (history_item_id), so this holds
            # after the history was cleared or purged as well. Entries from
            # before 0.9.0 name none: there a newer item that wrote the cache
            # decides (compared by item id, created_at only has seconds).
            cursor = conn.execute(
                f"""
                DELETE FROM searched_items WHERE instance_id=? AND cache_key=?
                AND (history_item_id = ? OR (history_item_id IS NULL AND grabbed_at IS NULL AND NOT EXISTS (
                    SELECT 1 FROM search_history_items si
                    JOIN search_history h ON h.id = si.run_id
                    WHERE h.instance_id=? AND si.cache_key=? AND si.id > ?
                      AND (si.command_id IS NOT NULL OR si.command_status IN ({_CACHED_CHECKED}))
                )))
                """,
                (instance_id, cache_key, item_id, instance_id, cache_key, item_id),
            )
            return cursor.rowcount > 0
    return False
```

`backend/db/history.py` — Edit, old:

```
def resolve_item(item_id: int, status: str, instance_id: int, cache_key: str) -> bool:
```

new:

```
# Items of the checked search that wrote the retry cache (a failed one only
# when it carries its key: a grab without a clear answer).
_CACHED_CHECKED = f"'{ITEM_GRABBED}', '{ITEM_NO_HIT}', '{ITEM_FAILED}'"


def resolve_item(item_id: int, status: str, instance_id: int, cache_key: str) -> bool:
```

- [ ] **Step 4: Tests laufen lassen**

Run: `cd <repo> && .venv/bin/python -m pytest tests/test_g2_history.py tests/test_p3_history.py tests/test_p3_verify.py tests/test_p3_housekeeping.py tests/test_p3_searched.py tests/test_verification.py tests/test_p2_search_missing.py -q -p no:cacheprovider`
Expected: alle grün (`24` neue)

- [ ] **Step 5: Commit**

```bash
git add backend/verification.py backend/db/history.py backend/db/searched.py backend/skills/verify_commands.py tests/test_g2_history.py
git commit -m "feat: settle grabbed and no-hit items at once and keep the log's retention"
```

---

### Task G2.4: Instanzen speichern, Probelauf-Runde

**Files:**
- Modify: `backend/db/instances.py` (ganze Datei unten)
- Test: `tests/test_g2_instances.py`

**Interfaces:**
- Consumes: G2.1 (Spalten)
- Produces: `db.instances.create/update/row_to_dict` mit den neuen Feldern, `db.instances.reset_dry_run`, `db.instances.store_profile_fingerprints`, `CHECKED_SEARCH_MODES` (Vertrag G2).

`update` behält `checked_search`, `checked_search_settings` und `search_again_after_profile_change`, wenn sie fehlen oder `None` sind (ein alter Client oder ein Skript setzt sie so nicht zurück). Ein Wechsel nach `dry_run` aus einem anderen Modus startet eine neue Runde (`dry_run_round + 1`, Beginn jetzt); Speichern im Modus `dry_run` behält die laufende Runde (geänderte Regel-Einstellungen erkennt der Probelauf am Einstellungs-Fingerabdruck, G3). `reset_dry_run` zählt die Runde ebenfalls hoch. `profile_fingerprints` und `profile_fingerprints_baseline` schreibt nur `store_profile_fingerprints` (G3 zu Laufbeginn), nie `create`/`update`: Die Grundlinie wird beim ersten erfolgreichen Abruf gesetzt und danach nie wieder (`COALESCE`).

- [ ] **Step 1: Failing test schreiben**

`tests/test_g2_instances.py`:

```python
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


BASE = {"name": "Radarr", "type": "radarr", "url": "http://127.0.0.1:9", "api_key": "k" * 32}


def test_new_instance_defaults_to_off(db_path):
    inst = db.instances.create(dict(BASE))
    assert inst["checked_search"] == "off"
    assert inst["checked_search_settings"] == {}
    assert (inst["dry_run_round"], inst["dry_run_round_started_at"]) == (0, None)
    assert inst["search_again_after_profile_change"] == 1
    assert (inst["profile_fingerprints"], inst["profile_fingerprints_baseline"]) == ({}, None)


def test_search_again_after_profile_change_is_kept_unless_sent(db_path):
    inst = db.instances.create({**BASE, "search_again_after_profile_change": False})
    assert inst["search_again_after_profile_change"] == 0
    assert db.instances.update(inst["id"], dict(BASE))["search_again_after_profile_change"] == 0
    updated = db.instances.update(inst["id"], {**BASE, "search_again_after_profile_change": True})
    assert updated["search_again_after_profile_change"] == 1


def test_profile_fingerprints_set_the_baseline_only_once(db_path):
    inst = db.instances.create(dict(BASE))
    assert db.instances.store_profile_fingerprints(inst["id"], {"1": "aaaa"}) == {"1": "aaaa"}
    assert db.instances.store_profile_fingerprints(inst["id"], {"1": "bbbb", "2": "cccc"}) == {"1": "aaaa"}
    stored = db.instances.get_by_id(inst["id"])
    assert stored["profile_fingerprints"] == {"1": "bbbb", "2": "cccc"}
    assert stored["profile_fingerprints_baseline"] == {"1": "aaaa"}
    # saving the form does not touch them
    db.instances.update(inst["id"], {**BASE, "interval_minutes": 30})
    assert db.instances.get_by_id(inst["id"])["profile_fingerprints"] == {"1": "bbbb", "2": "cccc"}
    assert db.instances.store_profile_fingerprints(999, {"1": "x"}) is None


def test_create_in_dry_run_starts_a_round_and_stores_settings(db_path):
    inst = db.instances.create({**BASE, "checked_search": "dry_run",
                                "checked_search_settings": {"year_tolerance": 2}})
    assert inst["checked_search"] == "dry_run"
    assert inst["checked_search_settings"] == {"year_tolerance": 2}
    assert len(inst["dry_run_round_started_at"]) == 23


def test_update_without_the_fields_keeps_them(db_path):
    inst = db.instances.create({**BASE, "checked_search": "active", "checked_search_settings": {"prefix_match": False}})
    updated = db.instances.update(inst["id"], {**BASE, "interval_minutes": 30})
    assert updated["checked_search"] == "active"
    assert updated["checked_search_settings"] == {"prefix_match": False}


def test_switching_to_dry_run_starts_a_new_round_staying_keeps_it(db_path):
    inst = db.instances.create(dict(BASE))
    first = db.instances.update(inst["id"], {**BASE, "checked_search": "dry_run"})
    assert first["dry_run_round_started_at"] and first["dry_run_round"] == 1
    sql("UPDATE instances SET dry_run_round_started_at='2026-01-01 00:00:00.000'")
    again = db.instances.update(inst["id"], {**BASE, "checked_search": "dry_run"})
    assert (again["dry_run_round"], again["dry_run_round_started_at"]) == (1, "2026-01-01 00:00:00.000")
    db.instances.update(inst["id"], {**BASE, "checked_search": "active"})
    back = db.instances.update(inst["id"], {**BASE, "checked_search": "dry_run"})
    assert back["dry_run_round"] == 2
    assert back["dry_run_round_started_at"] > "2026-01-01 00:00:00.000"


def test_reset_dry_run_counts_the_round_up(db_path):
    inst = db.instances.create({**BASE, "checked_search": "dry_run"})
    sql("UPDATE instances SET dry_run_round_started_at='2026-01-01 00:00:00.000'")
    value = db.instances.reset_dry_run(inst["id"])
    assert value > "2026-01-01 00:00:00.000"
    stored = db.instances.get_by_id(inst["id"])
    assert (stored["dry_run_round"], stored["dry_run_round_started_at"]) == (1, value)
    db.instances.reset_dry_run(inst["id"])
    assert db.instances.get_by_id(inst["id"])["dry_run_round"] == 2
    assert db.instances.reset_dry_run(999) is None


def test_unreadable_stored_settings_read_as_empty(db_path):
    inst = db.instances.create(dict(BASE))
    sql("UPDATE instances SET checked_search_settings='not json'")
    assert db.instances.get_by_id(inst["id"])["checked_search_settings"] == {}


def test_unknown_mode_from_a_caller_is_stored_as_off(db_path):
    inst = db.instances.create({**BASE, "checked_search": "sometimes"})
    assert inst["checked_search"] == "off"
```

- [ ] **Step 2: Test laufen lassen, er muss scheitern**

Run: `cd <repo> && .venv/bin/python -m pytest tests/test_g2_instances.py -q -p no:cacheprovider`
Expected: FAIL (u. a. `checked_search_settings` ist Text `'{}'` statt `{}`, `reset_dry_run` fehlt)

- [ ] **Step 3: Implementieren**

`backend/db/instances.py` komplett:

```python
import json
import sqlite3
from typing import Optional
from backend.database import get_db
from backend.crypto import encrypt, decrypt

CHECKED_SEARCH_MODES = ("off", "dry_run", "active")
# Local time with milliseconds — the format of checked_search_log.created_at.
# The round itself is the counter dry_run_round; its start is for display.
_NOW_MS = "strftime('%Y-%m-%d %H:%M:%f','now','localtime')"


def _mask_api_key(key: str) -> str:
    if len(key) <= 6:
        return "****"
    return key[:4] + "****" + key[-2:]


def _settings_dict(raw) -> dict:
    """The stored settings JSON as a dict. Anything unreadable counts as
    empty: the defaults apply then (CheckedSearchSettings.from_stored)."""
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _settings_json(value) -> str:
    return json.dumps(_settings_dict(value), sort_keys=True, ensure_ascii=False)


def _checked_mode(value) -> str:
    return value if value in CHECKED_SEARCH_MODES else "off"


def _flag(value, default: bool = True) -> int:
    return int(default if value is None else bool(value))


def row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    if "api_key" in d and d["api_key"]:
        d["api_key"] = decrypt(d["api_key"])
    if "checked_search_settings" in d:
        d["checked_search_settings"] = _settings_dict(d["checked_search_settings"])
    if "profile_fingerprints" in d:
        d["profile_fingerprints"] = _settings_dict(d["profile_fingerprints"])
    if "profile_fingerprints_baseline" in d:
        raw = d["profile_fingerprints_baseline"]
        d["profile_fingerprints_baseline"] = None if raw is None else _settings_dict(raw)
    return d


def get_all(include_disabled: bool = True) -> list[dict]:
    with get_db() as conn:
        if include_disabled:
            rows = conn.execute(
                "SELECT * FROM instances ORDER BY type, name"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM instances WHERE enabled=1 ORDER BY type, name"
            ).fetchall()
        return [row_to_dict(r) for r in rows]


def get_by_id(instance_id: int) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM instances WHERE id=?", (instance_id,)
        ).fetchone()
        return row_to_dict(row) if row else None


def create(data: dict) -> dict:
    checked = _checked_mode(data.get("checked_search") or "off")
    with get_db() as conn:
        conn.execute(
            f"""
            INSERT INTO instances (
                name, type, url, api_key,
                enabled, search_missing_enabled, search_upgrades_enabled,
                interval_minutes, retry_hours,
                rate_window_minutes, rate_cap,
                search_order, missing_mode,
                missing_per_run, upgrades_per_run,
                seconds_between_actions, hours_after_release,
                upgrade_source, quiet_start, quiet_end,
                checked_search, checked_search_settings, search_again_after_profile_change,
                dry_run_round_started_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                      CASE WHEN ? = 'dry_run' THEN {_NOW_MS} END)
            """,
            (
                data["name"], data["type"], data["url"], encrypt(data["api_key"]),
                int(data.get("enabled", True)),
                int(data.get("search_missing_enabled", True)),
                int(data.get("search_upgrades_enabled", False)),
                data.get("interval_minutes", 15),
                data.get("retry_hours", 0),
                data.get("rate_window_minutes", 60),
                data.get("rate_cap", 25),
                data.get("search_order", "random"),
                data.get("missing_mode", "episode"),
                data.get("missing_per_run", 5),
                data.get("upgrades_per_run", 1),
                data.get("seconds_between_actions", 2),
                data.get("hours_after_release", 9),
                data.get("upgrade_source", "monitored_items_only"),
                data.get("quiet_start") or None,
                data.get("quiet_end") or None,
                checked,
                _settings_json(data.get("checked_search_settings")),
                _flag(data.get("search_again_after_profile_change")),
                checked,
            ),
        )
        row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        row = conn.execute("SELECT * FROM instances WHERE id=?", (row_id,)).fetchone()
        return row_to_dict(row)


def update(instance_id: int, data: dict) -> Optional[dict]:
    """checked_search / checked_search_settings / search_again_after_profile_change
    missing or None: keep what is stored (a client that does not know them
    does not reset them). Switching to 'dry_run' from another mode starts a
    new dry-run round (counter + 1). The profile fingerprints are never
    touched here."""
    with get_db() as conn:
        existing = conn.execute(
            "SELECT api_key, checked_search, checked_search_settings, search_again_after_profile_change "
            "FROM instances WHERE id=?",
            (instance_id,),
        ).fetchone()
        if not existing:
            return None

        # Keep existing encrypted key if no new key provided
        raw_key = data.get("api_key")
        if raw_key:
            api_key = encrypt(raw_key)
        else:
            api_key = existing["api_key"]  # already encrypted in DB

        old_mode = existing["checked_search"]
        new_mode = _checked_mode(data.get("checked_search")) if data.get("checked_search") else old_mode
        settings_json = (
            _settings_json(data["checked_search_settings"])
            if data.get("checked_search_settings") is not None
            else existing["checked_search_settings"]
        )
        new_round = new_mode == "dry_run" and old_mode != "dry_run"
        search_again = _flag(data.get("search_again_after_profile_change"),
                             bool(existing["search_again_after_profile_change"]))

        conn.execute(
            f"""
            UPDATE instances SET
                name=?, type=?, url=?, api_key=?,
                enabled=?, search_missing_enabled=?, search_upgrades_enabled=?,
                interval_minutes=?, retry_hours=?,
                rate_window_minutes=?, rate_cap=?,
                search_order=?, missing_mode=?,
                missing_per_run=?, upgrades_per_run=?,
                seconds_between_actions=?, hours_after_release=?,
                upgrade_source=?, quiet_start=?, quiet_end=?,
                checked_search=?, checked_search_settings=?, search_again_after_profile_change=?,
                dry_run_round=dry_run_round + ?,
                dry_run_round_started_at=CASE WHEN ? THEN {_NOW_MS} ELSE dry_run_round_started_at END,
                updated_at=datetime('now')
            WHERE id=?
            """,
            (
                data["name"], data["type"], data["url"], api_key,
                int(data.get("enabled", True)),
                int(data.get("search_missing_enabled", True)),
                int(data.get("search_upgrades_enabled", False)),
                data.get("interval_minutes", 15),
                data.get("retry_hours", 0),
                data.get("rate_window_minutes", 60),
                data.get("rate_cap", 25),
                data.get("search_order", "random"),
                data.get("missing_mode", "episode"),
                data.get("missing_per_run", 5),
                data.get("upgrades_per_run", 1),
                data.get("seconds_between_actions", 2),
                data.get("hours_after_release", 9),
                data.get("upgrade_source", "monitored_items_only"),
                data.get("quiet_start") or None,
                data.get("quiet_end") or None,
                new_mode, settings_json, search_again, int(new_round), int(new_round),
                instance_id,
            ),
        )
        row = conn.execute(
            "SELECT * FROM instances WHERE id=?", (instance_id,)
        ).fetchone()
        return row_to_dict(row)


def reset_dry_run(instance_id: int) -> Optional[str]:
    """Start a new dry-run round: every title is checked once more. A run
    that is still going keeps writing into the round it began in. Returns
    the new round start, or None when the instance does not exist."""
    with get_db() as conn:
        cursor = conn.execute(
            f"UPDATE instances SET dry_run_round=dry_run_round + 1, dry_run_round_started_at={_NOW_MS} "
            "WHERE id=?",
            (instance_id,),
        )
        if cursor.rowcount == 0:
            return None
        return conn.execute(
            "SELECT dry_run_round_started_at FROM instances WHERE id=?", (instance_id,)
        ).fetchone()[0]


def store_profile_fingerprints(instance_id: int, fingerprints: dict) -> Optional[dict]:
    """Keep the profile fingerprints a run just read (profile id -> value).
    The first call also sets the baseline: cache entries from before 0.9.0
    count as searched under it, so the update alone releases nothing.
    Returns the baseline, or None when the instance does not exist."""
    text = json.dumps(fingerprints, sort_keys=True)
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE instances SET profile_fingerprints=?, "
            "profile_fingerprints_baseline=COALESCE(profile_fingerprints_baseline, ?) WHERE id=?",
            (text, text, instance_id),
        )
        if cursor.rowcount == 0:
            return None
        row = conn.execute(
            "SELECT profile_fingerprints_baseline FROM instances WHERE id=?", (instance_id,)
        ).fetchone()
        return _settings_dict(row[0])


def delete(instance_id: int) -> bool:
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM instances WHERE id=?", (instance_id,)
        )
        return cursor.rowcount > 0


def update_status(instance_id: int, status: str, last_seen_at: Optional[str] = None):
    with get_db() as conn:
        if last_seen_at:
            conn.execute(
                "UPDATE instances SET connection_status=?, last_seen_at=? WHERE id=?",
                (status, last_seen_at, instance_id),
            )
        else:
            conn.execute(
                "UPDATE instances SET connection_status=? WHERE id=?",
                (status, instance_id),
            )


def toggle_skill(instance_id: int, skill: str, enabled: bool):
    col = "search_missing_enabled" if skill == "missing" else "search_upgrades_enabled"
    with get_db() as conn:
        conn.execute(
            f"UPDATE instances SET {col}=?, updated_at=datetime('now') WHERE id=?",
            (int(enabled), instance_id),
        )


def toggle_enabled(instance_id: int, enabled: bool) -> Optional[dict]:
    with get_db() as conn:
        conn.execute(
            "UPDATE instances SET enabled=?, updated_at=datetime('now') WHERE id=?",
            (int(enabled), instance_id),
        )
        row = conn.execute(
            "SELECT * FROM instances WHERE id=?", (instance_id,)
        ).fetchone()
        return row_to_dict(row) if row else None
```

- [ ] **Step 4: Tests laufen lassen**

Run: `cd <repo> && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: `410 passed` (im G2-Worktree: 353 alte + 57 neue aus G2.1–G2.4)

- [ ] **Step 5: Commit**

```bash
git add backend/db/instances.py tests/test_g2_instances.py
git commit -m "feat: store checked-search mode and settings per instance, with a dry-run round counter"
```

---

### Task G2.5: Such-Cache mit Profil-Fingerabdruck (Spec-Nachtrag)

**Files:**
- Modify: `backend/db/searched.py` (`add`, `lookup_many`)
- Test: `tests/test_g2_searched.py`

**Interfaces:**
- Consumes: G2.1 (Spalte `searched_items.profile_fingerprint`)
- Produces: `db.searched.add(…, profile_fingerprint=None)`, `db.searched.lookup_many(…, fingerprints=None, grab_release_days=0)` (Vertrag G2). G3 baut `fingerprints` je Lauf aus dem Profilstand (`ProfileState.cache_filter`) und übergibt `grab_release_days` für Kandidaten aus Wanted-Listen.

Regeln aus dem Spec-Nachtrag, alle in `lookup_many`, damit beide Suchwege (Befehl und geprüfte Suche) und beide Skills sie gleich anwenden:
- Ein Eintrag sperrt nur, solange sein Fingerabdruck der aktuelle Fingerabdruck des Profils seines Titels ist.
- Ein Eintrag ohne Fingerabdruck (vor 0.9.0 gemerkt) gilt als unter der Grundlinie gesucht; das Update allein gibt also nichts frei, erst die nächste Änderung des Profils.
- Ist der aktuelle Fingerabdruck unbekannt (Profile nie gelesen, Titel ohne Profil-ID, Sonarr-Serie unbekannt), sperrt der Eintrag: im Zweifel nichts freigeben.
- `fingerprints=None` (Einstellung „Search again after profile changes“ aus): jeder Eintrag sperrt wie bisher.
- Das Zeitfenster `retry_hours` gilt unverändert zusätzlich, außer für Grabs (nächster Punkt).
- „Search again if still missing after (days)“ (Entscheidung Daniel 01.10.2026): Mit `grab_release_days > 0` sperrt ein Eintrag mit `grabbed_at` (Grab der geprüften Suche oder unklarer Grab) genau so viele Tage, **auch wenn `retry_hours` kürzer ist** (der SQL-Filter des Zeitfensters lässt solche Zeilen deshalb durch, Codex-Runde 2, F1: vorher fiel ein Grab nach `retry_hours` heraus, die Sperre war min(`retry_hours`, N Tage)); danach ist er frei, auch bei gleichem Fingerabdruck. Eine Profiländerung gibt ihn wie jeden Eintrag schon vorher frei (Spec „Profiländerungen erkennen“: ein Eintrag sperrt nur unter dem aktuellen Fingerabdruck). Die Skills übergeben den Wert nur für Kandidaten aus einer Wanted-Liste (fehlend bzw. Cutoff nicht erreicht): Steht ein geladener Titel dort noch, fehlt er noch. Mit `grab_release_days = 0` (Upgrade-Quelle „monitored movies“) gilt für Grabs das normale Zeitfenster wie bisher. Die Hauspflege löscht solche Zeilen erst nach N Tagen (`purge_expired(…, keep_grab_days=)`, G2.3).
- `add` übernimmt wie `_UPSERT_SEARCHED` (G2.3) einen vorhandenen Fingerabdruck, wenn kein neuer kommt (Codex K3), und leert `grabbed_at` und `history_item_id` (kein Item hat den Eintrag geschrieben; im Bestand rufen `add` nur Tests auf).

`unsaved_cache_keys` (gesendete, noch nicht gespeicherte Befehle) sperrt weiter ohne Fingerabdruck; diese Einträge leben nur bis zum nächsten Lauf im Speicher.

- [ ] **Step 1: Failing test schreiben**

`tests/test_g2_searched.py`:

```python
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


def blocked(inst, keys, fingerprints=None, retry_hours=0, grab_release_days=0):
    return set(db.searched.lookup_many(inst["id"], keys, retry_hours, fingerprints=fingerprints,
                                       grab_release_days=grab_release_days))


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
```

- [ ] **Step 2: Test laufen lassen, er muss scheitern**

Run: `cd <repo> && .venv/bin/python -m pytest tests/test_g2_searched.py -q -p no:cacheprovider`
Expected: FAIL (`TypeError: add() got an unexpected keyword argument 'profile_fingerprint'`)

- [ ] **Step 3: Implementieren**

`backend/db/searched.py` — Edit, old:

```
def add(instance_id: int, cache_key: str, title: str, item_type: str) -> None:
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO searched_items (instance_id, cache_key, title, item_type)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(instance_id, cache_key) DO UPDATE SET searched_at=datetime('now','localtime')
            """,
            (instance_id, cache_key, title, item_type),
        )
```

new:

```
def add(instance_id: int, cache_key: str, title: str, item_type: str,
        profile_fingerprint: Optional[str] = None) -> None:
    """profile_fingerprint: the title's quality profile when it was searched
    (0.9.0). A new search of a cached title takes the new one over, never
    NULL over a known one; it is no grab of the checked search (grabbed_at
    cleared) and no history item wrote it (history_item_id cleared)."""
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO searched_items (instance_id, cache_key, title, item_type, profile_fingerprint)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(instance_id, cache_key) DO UPDATE SET
                searched_at=datetime('now','localtime'),
                profile_fingerprint=COALESCE(excluded.profile_fingerprint, searched_items.profile_fingerprint),
                grabbed_at=NULL,
                history_item_id=NULL
            """,
            (instance_id, cache_key, title, item_type, profile_fingerprint),
        )
```

Edit, old:

```
def lookup_many(instance_id: int, keys: Iterable[str], retry_hours: int = 0) -> dict[str, datetime]:
    """cache_key -> searched_at (UTC) for every key in the cache.

    One connection for a whole page of candidates instead of one per record
    (A-L3). With retry_hours > 0 only entries inside the window count.
    """
```

new:

```
def lookup_many(
    instance_id: int,
    keys: Iterable[str],
    retry_hours: int = 0,
    fingerprints: Optional[dict] = None,
    grab_release_days: int = 0,
) -> dict[str, datetime]:
    """cache_key -> searched_at (UTC) for every key in the cache.

    One connection for a whole page of candidates instead of one per record
    (A-L3). With retry_hours > 0 only entries inside the window count.

    fingerprints (0.9.0, "Search again after profile changes"): cache_key ->
    (current, baseline) fingerprint of the title's quality profile. An entry
    then blocks only while it was searched under the current fingerprint; an
    entry from before 0.9.0 (no fingerprint) counts as searched under the
    baseline. Without a current fingerprint (profile unknown) the entry keeps
    blocking. None: every entry blocks, as before.

    grab_release_days (0.9.0, "Search again if still missing after"): an
    entry of a checked-search grab (grabbed_at) blocks exactly that many
    days — also when retry_hours is shorter — and is released afterwards,
    also under the same fingerprint (a changed profile releases it earlier,
    like any entry). Pass it only for candidates from a wanted list — there
    a grab that is still listed is still missing. 0: off, a grab keeps the
    plain retry window.
    """
```

Edit, old:

```
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
```

new:

```
            statement = (
                "SELECT cache_key, searched_at, profile_fingerprint, "
                "(grabbed_at IS NOT NULL AND grabbed_at <= datetime('now', 'localtime', ? || ' days')) "
                "AS grab_due FROM searched_items "
                f"WHERE instance_id=? AND cache_key IN ({placeholders})"
            )
            params: list = [f"-{max(0, grab_release_days)}", instance_id, *chunk]
            if retry_hours > 0:
                # A grab's own days decide when it is released, not the window.
                statement += (" AND (searched_at > datetime('now', 'localtime', ? || ' hours')"
                              " OR (? > 0 AND grabbed_at IS NOT NULL))")
                params += [f"-{retry_hours}", grab_release_days]
            for row in conn.execute(statement, params):
                if grab_release_days > 0 and row["grab_due"]:
                    continue
                if fingerprints is not None and not _same_profile(row, fingerprints):
                    continue
                found[row["cache_key"]] = local_to_utc(row["searched_at"])
    return found


def _same_profile(row, fingerprints: dict) -> bool:
    """Was this entry searched under the current fingerprint of its title's
    profile? Unknown current fingerprint: yes (nothing is released)."""
    current, baseline = fingerprints.get(row["cache_key"], (None, None))
    if current is None:
        return True
    stored = row["profile_fingerprint"] if row["profile_fingerprint"] is not None else baseline
    return stored == current
```

- [ ] **Step 4: Tests laufen lassen**

Run: `cd <repo> && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: `419 passed` (im G2-Worktree: 353 alte + 66 neue)

- [ ] **Step 5: Commit**

```bash
git add backend/db/searched.py tests/test_g2_searched.py
git commit -m "feat: let a cache entry block only under the profile it was searched with"
```

### Wellen-Abnahme 1

Im Haupt-Arbeitsbaum: `git merge --no-ff feat/checked-search-g1`, `git merge --no-ff feat/checked-search-g2`, dann
Run: `cd <repo> && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: `498 passed, 2 skipped`. Danach Worktrees und Zweige `g1`, `g2` entfernen.

---

## Paket G3 — Runner und Einbindung in die Skills (Welle 2)

Abnahme G3: `tests/test_g3_*.py` grün (`88 passed`); Gesamtsuite im Worktree `586 passed, 2 skipped`; nur der Runner liest Treffer-Felder, und `guid` wird nur für den POST benutzt:
`grep -rn '"downloadUrl"\|"infoUrl"\|"magnetUrl"\|get("guid")' backend/` → genau eine Zeile (`runner._approved`, `guid=str(item.get("guid") or "")`). Bei `checked_search='off'` bleiben die alten Tests (`test_p2_*`) unverändert grün; dort scheitert der Profil-Abruf an der Test-Attrappe (sie kennt den Parameter `timeout` nicht), und der Lauf sucht mit dem gespeicherten (leeren) Stand wie bisher.

### Task G3.1: Runner, Profilstand und Fehlend-Suche

**Files:**
- Create: `backend/checked_search/runner.py`, `backend/skills/profiles.py`
- Modify: `backend/agents/base.py` (`http_get`, `http_post`), `backend/skills/base.py` (`SearchResult`, `SubmitOutcome`, `_record`, `submit_candidates`, `finish_search_run`), `backend/skills/search_missing.py`
- Test: `tests/test_g3_runner.py`

**Interfaces:**
- Consumes: G1 (`radarr_rules`, `sonarr_rules`, `CheckedSearchSettings.from_stored/rules_fingerprint/search_again_after_days`, `Verdict`, `REASON_PARSE_ERROR`, `REASON_SEASON_PACK`, `REASON_TARGET`, `parse_utc`, `fingerprint.fingerprints/changes/short`), G2 (`db.history.record_checked(…, cache=)`, `record_submission(…, profile_fingerprint)`, `db.checked_search_log.insert/dry_run_keys(…, dry_run_round)`, `db.searched.lookup_many(…, fingerprints, grab_release_days)`, `db.instances.store_profile_fingerprints`, `db.instances.reset_dry_run`, `ITEM_GRABBED/ITEM_NO_HIT/ITEM_FAILED`, `db.instances` mit `checked_search`, `checked_search_settings` als dict, `dry_run_round`, `search_again_after_profile_change`, `profile_fingerprints`, `profile_fingerprints_baseline`)
- Produces: `run_checked`, `CheckedTask`, `CheckedRunOutcome`, `MODE_*`, `OUTCOME_*` (Vertrag G3); `profiles.refresh`, `ProfileState`; `http_get(…, timeout=)`, `http_post(…, timeout=)`; `SubmitOutcome.handled`, `SubmitOutcome.paused`, `UnsavedCheckedGrab`, `SearchResult.profile_fingerprint`, `submit_candidates(…, fingerprint_of=)`.

Ablauf pro Titel (`_TitleCheck.run`), genau nach Spec:
1. Radarr `GET /api/v3/movie/{id}`; Sonarr `GET /api/v3/episode/{id}` und `GET /api/v3/series/{seriesId}`. Fehler → `error`; aktiv Verlaufseintrag `failed` ohne Cache (Codex K4: sonst endete ein Lauf mit einem Ladefehler und einem Grab als `success` statt `partial`, `record_failed_submission` und `skills/base.py` halten das seit 0.8.0 so), Probelauf nur Protokoll; Rate-Slot zurück (es lief keine Suche). Der Profil-Fingerabdruck des Titels kommt ab hier aus dem gerade geladenen Film bzw. der Serie (`qualityProfileId`; Codex K3), nur ohne Treffer im Profilstand aus dem Datensatz der Liste.
2. `GET /api/v3/release?movieId=` bzw. `?episodeId=` mit `timeout=release_timeout_seconds`. Fehler → `error`; aktiv Verlaufseintrag `failed` (kein Cache), Probelauf nur Protokoll (die Zeile zählt nicht zur Runde, G2.2). Rate-Slot (Codex K5): zurück nur, wenn die Anfrage \*arr nachweislich nicht erreicht hat (`ConnectTimeout`, oder `ConnectionError` mit `MaxRetryError(reason=NewConnectionError)` — Verbindung abgelehnt, Name nicht auflösbar) oder \*arr mit 3xx/4xx ablehnt (`_refused`). Jede andere `ConnectionError` (Reset oder Schließen nach dem Senden, hängender Antwortkörper: requests packt `ProtocolError` und `ReadTimeoutError` des Körpers in `ConnectionError`, `adapters.py:500f`, `models.py:821f`), `ReadTimeout`, 5xx oder eine unlesbare Antwort lassen ihn belegt: Radarr und Sonarr rufen die Suche ohne Abbruchsignal auf (`ReleaseController.cs` `MovieSearch`/`EpisodeSearch` ohne `CancellationToken`), sie läuft zu Ende (Spec: „zählt als eine Aktion gegen das Rate-Limit“).
3. **Abbruch prüfen** (direkt nach der Suche). Dann nur `approved is True`, in der gelieferten Reihenfolge; der erste ist, was der Such-Befehl geladen hätte (`arr_pick`). Keiner → Indexer-Störung prüfen (siehe unten „Indexer-Störung“), ohne Störung `no_results` (aktiv: `no_hit` + Cache).
4. Je Kandidat, **vor jedem** `/parse` (auch vor dem ersten) Abbruch prüfen, dann `GET /api/v3/parse?title=` (Standard-Wartezeit 10 s). Scheitert es (Zeitüberschreitung, HTTP-Fehler, unlesbare Antwort; `_ParseFailed`) → Urteil `error` mit Grund `parse error`: nie geladen, aber keine Regel-Ablehnung (zählt nicht zu „Rejected“, Codex-Prüfung 0.9.0). Ohne `/parse` verworfen werden: Treffer, die `GET /release` nicht genau diesem Titel zugeordnet hat oder die keine Qualität (`dict`) und Sprachen (`list`) melden (Grund `not mapped to this title`; Radarr `mappedMovieId == movieId`, Sonarr `mappedSeriesId` = Serie der Folge und die Folge in `mappedEpisodeInfo`), Sonarr-Treffer mit `fullSeason` (Grund `season pack`) und Sonarr-Treffer, deren `mappedEpisodeInfo` mehr als eine Folge nennt (Grund `multi-episode release`; Entscheidung Daniel 01.10.2026, Codex-Runde 2, F4: Sonarr gibt `S01E01E02` bei der Suche nach E01 frei, `SingleEpisodeSearchMatchSpecification.cs:59` verlangt nur, dass die gesuchte Folge enthalten ist; S3/S4 und der Cache-Eintrag deckten aber nur die gesuchte Folge ab, die zweite würde ungeprüft geladen und blieb ohne Sperre). Ein Abbruch beendet den Lauf, der Titel bekommt keine Zeile.
5. Aktiv: erster bestehender Kandidat → **Abbruch prüfen** → `POST /api/v3/release` mit `timeout=release_timeout_seconds` und **festgelegtem Ziel** (Codex K1): `{"guid", "indexerId", "shouldOverride": true, "quality", "languages"}` plus Radarr `movieId`, Sonarr `seriesId` und `episodeIds` (genau die gesuchte Folge, `[episodeId]`; Mehrfachfolgen kommen nicht bis hier). `quality` und `languages` gehen unverändert zurück, wie `GET /release` sie lieferte. Die übrigen Kandidaten gehen als `unchecked` ins Protokoll. Erfolg → `grabbed` (Item + Cache mit `grabbed_at` + Protokoll in einer Transaktion, mit Fingerabdruck). Fehler (Codex K2, `_refused` wie oben): abgelehnt → `grab_failed`, Item `failed`, kein Cache; sonst → `grab_uncertain`, Item `failed` mit Hinweis auf die Warteschlange, **mit** Cache und `grabbed_at` (der Download läuft vielleicht). In beiden Fällen **kein zweiter Kandidat**. Keiner besteht → Indexer-Störung prüfen, ohne Störung `no_clean_hit` (Item `no_hit` + Cache). Keiner besteht und mindestens ein `/parse` scheiterte → `error` mit dem `/parse`-Fehler in `error_message`, Item `failed`, **kein** Cache, Rate-Slot belegt (die Suche lief): Der nicht prüfbare Treffer kann der richtige sein, ein `no_hit` sperrte den Titel bei `retry_hours=0` für immer. Besteht ein Kandidat nach einem nicht prüfbaren, wird er wie sonst geladen (er hat alle Regeln bestanden, es leidet höchstens der Rang; ein `/parse`, das für einen Release-Namen jedes Mal scheitert, ließe den Titel sonst nie laden und kostete jeden Lauf eine Indexer-Suche).
6. Probelauf: höchstens `dry_run_max_releases` Kandidaten prüfen, der Rest `unchecked`; Ergebnis `would_grab` oder `no_clean_hit`, oder `error` wie in 5, wenn keiner besteht und ein `/parse` scheiterte oder ein Indexer gestört war (zählt nicht zur Runde); nur Protokoll (mit Profil-Fingerabdruck, Runde und Einstellungs-Fingerabdruck).

**Indexer-Störung (Codex-Prüfung 0.9.0, am Quellcode geprüft).** Ein Indexer-Fehler lässt `GET /release` nicht scheitern: `HttpIndexerBase.FetchReleases` fängt Netz-, HTTP-, Schlüssel-, Captcha- und Zeitfehler selbst ab, ruft `IndexerStatusService.RecordFailure` (bzw. `RecordConnectionFailure`) und liefert, was bis dahin da war; `ReleaseSearchService.DispatchIndexer` fängt den Rest und gibt eine leere Liste (Radarr `:130–141`, Sonarr `:546–557`). `RecordFailure` setzt `MostRecentFailure` und sperrt den Indexer mindestens 60 s (`EscalationBackOff.Periods[1]`, steigend bis 24 h); `InteractiveSearchEnabled()` lässt gesperrte Indexer dann ganz weg (`FilterBlockedIndexers`). Leere oder nur abgelehnte Treffer sähen so wie ein sauberer Fehlschlag aus, `no_hit` sperrte den Titel bei `retry_hours=0` für immer, ein Probelauf zählte ihn zur Runde. Einen Endpunkt `/api/v3/indexerstatus` haben Radarr und Sonarr nicht (nur Prowlarr; nicht in `Radarr.Api.V3`/`Sonarr.Api.V3` und nicht in deren `openapi.json`). Sichtbar ist die Sperre nur in `GET /api/v3/health`: `IndexerStatusCheck` (gesperrt, erste Störung unter 6 h) und `IndexerLongTermStatusCheck` (länger) nennen die gesperrten Indexer mit Namen (`"Indexers unavailable due to failures: A, B"`, alle gesperrt: ohne Namen). Beide laufen bei `ProviderStatusChangedEvent` (also bei jeder Störung und bei der Erholung), entprellt um 5 s (`HealthCheckService`, `Debouncer` 5 s); daher wartet der Runner `HEALTH_SETTLE_SECONDS` = 6 s, bevor er liest, sonst fiele eine Störung kurz vor dem Ende der Suche durch, und eine eben behobene stünde noch da. Die Meldung bleibt bis zur nächsten Auswertung stehen, auch wenn die 60 s Sperre schon um sind; erst ein erfolgreicher Abruf (`RecordSuccess`) räumt sie. Deshalb gilt „gesperrt laut Health“ als Störung während der Suche. Gezählt wird nur ein Indexer, den die Suche fragt: Ein Name, der in `GET /api/v3/indexer` zu einem Indexer mit „Interactive Search“ aus gehört oder zu einem mit Tags, von denen der Film bzw. die Serie keinen trägt, zählt nicht (`_asked`; \*arr fragt einen Indexer mit Tags nur für Titel mit einem gemeinsamen Tag, `ReleaseSearchService.Dispatch`, Radarr `:105` mit `Movie.Tags`, Sonarr `:521` mit `Series.Tags`; die Tags kommen aus dem in Schritt 1 geladenen Film bzw. der Serie, `load`). Sonst blieben bei einem dauerhaft gestörten Indexer mit Tags alle anderen Titel ohne sauberen Treffer für immer ungemerkt, obwohl er für sie gar nicht gefragt wird. Alles andere zählt (unbekannter Name, Meldung ohne Namen, übersetzte Meldung ohne `": "`, Tags des Titels nicht lesbar). Die Indexer-Liste wird nur gelesen, wenn es eine solche Meldung gibt. Ergebnis bei Störung: `error` (aktiv `failed` ohne Cache, Probelauf zählt nicht zur Runde, Rate-Slot belegt), Grund in `error_message` und im Aktivitätslog (`"Checked search for <titel> failed: indexer failure during search — *arr reports: …"`). Health oder Indexer-Liste nicht lesbar: ebenso `error` (im Zweifel nicht merken). Ein Treffer, der besteht, braucht die Abfrage nicht. Grenze: Ein Fehler, den Prowlarr selbst schluckt und als leere Antwort weitergibt, sieht \*arr nicht; das gilt für den Such-Befehl genauso.

**Warum das Ziel festgelegt wird (Codex K1, am Quellcode bestätigt).** Radarr und Sonarr legen jede Entscheidung einer Suche 30 Minuten in einen prozessweiten Zwischenspeicher, Schlüssel `indexerId + "_" + guid` (Radarr `ReleaseController.cs:65,175–185`, Sonarr `:66,237–247`); jede Suche, die denselben Treffer liefert, überschreibt den Eintrag mit *ihrer* Zuordnung (`ParsingService` ordnet ohne passende ID über Titel, Alias oder die gesuchte Folge zu). Ohne `shouldOverride` lädt `POST` den Treffer für das Ziel aus dem Zwischenspeicher (`movieId`/`seriesId` wirken nur, wenn dort keins steht). Überlappen kann sich vieles: die beiden Skills derselben Instanz (eigene APScheduler-Jobs, je eigene Sperre), Handsuchen, andere Clients, RSS-Abfragen. Mit `shouldOverride` setzt \*arr Film bzw. Serie und Folgen, Qualität und Sprachen aus dem POST (Radarr `:83–106`, Sonarr `:84–112`); aus dem Zwischenspeicher bleiben nur der Treffer selbst (dieselbe Download-Adresse), die Titel-Zerlegung und Verlaufsdaten (Custom Formats, Punkte). Fehlt der Eintrag (abgelaufen): 404 → `grab_failed`.

**Indexer-Schalter (Entscheidung Daniel 01.10.2026).** `GET /release` ruft in Radarr `MovieSearch(movieId, true, true)` und in Sonarr `EpisodeSearch(episodeId, true, true)` mit `interactiveSearch=true` auf; `ReleaseSearchService.Dispatch` fragt dann die Indexer mit „Interactive Search“ statt der mit „Automatic Search“ (Radarr `ReleaseSearchService.cs:100–102`, Sonarr `:516–518`). Jeder geprüfte Lauf liest deshalb zu Beginn `GET /api/v3/indexer` (keine Indexer-Last), und zwar im Skill nach dem Profilstand und **vor dem Sammeln der Kandidaten** (Codex-Runde 3, G4, mit Probe bestätigt: stand die Prüfung erst im Runner und nur bei vorhandenen Titeln, endete ein Lauf ohne Titel — fertige Probelauf-Runde, alles gesperrt, leere Wanted- oder Cutoff-Liste — über die alten Ausgänge „Nothing to search“ bzw. „No upgrade candidates found“ als gewöhnlicher Erfolg, ohne Hinweis und mit neuem `last_sync`, obwohl ein Schalter abwich). Der Skill übergibt dann `check_indexers=False` an `run_checked`; ein direkter Aufruf von `run_checked` prüft selbst. Ein Lauf mit „per run“ = 0 prüft nicht: nichts zu tun ist kein geprüfter Lauf. Hat ein Indexer die beiden Schalter verschieden gesetzt (oder ist die Liste nicht lesbar), **setzt der Lauf aus** (`indexer_pause`): keine Suche, kein Protokoll, kein Verlauf, kein Cache, keine Rate-Aktion; Warnung im Aktivitätslog mit dem Indexer bzw. dem Lesefehler (eigener Hinweistext `"Checked search paused — could not read the indexer list: <fehler>"`), der Lauf endet mit Status **`success`** und dem Hinweis `"Checked search paused — …"` vorn im Fehlertext (`SubmitOutcome.paused`; Entscheidung Daniel 01.10.2026: das Aussetzen ist gewollt, kein Fehler, und ein Lauf mit `error` fiele in der History zwischen echten Fehlern auf, obwohl nichts kaputt ist). **`last_sync` bleibt stehen:** Die Karte nennt unter „Last sync“ den letzten Lauf, der wirklich gesucht hat. Ein ausgesetzter Lauf hat nichts gesucht; rückte `last_sync` vor, sähe die Karte bei einem stundenlang abweichenden Schalter frisch aus. So zeigt die Karte das Aussetzen, ohne dass der Lauf als Fehler zählt; den Grund nennen History und Aktivitätslog. Der nächste Lauf prüft erneut. Hintergrund: Ein externer Prüflauf schaltet sonntags früh kurz die interaktive Suche eines Indexers ab; ein Lauf in diesem Fenster hätte sonst mit unvollständigen Treffern Titel als gesucht gemerkt. Das ersetzt das frühere Verwerfen von Treffern solcher Indexer.

**Profilstand (Spec-Nachtrag).** Beide Such-Skills rufen nach der „per run“-Prüfung `profiles.refresh` auf, in beiden Suchwegen: `GET /api/v3/qualityprofile`, `/customformat`, `/releaseprofile`, `/qualitydefinition`, `/config/indexer` (je `timeout=60`; die letzten beiden seit dem Nachtrag vom 02.10.2026, G1.6: globale Größengrenzen und Indexer-Einstellungen, die über `approved` mitentscheiden; scheitert einer der fünf Abrufe oder hat die Antwort nicht die erwartete Form, gilt dasselbe wie bei den Profilen: gespeicherter Stand, nichts freigegeben, dieselbe Warnung), Fingerabdruck je Profil (G1.6), Vergleich mit `instances.profile_fingerprints`; jede Änderung eines vorhandenen Profils kommt als `"Quality profile changed: <name> (<alt 8> → <neu 8>)"` ins Aktivitätslog; dann `store_profile_fingerprints` (setzt beim ersten Mal die Grundlinie). Scheitert der Abruf, gilt der gespeicherte Stand (Warnung), es wird nichts neu freigegeben. Scheitert beim ersten Lauf von 0.9.0 nur das **Speichern** (Datenbank gesperrt, Platte voll), gilt für diesen Lauf der gerade gelesene Stand als Grundlinie (Codex-Runde 3, G2, mit Probe bestätigt: vorher blieb die Grundlinie `None`, Alt-Einträge verglichen `None` mit dem neuen Fingerabdruck und wurden frei, auch bei Checked search Off). Der nächste Lauf speichert die Grundlinie dann wirklich (`COALESCE`); eine Profiländerung genau zwischen diesen beiden Läufen gibt Alt-Einträge nicht frei, die vorsichtige Richtung. Das Profil eines Titels: Radarr `qualityProfileId` am Film (Wanted-, Cutoff- und Film-Liste liefern ihn). Sonarr-Folgen tragen keins; `series.qualityProfileId` steht in `/wanted/missing` und `/wanted/cutoff` nur mit `includeSeries=true` (Sonarr `MissingController.cs:29`, `CutoffController.cs:33`, Voreinstellung `false`), und missingarr sendet den Parameter nicht. Sonarr liest deshalb einmal pro Lauf `GET /api/v3/series` (Serie → Profil). `includeSeries=true` wäre die Alternative, ist aber verworfen: Jede Folge einer Seite (bis 1.000) brächte die ganze Serie mit, und `tests/test_p2_search_missing.py:146` legt die Parameter der Wanted-Abfrage fest. Profil unbekannt (Serienliste nicht lesbar, Titel ohne Profil) → beim **Auswählen** Fingerabdruck `None` → der Cache-Eintrag sperrt, nichts wird freigegeben. Beim **Merken** darf der Fingerabdruck dagegen nicht fehlen (Codex K3: ein neuer Eintrag mit `NULL` gälte als Alt-Eintrag unter der Grundlinie und würde nach einer früheren Profiländerung sofort wieder frei): Die geprüfte Suche nimmt ihn aus dem geladenen Film bzw. der Serie, der Befehlsweg fragt bei nicht lesbarer Serienliste die einzelne Serie (`ProfileState.stored_fingerprint`, je Serie und Lauf einmal, nur für eingereichte Titel), und `_UPSERT_SEARCHED` ersetzt einen vorhandenen Fingerabdruck nie durch `NULL` (G2.3).

Pro Lauf: einmal die Indexer-Liste (im Skill vor dem Sammeln, Aussetzen siehe oben), dann vor jedem Titel Abbruch und Zeitbudget prüfen (kein neuer Titel nach Ablauf, Hinweis am Lauf; das Budget zählt ab dem Start des Skills, `started`, Codex K9), dann `reserve_action()` (eine Aktion pro Titel; `None` → Rate-Cap, Lauf endet). Zwischen Titeln `seconds_between_actions` per `wait_or_stop`. Scheitert das Schreiben in die Datenbank, endet der Lauf wie in `submit_candidates` mit `store_error`; war der Titel geladen (oder unklar geladen), bleibt er als `UnsavedCheckedGrab` in `runtime.unsaved_submissions` (Codex K2), sperrt seinen Titel (`unsaved_cache_keys`) und wird von `store_unsaved_submissions` zu Beginn des nächsten Laufs nachgespeichert, dem ursprünglichen Lauf zugeordnet (oder dem neuen, wenn es den alten nicht mehr gibt). Die Instanz-Konfiguration liest der Runner einmal beim Start (`config=cfg` vom Skill): Runde, Einstellungen und Einstellungs-Fingerabdruck ändern sich während eines Laufs nicht, auch wenn das Formular gespeichert oder die Runde zurückgesetzt wird (`agent.reload`/`refresh_config` tauschen `agent.config` aus). Zählung: Probelauf `handled` = Titel ohne Fehler, `triggered` = 0; aktiv `triggered` = `grabbed + no_hit`. So bleibt `finish_search_run` für beide Modi ehrlich (alle gescheitert → `error`; einige → `success` mit Fehlertext). `finish_search_run` liest `last_verified` jetzt aus dem gerade beendeten Lauf statt es auf 0 zu setzen: Ein aktiver Lauf der geprüften Suche hat sein `verified_count` sofort (G2.3), die Karte zeigt die bestätigten Titel also gleich, nicht erst nach der nächsten Befehlsprüfung; für Befehls-Läufe ist der Wert wie bisher 0.

In der Fehlend-Suche gilt bei `checked_search != 'off'`: Sonarr arbeitet immer im Modus `episode` (auch wenn in der DB ein anderer steht), und im Probelauf ersetzt die Runde den Such-Cache: ein Datensatz fällt nur weg, wenn sein eigener Schlüssel (`mov:`/`ep:`) in der Runde, mit der der Lauf beginnt (`cfg["dry_run_round"]`), schon eine Zeile **mit dem aktuellen Fingerabdruck seines Profils und dem aktuellen Einstellungs-Fingerabdruck** hat (`ProfileState.round_blocks`; unabhängig von der Einstellung, Spec: „der Probelauf berücksichtigt Profiländerungen immer“; Einstellungen: Entscheidung Daniel 01.10.2026). Das gilt **auch beim Force Run** (Codex K8, Entscheidung Daniel): `force` übergeht Ruhezeiten, Release-Fenster und Such-Cache, aber nicht die Probelauf-Runde; wer neu prüfen will, nutzt „Reset dry run“. Wie gespeicherte Cache-Einträge (und wie ungespeicherte Befehle seit 0.8.0, `hits = {} if force …`) übergeht der Force Run auch ungespeicherte Grabs (`unsaved_cache_keys`); normale Läufe sperren sie bis zum Nachspeichern (Codex-Runde 2, F2, widerlegt als eigene Lücke: ein gespeicherter Grab verhält sich beim Force Run genauso). Sonst fragt die Cache-Prüfung `lookup_many` mit `ProfileState.cache_filter` (bei ausgeschalteter Einstellung `None`) und `grab_release_days = search_again_after_days` (die Datensätze stammen aus der Wanted-Liste). Beide Suchwege schreiben den Fingerabdruck des Titels: der alte über `submit_candidates(…, fingerprint_of=profiles.stored_fingerprint)` → `SearchResult.profile_fingerprint` → `record_submission`, der geprüfte über den Fingerabdruck des geladenen Titels → `record_checked` und Protokoll.

- [ ] **Step 1: Failing test schreiben**

`tests/test_g3_runner.py`:

```python
import copy
import itertools
import json
import sqlite3

import pytest
import requests
from urllib3.exceptions import MaxRetryError, NewConnectionError, ProtocolError, ReadTimeoutError

from backend import database, db
from backend.agents.base import BaseAgent
from backend.config import settings
from backend.db import history
from backend.skills.search_missing import SearchMissingSkill

WANTED = "/api/v3/wanted/missing"
CUTOFF = "/api/v3/wanted/cutoff"
RELEASE = "/api/v3/release"
PARSE = "/api/v3/parse"
COMMAND = "/api/v3/command"
HEALTH = "/api/v3/health"
QUALITY_PROFILES = "/api/v3/qualityprofile"
INDEXER_KEY = "PROWLARRSECRET123"
API_KEY = "ARRSECRETKEY1234567890"

PROFILES = [{"id": 1, "name": "HD", "cutoff": 7, "minFormatScore": 0,
             "formatItems": [{"format": 1, "name": "German", "score": 100}]}]
FORMATS = [{"id": 1, "name": "German", "specifications": []}]
INDEXERS = [{"id": 7, "name": "Indexer", "enableAutomaticSearch": True, "enableInteractiveSearch": True}]
# Quality and languages as GET /release reports them; the grab sends them back unchanged.
QUALITY = {"quality": {"id": 7, "name": "Bluray-1080p"}, "revision": {"version": 1, "real": 0, "isRepack": False}}
LANGUAGES = [{"id": 4, "name": "German"}]


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
    from backend.checked_search import runner
    monkeypatch.setattr(runner, "HEALTH_SETTLE_SECONDS", 0, raising=False)


def sql(statement, params=()):
    with database.get_db() as conn:
        return [tuple(r) for r in conn.execute(statement, params).fetchall()]


def make_instance(**fields):
    data = {"name": "Radarr", "type": "radarr", "url": "http://127.0.0.1:9", "api_key": API_KEY,
            "seconds_between_actions": 0, "hours_after_release": 0, "rate_cap": 1000,
            "search_order": "oldest_first", "missing_per_run": 5, "checked_search": "dry_run"}
    data.update(fields)
    return db.instances.create(data)


def movie(movie_id, title, year, **extra):
    data = {"id": movie_id, "title": title, "originalTitle": title, "alternateTitles": [], "year": year,
            "hasFile": False, "monitored": True, "digitalRelease": f"{year}-06-01T00:00:00Z",
            "qualityProfileId": 1}
    data.update(extra)
    return data


def release(title, guid, approved=True, indexer_id=7, score=100, published="2026-01-01T00:00:00Z",
            movie_id=1, series_id=None, episode_ids=()):
    """A release as GET /release returns it. Radarr maps it to movie_id;
    with series_id it is a Sonarr release mapped to that series and episodes."""
    data = {"title": title, "guid": guid, "indexerId": indexer_id, "indexer": "Indexer",
            "approved": approved, "customFormatScore": score, "size": 4_000_000_000,
            "quality": copy.deepcopy(QUALITY), "languages": copy.deepcopy(LANGUAGES),
            "movieTitles": [], "publishDate": published,
            "downloadUrl": f"http://127.0.0.1:9696/1/download?apikey={INDEXER_KEY}&link=abc",
            "infoUrl": f"http://127.0.0.1:9696/info?apikey={INDEXER_KEY}",
            "magnetUrl": f"magnet:?xt=urn:btih:abc&tr=http://127.0.0.1:9696/{INDEXER_KEY}/announce"}
    if series_id is None:
        data["mappedMovieId"] = movie_id
    else:
        data["mappedSeriesId"] = series_id
        data["mappedEpisodeInfo"] = [{"id": e, "seasonNumber": 1, "episodeNumber": e} for e in episode_ids]
    return data


def movie_grab(guid, movie_id=1, indexer_id=7):
    """The POST body of a grab with its target named (shouldOverride)."""
    return {"guid": guid, "indexerId": indexer_id, "shouldOverride": True, "quality": QUALITY,
            "languages": LANGUAGES, "movieId": movie_id}


def episode_grab(guid, series_id=10, episode_ids=(3,), indexer_id=7):
    return {"guid": guid, "indexerId": indexer_id, "shouldOverride": True, "quality": QUALITY,
            "languages": LANGUAGES, "seriesId": series_id, "episodeIds": list(episode_ids)}


def http_error(status):
    response = requests.Response()
    response.status_code = status
    return requests.exceptions.HTTPError(f"{status} answer", response=response)


def refused():
    """What requests raises when nothing listens: the request never left."""
    return requests.exceptions.ConnectionError(
        MaxRetryError(None, RELEASE, NewConnectionError(None, "Connection refused")))


def changed_profiles(score=50):
    profiles = copy.deepcopy(PROFILES)
    profiles[0]["formatItems"][0]["score"] = score
    return profiles


class FakeArr(BaseAgent):
    """Radarr/Sonarr stand-in for the checked search. parses: release title ->
    /parse answer; anything missing parses to nothing. get_errors: path
    prefix (or exact path with a trailing $) -> exception. on_get: path ->
    hook(fake) run before that GET is answered.

    Like *arr it keeps every release of a GET /release under (indexerId,
    guid), mapped to the title of the search that returned it last; a POST
    without shouldOverride grabs for that cached title, with shouldOverride
    for the title in the body (required fields checked like upstream).
    grabbed: the titles really grabbed (movie id, or (series id, episode ids))."""

    def __init__(self, config, missing=(), cutoff=(), movies=(), episodes=(), series=(), releases=None,
                 parses=None, get_errors=None, post_error=None, abort_after_parses=None, abort_on_release=False,
                 profiles=None, custom_formats=None, release_profiles=None, indexers=None, on_get=None,
                 health=None):
        super().__init__(config)
        self.missing, self.cutoff = list(missing), list(cutoff)
        self.movies = {m["id"]: m for m in movies}
        self.episodes = {e["id"]: e for e in episodes}
        self.series = {s["id"]: s for s in series}
        self.releases = releases or {}
        self.parses = parses or {}
        self.get_errors = get_errors or {}
        self.post_error = post_error
        self.abort_after_parses = abort_after_parses
        self.abort_on_release = abort_on_release
        self.profiles = copy.deepcopy(PROFILES if profiles is None else profiles)
        self.custom_formats = copy.deepcopy(FORMATS if custom_formats is None else custom_formats)
        self.release_profiles = list(release_profiles or [])
        self.indexers = copy.deepcopy(INDEXERS if indexers is None else indexers)
        self.health = copy.deepcopy(health or [])
        self.on_get = on_get or {}
        self.gets, self.posts, self.commands, self.timeouts = [], [], [], {}
        self.release_cache, self.grabbed = {}, []

    def build_skills(self):
        return []

    @staticmethod
    def _page(records, params):
        size, page = int(params["pageSize"]), int(params["page"])
        return {"totalRecords": len(records), "records": records[(page - 1) * size: page * size]}

    @staticmethod
    def _matches(path, pattern):
        return path == pattern[:-1] if pattern.endswith("$") else path.startswith(pattern)

    @staticmethod
    def _target(item):
        if "mappedSeriesId" in item:
            return item.get("mappedSeriesId"), tuple(e["id"] for e in item.get("mappedEpisodeInfo") or [])
        return item.get("mappedMovieId")

    def http_get(self, path, params=None, timeout=10):
        params = dict(params or {})
        self.gets.append((path, params))
        self.timeouts[path] = timeout
        if path in self.on_get:
            self.on_get[path](self)
        for pattern, error in self.get_errors.items():
            if self._matches(path, pattern):
                raise error
        if path == WANTED:
            return self._page(self.missing, params)
        if path == CUTOFF:
            return self._page(self.cutoff, params)
        if path == QUALITY_PROFILES:
            return copy.deepcopy(self.profiles)
        if path == "/api/v3/customformat":
            return copy.deepcopy(self.custom_formats)
        if path == "/api/v3/releaseprofile":
            return copy.deepcopy(self.release_profiles)
        if path == "/api/v3/indexer":
            return copy.deepcopy(self.indexers)
        if path == HEALTH:
            return copy.deepcopy(self.health)
        if path == "/api/v3/movie":
            return list(self.movies.values())
        if path.startswith("/api/v3/movie/"):
            return self.movies[int(path.rsplit("/", 1)[1])]
        if path.startswith("/api/v3/episode/"):
            return self.episodes[int(path.rsplit("/", 1)[1])]
        if path == "/api/v3/series":
            return list(self.series.values())
        if path.startswith("/api/v3/series/"):
            return self.series[int(path.rsplit("/", 1)[1])]
        if path == RELEASE:
            if self.abort_on_release:
                self.request_abort()   # instance switched off while *arr searches
            key = params.get("movieId") or params.get("episodeId")
            answer = copy.deepcopy(self.releases.get(key, []))
            for item in answer:
                self.release_cache[(item.get("indexerId"), item.get("guid"))] = self._target(item)
            return answer
        if path == PARSE:
            parses = [p for p, _ in self.gets if p == PARSE]
            if self.abort_after_parses is not None and len(parses) >= self.abort_after_parses:
                self.request_abort()
            answer = self.parses.get(params["title"], {})
            if isinstance(answer, Exception):
                raise answer
            return answer
        raise AssertionError(f"unexpected GET {path}")

    def http_post(self, path, body, timeout=10):
        if path == COMMAND:   # the old search path (checked search off)
            self.commands.append(body)
            return {"id": 100 + len(self.commands)}
        assert path == RELEASE, path
        self.posts.append(copy.deepcopy(body))
        if self.post_error is not None:
            raise self.post_error
        key = (body.get("indexerId"), body.get("guid"))
        if key not in self.release_cache:
            raise http_error(404)        # "Couldn't find requested release in cache"
        if body.get("shouldOverride"):
            if self.config["type"] == "radarr":
                missing = body.get("movieId") is None
                target = body.get("movieId")
            else:
                missing = body.get("seriesId") is None or not body.get("episodeIds")
                target = (body.get("seriesId"), tuple(body.get("episodeIds") or ()))
            if missing or body.get("quality") is None or body.get("languages") is None:
                raise http_error(400)
        else:
            target = self.release_cache[key]
        self.grabbed.append(target)
        return dict(body)


def agent_for(inst, **kwargs):
    return FakeArr(db.instances.get_by_id(inst["id"]), **kwargs)


def radarr_parse(title, year, movie_id=None):
    return {"parsedMovieInfo": {"movieTitles": [title], "year": year},
            "movie": {"id": movie_id} if movie_id else None}


THE_THING = movie(1, "Das Ding aus einer anderen Welt", 1982, originalTitle="The Thing",
                  inCinemas="1982-06-25T00:00:00Z")
WRONG = "Das.Ding.aus.einer.anderen.Welt.1951.German.DL.1080p.BluRay.x265-GRP"
RIGHT = "The.Thing.1982.German.DL.1080p.BluRay.x264-GRP"
PARSES = {WRONG: radarr_parse("Das Ding aus einer anderen Welt", 1951),
          RIGHT: radarr_parse("The Thing", 1982, movie_id=1)}


def the_thing_agent(inst, **kwargs):
    defaults = dict(missing=[THE_THING], movies=[THE_THING],
                    releases={1: [release(WRONG, "guid-wrong"), release(RIGHT, "guid-right")]}, parses=PARSES)
    defaults.update(kwargs)
    return agent_for(inst, **defaults)


def log_rows():
    return db.checked_search_log.query(limit=100)


def last_run():
    return history.query(limit=1)[0]


def activity_messages():
    return [r["message"] for r in db.activity.query(limit=200, include_debug=True)]


def stored_fingerprint(inst, profile_id="1"):
    return db.instances.get_by_id(inst["id"])["profile_fingerprints"][profile_id]


# ── Dry run ──────────────────────────────────────────────────────────────────

def test_dry_run_grabs_nothing_and_remembers_nothing(db_path):
    inst = make_instance()
    agent = the_thing_agent(inst)
    SearchMissingSkill().execute(agent)

    assert agent.posts == []
    assert sql("SELECT COUNT(*) FROM search_history_items")[0][0] == 0
    assert sql("SELECT COUNT(*) FROM searched_items")[0][0] == 0
    [row] = log_rows()
    assert (row["mode"], row["outcome"], row["arr_pick"], row["pick"]) == ("dry_run", "would_grab", WRONG, RIGHT)
    assert [(c["verdict"], c["reasons"], c["chosen"], c["arr_choice"]) for c in row["candidates"]] == [
        ("reject", ["year"], False, True), ("pass", [], True, False)]
    assert row["profile_fingerprint"] == stored_fingerprint(inst)
    run = last_run()
    assert (run["status"], run["triggered_count"], run["error_message"]) == ("success", 0, None)
    assert "Dry run: 1 title(s) checked, 1 would grab" in activity_messages()


def test_dry_run_checks_every_title_once_per_round(db_path):
    inst = make_instance()
    SearchMissingSkill().execute(the_thing_agent(inst))
    second = the_thing_agent(inst)
    SearchMissingSkill().execute(second)
    assert [p for p, _ in second.gets if p == RELEASE] == []
    assert len(log_rows()) == 1

    db.instances.reset_dry_run(inst["id"])
    third = the_thing_agent(inst)
    SearchMissingSkill().execute(third)
    assert len([p for p, _ in third.gets if p == RELEASE]) == 1
    assert [r["dry_run_round"] for r in log_rows()] == [1, 0]


def test_force_run_in_dry_run_respects_the_round(db_path):
    inst = make_instance()
    SearchMissingSkill().execute(the_thing_agent(inst), force=True)
    forced = the_thing_agent(inst)
    SearchMissingSkill().execute(forced, force=True)
    assert [p for p, _ in forced.gets if p == RELEASE] == []
    assert len(log_rows()) == 1


def test_reset_during_a_running_dry_run_keeps_its_rows_in_the_old_round(db_path):
    inst = make_instance()
    # "Reset dry run" while *arr is still searching for the title
    agent = the_thing_agent(inst, on_get={RELEASE: lambda fake: db.instances.reset_dry_run(inst["id"])})
    SearchMissingSkill().execute(agent)
    [row] = log_rows()
    assert row["dry_run_round"] == 0
    assert db.instances.get_by_id(inst["id"])["dry_run_round"] == 1
    assert db.checked_search_log.query(current_round=True) == []
    again = the_thing_agent(inst)
    SearchMissingSkill().execute(again)
    assert len([p for p, _ in again.gets if p == RELEASE]) == 1


def test_dry_run_checks_again_after_the_rule_settings_changed(db_path):
    inst = make_instance()
    SearchMissingSkill().execute(the_thing_agent(inst))
    sql("UPDATE instances SET checked_search_settings=?", (json.dumps({"release_timeout_seconds": 300}),))
    same = the_thing_agent(inst)
    SearchMissingSkill().execute(same)
    assert [p for p, _ in same.gets if p == RELEASE] == []          # not a rule: nothing to check again
    sql("UPDATE instances SET checked_search_settings=?", (json.dumps({"year_tolerance": 2}),))
    changed = the_thing_agent(inst)
    SearchMissingSkill().execute(changed)
    assert len([p for p, _ in changed.gets if p == RELEASE]) == 1
    newest, oldest = log_rows()
    assert newest["settings_fingerprint"] != oldest["settings_fingerprint"]


def test_dry_run_checks_a_title_again_after_an_error(db_path):
    inst = make_instance()
    SearchMissingSkill().execute(the_thing_agent(inst, get_errors={RELEASE: requests.exceptions.ReadTimeout("slow")}))
    assert log_rows()[0]["outcome"] == "error"
    second = the_thing_agent(inst)
    SearchMissingSkill().execute(second)
    assert len([p for p, _ in second.gets if p == RELEASE]) == 1
    assert [r["outcome"] for r in log_rows()] == ["would_grab", "error"]


def test_dry_run_ignores_the_search_cache(db_path):
    inst = make_instance()
    db.searched.add(inst["id"], "mov:1", "The Thing", "movie")
    agent = the_thing_agent(inst)
    SearchMissingSkill().execute(agent)
    assert len(log_rows()) == 1


def test_dry_run_checks_at_most_the_configured_number_of_releases(db_path):
    inst = make_instance(checked_search_settings={"dry_run_max_releases": 1})
    agent = the_thing_agent(inst)
    SearchMissingSkill().execute(agent)
    [row] = log_rows()
    assert row["outcome"] == "no_clean_hit"
    assert [c["verdict"] for c in row["candidates"]] == ["reject", "unchecked"]
    assert len([p for p, _ in agent.gets if p == PARSE]) == 1


def test_release_search_uses_its_own_timeout(db_path):
    inst = make_instance(checked_search_settings={"release_timeout_seconds": 300})
    agent = the_thing_agent(inst)
    SearchMissingSkill().execute(agent)
    assert agent.timeouts[RELEASE] == 300
    assert agent.timeouts[PARSE] == 10


# ── Active ───────────────────────────────────────────────────────────────────

def test_active_grabs_the_first_clean_release(db_path):
    inst = make_instance(checked_search="active")
    agent = the_thing_agent(inst)
    SearchMissingSkill().execute(agent)

    assert agent.posts == [movie_grab("guid-right")]
    assert agent.grabbed == [1]
    assert sql("SELECT command_status, cache_key FROM search_history_items") == [("grabbed", "mov:1")]
    assert sql("SELECT cache_key, profile_fingerprint FROM searched_items") == [("mov:1", stored_fingerprint(inst))]
    [row] = log_rows()
    assert (row["mode"], row["outcome"], row["run_id"]) == ("active", "grabbed", last_run()["id"])
    run = last_run()
    assert (run["status"], run["triggered_count"], run["verified_count"]) == ("success", 1, 1)
    # the card shows the confirmed title at once, not after the next verification
    assert (agent.state["last_triggered"], agent.state["last_verified"]) == (1, 1)


def test_active_stops_checking_after_the_pick(db_path):
    inst = make_instance(checked_search="active")
    third = "The.Thing.1982.720p.WEB.x264-OTHER"
    agent = the_thing_agent(inst, releases={1: [release(RIGHT, "g1"), release(third, "g2")]})
    SearchMissingSkill().execute(agent)
    assert [params["title"] for p, params in agent.gets if p == PARSE] == [RIGHT]
    assert [c["verdict"] for c in log_rows()[0]["candidates"]] == ["pass", "unchecked"]


def test_an_overlapping_search_cannot_redirect_the_grab(db_path):
    # *arr keeps a release under indexer + guid for whichever search returned
    # it last. A search for another movie between check and grab maps the same
    # release to that movie; the grab still goes to the checked one.
    inst = make_instance(checked_search="active")
    other = movie(2, "Example Film", 2011)
    agent = the_thing_agent(inst, movies=[THE_THING, other],
                            releases={1: [release(RIGHT, "shared", movie_id=1)],
                                      2: [release(RIGHT, "shared", movie_id=2)]},
                            on_get={PARSE: lambda fake: fake.http_get(RELEASE, {"movieId": 2})})
    SearchMissingSkill().execute(agent)
    assert agent.release_cache[(7, "shared")] == 2
    assert agent.grabbed == [1]
    assert agent.posts == [movie_grab("shared")]


def test_an_overlapping_search_cannot_redirect_an_episode_grab(db_path):
    inst = make_instance(name="Sonarr", type="sonarr", checked_search="active")
    other_series = {**GUEST_SERIES, "id": 11, "year": 2023}
    other_episode = {**guest_episode(30), "seriesId": 11}
    right = "The.Guest.S01E03.German.1080p.WEB.x264-GRP"
    agent = agent_for(
        inst, missing=[guest_episode()], episodes=[guest_episode(), other_episode],
        series=[GUEST_SERIES, other_series],
        releases={3: [release(right, "shared", series_id=10, episode_ids=(3,))],
                  30: [release(right, "shared", series_id=11, episode_ids=(30,))]},
        parses={right: {"parsedEpisodeInfo": {"seriesTitle": "The Guest"}, "series": {"id": 10}}},
        on_get={PARSE: lambda fake: fake.http_get(RELEASE, {"episodeId": 30})},
    )
    SearchMissingSkill().execute(agent)
    assert agent.release_cache[(7, "shared")] == (11, (30,))
    assert agent.grabbed == [(10, (3,))]
    assert agent.posts == [episode_grab("shared")]


@pytest.mark.parametrize("mapping", [{"mappedMovieId": 2}, {"mappedMovieId": None}, {"languages": None},
                                     {"quality": None}],
                         ids=["other movie", "no movie", "no languages", "no quality"])
def test_a_release_not_mapped_to_this_movie_is_never_grabbed(db_path, mapping):
    inst = make_instance(checked_search="active")
    agent = the_thing_agent(inst, releases={1: [{**release(RIGHT, "g1"), **mapping}]})
    SearchMissingSkill().execute(agent)
    assert agent.posts == []
    [row] = log_rows()
    assert (row["outcome"], row["candidates"][0]["reasons"]) == ("no_clean_hit", ["not mapped to this title"])
    assert [p for p, _ in agent.gets if p == PARSE] == []


def test_a_release_mapped_to_other_episodes_is_never_grabbed(db_path):
    inst = make_instance(name="Sonarr", type="sonarr", checked_search="active")
    right = "The.Guest.S01E03.German.1080p.WEB.x264-GRP"
    agent = agent_for(inst, missing=[guest_episode()], episodes=[guest_episode()], series=[GUEST_SERIES],
                      releases={3: [release(right, "g1", series_id=10, episode_ids=(4,))]},
                      parses={right: {"parsedEpisodeInfo": {"seriesTitle": "The Guest"}, "series": {"id": 10}}})
    SearchMissingSkill().execute(agent)
    assert agent.posts == []
    assert log_rows()[0]["candidates"][0]["reasons"] == ["not mapped to this title"]


def test_active_without_a_clean_release_marks_the_title_searched(db_path):
    inst = make_instance(checked_search="active")
    agent = the_thing_agent(inst, releases={1: [release(WRONG, "g1")]})
    SearchMissingSkill().execute(agent)
    assert agent.posts == []
    assert sql("SELECT command_status FROM search_history_items") == [("no_hit",)]
    assert sql("SELECT cache_key FROM searched_items") == [("mov:1",)]
    assert log_rows()[0]["outcome"] == "no_clean_hit"


def test_active_without_results_marks_the_title_searched(db_path):
    inst = make_instance(checked_search="active")
    agent = the_thing_agent(inst, releases={1: [release(RIGHT, "g1", approved=False)]})
    SearchMissingSkill().execute(agent)
    assert sql("SELECT command_status FROM search_history_items") == [("no_hit",)]
    assert log_rows()[0]["outcome"] == "no_results"
    assert last_run()["status"] == "success"


def test_error_before_the_search_is_a_failed_item(db_path):
    inst = make_instance(checked_search="active")
    agent = the_thing_agent(inst, get_errors={"/api/v3/movie/": requests.exceptions.ConnectionError("down")})
    SearchMissingSkill().execute(agent)
    assert sql("SELECT command_status, cache_key FROM search_history_items") == [("failed", "")]
    assert sql("SELECT COUNT(*) FROM searched_items")[0][0] == 0
    assert log_rows()[0]["outcome"] == "error"
    run = last_run()
    assert run["status"] == "error"
    assert run["error_message"].startswith("All 1 submission(s) failed")
    assert agent.get_rate_used() == 0


def test_a_load_failure_next_to_a_grab_makes_the_run_partial(db_path):
    inst = make_instance(checked_search="active")
    other = movie(2, "Example Film", 2005)
    clean = "Example.Film.2005.German.1080p.WEB.x264-GRP"
    agent = agent_for(inst, missing=[THE_THING, other], movies=[THE_THING, other],
                      releases={2: [release(clean, "g-example", movie_id=2)]},
                      parses={clean: radarr_parse("Example Film", 2005, 2)},
                      get_errors={"/api/v3/movie/1$": requests.exceptions.ConnectionError("down")})
    SearchMissingSkill().execute(agent)
    run = last_run()
    assert run["status"] == "partial"
    assert (run["triggered_count"], run["verified_count"]) == (1, 1)
    assert run["error_message"].startswith("1 of 2 submission(s) failed")
    assert sorted(r[0] for r in sql("SELECT command_status FROM search_history_items")) == ["failed", "grabbed"]
    assert agent.state["last_verified"] == 1


def test_failed_release_search_is_a_failed_item(db_path):
    inst = make_instance(checked_search="active")
    agent = the_thing_agent(inst, get_errors={RELEASE: requests.exceptions.ReadTimeout("slow")})
    SearchMissingSkill().execute(agent)
    assert sql("SELECT command_status FROM search_history_items") == [("failed",)]
    assert sql("SELECT COUNT(*) FROM searched_items")[0][0] == 0
    assert "release search failed" in log_rows()[0]["error_message"]


@pytest.mark.parametrize("error,used", [
    (requests.exceptions.ReadTimeout("slow"), 1),
    (refused(), 0),
    (requests.exceptions.ConnectTimeout("connect timeout"), 0),
    (requests.exceptions.ConnectionError(ProtocolError("Connection aborted.", ConnectionResetError())), 1),
    (requests.exceptions.ConnectionError(ReadTimeoutError(None, RELEASE, "Read timed out.")), 1),
    (requests.exceptions.ConnectionError("no inner error"), 1),
    (http_error(400), 0),
    (http_error(503), 1),
], ids=["read timeout", "refused", "connect timeout", "reset after send", "stalled body", "bare",
        "4xx", "5xx"])
def test_a_failed_release_search_keeps_the_rate_slot_unless_it_never_ran(db_path, error, used):
    # *arr runs the indexer search without a cancellation signal: once the
    # request reached it, the search counts, even if the answer got lost.
    inst = make_instance(checked_search="active")
    agent = the_thing_agent(inst, get_errors={RELEASE: error})
    SearchMissingSkill().execute(agent)
    assert agent.get_rate_used() == used


def test_parse_error_rejects_only_that_candidate(db_path):
    # A clean release after one /parse could not check is still grabbed: it
    # passed every rule. Only the rank may suffer, not the title.
    inst = make_instance(checked_search="active")
    parses = {**PARSES, WRONG: requests.exceptions.ConnectionError("parse down")}
    agent = the_thing_agent(inst, parses=parses)
    SearchMissingSkill().execute(agent)
    [row] = log_rows()
    assert [(c["verdict"], c["reasons"]) for c in row["candidates"]] == [("error", ["parse error"]), ("pass", [])]
    assert row["outcome"] == "grabbed"
    assert row["rejected_count"] == 0


PARSE_TIMEOUT = requests.exceptions.ReadTimeout("parse slow")


@pytest.mark.parametrize("error", [PARSE_TIMEOUT, http_error(503)], ids=["timeout", "5xx"])
def test_parse_failures_on_every_release_leave_the_title_free(db_path, error):
    inst = make_instance(checked_search="active")
    agent = the_thing_agent(inst, parses={WRONG: error, RIGHT: error})
    SearchMissingSkill().execute(agent)

    assert agent.posts == []
    assert sql("SELECT command_status, cache_key FROM search_history_items") == [("failed", "")]
    assert sql("SELECT COUNT(*) FROM searched_items")[0][0] == 0
    [row] = log_rows()
    assert row["outcome"] == "error"
    assert [(c["verdict"], c["reasons"]) for c in row["candidates"]] == [("error", ["parse error"])] * 2
    assert row["rejected_count"] == 0
    assert "/parse failed for 2 release(s)" in row["error_message"] and str(error) in row["error_message"]
    run = last_run()
    assert run["status"] == "error"
    assert run["error_message"].startswith("All 1 submission(s) failed")
    assert agent.get_rate_used() == 1           # the indexer search did run
    assert any(m.startswith("Checked search for ") and "/parse failed" in m for m in activity_messages())


def test_a_rule_rejection_next_to_a_parse_failure_is_no_clean_miss(db_path):
    # The release /parse could not check may have been the right one.
    inst = make_instance(checked_search="active")
    agent = the_thing_agent(inst, parses={**PARSES, RIGHT: PARSE_TIMEOUT})
    SearchMissingSkill().execute(agent)
    assert agent.posts == []
    assert sql("SELECT command_status, cache_key FROM search_history_items") == [("failed", "")]
    assert sql("SELECT COUNT(*) FROM searched_items")[0][0] == 0
    [row] = log_rows()
    assert row["outcome"] == "error"
    assert [(c["verdict"], c["reasons"]) for c in row["candidates"]] == [
        ("reject", ["year"]), ("error", ["parse error"])]
    assert row["rejected_count"] == 1
    assert "/parse failed for 1 release(s)" in row["error_message"]
    assert last_run()["status"] == "error"


@pytest.mark.parametrize("mode,recovered", [("active", "grabbed"), ("dry_run", "would_grab")])
def test_a_title_is_searched_again_once_parse_answers(db_path, mode, recovered):
    inst = make_instance(checked_search=mode)
    SearchMissingSkill().execute(the_thing_agent(inst, parses={WRONG: PARSE_TIMEOUT, RIGHT: PARSE_TIMEOUT}))
    assert [r["outcome"] for r in log_rows()] == ["error"]
    again = the_thing_agent(inst)
    SearchMissingSkill().execute(again)
    assert len([p for p, _ in again.gets if p == RELEASE]) == 1
    assert [r["outcome"] for r in log_rows()] == [recovered, "error"]


# ── Indexer failures ─────────────────────────────────────────────────────────
# *arr catches a failing indexer inside the release search and answers with
# what the others found (often nothing); an indexer blocked after failures is
# not asked at all. Only its health checks show it.

def indexer_down(*names, source="IndexerStatusCheck"):
    message = ("Indexers unavailable due to failures: " + ", ".join(names) if names
               else "All indexers are unavailable due to failures")
    return [{"source": source, "type": "warning", "message": message,
             "wikiUrl": "https://wiki.servarr.com/radarr/system#indexers-are-unavailable-due-to-failures"}]


RSS_ONLY = {"id": 8, "name": "RSS only", "enableAutomaticSearch": False, "enableInteractiveSearch": False}


def test_an_indexer_failure_during_an_empty_search_leaves_the_title_free(db_path):
    inst = make_instance(checked_search="active")
    agent = the_thing_agent(inst, releases={}, health=indexer_down("Indexer"))
    SearchMissingSkill().execute(agent)

    assert sql("SELECT command_status, cache_key FROM search_history_items") == [("failed", "")]
    assert sql("SELECT COUNT(*) FROM searched_items")[0][0] == 0
    [row] = log_rows()
    assert row["outcome"] == "error"
    assert row["error_message"].startswith("indexer failure during search")
    assert "Indexers unavailable due to failures: Indexer" in row["error_message"]
    run = last_run()
    assert run["status"] == "error"
    assert run["error_message"].startswith("All 1 submission(s) failed")
    assert agent.get_rate_used() == 1           # the indexer search did run
    assert any(m.startswith("Checked search for ") and "indexer failure during search" in m
               for m in activity_messages())


def test_an_indexer_failure_next_to_rejected_releases_is_no_clean_miss(db_path):
    # The failing indexer may have had the right release.
    inst = make_instance(checked_search="active")
    agent = the_thing_agent(inst, releases={1: [release(WRONG, "g1")]},
                            health=indexer_down("Indexer", source="IndexerLongTermStatusCheck"))
    SearchMissingSkill().execute(agent)
    assert sql("SELECT command_status, cache_key FROM search_history_items") == [("failed", "")]
    assert sql("SELECT COUNT(*) FROM searched_items")[0][0] == 0
    [row] = log_rows()
    assert row["outcome"] == "error"
    assert "indexer failure during search" in row["error_message"]
    assert [(c["verdict"], c["reasons"]) for c in row["candidates"]] == [("reject", ["year"])]


@pytest.mark.parametrize("mode,recovered", [("active", "grabbed"), ("dry_run", "would_grab")])
def test_a_title_is_searched_again_once_the_indexers_answer(db_path, mode, recovered):
    inst = make_instance(checked_search=mode)
    SearchMissingSkill().execute(the_thing_agent(inst, releases={}, health=indexer_down()))
    assert [r["outcome"] for r in log_rows()] == ["error"]
    again = the_thing_agent(inst)
    SearchMissingSkill().execute(again)
    assert len([p for p, _ in again.gets if p == RELEASE]) == 1
    assert [r["outcome"] for r in log_rows()] == [recovered, "error"]


@pytest.mark.parametrize("releases,outcome", [({}, "no_results"), ({1: [release(WRONG, "g1")]}, "no_clean_hit")],
                         ids=["empty", "rejected"])
def test_a_miss_without_an_indexer_failure_is_remembered(db_path, releases, outcome):
    inst = make_instance(checked_search="active")
    agent = the_thing_agent(inst, releases=releases)
    SearchMissingSkill().execute(agent)
    assert [p for p, _ in agent.gets if p == HEALTH] == [HEALTH]
    assert sql("SELECT command_status, cache_key FROM search_history_items") == [("no_hit", "mov:1")]
    assert sql("SELECT cache_key FROM searched_items") == [("mov:1",)]
    assert log_rows()[0]["outcome"] == outcome


def test_a_clean_hit_needs_no_indexer_status(db_path):
    inst = make_instance(checked_search="active")
    agent = the_thing_agent(inst, health=indexer_down("Indexer"))
    SearchMissingSkill().execute(agent)
    assert agent.grabbed == [1]
    assert [p for p, _ in agent.gets if p == HEALTH] == []


@pytest.mark.parametrize("kwargs", [
    {"get_errors": {HEALTH: requests.exceptions.ReadTimeout("health slow")}},
    {"health": {"message": "no list"}},
    {"health": indexer_down("RSS only"), "indexers": [INDEXERS[0], RSS_ONLY],
     "get_errors": {"/api/v3/indexer$": requests.exceptions.ConnectionError("down")}},
], ids=["health unreadable", "health no list", "indexer list unreadable"])
def test_an_unreadable_indexer_status_leaves_the_title_free(db_path, kwargs):
    inst = make_instance(checked_search="active")
    kwargs = dict(kwargs)
    errors = kwargs.pop("get_errors", {})
    agent = the_thing_agent(inst, releases={}, **kwargs)
    # The skill reads the indexer list before collecting; only the second read fails.
    reads = []
    if errors.get("/api/v3/indexer$"):
        agent.on_get["/api/v3/indexer"] = lambda fake: reads.append(1) or (
            fake.get_errors.update(errors) if len(reads) > 1 else None)
    else:
        agent.get_errors.update(errors)
    SearchMissingSkill().execute(agent)
    assert sql("SELECT command_status, cache_key FROM search_history_items") == [("failed", "")]
    assert sql("SELECT COUNT(*) FROM searched_items")[0][0] == 0
    [row] = log_rows()
    assert row["outcome"] == "error"
    assert "could not read the indexer status" in row["error_message"]


@pytest.mark.parametrize("names,outcome", [
    (("RSS only",), "no_results"),
    (("RSS only", "Indexer"), "error"),
    (("Gone",), "error"),
    ((), "error"),
], ids=["unused indexer", "used one among them", "unknown name", "all indexers"])
def test_only_a_failure_of_an_indexer_the_search_asks_counts(db_path, names, outcome):
    inst = make_instance(checked_search="active")
    agent = the_thing_agent(inst, releases={}, indexers=[INDEXERS[0], RSS_ONLY], health=indexer_down(*names))
    SearchMissingSkill().execute(agent)
    assert log_rows()[0]["outcome"] == outcome
    assert sql("SELECT COUNT(*) FROM searched_items")[0][0] == (1 if outcome == "no_results" else 0)


TAGGED = {"id": 9, "name": "Tagged", "enableAutomaticSearch": True, "enableInteractiveSearch": True, "tags": [5]}


@pytest.mark.parametrize("tags,outcome", [
    ([], "no_results"),
    ([6], "no_results"),
    ([5, 6], "error"),
    (None, "error"),
], ids=["untagged movie", "other tag", "shared tag", "tags unknown"])
def test_a_tagged_indexer_counts_only_for_a_movie_sharing_a_tag(db_path, tags, outcome):
    # *arr asks a tagged indexer only for a movie or series sharing one of
    # its tags (ReleaseSearchService.Dispatch). A blocked one elsewhere must
    # not keep every other miss from being remembered.
    inst = make_instance(checked_search="active")
    thing = {**THE_THING, "tags": tags} if tags is not None else THE_THING
    agent = the_thing_agent(inst, missing=[thing], movies=[thing], releases={},
                            indexers=[INDEXERS[0], TAGGED], health=indexer_down("Tagged"))
    SearchMissingSkill().execute(agent)
    assert log_rows()[0]["outcome"] == outcome
    assert sql("SELECT COUNT(*) FROM searched_items")[0][0] == (1 if outcome == "no_results" else 0)


@pytest.mark.parametrize("tags,outcome", [([6], "no_results"), ([5], "error")], ids=["other tag", "shared tag"])
def test_sonarr_matches_indexer_tags_with_the_series(db_path, tags, outcome):
    inst = make_instance(name="Sonarr", type="sonarr", checked_search="active")
    series = {**GUEST_SERIES, "tags": tags}
    agent = agent_for(inst, missing=[guest_episode()], episodes=[guest_episode()], series=[series], releases={},
                      indexers=[INDEXERS[0], TAGGED], health=indexer_down("Tagged"))
    SearchMissingSkill().execute(agent)
    assert log_rows()[0]["outcome"] == outcome


def test_other_health_warnings_do_not_count(db_path):
    inst = make_instance(checked_search="active")
    health = [{"source": "IndexerRssCheck", "type": "warning", "message": "No indexers available with RSS sync"}]
    agent = the_thing_agent(inst, releases={}, health=health)
    SearchMissingSkill().execute(agent)
    assert log_rows()[0]["outcome"] == "no_results"


def test_the_indexer_status_is_read_after_arr_refreshed_its_health(db_path, monkeypatch):
    # *arr re-evaluates its health checks up to 5 s after an indexer failure
    # (debounced): a failure late in the search shows only after that.
    from backend.checked_search import runner
    monkeypatch.setattr(runner, "HEALTH_SETTLE_SECONDS", 6)
    inst = make_instance(checked_search="active")
    events = []
    agent = the_thing_agent(inst, releases={}, on_get={HEALTH: lambda fake: events.append("health")})
    agent.wait_or_stop = lambda seconds: events.append(("wait", seconds)) or False
    SearchMissingSkill().execute(agent)
    assert events == [("wait", 6), "health"]
    assert log_rows()[0]["outcome"] == "no_results"


def test_an_abort_while_arr_refreshes_its_health_stops_the_run(db_path):
    inst = make_instance(checked_search="active")
    agent = the_thing_agent(inst, releases={})
    agent.wait_or_stop = lambda seconds: True
    SearchMissingSkill().execute(agent)
    assert log_rows() == []
    assert [p for p, _ in agent.gets if p == HEALTH] == []
    assert sql("SELECT COUNT(*) FROM searched_items")[0][0] == 0


def test_a_grab_without_a_clear_answer_blocks_the_title(db_path):
    # The POST may have reached *arr: no second release, and the title stays
    # blocked like after a grab (the queue of *arr does not reliably stop a
    # second grab of another release).
    inst = make_instance(checked_search="active")
    other = "The.Thing.1982.720p.WEB.x264-OTHER"
    agent = the_thing_agent(inst, releases={1: [release(RIGHT, "g1"), release(other, "g2")]},
                            parses={**PARSES, other: radarr_parse("The Thing", 1982, 1)},
                            post_error=requests.exceptions.ReadTimeout("no answer"))
    SearchMissingSkill().execute(agent)
    assert len(agent.posts) == 1
    assert sql("SELECT command_status, cache_key FROM search_history_items") == [("failed", "mov:1")]
    assert sql("SELECT cache_key, grabbed_at IS NOT NULL FROM searched_items") == [("mov:1", 1)]
    [row] = log_rows()
    assert row["outcome"] == "grab_uncertain" and "check the *arr queue" in row["error_message"]
    assert last_run()["status"] == "error"
    again = the_thing_agent(inst)
    SearchMissingSkill().execute(again)
    assert [p for p, _ in again.gets if p == RELEASE] == []


@pytest.mark.parametrize("error", [http_error(404), http_error(409), refused()],
                         ids=["release expired", "indexer refused", "not sent"])
def test_a_refused_grab_leaves_the_title_free(db_path, error):
    inst = make_instance(checked_search="active")
    agent = the_thing_agent(inst, post_error=error)
    SearchMissingSkill().execute(agent)
    assert sql("SELECT command_status, cache_key FROM search_history_items") == [("failed", "")]
    assert sql("SELECT COUNT(*) FROM searched_items")[0][0] == 0
    assert log_rows()[0]["outcome"] == "grab_failed"
    again = the_thing_agent(inst)
    SearchMissingSkill().execute(again)
    assert len([p for p, _ in again.gets if p == RELEASE]) == 1


def test_an_unsaved_grab_is_stored_by_the_next_run(db_path, monkeypatch):
    inst = make_instance(checked_search="active")
    agent = the_thing_agent(inst)
    real = db.history.record_checked

    def broken(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(db.history, "record_checked", broken)
    SearchMissingSkill().execute(agent)
    first = last_run()
    assert first["status"] == "error" and len(agent.posts) == 1
    monkeypatch.setattr(db.history, "record_checked", real)
    SearchMissingSkill().execute(agent)          # same instance runtime, the next run
    assert len(agent.posts) == 1                 # no second grab
    assert sql("SELECT run_id, command_status FROM search_history_items") == [(first["id"], "grabbed")]
    assert sql("SELECT cache_key FROM searched_items") == [("mov:1",)]
    assert [(r["run_id"], r["outcome"]) for r in log_rows()] == [(first["id"], "grabbed")]
    assert agent.runtime.unsaved_submissions == []


def test_an_unsaved_grab_blocks_its_title_while_the_database_refuses(db_path, monkeypatch):
    inst = make_instance(checked_search="active")
    agent = the_thing_agent(inst)

    def broken(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(db.history, "record_checked", broken)
    SearchMissingSkill().execute(agent)
    SearchMissingSkill().execute(agent)
    assert len([p for p, _ in agent.gets if p == RELEASE]) == 1
    assert len(agent.posts) == 1
    assert len(agent.runtime.unsaved_submissions) == 1


def test_a_grabbed_title_still_missing_is_searched_again_after_the_set_days(db_path):
    # Replaces *arr's "Redownload Failed from Interactive Search", which is
    # switched off for the checked search.
    inst = make_instance(checked_search="active", checked_search_settings={"search_again_after_days": 3})
    SearchMissingSkill().execute(the_thing_agent(inst))
    same = the_thing_agent(inst)
    SearchMissingSkill().execute(same)
    assert [p for p, _ in same.gets if p == RELEASE] == []
    sql("UPDATE searched_items SET grabbed_at=datetime('now','localtime','-4 days')")
    later = the_thing_agent(inst)
    SearchMissingSkill().execute(later)
    assert len([p for p, _ in later.gets if p == RELEASE]) == 1
    assert later.posts == [movie_grab("guid-right")]


@pytest.mark.parametrize("indexers,errors,hint", [
    ([*INDEXERS, {"id": 9, "name": "Weekly check", "enableAutomaticSearch": True,
                  "enableInteractiveSearch": False}], {}, "Checked search paused — indexer Weekly check"),
    ([*INDEXERS, {"id": 9, "name": "Manual only", "enableAutomaticSearch": False,
                  "enableInteractiveSearch": True}], {}, "Checked search paused — indexer Manual only"),
    (None, {"/api/v3/indexer": requests.exceptions.ConnectionError("down")},
     "Checked search paused — could not read the indexer list: down"),
], ids=["interactive off", "automatic off", "list unreadable"])
def test_differing_indexer_switches_pause_the_checked_search(db_path, indexers, errors, hint):
    # A pause is no fault (decision Daniel 01.10.2026): the run is a success
    # with the reason, a warning goes to the activity log, last_sync stays.
    inst = make_instance(checked_search="active")
    agent = the_thing_agent(inst, indexers=indexers, get_errors=errors)
    SearchMissingSkill().execute(agent)
    assert [p for p, _ in agent.gets if p in (RELEASE, PARSE)] == []
    assert agent.posts == [] and log_rows() == []
    assert sql("SELECT COUNT(*) FROM search_history_items")[0][0] == 0
    assert sql("SELECT COUNT(*) FROM searched_items")[0][0] == 0
    assert agent.get_rate_used() == 0
    run = last_run()
    assert run["status"] == "success" and run["error_message"].startswith(hint)
    assert any(r["level"] == "warn" and r["message"].startswith(hint)
               for r in db.activity.query(limit=200, include_debug=True))
    assert agent.state["last_sync"] is None
    alike = the_thing_agent(inst)                                    # switches alike again
    SearchMissingSkill().execute(alike)
    assert alike.state["last_sync"] is not None


WEEKLY_CHECK = [*INDEXERS, {"id": 9, "name": "Weekly check", "enableAutomaticSearch": True,
                            "enableInteractiveSearch": False}]


@pytest.mark.parametrize("mode,first_run,wanted", [
    ("dry_run", True, True),       # the round is done: nothing left to check
    ("active", True, True),        # every title blocked by the cache
    ("active", False, False),      # empty wanted list
], ids=["round done", "all cached", "nothing wanted"])
def test_a_checked_run_without_titles_still_pauses(db_path, mode, first_run, wanted):
    # Codex round 3, G4: every checked run reads the indexer list, also one
    # that finds nothing to search — a lasting pause must show (spec).
    inst = make_instance(checked_search=mode)
    agent = the_thing_agent(inst) if wanted else agent_for(inst)
    if first_run:
        SearchMissingSkill().execute(agent)
    agent.indexers = copy.deepcopy(WEEKLY_CHECK)
    agent.state["last_sync"] = "2000-01-01 00:00"
    searches = len([p for p, _ in agent.gets if p == RELEASE])
    SearchMissingSkill().execute(agent)
    assert len([p for p, _ in agent.gets if p == RELEASE]) == searches
    run = last_run()
    assert run["status"] == "success"
    assert run["error_message"].startswith("Checked search paused — indexer Weekly check")
    assert any(r["level"] == "warn" and r["message"].startswith("Checked search paused")
               for r in db.activity.query(limit=200, include_debug=True))
    assert agent.state["last_sync"] == "2000-01-01 00:00"


# ── Limits ───────────────────────────────────────────────────────────────────

def three_movies():
    return [movie(i, f"Film {i}", 2000 + i) for i in (1, 2, 3)]


def test_time_budget_starts_no_new_title(db_path, monkeypatch):
    from backend.checked_search import runner
    inst = make_instance(checked_search_settings={"time_budget_minutes": 1})
    ticks = itertools.chain([0, 61], itertools.repeat(61))
    real = runner.run_checked
    monkeypatch.setattr("backend.skills.search_missing.run_checked",
                        lambda *a, **k: real(*a, **{**k, "clock": lambda: next(ticks), "started": 0}))
    films = three_movies()
    agent = agent_for(inst, missing=films, movies=films)
    SearchMissingSkill().execute(agent)
    assert len([p for p, _ in agent.gets if p == RELEASE]) == 1
    run = last_run()
    assert run["status"] == "success"
    assert "Time budget of 1 min used up — 2 title(s) left for the next run" in run["error_message"]


def test_time_budget_counts_from_the_start_of_the_run(db_path):
    # Reading the profiles and collecting the candidates used the budget up.
    from backend.checked_search.runner import CheckedTask, run_checked
    inst = make_instance(checked_search_settings={"time_budget_minutes": 1})
    films = three_movies()
    agent = agent_for(inst, missing=films, movies=films)
    run_id = history.start_run(inst["id"], "Radarr", "search_missing")
    tasks = [CheckedTask(f["id"], f["title"], "movie", f"mov:{f['id']}") for f in films]
    outcome = run_checked("search_missing", agent, run_id, tasks, "dry_run", clock=lambda: 61, started=0)
    assert [p for p, _ in agent.gets if p == RELEASE] == []
    assert outcome.notes == ["Time budget of 1 min used up — 3 title(s) left for the next run"]


def test_abort_between_parse_calls_stops_the_run(db_path):
    inst = make_instance(checked_search="active")
    agent = the_thing_agent(inst, abort_after_parses=1)
    SearchMissingSkill().execute(agent)
    assert agent.posts == []
    assert log_rows() == []
    assert last_run()["status"] == "error"


def test_abort_during_release_search_grabs_nothing(db_path):
    inst = make_instance(checked_search="active")
    agent = the_thing_agent(inst, abort_on_release=True)
    SearchMissingSkill().execute(agent)
    assert agent.posts == []
    assert [p for p, _ in agent.gets if p == PARSE] == []
    assert log_rows() == []
    assert last_run()["status"] == "error"


def test_abort_after_the_last_parse_grabs_nothing(db_path):
    inst = make_instance(checked_search="active")
    agent = the_thing_agent(inst, releases={1: [release(RIGHT, "g1")]}, abort_after_parses=1)
    SearchMissingSkill().execute(agent)
    assert agent.posts == []
    assert log_rows() == []


def test_rate_cap_counts_one_action_per_title(db_path):
    inst = make_instance(rate_cap=1)
    films = three_movies()
    agent = agent_for(inst, missing=films, movies=films)
    SearchMissingSkill().execute(agent)
    assert len([p for p, _ in agent.gets if p == RELEASE]) == 1
    assert agent.get_rate_used() == 1


def test_a_store_failure_stops_the_run(db_path, monkeypatch):
    inst = make_instance(checked_search="active")
    films = three_movies()
    agent = agent_for(inst, missing=films, movies=films)

    def broken(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(db.history, "record_checked", broken)
    SearchMissingSkill().execute(agent)
    assert len([p for p, _ in agent.gets if p == RELEASE]) == 1
    run = last_run()
    assert run["status"] == "error"
    assert "database is locked" in run["error_message"]


# ── Sonarr ───────────────────────────────────────────────────────────────────

GUEST_SERIES = {"id": 10, "title": "The Guest", "year": 2018, "alternateTitles": [], "qualityProfileId": 1}


def guest_episode(ep_id=3, season=1):
    return {"id": ep_id, "seriesId": 10, "seasonNumber": season, "episodeNumber": ep_id, "title": f"E{ep_id}",
            "airDateUtc": "2018-10-11T12:00:00Z", "hasFile": False, "monitored": True,
            "series": {"title": "The Guest"}}


def test_sonarr_checks_single_episodes_even_with_another_stored_mode(db_path):
    inst = make_instance(name="Sonarr", type="sonarr", checked_search="active")
    sql("UPDATE instances SET missing_mode='show_batch'")
    foreign = "The.Guest.CO.2025.S01E03.MULTI.1080p.WEB.X264-GRP"
    right = "The.Guest.S01E03.German.1080p.WEB.x264-GRP"
    agent = agent_for(
        inst, missing=[guest_episode()], episodes=[guest_episode()], series=[GUEST_SERIES],
        # Sonarr maps the foreign release to the searched episode too (stamped tvdbId)
        releases={3: [release(foreign, "g1", published="2025-06-01T00:00:00Z", series_id=10, episode_ids=(3,)),
                      release(right, "g2", published="2018-10-11T20:00:00Z", series_id=10, episode_ids=(3,))]},
        parses={foreign: {"parsedEpisodeInfo": {"seriesTitle": "The Guest CO 2025"}},
                right: {"parsedEpisodeInfo": {"seriesTitle": "The Guest"}, "series": {"id": 10}}},
    )
    SearchMissingSkill().execute(agent)
    assert ("/api/v3/release", {"episodeId": 3}) in agent.gets
    assert agent.posts == [episode_grab("g2")]
    assert agent.grabbed == [(10, (3,))]
    [row] = log_rows()
    assert row["candidates"][0]["reasons"] == ["year suffix", "country suffix"]
    assert sql("SELECT item_type, cache_key FROM search_history_items") == [("episode", "ep:3")]


def test_sonarr_season_packs_are_not_taken(db_path):
    # Sonarr itself rejects packs in an episode search, but not for specials
    # (SearchSpecial): a pack can come back approved there.
    inst = make_instance(name="Sonarr", type="sonarr", checked_search="active")
    special = guest_episode(5, season=0)
    pack = "The.Guest.S00.German.1080p.WEB.x264-GRP"
    right = "The.Guest.S00E05.German.1080p.WEB.x264-GRP"
    agent = agent_for(
        inst, missing=[special], episodes=[special], series=[GUEST_SERIES],
        releases={5: [{**release(pack, "g1", series_id=10, episode_ids=(5,)), "fullSeason": True},
                      release(right, "g2", series_id=10, episode_ids=(5,))]},
        parses={right: {"parsedEpisodeInfo": {"seriesTitle": "The Guest"}, "series": {"id": 10}}},
    )
    SearchMissingSkill().execute(agent)
    assert agent.posts == [episode_grab("g2", episode_ids=(5,))]
    [row] = log_rows()
    assert row["arr_pick"] == pack
    assert row["candidates"][0]["reasons"] == ["season pack"]
    assert [params["title"] for p, params in agent.gets if p == PARSE] == [right]


DOUBLE = "The.Guest.S01E03E04.German.1080p.WEB.x264-GRP"


@pytest.mark.parametrize("checked", ["active", "dry_run"])
def test_a_multi_episode_release_is_never_taken(db_path, checked):
    # 0.9.0 grabs single episodes only (decision Daniel 01.10.2026): a release
    # mapped to E03 and E04 would be checked and cached for E03 alone.
    # Sonarr approves it in a search for E03 (SingleEpisodeSearchMatchSpecification).
    inst = make_instance(name="Sonarr", type="sonarr", checked_search=checked)
    right = "The.Guest.S01E03.German.1080p.WEB.x264-GRP"
    agent = agent_for(
        inst, missing=[guest_episode()], episodes=[guest_episode()], series=[GUEST_SERIES],
        releases={3: [release(DOUBLE, "g1", series_id=10, episode_ids=(3, 4)),
                      release(right, "g2", series_id=10, episode_ids=(3,))]},
        parses={right: {"parsedEpisodeInfo": {"seriesTitle": "The Guest"}, "series": {"id": 10}}},
    )
    SearchMissingSkill().execute(agent)
    [row] = log_rows()
    assert row["arr_pick"] == DOUBLE
    assert row["candidates"][0]["reasons"] == ["multi-episode release"]
    assert row["pick"] == right
    assert [params["title"] for p, params in agent.gets if p == PARSE] == [right]
    if checked == "active":
        assert agent.posts == [episode_grab("g2")]
        assert agent.grabbed == [(10, (3,))]
    else:
        assert agent.posts == [] and row["outcome"] == "would_grab"


# ── Profile changes (spec addendum) ──────────────────────────────────────────

def test_cache_frees_a_title_after_its_profile_changed(db_path):
    inst = make_instance(checked_search="off")
    first = the_thing_agent(inst)
    SearchMissingSkill().execute(first)
    assert first.commands == [{"name": "MoviesSearch", "movieIds": [1]}]
    assert sql("SELECT profile_fingerprint FROM searched_items") == [(stored_fingerprint(inst),)]

    same = the_thing_agent(inst)
    SearchMissingSkill().execute(same)
    assert same.commands == []

    changed = the_thing_agent(inst, profiles=changed_profiles())
    SearchMissingSkill().execute(changed)
    assert changed.commands == [{"name": "MoviesSearch", "movieIds": [1]}]
    assert any(m.startswith("Quality profile changed: HD (") for m in activity_messages())
    assert sql("SELECT profile_fingerprint FROM searched_items") == [(stored_fingerprint(inst),)]


def test_cache_entries_from_before_0_9_0_block_until_the_first_change(db_path):
    inst = make_instance(checked_search="off")
    db.searched.add(inst["id"], "mov:1", "The Thing", "movie")      # cached by 0.8.0, no fingerprint
    first = the_thing_agent(inst)
    SearchMissingSkill().execute(first)
    assert first.commands == []                                       # the update alone releases nothing
    assert db.instances.get_by_id(inst["id"])["profile_fingerprints_baseline"] == {"1": stored_fingerprint(inst)}

    changed = the_thing_agent(inst, profiles=changed_profiles())
    SearchMissingSkill().execute(changed)
    assert changed.commands == [{"name": "MoviesSearch", "movieIds": [1]}]


def test_with_the_setting_off_the_cache_ignores_profile_changes(db_path):
    inst = make_instance(checked_search="off", search_again_after_profile_change=False)
    SearchMissingSkill().execute(the_thing_agent(inst))
    changed = the_thing_agent(inst, profiles=changed_profiles())
    SearchMissingSkill().execute(changed)
    assert changed.commands == []


def test_dry_run_checks_again_after_a_profile_change(db_path):
    inst = make_instance()
    SearchMissingSkill().execute(the_thing_agent(inst))
    same = the_thing_agent(inst)
    SearchMissingSkill().execute(same)
    assert [p for p, _ in same.gets if p == RELEASE] == []

    changed = the_thing_agent(inst, profiles=changed_profiles())
    SearchMissingSkill().execute(changed)
    assert len([p for p, _ in changed.gets if p == RELEASE]) == 1
    assert [r["profile_changed"] for r in log_rows()] == [False, True]   # newest first


def test_log_rows_name_the_profile_they_were_checked_under(db_path):
    # The badge compares with this very profile (Codex round 2, F7).
    inst = make_instance()
    SearchMissingSkill().execute(the_thing_agent(inst))
    assert [r["profile_id"] for r in log_rows()] == [1]


def test_profiles_that_cannot_be_read_release_nothing(db_path):
    inst = make_instance(checked_search="off")
    SearchMissingSkill().execute(the_thing_agent(inst))
    broken = the_thing_agent(inst, profiles=changed_profiles(),
                             get_errors={QUALITY_PROFILES: requests.exceptions.ConnectionError("down")})
    SearchMissingSkill().execute(broken)
    assert broken.commands == []
    assert any(m.startswith("Could not read the quality profiles") for m in activity_messages())


@pytest.mark.parametrize("checked", ["off", "active"])
def test_a_failed_first_baseline_write_releases_nothing(db_path, monkeypatch, checked):
    # Codex round 3, G2: the first run of 0.9.0 reads the profiles, but the
    # database refuses to store them. Entries from before 0.9.0 must keep
    # blocking (spec: the update alone releases nothing), in either mode.
    inst = make_instance(checked_search=checked)
    db.searched.add(inst["id"], "mov:1", "The Thing", "movie")      # cached by 0.8.0, no fingerprint
    real = db.instances.store_profile_fingerprints

    def locked(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(db.instances, "store_profile_fingerprints", locked)
    first = the_thing_agent(inst)
    SearchMissingSkill().execute(first)
    assert first.commands == [] and [p for p, _ in first.gets if p == RELEASE] == []
    assert any(m.startswith("Could not store the quality profile fingerprints") for m in activity_messages())
    assert db.instances.get_by_id(inst["id"])["profile_fingerprints_baseline"] is None

    monkeypatch.setattr(db.instances, "store_profile_fingerprints", real)
    again = the_thing_agent(inst)
    SearchMissingSkill().execute(again)
    assert again.commands == [] and [p for p, _ in again.gets if p == RELEASE] == []
    assert db.instances.get_by_id(inst["id"])["profile_fingerprints_baseline"] == {"1": stored_fingerprint(inst)}


def test_sonarr_takes_the_profile_from_the_series_list(db_path):
    inst = make_instance(name="Sonarr", type="sonarr", checked_search="off")
    first = agent_for(inst, missing=[guest_episode()], episodes=[guest_episode()], series=[GUEST_SERIES])
    SearchMissingSkill().execute(first)
    assert first.commands == [{"name": "EpisodeSearch", "episodeIds": [3]}]
    assert sql("SELECT profile_fingerprint FROM searched_items") == [(stored_fingerprint(inst),)]
    changed = agent_for(inst, missing=[guest_episode()], episodes=[guest_episode()], series=[GUEST_SERIES],
                        profiles=changed_profiles())
    SearchMissingSkill().execute(changed)
    assert changed.commands == [{"name": "EpisodeSearch", "episodeIds": [3]}]


BASELINE = {"1": "aaaaaaaaaaaaaaaa"}   # a profile state from before a change
SERIES_LIST_DOWN = {"/api/v3/series$": requests.exceptions.ConnectionError("down")}


@pytest.mark.parametrize("checked", ["active", "dry_run"])
def test_a_checked_episode_keeps_its_profile_when_the_series_list_fails(db_path, checked):
    # Without the list the episode's profile is unknown while candidates are
    # picked, but the checked search loads the series anyway and stores its
    # fingerprint — not NULL, which would count as searched under the baseline.
    inst = make_instance(name="Sonarr", type="sonarr", checked_search=checked)
    db.instances.store_profile_fingerprints(inst["id"], BASELINE)
    episode = guest_episode()
    broken = agent_for(inst, missing=[episode], episodes=[episode], series=[GUEST_SERIES],
                       get_errors=SERIES_LIST_DOWN)
    SearchMissingSkill().execute(broken)
    current = stored_fingerprint(inst)
    assert current != BASELINE["1"]
    assert log_rows()[0]["profile_fingerprint"] == current
    if checked == "active":
        assert sql("SELECT profile_fingerprint FROM searched_items") == [(current,)]
    again = agent_for(inst, missing=[episode], episodes=[episode], series=[GUEST_SERIES])
    SearchMissingSkill().execute(again)
    assert [p for p, _ in again.gets if p == RELEASE] == []


def test_the_command_search_asks_for_the_series_when_the_list_fails(db_path):
    inst = make_instance(name="Sonarr", type="sonarr", checked_search="off")
    db.instances.store_profile_fingerprints(inst["id"], BASELINE)
    episode = guest_episode()
    broken = agent_for(inst, missing=[episode], episodes=[episode], series=[GUEST_SERIES],
                       get_errors=SERIES_LIST_DOWN)
    SearchMissingSkill().execute(broken)
    assert broken.commands == [{"name": "EpisodeSearch", "episodeIds": [3]}]
    assert ("/api/v3/series/10", {}) in broken.gets
    assert sql("SELECT profile_fingerprint FROM searched_items") == [(stored_fingerprint(inst),)]


# ── Secrets ──────────────────────────────────────────────────────────────────

def all_text():
    chunks = []
    with database.get_db() as conn:
        for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            if table == "instances":
                continue  # holds the encrypted API key by design
            for row in conn.execute(f"SELECT * FROM {table}"):
                chunks.append(json.dumps([str(v) for v in tuple(row)]))
    return "\n".join(chunks)


@pytest.mark.parametrize("mode,failure", [("dry_run", None), ("active", None), ("active", "grab"),
                                          ("active", "search"), ("active", "load")])
def test_no_secret_reaches_log_history_or_activity(db_path, mode, failure):
    inst = make_instance(checked_search=mode)
    errors = {
        "grab": {"post_error": requests.exceptions.ReadTimeout("no answer")},
        "search": {"get_errors": {RELEASE: requests.exceptions.ReadTimeout("slow")}},
        "load": {"get_errors": {"/api/v3/movie/": requests.exceptions.ConnectionError("down")}},
    }
    agent = the_thing_agent(inst, **errors.get(failure, {}))
    SearchMissingSkill().execute(agent)
    if mode == "active" and failure in (None, "grab"):
        assert agent.posts == [movie_grab("guid-right")]
    text = all_text()
    for secret in (INDEXER_KEY, "apikey", "downloadUrl", "infoUrl", "magnetUrl", "magnet:",
                   "guid-right", "guid-wrong", API_KEY):
        assert secret not in text
```

- [ ] **Step 2: Test laufen lassen, er muss scheitern**

Run: `cd <repo> && .venv/bin/python -m pytest tests/test_g3_runner.py -q -p no:cacheprovider`
Expected: FAIL — die Fehlend-Suche schickt noch `MoviesSearch` (als Befehl an die Attrappe), kein Protokoll, kein Profilstand.

- [ ] **Step 3: Agent — eigene Wartezeit**

`backend/agents/base.py` — Edit, old:

```
    def http_get(self, path: str, params: Optional[dict] = None) -> dict:
        url = self.config["url"].rstrip("/") + path
        resp = requests.get(
            url,
            headers={"X-Api-Key": self.config["api_key"]},
            params=params or {},
            timeout=10,
            allow_redirects=False,
        )
```

new:

```
    def http_get(self, path: str, params: Optional[dict] = None, timeout: float = 10) -> dict:
        """timeout: seconds; the checked search waits longer for /release,
        which runs the indexer search before it answers."""
        url = self.config["url"].rstrip("/") + path
        resp = requests.get(
            url,
            headers={"X-Api-Key": self.config["api_key"]},
            params=params or {},
            timeout=timeout,
            allow_redirects=False,
        )
```

Edit, old:

```
    def http_post(self, path: str, body: dict) -> dict:
        url = self.config["url"].rstrip("/") + path
        resp = requests.post(
            url,
            headers={"X-Api-Key": self.config["api_key"], "Content-Type": "application/json"},
            json=body,
            timeout=10,
            allow_redirects=False,
        )
```

new:

```
    def http_post(self, path: str, body: dict, timeout: float = 10) -> dict:
        url = self.config["url"].rstrip("/") + path
        resp = requests.post(
            url,
            headers={"X-Api-Key": self.config["api_key"], "Content-Type": "application/json"},
            json=body,
            timeout=timeout,
            allow_redirects=False,
        )
```

- [ ] **Step 4: Skill-Basis — Titel ohne Fehler zählen, Aussetzen, ungespeicherte Grabs, Fingerabdruck mitschreiben, Karte**

`backend/skills/base.py` — Edit, old:

```
from dataclasses import dataclass, field
from datetime import datetime, timezone
```

new:

```
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
```

Edit, old:

```
    arr_id: int | None = None
    command_id: int | None = None
    error: str = ""
```

new:

```
    arr_id: int | None = None
    command_id: int | None = None
    error: str = ""
    # Quality profile the title was searched under (0.9.0); stored with the
    # cache entry so a later profile change can release it.
    profile_fingerprint: str | None = None
```

Edit, old:

```
    # A command *arr accepted could not be stored; the run stopped there.
    store_error: str = ""
```

new:

```
    # A command *arr accepted could not be stored; the run stopped there.
    store_error: str = ""
    # Titles that ended without an error but are not counted in `triggered`
    # (a checked-search dry run grabs nothing, so its triggered_count stays 0).
    handled: int = 0
    # The checked search did not start (an indexer's search switches differ,
    # or the indexer list could not be read): the message closes the run.
    paused: str = ""
```

Edit, old:

```
    result: SearchResult
    sent_at: datetime
```

new:

```
    result: SearchResult
    sent_at: datetime


@dataclass(frozen=True)
class UnsavedCheckedGrab:
    """A grab of the checked search (or one without a clear answer) whose
    history item, cache entry and log row could not be written. Kept in the
    same list as the unsaved commands: it blocks its title until a later run
    stores it. Like them it lives in memory only."""

    run_id: int
    instance_id: int
    title: str
    arr_id: int | None
    item_type: str
    cache_key: str
    status: str
    log_entry: dict
    profile_fingerprint: str | None
    sent_at: datetime
    hold_key: str | None = None      # Sonarr upgrade: the season it holds for the command path
```

Edit, old:

```
    db.history.record_submission(
        run_id, agent.config["id"], result.title, result.arr_id,
        result.item_type, result.cache_key, result.command_id,
    )
```

new:

```
    db.history.record_submission(
        run_id, agent.config["id"], result.title, result.arr_id,
        result.item_type, result.cache_key, result.command_id,
        profile_fingerprint=result.profile_fingerprint,
    )
```

Edit, old: `def _store_submission(skill_name: str, agent, run_id: int, result: SearchResult) -> str:`
new:

```
def _store_unsaved(agent, run_id: int, entry) -> None:
    """Store one entry of runtime.unsaved_submissions under run_id."""
    if isinstance(entry, UnsavedCheckedGrab):
        db.history.record_checked(
            run_id, entry.instance_id, entry.title, entry.arr_id, entry.item_type, entry.cache_key,
            entry.status, {**entry.log_entry, "run_id": run_id},
            profile_fingerprint=entry.profile_fingerprint, cache=True, hold_key=entry.hold_key,
        )
    else:
        _record(agent, run_id, entry.result)


def _store_submission(skill_name: str, agent, run_id: int, result: SearchResult) -> str:
```

Edit, old:

```
            entry = runtime.unsaved_submissions[0]
            try:
                try:
                    _record(agent, entry.run_id, entry.result)
                except sqlite3.IntegrityError:
                    _record(agent, run_id, entry.result)
            except Exception as exc:
                failure = f"{len(runtime.unsaved_submissions)} sent command(s) still could not be stored: {exc}"
                break
            runtime.unsaved_submissions.pop(0)
            stored.append(entry.result)
    for result in stored:
        agent.log("info", skill_name, f"Stored command {result.command_id} for {result.title}, sent earlier")
```

new:

```
            entry = runtime.unsaved_submissions[0]
            try:
                try:
                    _store_unsaved(agent, entry.run_id, entry)
                except sqlite3.IntegrityError:
                    _store_unsaved(agent, run_id, entry)
            except Exception as exc:
                failure = (f"{len(runtime.unsaved_submissions)} sent command(s) or checked grab(s) "
                           f"still could not be stored: {exc}")
                break
            runtime.unsaved_submissions.pop(0)
            stored.append(entry)
    for entry in stored:
        if isinstance(entry, UnsavedCheckedGrab):
            agent.log("info", skill_name, f"Stored the checked search for {entry.title} ({entry.status}), "
                                          f"grabbed earlier")
        else:
            agent.log("info", skill_name,
                      f"Stored command {entry.result.command_id} for {entry.result.title}, sent earlier")
```

Edit, old:

```
    with agent.runtime.unsaved_lock:
        return {
            entry.result.cache_key: entry.sent_at
            for entry in agent.runtime.unsaved_submissions
            if entry.result.command_id is not None and entry.result.cache_key
        }
```

new:

```
    with agent.runtime.unsaved_lock:
        keys: dict[str, datetime] = {}
        for entry in agent.runtime.unsaved_submissions:
            if isinstance(entry, UnsavedCheckedGrab):
                # A grab (or one without a clear answer) always blocks its
                # title, and the season it holds for the command path.
                for key in (entry.cache_key, entry.hold_key):
                    if key:
                        keys[key] = entry.sent_at
            elif entry.result.command_id is not None and entry.result.cache_key:
                keys[entry.result.cache_key] = entry.sent_at
        return keys
```

Edit, old:

```
    fire: Callable[[object], SearchResult],
    delay: float,
) -> SubmitOutcome:
```

new:

```
    fire: Callable[[object], SearchResult],
    delay: float,
    fingerprint_of: Optional[Callable[[object], Optional[str]]] = None,
) -> SubmitOutcome:
```

Edit, old:

```
        result = fire(candidate)
        if result.ok:
            outcome.triggered += 1
```

new:

```
        result = fire(candidate)
        if result.ok:
            if fingerprint_of is not None:
                result = replace(result, profile_fingerprint=fingerprint_of(candidate))
            outcome.triggered += 1
```

Edit, old:

```
    notes = list(notes)
    failed = len(outcome.errors)
    if outcome.stopped:
        notes.append("Stopped early: instance disabled or deleted")

    if outcome.store_error:
        status = "error"
        notes.insert(0, f"Stopped: {outcome.store_error}")
    elif failed and outcome.triggered == 0:
        status = "error"
        notes.insert(0, f"All {failed} submission(s) failed — first error: {outcome.errors[0]}")
    elif outcome.stopped and outcome.triggered == 0:
        status = "error"
    else:
        status = "success"
        if failed:
            notes.insert(0, f"{failed} of {failed + outcome.triggered} submission(s) failed "
                            f"— first error: {outcome.errors[0]}")
```

new:

```
    notes = list(notes)
    failed = len(outcome.errors)
    succeeded = outcome.triggered + outcome.handled
    if outcome.stopped:
        notes.append("Stopped early: instance disabled or deleted")

    if outcome.store_error:
        status = "error"
        notes.insert(0, f"Stopped: {outcome.store_error}")
    elif outcome.paused:
        # A deliberate pause, no fault (decision Daniel 01.10.2026): the run
        # is a success carrying the reason; last_sync stays (see below).
        status = "success"
        notes.insert(0, outcome.paused)
    elif failed and succeeded == 0:
        status = "error"
        notes.insert(0, f"All {failed} submission(s) failed — first error: {outcome.errors[0]}")
    elif outcome.stopped and succeeded == 0:
        status = "error"
    else:
        status = "success"
        if failed:
            notes.insert(0, f"{failed} of {failed + succeeded} submission(s) failed "
                            f"— first error: {outcome.errors[0]}")
```

Edit, old:

```
    # last_triggered and last_verified always describe the same run (B-L4):
    # a run that just ended has nothing verified yet.
    agent.state["last_wanted"] = wanted
    agent.state["last_triggered"] = outcome.triggered
    agent.state["last_verified"] = 0
```

new:

```
    # last_triggered and last_verified always describe the same run (B-L4).
    # A command run that just ended has nothing verified yet (0); a checked
    # search settles its titles at once, finish_run stored them already.
    agent.state["last_wanted"] = wanted
    agent.state["last_triggered"] = outcome.triggered
    agent.state["last_verified"] = _verified_now(agent, run_id)
```

Edit, old:

```
    if status != "error":
        agent.state["last_sync"] = datetime.now().strftime("%Y-%m-%d %H:%M")
```

new:

```
    # A paused checked search searched nothing: the card's "Last sync" keeps
    # the last run that did, so a pause that lasts shows there.
    if status != "error" and not outcome.paused:
        agent.state["last_sync"] = datetime.now().strftime("%Y-%m-%d %H:%M")
```

Edit, old:

```
def finish_search_run(
```

new:

```
def _verified_now(agent, run_id: int) -> int:
    try:
        latest = db.history.get_latest_run_verification(agent.config["id"])
    except Exception:
        return 0
    return int(latest["verified_count"] or 0) if latest and latest["id"] == run_id else 0


def finish_search_run(
```

- [ ] **Step 5: Profilstand zu Laufbeginn**

`backend/skills/profiles.py`:

```python
"""Quality-profile fingerprints for the search cache and the dry run (0.9.0).

Spec addendum "notice profile changes": at the start of every search run the
skills read the quality profiles, custom formats and release profiles (no
indexer load) and fingerprint each profile (checked_search/fingerprint.py).
A cached title blocks only while it was searched under the current
fingerprint of its profile; a dry-run row counts for the round only with it.
If the profiles cannot be read, the fingerprints stored by the last run stay
in force: nothing is released that was not released before. If they cannot
be stored on the first run of 0.9.0, what was read counts as the baseline
for that run, so a failed write releases nothing either.

Which profile a title has: Radarr movies carry qualityProfileId (wanted,
cutoff and movie lists). Sonarr episodes do not; their series has it, and
the wanted lists name the series only with includeSeries=true, which
missingarr does not send. So a Sonarr run reads the series list once. If
that fails, nothing is released while candidates are picked, but a title
that is searched is still stored with its fingerprint: the checked search
takes it from the series it loads anyway, the command search asks for the
one series (stored_fingerprint). A NULL fingerprint would count as searched
under the baseline and could release the title again at once.

The dry run also compares the rule settings: a row counts for the round only
under the fingerprint of the settings in force (settings_fingerprint).
"""

from dataclasses import dataclass, field
from typing import Callable, Optional

from backend import db
from backend.checked_search.fingerprint import changes, fingerprints, short
from backend.checked_search.settings import CheckedSearchSettings

# In the order fingerprints() takes the answers.
PROFILE_PATHS = ("/api/v3/qualityprofile", "/api/v3/customformat", "/api/v3/releaseprofile",
                 "/api/v3/qualitydefinition", "/api/v3/config/indexer")
SERIES_PATH = "/api/v3/series"
# Read once per run; a large Sonarr library takes a while to list its series.
PROFILE_TIMEOUT = 60


@dataclass
class ProfileState:
    current: dict = field(default_factory=dict)          # str(profile id) -> fingerprint
    baseline: Optional[dict] = None                      # set by the first run of 0.9.0
    series_profiles: dict = field(default_factory=dict)  # Sonarr: series id -> quality profile id
    search_again: bool = True                            # "Search again after profile changes"
    settings_fingerprint: Optional[str] = None           # rule settings of the instance (dry run)
    # Sonarr when the series list could not be read: GET /series/{id}.
    series_loader: Optional[Callable] = None
    loaded_series: dict = field(default_factory=dict)    # series id -> quality profile id (this run)

    def profile_of(self, record: dict) -> Optional[int]:
        """Quality profile of a wanted/cutoff record, a movie or an upgrade item."""
        profile_id = record.get("qualityProfileId") or (record.get("series") or {}).get("qualityProfileId")
        if profile_id:
            return profile_id
        series_id = record.get("seriesId") or record.get("series_id")
        return self.series_profiles.get(series_id) if series_id is not None else None

    def fingerprint(self, profile_id) -> Optional[str]:
        return self.current.get(str(profile_id)) if profile_id is not None else None

    def stored_fingerprint(self, record: dict) -> Optional[str]:
        """The fingerprint a search of this record is stored under. Like
        fingerprint(profile_of(record)); when the Sonarr series list could
        not be read, the record's series is asked for (once per series and
        run, only for titles that are searched)."""
        current = self.fingerprint(self.profile_of(record))
        if current is not None or self.series_loader is None:
            return current
        series_id = record.get("seriesId") or record.get("series_id")
        if series_id is None:
            return None
        if series_id not in self.loaded_series:
            try:
                series = self.series_loader(series_id)
            except Exception:
                series = None
            self.loaded_series[series_id] = series.get("qualityProfileId") if isinstance(series, dict) else None
        return self.fingerprint(self.loaded_series[series_id])

    def expected(self, profile_id) -> tuple:
        """(current, baseline) fingerprint of a profile."""
        if profile_id is None:
            return None, None
        return self.current.get(str(profile_id)), (self.baseline or {}).get(str(profile_id))

    def cache_filter(self, keyed) -> Optional[dict]:
        """`fingerprints` for db.searched.lookup_many: cache key -> (current,
        baseline) of its record's profile. None while the setting is off:
        then every cache entry blocks, whatever the profile."""
        if not self.search_again:
            return None
        return {key: self.expected(self.profile_of(record)) for key, record in keyed}

    def round_blocks(self, round_keys: dict, key: str, record: dict) -> bool:
        """Dry run: was the title checked in this round under its current
        profile fingerprint and the current rule settings? An unknown current
        profile fingerprint counts as the same (nothing is released).
        Independent of the setting: a dry run always notices profile and
        settings changes."""
        current = self.fingerprint(self.profile_of(record))
        return any(
            (current is None or profile == current) and settings == self.settings_fingerprint
            for profile, settings in round_keys.get(key, ())
        )


def refresh(skill_name: str, agent) -> ProfileState:
    """Read the profiles, log every changed one, keep the new fingerprints.
    Never raises because of *arr: without an answer the stored state counts."""
    cfg = agent.config
    stored = db.instances.get_by_id(cfg["id"]) or {}
    state = ProfileState(
        current=dict(stored.get("profile_fingerprints") or {}),
        baseline=stored.get("profile_fingerprints_baseline"),
        search_again=bool(cfg.get("search_again_after_profile_change", 1)),
        settings_fingerprint=CheckedSearchSettings.from_stored(
            cfg.get("checked_search_settings")).rules_fingerprint(cfg.get("type") or ""),
    )
    try:
        answers = [agent.http_get(path, timeout=PROFILE_TIMEOUT) for path in PROFILE_PATHS]
        current = fingerprints(*answers)
    except Exception as exc:
        agent.log("warn", skill_name,
                  f"Could not read the quality profiles — the stored fingerprints stay in force: {exc}")
    else:
        names = {str(p.get("id")): p.get("name") for p in answers[0] if isinstance(p, dict)}
        for profile_id, old, new in changes(state.current, current):
            name = names.get(profile_id) or f"#{profile_id}"
            agent.log("info", skill_name, f"Quality profile changed: {name} ({short(old)} → {short(new)})")
        try:
            state.baseline = db.instances.store_profile_fingerprints(cfg["id"], current)
        except Exception as exc:
            agent.log("warn", skill_name, f"Could not store the quality profile fingerprints: {exc}")
            if state.baseline is None:
                # First run of 0.9.0 and the write failed: entries from before
                # count under what was just read, for this run — the update
                # alone releases nothing. The next run stores the baseline.
                state.baseline = dict(current)
        state.current = current
    if cfg.get("type") == "sonarr":
        try:
            series = agent.http_get(SERIES_PATH, timeout=PROFILE_TIMEOUT)
        except Exception as exc:
            agent.log("warn", skill_name,
                      f"Could not read the series list — the series of each searched episode is asked instead: {exc}")
            state.series_loader = lambda series_id: agent.http_get(f"{SERIES_PATH}/{series_id}")
        else:
            state.series_profiles = {
                s["id"]: s.get("qualityProfileId")
                for s in (series if isinstance(series, list) else [])
                if isinstance(s, dict) and "id" in s
            }
    return state
```

- [ ] **Step 6: Runner schreiben**

`backend/checked_search/runner.py`:

```python
"""Checked search: one title at a time, through the agent's HTTP methods.

Per title: load the movie (or episode and series), run the indexer search
via GET /release, check every approved release with the pre-filter, and in
active mode grab the first clean one via POST /release. Every title gets one
row in the pre-filter log.

GET /release is an interactive search for *arr: it asks the indexers with
"Interactive Search" on, the old search command asked those with
"Automatic Search" on. While an indexer has the two switches set
differently (or the indexer list cannot be read) a run checks nothing at
all: it pauses until the next run.

*arr keeps the releases of a search for 30 minutes under indexer id + guid,
mapped to the title of whichever search returned them last. The grab
therefore names its target itself (shouldOverride with the movie, or the
series and episodes, plus quality and languages exactly as GET /release
reported them): a search for another title in between cannot redirect it.
A release GET /release did not map to this very title is never grabbed.

A POST that failed was either refused (never sent, or 3xx/4xx: nothing was
grabbed, the title stays free) or has no clear answer (timeout, connection
lost after sending, 5xx: it may be downloading, the title is cached like
after a grab). Neither tries a second release.

A release /parse could not check (timeout, HTTP error) is never grabbed,
and it is no rule rejection either: when no release passes and at least one
could not be checked, the title ends as an error (not cached, not counted
for the dry-run round), so a later run searches it again. A release that
passes after such a one is still grabbed: it passed every rule, only its
rank may be lower, and a /parse failing for one release name every time
would otherwise keep the title from ever being grabbed.

An indexer failure does not fail GET /release: *arr catches it per indexer
and answers with what the others found, and it leaves an indexer blocked
after failures out of the search. Both show only in the health checks
(IndexerStatusCheck, IndexerLongTermStatusCheck: blocked indexers by name;
Radarr and Sonarr have no indexer status endpoint). So before a title ends
without a clean hit or without results, the runner waits until *arr has
refreshed its health checks and reads them: is an indexer the release search
asks named there, or can the health or indexer list not be read, the title
ends as an error (not cached, not counted for the dry-run round).

Secrets: a release from *arr carries downloadUrl (with the indexer's API
key), infoUrl, magnetUrl and guid. Only the fields below are kept; guid,
the mapping, quality and languages live in memory for the one POST and are
never logged or stored.
"""

import copy
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests
import urllib3.exceptions

from backend import db
from backend.checked_search import radarr_rules, sonarr_rules
from backend.checked_search.normalize import parse_utc
from backend.checked_search.settings import CheckedSearchSettings
from backend.checked_search.verdict import (
    REASON_MULTI_EPISODE, REASON_PARSE_ERROR, REASON_SEASON_PACK, REASON_TARGET, Verdict,
)
from backend.skills.base import SubmitOutcome, UnsavedCheckedGrab
from backend.verification import ITEM_FAILED, ITEM_GRABBED, ITEM_NO_HIT

MODE_DRY_RUN = "dry_run"
MODE_ACTIVE = "active"

OUTCOME_GRABBED = "grabbed"
OUTCOME_WOULD_GRAB = "would_grab"
OUTCOME_NO_CLEAN_HIT = "no_clean_hit"
OUTCOME_NO_RESULTS = "no_results"
OUTCOME_ERROR = "error"
OUTCOME_GRAB_FAILED = "grab_failed"
OUTCOME_GRAB_UNCERTAIN = "grab_uncertain"

RELEASE_PATH = "/api/v3/release"
PARSE_PATH = "/api/v3/parse"
INDEXER_PATH = "/api/v3/indexer"
HEALTH_PATH = "/api/v3/health"
# Health checks naming the indexers *arr blocks after failures (backoff of at
# least a minute per failure): within six hours of the first failure, and after.
INDEXER_HEALTH_SOURCES = ("IndexerStatusCheck", "IndexerLongTermStatusCheck")
# *arr re-evaluates these checks up to 5 s after an indexer status changed
# (debounced): a failure late in the search shows only after that.
HEALTH_SETTLE_SECONDS = 6

VERDICT_PASS = "pass"
VERDICT_REJECT = "reject"
VERDICT_UNCHECKED = "unchecked"
VERDICT_ERROR = "error"       # /parse failed: not checked, not a rule rejection


@dataclass(frozen=True)
class CheckedTask:
    """One title to check. arr_id: movie id (Radarr) or episode id (Sonarr)."""

    arr_id: int
    title: str
    item_type: str            # 'movie' or 'episode'
    cache_key: str            # mov:<id>, ep:<id>, upg:<id>
    series_id: int | None = None
    profile_fingerprint: str | None = None   # the title's quality profile (spec addendum)
    # A second key a grab holds (Sonarr upgrade: upg:sea-hold:<series>:<season>,
    # so the command path waits with the season search while the grab blocks).
    hold_key: str | None = None


@dataclass
class CheckedRunOutcome(SubmitOutcome):
    checked: int = 0          # titles that ended without an error
    would_grab: int = 0
    grabbed: int = 0
    no_hit: int = 0           # no clean hit or no results (active)
    budget_exhausted: bool = False
    notes: list = field(default_factory=list)


@dataclass
class _Release:
    title: str
    indexer: str
    indexer_id: int | None
    score: int | None
    size: int | None
    quality: str
    movie_titles: tuple
    publish_date: datetime | None
    full_season: bool = False
    # What GET /release mapped the release to, and its quality and languages
    # as reported: the grab names this target (shouldOverride). Memory only.
    mapped_movie_id: int | None = None
    mapped_series_id: int | None = None
    mapped_episode_ids: tuple = ()
    quality_raw: object = field(default=None, repr=False)
    languages_raw: object = field(default=None, repr=False)
    guid: str = field(default="", repr=False)


@dataclass
class _Result:
    outcome: str
    error: str
    entry: dict
    history_status: str | None
    # *arr ran (or still runs) the indexer search: the rate slot stays used.
    searched: bool = True
    # Active: cache the title although the item failed (a grab without a
    # clear answer may be downloading). None: the default of record_checked.
    cache: bool | None = None


class _Stopped(Exception):
    """Abort requested (instance off, deleted, shutdown) while a title was checked."""


class _ParseFailed(Exception):
    """GET /parse failed for one release: it could not be checked."""


def _int(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _approved(payload) -> list[_Release]:
    """Approved releases in *arr's order — its ranking."""
    releases: list[_Release] = []
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, dict) or item.get("approved") is not True:
            continue
        quality = ((item.get("quality") or {}).get("quality") or {}).get("name") or ""
        episodes = item.get("mappedEpisodeInfo")
        releases.append(_Release(
            title=str(item.get("title") or ""),
            indexer=str(item.get("indexer") or ""),
            indexer_id=_int(item.get("indexerId")),
            score=_int(item.get("customFormatScore")),
            size=_int(item.get("size")),
            quality=str(quality),
            movie_titles=tuple(t for t in item.get("movieTitles") or () if isinstance(t, str)),
            publish_date=parse_utc(item.get("publishDate")),
            full_season=item.get("fullSeason") is True,
            mapped_movie_id=_int(item.get("mappedMovieId")),
            mapped_series_id=_int(item.get("mappedSeriesId")),
            mapped_episode_ids=tuple(
                e["id"] for e in episodes if isinstance(e, dict) and _int(e.get("id")) is not None
            ) if isinstance(episodes, list) else (),
            quality_raw=copy.deepcopy(item.get("quality")),
            languages_raw=copy.deepcopy(item.get("languages")),
            guid=str(item.get("guid") or ""),
        ))
    return releases


def _not_sent(exc: Exception) -> bool:
    """The request never reached *arr: no connection could be opened
    (refused, name not resolved, connect timeout). requests raises
    ConnectionError for much more — a reset or close after the request was
    sent, a stalled answer body — and those may have reached *arr."""
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return True
    if isinstance(exc, requests.exceptions.ConnectionError):
        inner = exc.args[0] if exc.args else None
        return isinstance(inner, urllib3.exceptions.MaxRetryError) and isinstance(
            inner.reason, urllib3.exceptions.NewConnectionError)
    return False


def _refused(exc: Exception) -> bool:
    """*arr did not act on the request: it never got it, or answered 3xx/4xx.
    For GET /release the rate slot goes back then; for the grab nothing was
    grabbed. Anything else (read timeout, connection lost after sending, 5xx,
    unreadable answer): *arr searched (or still searches without a
    cancellation signal), or may have grabbed."""
    if _not_sent(exc):
        return True
    response = getattr(exc, "response", None)
    return isinstance(exc, requests.exceptions.HTTPError) and response is not None and response.status_code < 500


def indexer_pause(agent) -> str:
    """'' when the checked search may run, else why it pauses. The skills ask
    before they collect candidates, so a run with nothing to search pauses
    (and shows it) as well. GET /release
    asks the indexers with interactive search on, the search command asked
    those with automatic search on; while an indexer has the two switches
    set differently its results would not be the command's (decision of
    01.10.2026: pause instead of checking with a different indexer set)."""
    try:
        indexers = agent.http_get(INDEXER_PATH)
    except Exception as exc:
        return f"Checked search paused — could not read the indexer list: {exc}"
    if not isinstance(indexers, list):
        return "Checked search paused — the indexer list was no list"
    differing = []
    for indexer in indexers:
        if not isinstance(indexer, dict):
            continue
        auto = indexer.get("enableAutomaticSearch") is True
        interactive = indexer.get("enableInteractiveSearch") is True
        if auto != interactive:
            differing.append(f"{indexer.get('name') or indexer.get('id')} (automatic search "
                             f"{'on' if auto else 'off'}, interactive search {'on' if interactive else 'off'})")
    if not differing:
        return ""
    return ("Checked search paused — indexer " + ", ".join(differing) + ": the release search would not ask "
            "the indexers the search command asks. Nothing was searched or remembered; set both switches alike.")


def _named_indexers(message: str) -> list[str] | None:
    """The indexer names a health message lists after its colon ("Indexers
    unavailable due to failures: A, B"); None when it names none ("All
    indexers are unavailable …", or a translation without ': ')."""
    _, colon, names = message.partition(": ")
    return [name.strip() for name in names.split(", ")] if colon else None


def _asked(indexer: dict, title_tags) -> bool:
    """Does the release search ask this indexer for the title? Interactive
    search on, and untagged or sharing a tag with the movie or series
    (ReleaseSearchService.Dispatch). Unknown title tags: asked."""
    if indexer.get("enableInteractiveSearch") is not True:
        return False
    own = indexer.get("tags")
    if not isinstance(title_tags, list) or not isinstance(own, list) or not own:
        return True
    return any(tag in title_tags for tag in own)


def _candidate(release: _Release, verdict: str, reasons=(), notes=(), chosen=False, arr_choice=False) -> dict:
    return {
        "title": release.title, "indexer": release.indexer, "score": release.score, "size": release.size,
        "quality": release.quality, "verdict": verdict, "reasons": list(reasons), "notes": list(notes),
        "chosen": chosen, "arr_choice": arr_choice,
    }


def _pick(release: _Release | None) -> dict | None:
    if release is None:
        return None
    return {"title": release.title, "indexer": release.indexer, "score": release.score,
            "size": release.size, "quality": release.quality}


class _TitleCheck:
    def __init__(self, skill_name, agent, run_id, mode, settings: CheckedSearchSettings, config: dict,
                 profiles=None):
        self.skill_name, self.agent, self.run_id, self.mode, self.settings = skill_name, agent, run_id, mode, settings
        self.profiles = profiles
        self.arr_type = config["type"]
        self.instance_id = config["id"]
        # The round the run began in: rows of a run that is still going after
        # "Reset dry run" stay in that round (spec: round as a counter).
        self.dry_run_round = int(config.get("dry_run_round") or 0) if mode == MODE_DRY_RUN else None
        self.settings_fingerprint = settings.rules_fingerprint(self.arr_type)

    # ── *arr calls ───────────────────────────────────────────────────────

    def load(self, task: CheckedTask):
        """(title data for the rules, quality profile id and tags of the
        loaded movie or series)."""
        if self.arr_type == "radarr":
            movie = self.agent.http_get(f"/api/v3/movie/{task.arr_id}")
            return radarr_rules.movie_from_resource(movie), movie.get("qualityProfileId"), movie.get("tags")
        episode = self.agent.http_get(f"/api/v3/episode/{task.arr_id}")
        series_id = task.series_id or episode.get("seriesId")
        series = self.agent.http_get(f"/api/v3/series/{series_id}")
        return (sonarr_rules.episode_from_resources(episode, series), series.get("qualityProfileId"),
                series.get("tags"))

    def fingerprint(self, task: CheckedTask, profile_id) -> str | None:
        """The profile fingerprint the title is checked and stored under: from
        the resource just loaded (the Sonarr series list may have failed);
        the record's value only when that profile is not in the state."""
        if self.profiles is not None:
            current = self.profiles.fingerprint(profile_id)
            if current is not None:
                return current
        return task.profile_fingerprint

    def search(self, task: CheckedTask) -> list[_Release]:
        key = "movieId" if self.arr_type == "radarr" else "episodeId"
        payload = self.agent.http_get(RELEASE_PATH, params={key: task.arr_id},
                                      timeout=self.settings.release_timeout_seconds)
        return _approved(payload)

    def mapped_here(self, task: CheckedTask, info, release: _Release) -> bool:
        """Did GET /release map the release to this very title, with quality
        and languages to send back? Only then can the grab name its target."""
        if not isinstance(release.quality_raw, dict) or not isinstance(release.languages_raw, list):
            return False
        if self.arr_type == "radarr":
            return release.mapped_movie_id == task.arr_id
        return release.mapped_series_id == info.series_id and task.arr_id in release.mapped_episode_ids

    def verdict(self, task: CheckedTask, info, release: _Release) -> Verdict:
        if not self.mapped_here(task, info, release):
            return Verdict((REASON_TARGET,))
        if release.full_season:
            return Verdict((REASON_SEASON_PACK,))
        if len(set(release.mapped_episode_ids)) > 1:
            # Single episodes only (decision of 01.10.2026): the rules and the
            # cache would cover the searched episode alone.
            return Verdict((REASON_MULTI_EPISODE,))
        try:
            parsed = self.agent.http_get(PARSE_PATH, params={"title": release.title})
        except Exception as exc:
            raise _ParseFailed(f"{release.title}: {exc}") from exc
        if self.arr_type == "radarr":
            return radarr_rules.evaluate(info, release.title,
                                         radarr_rules.parse_from_resource(parsed, release.movie_titles),
                                         self.settings)
        return sonarr_rules.evaluate(info, release.title, release.publish_date,
                                     sonarr_rules.parse_from_resource(parsed), self.settings)

    def grab(self, task: CheckedTask, release: _Release) -> None:
        """POST /release with the target named: *arr's cache holds the
        release mapped to whichever search returned it last."""
        body = {"guid": release.guid, "indexerId": release.indexer_id, "shouldOverride": True,
                "quality": release.quality_raw, "languages": release.languages_raw}
        if self.arr_type == "radarr":
            body["movieId"] = task.arr_id
        else:
            # Exactly the searched episode: a multi-episode release never gets here.
            body["seriesId"] = release.mapped_series_id
            body["episodeIds"] = [task.arr_id]
        self.agent.http_post(RELEASE_PATH, body, timeout=self.settings.release_timeout_seconds)

    def indexer_failure(self, tags) -> str:
        """'' when no indexer the release search asks is blocked after
        failures, else why the miss is no clean one. Waits for *arr to
        refresh its health checks first (an abort meanwhile ends the run).
        Unreadable health or indexer list: a failure too (in doubt, do not
        remember). tags: those of the movie or series; surely not asked is
        an indexer with interactive search off or with tags the title has
        none of."""
        if self.agent.wait_or_stop(HEALTH_SETTLE_SECONDS):
            raise _Stopped()
        try:
            health = self.agent.http_get(HEALTH_PATH)
            if not isinstance(health, list):
                raise ValueError("the health list was no list")
            messages = [str(check.get("message") or "") for check in health
                        if isinstance(check, dict) and check.get("source") in INDEXER_HEALTH_SOURCES]
            if not messages:
                return ""
            indexers = self.agent.http_get(INDEXER_PATH)
            if not isinstance(indexers, list):
                raise ValueError("the indexer list was no list")
        except Exception as exc:
            return f"could not read the indexer status, the miss may be an indexer failure: {exc}"
        not_asked = {i.get("name") for i in indexers if isinstance(i, dict) and not _asked(i, tags)}
        asked = [m for m in messages
                 if (names := _named_indexers(m)) is None or any(n not in not_asked for n in names)]
        if not asked:
            return ""
        return f"indexer failure during search — *arr reports: {'; '.join(asked)}"

    def stop_check(self) -> None:
        """The release search can take minutes: an abort that arrived
        meanwhile ends the run before anything is parsed or grabbed."""
        if self.agent.stop_requested():
            raise _Stopped()

    # ── One title ────────────────────────────────────────────────────────

    def entry(self, task: CheckedTask, outcome: str, fingerprint: str | None, **extra) -> dict:
        data = {"instance_id": self.instance_id, "run_id": self.run_id, "mode": self.mode,
                "skill": self.skill_name, "arr_id": task.arr_id, "cache_key": task.cache_key,
                "title": task.title, "outcome": outcome, "arr_pick": None, "pick": None,
                "candidates": [], "error_message": None, "profile_fingerprint": fingerprint,
                "dry_run_round": self.dry_run_round, "settings_fingerprint": self.settings_fingerprint}
        data.update(extra)
        return data

    def store(self, task: CheckedTask, result: _Result) -> None:
        if self.mode == MODE_ACTIVE and result.history_status is not None:
            db.history.record_checked(self.run_id, self.instance_id, task.title, task.arr_id,
                                      task.item_type, task.cache_key, result.history_status, result.entry,
                                      profile_fingerprint=result.entry.get("profile_fingerprint"),
                                      cache=result.cache, hold_key=task.hold_key)
        else:
            db.checked_search_log.insert(result.entry)

    def run(self, task: CheckedTask) -> _Result:
        """Raises _Stopped when an abort arrives after the search, before a
        /parse call or before the grab."""
        try:
            info, profile_id, tags = self.load(task)
        except Exception as exc:
            error = f"could not load the title: {exc}"
            # A failed item (no cache entry): a run with a grab next to it ends
            # partial, not as a clean success (A6). No search ran.
            return _Result(OUTCOME_ERROR, error,
                           self.entry(task, OUTCOME_ERROR, task.profile_fingerprint, error_message=error),
                           ITEM_FAILED, searched=False)
        fingerprint = self.fingerprint(task, profile_id)

        def entry(outcome: str, **extra) -> dict:
            # The row names the profile it was checked under: the page's
            # "profile changed" compares with this very profile.
            return self.entry(task, outcome, fingerprint, profile_id=_int(profile_id), **extra)

        try:
            releases = self.search(task)
        except Exception as exc:
            error = f"release search failed: {exc}"
            return _Result(OUTCOME_ERROR, error, entry(OUTCOME_ERROR, error_message=error),
                           ITEM_FAILED, searched=not _refused(exc))
        self.stop_check()
        if not releases:
            failure = self.indexer_failure(tags)
            if failure:
                return _Result(OUTCOME_ERROR, failure, entry(OUTCOME_ERROR, error_message=failure), ITEM_FAILED)
            return _Result(OUTCOME_NO_RESULTS, "", entry(OUTCOME_NO_RESULTS), ITEM_NO_HIT)

        limit = self.settings.dry_run_max_releases if self.mode == MODE_DRY_RUN else len(releases)
        candidates: list[dict] = []
        pick: _Release | None = None
        parse_failures: list[str] = []
        for index, release in enumerate(releases):
            # The first approved release is what the search command would have grabbed.
            arr_choice = index == 0
            done = (pick is not None and self.mode == MODE_ACTIVE) or index >= limit
            if done:
                candidates.append(_candidate(release, VERDICT_UNCHECKED, arr_choice=arr_choice))
                continue
            self.stop_check()
            try:
                verdict = self.verdict(task, info, release)
            except _ParseFailed as exc:
                parse_failures.append(str(exc))
                candidates.append(_candidate(release, VERDICT_ERROR, (REASON_PARSE_ERROR,),
                                             arr_choice=arr_choice))
                continue
            chosen = verdict.ok and pick is None
            if chosen:
                pick = release
            candidates.append(_candidate(release, VERDICT_PASS if verdict.ok else VERDICT_REJECT,
                                         verdict.reasons, verdict.notes, chosen, arr_choice))

        common = {"arr_pick": releases[0].title, "pick": _pick(pick), "candidates": candidates}
        if pick is None and parse_failures:
            # Not a clean miss: the release /parse could not check may be the
            # right one. Failed item without a cache entry, searched again later.
            error = (f"/parse failed for {len(parse_failures)} release(s) and no release passed — "
                     f"first: {parse_failures[0]}")
            return _Result(OUTCOME_ERROR, error, entry(OUTCOME_ERROR, error_message=error, **common), ITEM_FAILED)
        if pick is None:
            # A failing indexer may have had the clean release: not a clean miss.
            failure = self.indexer_failure(tags)
            if failure:
                return _Result(OUTCOME_ERROR, failure, entry(OUTCOME_ERROR, error_message=failure, **common),
                               ITEM_FAILED)
            return _Result(OUTCOME_NO_CLEAN_HIT, "", entry(OUTCOME_NO_CLEAN_HIT, **common), ITEM_NO_HIT)
        if self.mode == MODE_DRY_RUN:
            return _Result(OUTCOME_WOULD_GRAB, "", entry(OUTCOME_WOULD_GRAB, **common), None)
        self.stop_check()
        try:
            self.grab(task, pick)
        except Exception as exc:
            # Never a second candidate: a grab that did happen must not turn
            # into a second download.
            if _refused(exc):
                error = f"grab failed: {exc}"
                return _Result(OUTCOME_GRAB_FAILED, error,
                               entry(OUTCOME_GRAB_FAILED, error_message=error, **common), ITEM_FAILED)
            error = ("grab sent, no clear answer — it may be downloading; check the *arr queue, "
                     f"reset the cache to retry sooner: {exc}")
            return _Result(OUTCOME_GRAB_UNCERTAIN, error,
                           entry(OUTCOME_GRAB_UNCERTAIN, error_message=error, **common), ITEM_FAILED, cache=True)
        return _Result(OUTCOME_GRABBED, "", entry(OUTCOME_GRABBED, **common), ITEM_GRABBED)


def run_checked(skill_name: str, agent, run_id: int, tasks: list[CheckedTask], mode: str,
                clock=time.monotonic, *, profiles=None, started: float | None = None,
                config: dict | None = None, check_indexers: bool = True) -> CheckedRunOutcome:
    """Check the titles in order. Pauses (checks nothing) while an indexer's
    search switches differ. Stops on an abort (between titles, after a
    release search, before a /parse call and before a grab), at the rate
    cap, when the time budget is used up (no new title is started) or when
    the database refuses a write.

    profiles: the run's ProfileState — the fingerprint of a title comes from
    the movie or series it loads. started: clock() when the skill began, so
    the budget includes reading the profiles and collecting candidates.
    config: the instance as the run began (dry-run round, settings); the
    agent's config may be swapped while the run goes on. check_indexers:
    False when the skill read the indexer list already (before collecting)."""
    config = config if config is not None else agent.config
    settings = CheckedSearchSettings.from_stored(config.get("checked_search_settings"))
    delay = int(config.get("seconds_between_actions", 2) or 0)
    deadline = (clock() if started is None else started) + settings.time_budget_minutes * 60
    outcome = CheckedRunOutcome()
    pause = indexer_pause(agent) if check_indexers else ""
    if pause:
        agent.log("warn", skill_name, pause)
        outcome.paused = pause
        return outcome
    check = _TitleCheck(skill_name, agent, run_id, mode, settings, config, profiles)

    for index, task in enumerate(tasks):
        if agent.stop_requested():
            outcome.stopped = True
            break
        if clock() >= deadline:
            outcome.budget_exhausted = True
            note = (f"Time budget of {settings.time_budget_minutes} min used up — "
                    f"{len(tasks) - index} title(s) left for the next run")
            outcome.notes.append(note)
            agent.log("warn", skill_name, note)
            break
        token = agent.reserve_action()
        if token is None:
            agent.log("warn", skill_name, "Rate cap reached — stopping run")
            outcome.rate_capped = True
            break

        try:
            result = check.run(task)
        except _Stopped:
            outcome.stopped = True
            break

        if not result.searched:
            agent.release_action(token)
        try:
            check.store(task, result)
        except Exception as exc:
            message = f"Could not store the checked search for {task.title} ({result.outcome}): {exc}"
            agent.log("error", skill_name, message)
            if mode == MODE_ACTIVE and result.outcome in (OUTCOME_GRABBED, OUTCOME_GRAB_UNCERTAIN):
                _keep_unsaved(agent, check.instance_id, run_id, task, result)
            outcome.store_error = message
            break
        _count(outcome, mode, result.outcome, task, result.error)
        _log_title(agent, skill_name, mode, task, result.outcome, result.entry, result.error)

        if delay > 0 and index < len(tasks) - 1 and agent.wait_or_stop(delay):
            outcome.stopped = True
            break

    if mode == MODE_DRY_RUN:
        outcome.handled = outcome.checked
        agent.log("info", skill_name,
                  f"Dry run: {outcome.checked} title(s) checked, {outcome.would_grab} would grab")
    else:
        outcome.triggered = outcome.grabbed + outcome.no_hit
        agent.log("info", skill_name,
                  f"Checked search: {outcome.grabbed} grabbed, {outcome.no_hit} without a clean hit, "
                  f"{len(outcome.errors)} failed")
    return outcome


def _keep_unsaved(agent, instance_id: int, run_id: int, task: CheckedTask, result: _Result) -> None:
    """The grab is out (or may be); only the bookkeeping failed. Keep it with
    the unsaved commands: it blocks the title, and a later run stores it."""
    with agent.runtime.unsaved_lock:
        agent.runtime.unsaved_submissions.append(UnsavedCheckedGrab(
            run_id=run_id, instance_id=instance_id, title=task.title, arr_id=task.arr_id,
            item_type=task.item_type, cache_key=task.cache_key, status=result.history_status,
            log_entry=result.entry, profile_fingerprint=result.entry.get("profile_fingerprint"),
            sent_at=datetime.now(timezone.utc), hold_key=task.hold_key,
        ))


def _count(outcome: CheckedRunOutcome, mode: str, result: str, task: CheckedTask, error: str) -> None:
    if result in (OUTCOME_ERROR, OUTCOME_GRAB_FAILED, OUTCOME_GRAB_UNCERTAIN):
        outcome.errors.append(f"{task.title}: {error}")
        return
    outcome.checked += 1
    if result == OUTCOME_WOULD_GRAB:
        outcome.would_grab += 1
    elif result == OUTCOME_GRABBED:
        outcome.grabbed += 1
    elif mode == MODE_ACTIVE:
        outcome.no_hit += 1


def _log_title(agent, skill_name, mode, task, result, entry, error) -> None:
    pick = (entry.get("pick") or {}).get("title")
    if result == OUTCOME_GRABBED:
        agent.log("info", skill_name, f"Grabbed {pick} for {task.title}")
    elif result == OUTCOME_GRAB_FAILED:
        agent.log("warn", skill_name, f"Could not grab {pick} for {task.title}: {error}")
    elif result == OUTCOME_GRAB_UNCERTAIN:
        agent.log("warn", skill_name, f"Grab of {pick} for {task.title} without a clear answer, "
                                      f"the title stays blocked: {error}")
    elif result == OUTCOME_ERROR:
        agent.log("warn", skill_name, f"Checked search for {task.title} failed: {error}")
    elif result == OUTCOME_WOULD_GRAB:
        agent.log("debug", skill_name, f"Dry run — {task.title}: would grab {pick} (*arr: {entry.get('arr_pick')})")
    elif result == OUTCOME_NO_CLEAN_HIT:
        rejected = sum(1 for c in entry["candidates"] if c["verdict"] == VERDICT_REJECT)
        agent.log("debug" if mode == MODE_DRY_RUN else "info", skill_name,
                  f"No clean release for {task.title} — {rejected} rejected")
    else:
        agent.log("debug", skill_name, f"No approved release for {task.title}")
```

- [ ] **Step 7: Fehlend-Suche umstellen**

`backend/skills/search_missing.py` — Edit, old:

```
import random
from dataclasses import dataclass, field
```

new:

```
import random
import time
from dataclasses import dataclass, field
```

Edit, old:

```
from backend import db
from backend.database import ANCESTOR_RULE_SINCE_SETTING
```

new:

```
from backend import db
from backend.checked_search.runner import CheckedTask, indexer_pause, run_checked
from backend.checked_search.settings import CheckedSearchSettings
from backend.database import ANCESTOR_RULE_SINCE_SETTING
from backend.skills.profiles import ProfileState
from backend.skills.profiles import refresh as refresh_profiles
```

Edit, old:

```
        cfg = agent.config
        run_id = db.history.start_run(cfg["id"], cfg["name"], self.name)
```

new:

```
        cfg = agent.config
        # The checked search's time budget counts from here (profiles and
        # candidate collection included).
        started = time.monotonic()
        run_id = db.history.start_run(cfg["id"], cfg["name"], self.name)
```

Edit, old:

```
            order = cfg.get("search_order", "random")
            mode = cfg.get("missing_mode", "episode")
```

new:

```
            order = cfg.get("search_order", "random")
            checked = cfg.get("checked_search") or "off"
            # The checked search handles single episodes only; the form and
            # the API refuse other modes while it is on (spec: Sonarr).
            mode = "episode" if checked != "off" else cfg.get("missing_mode", "episode")
            # Fingerprints of the quality profiles: a cached title whose
            # profile changed may be searched again (spec addendum).
            profiles = refresh_profiles(self.name, agent)
            if checked != "off":
                # Every checked run reads the indexer list before it collects,
                # also one that will find nothing to search: a lasting pause
                # must show in the History and keep last_sync (spec).
                pause = indexer_pause(agent)
                if pause:
                    agent.log("warn", self.name, pause)
                    finish_search_run(self.name, agent, run_id, 0, SubmitOutcome(paused=pause))
                    return
            # A dry run ignores the search cache: titles searched long ago
            # are checked too. Instead each title is checked once per round
            # (the round this run begins in) — a force run as well.
            round_keys = (
                db.checked_search_log.dry_run_keys(cfg["id"], int(cfg.get("dry_run_round") or 0))
                if checked == "dry_run" else None
            )
```

Edit, old:

```
            if order == "random":
                candidates, stats = self._collect_random(agent, cfg, per_run, mode, cutoff, force)
            else:
                candidates, stats = self._collect_ordered(agent, cfg, per_run, mode, order, cutoff, force)
```

new:

```
            if order == "random":
                candidates, stats = self._collect_random(agent, cfg, per_run, mode, cutoff, force,
                                                         round_keys, profiles)
            else:
                candidates, stats = self._collect_ordered(agent, cfg, per_run, mode, order, cutoff, force,
                                                          round_keys, profiles)
```

Edit, old:

```
            series_lookup = self._series_lookup(agent, cfg, candidates)
            delay = cfg.get("seconds_between_actions", 2) or 0
            outcome = submit_candidates(
                self.name, agent, run_id, candidates,
                lambda record: self._trigger_search(agent, cfg, record, mode, series_lookup),
                delay,
            )
```

new:

```
            series_lookup = self._series_lookup(agent, cfg, candidates)
            if checked != "off":
                tasks = [self._checked_task(cfg["type"], record, series_lookup, profiles) for record in candidates]
                outcome = run_checked(self.name, agent, run_id, tasks, checked,
                                      profiles=profiles, started=started, config=cfg, check_indexers=False)
                finish_search_run(self.name, agent, run_id, wanted_count, outcome, stats.notes + outcome.notes)
                return

            delay = cfg.get("seconds_between_actions", 2) or 0
            outcome = submit_candidates(
                self.name, agent, run_id, candidates,
                lambda record: self._trigger_search(agent, cfg, record, mode, series_lookup),
                delay,
                fingerprint_of=profiles.stored_fingerprint,
            )
```

Edit, old: `    def _collect_random(self, agent, cfg, per_run, mode, cutoff, force):`
new: `    def _collect_random(self, agent, cfg, per_run, mode, cutoff, force, round_keys=None, profiles=None):`

Edit, old:

```
            self._take_eligible(agent, cfg, records, mode, cutoff, force, per_run, candidates, seen, stats)
        return candidates, stats
```

new:

```
            self._take_eligible(agent, cfg, records, mode, cutoff, force, per_run, candidates, seen, stats,
                                round_keys, profiles)
        return candidates, stats
```

Edit, old: `    def _collect_ordered(self, agent, cfg, per_run, mode, order, cutoff, force):`
new: `    def _collect_ordered(self, agent, cfg, per_run, mode, order, cutoff, force, round_keys=None, profiles=None):`

Edit, old:

```
        self._take_eligible(agent, cfg, ordered, mode, cutoff, force, per_run, candidates, set(), stats)
```

new:

```
        self._take_eligible(agent, cfg, ordered, mode, cutoff, force, per_run, candidates, set(), stats,
                            round_keys, profiles)
```

Edit, old:

```
    def _take_eligible(self, agent, cfg, records, mode, cutoff, force, per_run, candidates, seen, stats):
        """Append records, in the given order, that are missing, released and
        not in the cache, until per_run candidates exist. The cache is asked
        once per chunk, not once per record (A-L3). Commands still waiting to
        be stored count as cached."""
```

new:

```
    def _take_eligible(self, agent, cfg, records, mode, cutoff, force, per_run, candidates, seen, stats,
                       round_keys=None, profiles=None):
        """Append records, in the given order, that are missing, released and
        not in the cache, until per_run candidates exist. The cache is asked
        once per chunk, not once per record (A-L3). Commands still waiting to
        be stored count as cached.

        profiles: a cache entry blocks only under the current fingerprint of
        the record's quality profile (spec addendum), unless the instance
        switched that off. A grab of the checked search blocks only "Search
        again if still missing after (days)": the records come from the
        wanted list, so a listed grab is still missing. round_keys
        (checked-search dry run, force run too): the cache is not asked at
        all; a record is skipped only when its own key was checked in this
        round under its current fingerprint and the current rule settings."""
        profiles = profiles or ProfileState()
        grab_days = CheckedSearchSettings.from_stored(cfg.get("checked_search_settings")).search_again_after_days
```

Edit, old:

```
            hits = {} if force else {
                **db.searched.lookup_many(
                    cfg["id"], [key for r in pool for key in self._check_keys(arr_type, r)], retry_hours
                ),
                **unsaved_cache_keys(agent),
            }
```

new:

```
            if round_keys is not None:
                now = datetime.now(timezone.utc)
                hits = {
                    self._own_key(arr_type, r): now for r in pool
                    if profiles.round_blocks(round_keys, self._own_key(arr_type, r), r)
                }
            elif force:
                hits = {}
            else:
                keyed = [(key, r) for r in pool for key in self._check_keys(arr_type, r)]
                hits = {
                    **db.searched.lookup_many(
                        cfg["id"], [key for key, _ in keyed], retry_hours,
                        fingerprints=profiles.cache_filter(keyed), grab_release_days=grab_days,
                    ),
                    **unsaved_cache_keys(agent),
                }
```

Edit, old: `                if not force and self._blocked(arr_type, record, hits, window, since):`
new: `                if (not force or round_keys is not None) and self._blocked(arr_type, record, hits, window, since):`

Edit, old:

```
    @staticmethod
    def _ancestor_rule_since() -> datetime | None:
```

new:

```
    @staticmethod
    def _own_key(arr_type: str, record: dict) -> str:
        return f"mov:{record.get('id')}" if arr_type == "radarr" else f"ep:{record.get('id')}"

    @staticmethod
    def _ancestor_rule_since() -> datetime | None:
```

Edit, old:

```
        own = f"mov:{record.get('id')}" if arr_type == "radarr" else f"ep:{record.get('id')}"
        if own in hits:
```

new:

```
        own = self._own_key(arr_type, record)
        if own in hits:
```

Edit, old: `    def _trigger_search(self, agent, cfg, record, mode, series_lookup) -> SearchResult:`
new:

```
    def _checked_task(self, arr_type: str, record: dict, series_lookup: dict,
                      profiles: ProfileState | None = None) -> CheckedTask:
        """The title as the checked search sees it: always one movie or one
        episode, keyed like the commands it replaces. profile_fingerprint is
        the fallback; the runner takes the one of the movie or series it loads."""
        profiles = profiles or ProfileState()
        return CheckedTask(
            arr_id=record.get("id"),
            title=self._label(arr_type, record, series_lookup or {}),
            item_type="movie" if arr_type == "radarr" else "episode",
            cache_key=self._own_key(arr_type, record),
            series_id=record.get("seriesId") if arr_type == "sonarr" else None,
            profile_fingerprint=profiles.fingerprint(profiles.profile_of(record)),
        )

    def _trigger_search(self, agent, cfg, record, mode, series_lookup) -> SearchResult:
```

- [ ] **Step 8: Tests laufen lassen**

Run: `cd <repo> && .venv/bin/python -m pytest tests/test_g3_runner.py tests/test_p2_base.py tests/test_p2_search_missing.py tests/test_p2_cache_rules.py tests/test_p1_http.py -q -p no:cacheprovider`
Expected: alle grün (`73` neue)

- [ ] **Step 9: Commit**

```bash
git add backend/checked_search/runner.py backend/skills/profiles.py backend/agents/base.py backend/skills/base.py backend/skills/search_missing.py tests/test_g3_runner.py
git commit -m "feat: run the checked search for missing titles and notice profile changes"
```

---

### Task G3.2: Upgrades über denselben Runner

**Files:**
- Modify: `backend/skills/search_upgrades.py`
- Test: `tests/test_g3_runner.py` (Upgrade-Tests ergänzen: 15 Fälle)

**Interfaces:**
- Consumes: G3.1 (`run_checked`, `CheckedTask`, `profiles.refresh`, `ProfileState`, `submit_candidates(…, fingerprint_of=)`), G2 (`dry_run_keys`, `lookup_many(…, fingerprints=)`)
- Produces: Upgrade-Suche bei `checked_search != 'off'` über `run_checked`; Cache-Schlüssel `upg:<id>` je Film bzw. je Folge; Profilstand und Fingerabdruck auch für Upgrades (beide Suchwege).

Sonarr-Upgrades suchen sonst je Staffel (`SeasonSearch`, Schlüssel `upg:sea:<serie>:<staffel>`). Die geprüfte Suche kennt nur einzelne Folgen; im geprüften Modus ist der Schlüssel deshalb `upg:<folge>`. **Der Schlüssel des anderen Suchwegs sperrt mit** (Codex-Runde 2, F5): `_keep_uncached` fragt bei Sonarr je Eintrag zusätzlich den Schlüssel des anderen Wegs ab (geprüft: die Staffel `upg:sea:<serie>:<staffel>`; Befehl: die Folge `upg:<folge>`), in derselben Abfrage `lookup_many` und mit demselben Profil-Fingerabdruck (`cache_filter`), dazu `unsaved_cache_keys`. Ein Eintrag fällt weg, wenn einer der beiden Schlüssel sperrt. So gibt das Umschalten auf Active eine Staffel, die 0.8.0 (oder der Befehlsweg) gemerkt hat, nicht frei. Die Zusage der Spec „Das Update allein gibt also nichts frei, erst die nächste Profiländerung“ gilt damit auch für Sonarr-Upgrades. `seen` bleibt beim eigenen Schlüssel. Der Probelauf braucht das nicht: Er übergeht den Cache und fragt nur die eigene Runde.

**Staffel-Halter für den Befehlsweg (Codex-Runde 3, G3, mit Probe bestätigt; Entscheidung Daniel 02.10.2026).** Der Folgen-Schlüssel allein reichte in der Gegenrichtung nicht: Nach einem Grab von E03 durch die geprüfte Suche wählte der Befehlsweg (Off) die freie Folge E04 und schickte `SeasonSearch` für die ganze Staffel. Sonarr sucht dabei alle überwachten Folgen (`SeasonSearchService`: `missingOnly: false`), die Verlaufsprüfung entfällt bei Suchen (`HistorySpecification`: „Skipping history check during search“), und `ProcessDownloadDecisions` lädt jeden freigegebenen Treffer: E03 konnte innerhalb seiner Sperre ein zweites Mal geladen werden (bei einem unklaren oder noch nicht in der Warteschlange stehenden Grab auch doppelt). Die Cutoff-Liste hilft nicht, um die Geschwister zu erkennen (E03 fällt nach dem Import heraus), und `searched_items` kennt weder Serie noch Staffel. Deshalb schreibt die geprüfte Suche bei einem Sonarr-Upgrade-Grab (`grabbed` oder `grab_uncertain`) zusätzlich den Staffel-Halter `upg:sea-hold:<serie>:<staffel>` (`CheckedTask.hold_key` → `record_checked(hold_key=)`, auch beim Nachspeichern eines `UnsavedCheckedGrab`; `unsaved_cache_keys` sperrt ihn bis dahin). Er trägt denselben Fingerabdruck und dasselbe `grabbed_at` wie der Grab und sperrt deshalb genauso lange: „Search again if still missing after (days)“ (die Cutoff-Liste ist eine Wanted-Liste), bei einer Profiländerung früher, mit ausgeschalteter Einstellung „Search again after profile changes“ die vollen N Tage. Auf dem Befehlsweg fragt `_keep_uncached` je Eintrag zusätzlich diesen Halter ab; sperrt er, wartet die Staffel. **Keine Einzelbefehle als Ersatz** (Entscheidung Daniel): die freien Folgen der Staffel kommen mit der Staffelsuche dran, sobald der Halter abläuft. Ein `no_hit` schreibt keinen Halter: Die Folge selbst bleibt über ihren Schlüssel gesperrt, eine Staffel mit einer freien Folge wird gesucht wie bisher (`SeasonSearch` bringt für die gesperrte Folge dann höchstens ein echtes Upgrade). Im geprüften Modus fragt niemand den Halter ab: Dort steht jede Folge für sich, eine geladene Folge hält ihre Geschwister nicht auf. Der Force Run übergeht den Halter wie jeden Cache-Eintrag. `upg:sea-hold:` ist ein eigener Präfix, damit kein bestehender Code ihn für einen Staffel-Schlüssel des Befehlswegs (`upg:sea:`) hält.

Der Indexer-Abruf steht wie in G3.1 nach dem Profilstand und vor dem Sammeln (Codex-Runde 3, G4): Ein geprüfter Upgrade-Lauf ohne Kandidaten setzt bei abweichenden Schaltern ebenso aus.

Profilstand wie in G3.1: `refresh_profiles` nach der „per run“-Prüfung, Radarr-Einträge tragen `qualityProfileId` (aus der Cutoff- bzw. Film-Liste), Sonarr-Einträge finden ihr Profil über `series_id` in der Serienliste (sonst `stored_fingerprint` bzw. der Runner aus der geladenen Serie). Für Upgrades ist der Nachtrag besonders wichtig: Nach einer Änderung von Punkten oder Formaten darf ein Film mit Datei wieder gesucht werden. Probelauf-Runde wie in G3.1 (auch beim Force Run, Runde und Einstellungen beim Start). „Search again if still missing after (days)“ gilt nur für die Cutoff-Liste (`wanted_list=True`): Die Quelle „monitored movies“ listet jeden Film mit Datei und sagt nicht, ob ein Grab noch fehlt.

- [ ] **Step 1: Failing tests ergänzen**

`tests/test_g3_runner.py` — Edit, old:

```
from backend.skills.search_missing import SearchMissingSkill
```

new:

```
from backend.skills.search_missing import SearchMissingSkill
from backend.skills.search_upgrades import SearchUpgradesSkill
```

Edit, old (die ganze Zeile):

```
# ── Secrets ──────────────────────────────────────────────────────────────────
```

new:

```
# ── Upgrades ─────────────────────────────────────────────────────────────────

def test_radarr_upgrade_skips_the_release_of_the_existing_file(db_path):
    inst = make_instance(checked_search="active", search_upgrades_enabled=True, upgrades_per_run=1,
                         upgrade_source="monitored_items_only")
    current = "The.Thing.1982.720p.BluRay.x264-OLD"
    owned = movie(1, "The Thing", 1982, hasFile=True, movieFile={"sceneName": current})
    agent = agent_for(inst, movies=[owned],
                      releases={1: [release(current, "g1"), release(RIGHT, "g2")]},
                      parses={current: radarr_parse("The Thing", 1982, 1), RIGHT: radarr_parse("The Thing", 1982, 1)})
    SearchUpgradesSkill().execute(agent)
    assert agent.posts == [movie_grab("g2")]
    assert log_rows()[0]["candidates"][0]["reasons"] == ["existing file"]
    assert sql("SELECT cache_key FROM searched_items") == [("upg:1",)]


def test_sonarr_upgrade_works_per_episode(db_path):
    inst = make_instance(name="Sonarr", type="sonarr", checked_search="dry_run", search_upgrades_enabled=True)
    episode = {**guest_episode(), "hasFile": True}
    agent = agent_for(inst, cutoff=[episode], episodes=[episode], series=[GUEST_SERIES],
                      releases={3: [release("The.Guest.S01E03.1080p-GRP", "g1", series_id=10, episode_ids=(3,))]})
    SearchUpgradesSkill().execute(agent)
    [row] = log_rows()
    assert (row["skill"], row["cache_key"]) == ("search_upgrades", "upg:3")
    assert row["profile_fingerprint"] == stored_fingerprint(inst)


def test_force_run_of_upgrades_in_dry_run_respects_the_round(db_path):
    inst = make_instance(search_upgrades_enabled=True, upgrades_per_run=1, upgrade_source="monitored_items_only")
    owned = movie(1, "The Thing", 1982, hasFile=True)
    SearchUpgradesSkill().execute(agent_for(inst, movies=[owned]), force=True)
    forced = agent_for(inst, movies=[owned])
    SearchUpgradesSkill().execute(forced, force=True)
    assert [p for p, _ in forced.gets if p == RELEASE] == []
    assert len(log_rows()) == 1


@pytest.mark.parametrize("source,searched_again", [("wanted_list_only", 1), ("monitored_items_only", 0)])
def test_a_grabbed_upgrade_is_searched_again_only_from_the_cutoff_list(db_path, source, searched_again):
    # The cutoff list names the movie only while it is still below the
    # cutoff; the movie list names every movie with a file.
    inst = make_instance(checked_search="active", search_upgrades_enabled=True, upgrades_per_run=1,
                         upgrade_source=source, checked_search_settings={"search_again_after_days": 1})
    owned = movie(1, "The Thing", 1982, hasFile=True)
    kwargs = dict(cutoff=[owned], movies=[owned], releases={1: [release(RIGHT, "g1")]}, parses=PARSES)
    first = agent_for(inst, **kwargs)
    SearchUpgradesSkill().execute(first)
    assert first.posts == [movie_grab("g1")]
    sql("UPDATE searched_items SET grabbed_at=datetime('now','localtime','-2 days')")
    again = agent_for(inst, **kwargs)
    SearchUpgradesSkill().execute(again)
    assert len([p for p, _ in again.gets if p == RELEASE]) == searched_again


def test_upgrade_cache_frees_a_movie_after_its_profile_changed(db_path):
    inst = make_instance(checked_search="off", search_upgrades_enabled=True, upgrades_per_run=1,
                         upgrade_source="monitored_items_only")
    owned = movie(1, "The Thing", 1982, hasFile=True)
    first = agent_for(inst, movies=[owned])
    SearchUpgradesSkill().execute(first)
    assert first.commands == [{"name": "MoviesSearch", "movieIds": [1]}]
    assert sql("SELECT cache_key, profile_fingerprint FROM searched_items") == [("upg:1", stored_fingerprint(inst))]
    same = agent_for(inst, movies=[owned])
    SearchUpgradesSkill().execute(same)
    assert same.commands == []
    changed = agent_for(inst, movies=[owned], profiles=changed_profiles())
    SearchUpgradesSkill().execute(changed)
    assert changed.commands == [{"name": "MoviesSearch", "movieIds": [1]}]


@pytest.mark.parametrize("search_again", [True, False])
def test_a_season_upgrade_cached_by_the_command_blocks_its_episodes_in_checked_mode(db_path, search_again):
    # Codex round 2, F5: 0.8.0 cached Sonarr upgrades per season; switching to
    # the checked search (per episode) must not release them early.
    inst = make_instance(name="Sonarr", type="sonarr", checked_search="active", search_upgrades_enabled=True,
                         upgrades_per_run=1, search_again_after_profile_change=search_again)
    db.searched.add(inst["id"], "upg:sea:10:1", "The Guest S01", "season")      # 0.8.0: no fingerprint
    episode = {**guest_episode(), "hasFile": True}
    kwargs = dict(cutoff=[episode], episodes=[episode], series=[GUEST_SERIES],
                  releases={3: [release("The.Guest.S01E03.1080p-GRP", "g1", series_id=10, episode_ids=(3,))]})
    first = agent_for(inst, **kwargs)
    SearchUpgradesSkill().execute(first)
    assert [p for p, _ in first.gets if p == RELEASE] == []                    # the update releases nothing
    changed = agent_for(inst, profiles=changed_profiles(), **kwargs)
    SearchUpgradesSkill().execute(changed)
    assert len([p for p, _ in changed.gets if p == RELEASE]) == (1 if search_again else 0)


def test_an_episode_upgrade_without_a_grab_holds_only_itself(db_path):
    # A checked search without a grab (no_hit) blocks its own episode; a free
    # episode of the same season still brings the season search (decision
    # Daniel 02.10.2026: only a grab holds the season).
    inst = make_instance(name="Sonarr", type="sonarr", checked_search="off", search_upgrades_enabled=True,
                         upgrades_per_run=1)
    run = history.start_run(inst["id"], "Sonarr", "search_upgrades")
    history.record_checked(run, inst["id"], "The Guest S01E03", 3, "episode", "upg:3", "no_hit")
    episode = {**guest_episode(), "hasFile": True}
    agent = agent_for(inst, cutoff=[episode], episodes=[episode], series=[GUEST_SERIES])
    SearchUpgradesSkill().execute(agent)
    assert agent.commands == []
    free = {**guest_episode(4), "hasFile": True}                                 # a second, uncached episode
    other = agent_for(inst, cutoff=[episode, free], episodes=[episode, free], series=[GUEST_SERIES])
    SearchUpgradesSkill().execute(other)
    assert other.commands == [{"name": "SeasonSearch", "seriesId": 10, "seasonNumber": 1}]


E03_UPGRADE = "The.Guest.S01E03.German.1080p.WEB.x264-GRP"


def grab_e03_upgrade(inst, outcome="grabbed", then_off=True):
    """The checked search upgrades E03 (grabbed, or sent without a clear
    answer); then, if asked, the instance goes back to the command path."""
    episode = {**guest_episode(), "hasFile": True}
    kwargs = dict(cutoff=[episode], episodes=[episode], series=[GUEST_SERIES],
                  releases={3: [release(E03_UPGRADE, "g1", series_id=10, episode_ids=(3,))]},
                  parses={E03_UPGRADE: {"parsedEpisodeInfo": {"seriesTitle": "The Guest"}, "series": {"id": 10}}})
    if outcome == "grab_uncertain":
        kwargs["post_error"] = requests.exceptions.ReadTimeout("no answer")
    SearchUpgradesSkill().execute(agent_for(inst, **kwargs))
    assert [r["outcome"] for r in log_rows()] == [outcome]
    if then_off:
        sql("UPDATE instances SET checked_search='off'")
    return episode, {**guest_episode(4), "hasFile": True}


@pytest.mark.parametrize("outcome", ["grabbed", "grab_uncertain"])
def test_a_checked_episode_upgrade_holds_its_season_search_in_off_mode(db_path, outcome):
    # Codex round 3, G3, decision Daniel 02.10.2026: SeasonSearch covers every
    # monitored episode, the grabbed one too, and *arr skips its history check
    # during searches. While the grab blocks, the command path leaves the
    # season alone — no single episode commands instead.
    inst = make_instance(name="Sonarr", type="sonarr", checked_search="active", search_upgrades_enabled=True,
                         upgrades_per_run=1, checked_search_settings={"search_again_after_days": 3})
    episode, free = grab_e03_upgrade(inst, outcome)
    left = agent_for(inst, cutoff=[free], episodes=[episode, free], series=[GUEST_SERIES])   # E03 off the list
    SearchUpgradesSkill().execute(left)
    assert left.commands == []
    both = agent_for(inst, cutoff=[episode, free], episodes=[episode, free], series=[GUEST_SERIES])
    SearchUpgradesSkill().execute(both)
    assert both.commands == []
    sql("UPDATE searched_items SET grabbed_at=datetime('now','localtime','-4 days') WHERE grabbed_at IS NOT NULL")
    later = agent_for(inst, cutoff=[free], episodes=[episode, free], series=[GUEST_SERIES])
    SearchUpgradesSkill().execute(later)
    assert later.commands == [{"name": "SeasonSearch", "seriesId": 10, "seasonNumber": 1}]


def test_a_held_season_upgrade_is_free_after_a_profile_change(db_path):
    inst = make_instance(name="Sonarr", type="sonarr", checked_search="active", search_upgrades_enabled=True,
                         upgrades_per_run=1)
    episode, free = grab_e03_upgrade(inst)
    changed = agent_for(inst, cutoff=[free], episodes=[episode, free], series=[GUEST_SERIES],
                        profiles=changed_profiles())
    SearchUpgradesSkill().execute(changed)
    assert changed.commands == [{"name": "SeasonSearch", "seriesId": 10, "seasonNumber": 1}]


def test_a_checked_episode_upgrade_does_not_hold_its_siblings_in_checked_mode(db_path):
    inst = make_instance(name="Sonarr", type="sonarr", checked_search="active", search_upgrades_enabled=True,
                         upgrades_per_run=1)
    episode, free = grab_e03_upgrade(inst, then_off=False)
    sibling = agent_for(inst, cutoff=[free], episodes=[episode, free], series=[GUEST_SERIES])
    SearchUpgradesSkill().execute(sibling)
    assert [params for p, params in sibling.gets if p == RELEASE] == [{"episodeId": 4}]


def test_an_unsaved_checked_upgrade_grab_holds_its_season(db_path, monkeypatch):
    # The database refuses the grab's bookkeeping: until a later run stores
    # it, the grab in memory holds its season for the command path as well.
    inst = make_instance(name="Sonarr", type="sonarr", checked_search="active", search_upgrades_enabled=True,
                         upgrades_per_run=1)
    episode, free = {**guest_episode(), "hasFile": True}, {**guest_episode(4), "hasFile": True}
    agent = agent_for(inst, cutoff=[episode], episodes=[episode, free], series=[GUEST_SERIES],
                      releases={3: [release(E03_UPGRADE, "g1", series_id=10, episode_ids=(3,))]},
                      parses={E03_UPGRADE: {"parsedEpisodeInfo": {"seriesTitle": "The Guest"}, "series": {"id": 10}}})

    def broken(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(db.history, "record_checked", broken)
    SearchUpgradesSkill().execute(agent)
    assert len(agent.posts) == 1 and len(agent.runtime.unsaved_submissions) == 1
    agent.config = {**agent.config, "checked_search": "off"}
    agent.cutoff = [free]
    SearchUpgradesSkill().execute(agent)
    assert agent.commands == []


def test_an_empty_checked_upgrade_run_still_checks_the_indexers(db_path):
    # Codex round 3, G4: no candidates, but an indexer's switches differ.
    inst = make_instance(checked_search="active", search_upgrades_enabled=True, upgrades_per_run=1,
                         upgrade_source="monitored_items_only")
    agent = agent_for(inst, indexers=WEEKLY_CHECK)
    agent.state["last_sync"] = "2000-01-01 00:00"
    SearchUpgradesSkill().execute(agent)
    run = last_run()
    assert run["status"] == "success"
    assert run["error_message"].startswith("Checked search paused — indexer Weekly check")
    assert agent.state["last_sync"] == "2000-01-01 00:00"


# ── Secrets ──────────────────────────────────────────────────────────────────
```

- [ ] **Step 2: Tests laufen lassen, sie müssen scheitern**

Run: `cd <repo> && .venv/bin/python -m pytest tests/test_g3_runner.py -q -p no:cacheprovider -k upgrade`
Expected: `14 failed, 1 passed` (die Upgrade-Suche schickt noch `MoviesSearch`/`SeasonSearch`, kein Protokoll, kein Fingerabdruck, keine Indexer-Prüfung, kein Staffel-Halter, und eine Folge aus der geprüften Suche sperrt sich selbst nicht; `test_a_season_upgrade_cached_by_the_command_blocks_its_episodes_in_checked_mode[False]` besteht schon vorher, weil der Befehlsweg die Staffel ohnehin sperrt)

- [ ] **Step 3: Implementieren**

`backend/skills/search_upgrades.py` — Edit, old:

```
import math
import random

from backend import db
from backend.skills.base import (
```

new:

```
import math
import random
import time

from backend import db
from backend.checked_search.runner import CheckedTask, indexer_pause, run_checked
from backend.checked_search.settings import CheckedSearchSettings
from backend.skills.profiles import ProfileState
from backend.skills.profiles import refresh as refresh_profiles
from backend.skills.base import (
```

Edit, old:

```
        cfg = agent.config
        run_id = db.history.start_run(cfg["id"], cfg["name"], self.name)
```

new:

```
        cfg = agent.config
        # The checked search's time budget counts from here.
        started = time.monotonic()
        run_id = db.history.start_run(cfg["id"], cfg["name"], self.name)
```

Edit, old:

```
            agent.log("info", self.name, "Searching for upgrade candidates...")
            candidates, failures, notes, requested = self._collect_candidates(agent, cfg, per_run, force)
```

new:

```
            agent.log("info", self.name, "Searching for upgrade candidates...")
            checked = cfg.get("checked_search") or "off"
            # Fingerprints of the quality profiles: a cached title whose
            # profile changed may be searched again (spec addendum).
            profiles = refresh_profiles(self.name, agent)
            if checked != "off":
                # Before collecting: a run without candidates pauses visibly too.
                pause = indexer_pause(agent)
                if pause:
                    agent.log("warn", self.name, pause)
                    finish_search_run(self.name, agent, run_id, 0, SubmitOutcome(paused=pause))
                    return
            # Dry run: once per round, a force run as well.
            round_keys = (
                db.checked_search_log.dry_run_keys(cfg["id"], int(cfg.get("dry_run_round") or 0))
                if checked == "dry_run" else None
            )
            candidates, failures, notes, requested = self._collect_candidates(
                agent, cfg, per_run, force, checked != "off", round_keys, profiles)
```

Edit, old:

```
            delay = cfg.get("seconds_between_actions", 2) or 0
            outcome = submit_candidates(
                self.name, agent, run_id, candidates,
                lambda item: self._fire_upgrade(agent, cfg["type"], item),
                delay,
            )
```

new:

```
            if checked != "off":
                tasks = [self._checked_task(cfg["type"], item, profiles) for item in candidates]
                outcome = run_checked(self.name, agent, run_id, tasks, checked,
                                      profiles=profiles, started=started, config=cfg, check_indexers=False)
                finish_search_run(self.name, agent, run_id, wanted_count, outcome, notes + outcome.notes)
                return

            delay = cfg.get("seconds_between_actions", 2) or 0
            outcome = submit_candidates(
                self.name, agent, run_id, candidates,
                lambda item: self._fire_upgrade(agent, cfg["type"], item),
                delay,
                fingerprint_of=profiles.stored_fingerprint,
            )
```

Edit, old:

```
    def _cache_key(self, arr_type: str, item: dict) -> str:
        if arr_type == "radarr":
            return f"upg:{item['id']}"
```

new:

```
    def _cache_key(self, arr_type: str, item: dict, checked: bool = False) -> str:
        """checked: the checked search works per episode, so a Sonarr
        upgrade is keyed by its episode, not by the season."""
        if arr_type == "radarr" or checked:
            return f"upg:{item['id']}"
```

Edit, old: `    def _trigger_upgrade(self, agent, arr_type: str, item: dict) -> SearchResult:`
new:

```
    @staticmethod
    def _hold_key(arr_type: str, item: dict) -> str | None:
        """Sonarr: the key a checked grab of this episode also writes, so the
        command path waits with the season search while the grab blocks
        (decision Daniel 02.10.2026). Only the command path asks for it: in
        the checked search each episode stands alone."""
        series_id, season_number = item.get("series_id"), item.get("season_number")
        if arr_type != "sonarr" or series_id is None or season_number is None:
            return None
        return f"upg:sea-hold:{series_id}:{season_number}"

    def _checked_task(self, arr_type: str, item: dict, profiles: ProfileState | None = None) -> CheckedTask:
        profiles = profiles or ProfileState()
        return CheckedTask(
            arr_id=item["id"],
            title=item.get("label") or f"#{item['id']}",
            item_type="movie" if arr_type == "radarr" else "episode",
            cache_key=self._cache_key(arr_type, item, checked=True),
            series_id=item.get("series_id"),
            profile_fingerprint=profiles.fingerprint(profiles.profile_of(item)),
            hold_key=self._hold_key(arr_type, item),
        )

    def _trigger_upgrade(self, agent, arr_type: str, item: dict) -> SearchResult:
```

Edit, old: `    def _collect_candidates(self, agent, cfg, per_run, force):`
new: `    def _collect_candidates(self, agent, cfg, per_run, force, checked=False, round_keys=None, profiles=None):`

Edit, old:

```
                if source == "cutoff":
                    self._collect_cutoff(agent, cfg, per_run, force, found, seen, notes)
                else:
                    self._collect_monitored(agent, cfg, per_run, force, found, seen)
```

new:

```
                if source == "cutoff":
                    self._collect_cutoff(agent, cfg, per_run, force, found, seen, notes,
                                         checked, round_keys, profiles)
                else:
                    self._collect_monitored(agent, cfg, per_run, force, found, seen, checked, round_keys, profiles)
```

Edit, old:

```
    def _keep_uncached(self, agent, cfg, items, force, found, seen, limit) -> None:
        """Commands still waiting to be stored count as cached."""
        keyed = [(self._cache_key(cfg["type"], item), item) for item in items]
        hits = {} if force else {
            **db.searched.lookup_many(
                cfg["id"], [key for key, _ in keyed], int(cfg.get("retry_hours", 0) or 0)
            ),
            **unsaved_cache_keys(agent),
        }
        for key, item in keyed:
            if len(found) >= limit:
                return
            if key in hits or key in seen:
                continue
```

new:

```
    def _keep_uncached(self, agent, cfg, items, force, found, seen, limit, checked=False, round_keys=None,
                       profiles=None, wanted_list=False) -> None:
        """Commands still waiting to be stored count as cached. A cache entry
        blocks only under the current fingerprint of the item's quality
        profile (spec addendum, unless switched off). wanted_list (the cutoff
        list): a grab of the checked search blocks only "Search again if
        still missing after (days)" — the movie list cannot tell whether a
        grab is still missing. In a checked-search dry run (round_keys, force
        run too) only this round's log counts, under the current profile
        fingerprint and rule settings.

        Sonarr keys an upgrade by season on the command path and by episode
        in the checked search: the other path's key blocks as well, so
        switching the mode releases nothing early. On the command path a
        season also waits while a checked grab of one of its episodes blocks
        (hold key, decision Daniel 02.10.2026): SeasonSearch would search
        the grabbed episode again."""
        profiles = profiles or ProfileState()
        arr_type = cfg["type"]
        keyed = [(self._cache_key(arr_type, item, checked), item) for item in items]
        other = [self._cache_key(arr_type, item, not checked) for item in items]
        held = [None if checked else self._hold_key(arr_type, item) for item in items]
        grab_days = (CheckedSearchSettings.from_stored(cfg.get("checked_search_settings")).search_again_after_days
                     if wanted_list else 0)
        if round_keys is not None:
            hits = {key: True for key, item in keyed if profiles.round_blocks(round_keys, key, item)}
            other = [None] * len(keyed)
            held = [None] * len(keyed)
        elif force:
            hits = {}
        else:
            asked = (keyed + [(key, item) for key, (own, item) in zip(other, keyed) if key != own]
                     + [(key, item) for key, (_, item) in zip(held, keyed) if key])
            hits = {
                **db.searched.lookup_many(
                    cfg["id"], [key for key, _ in asked], int(cfg.get("retry_hours", 0) or 0),
                    fingerprints=profiles.cache_filter(asked), grab_release_days=grab_days,
                ),
                **unsaved_cache_keys(agent),
            }
        for (key, item), other_key, held_key in zip(keyed, other, held):
            if len(found) >= limit:
                return
            if key in hits or other_key in hits or held_key in hits or key in seen:
                continue
```

Edit, old: `    def _collect_cutoff(self, agent, cfg, per_run, force, found, seen, notes) -> None:`
new: `    def _collect_cutoff(self, agent, cfg, per_run, force, found, seen, notes, checked=False, round_keys=None, profiles=None) -> None:`

Edit, old:

```
            self._keep_uncached(agent, cfg, items, force, found, seen, limit)

    @staticmethod
```

new:

```
            self._keep_uncached(agent, cfg, items, force, found, seen, limit, checked, round_keys, profiles,
                                wanted_list=True)

    @staticmethod
```

Edit, old:

```
            year = record.get("year", "")
            title = record.get("title") or f"Movie #{record['id']}"
            return {"id": record["id"], "label": f"{title} ({year})" if year else title}
```

new:

```
            year = record.get("year", "")
            title = record.get("title") or f"Movie #{record['id']}"
            return {"id": record["id"], "label": f"{title} ({year})" if year else title,
                    "qualityProfileId": record.get("qualityProfileId")}
```

Edit, old: `    def _collect_monitored(self, agent, cfg, per_run, force, found, seen) -> None:`
new: `    def _collect_monitored(self, agent, cfg, per_run, force, found, seen, checked=False, round_keys=None, profiles=None) -> None:`

Edit, old:

```
            year = movie.get("year", "")
            title = movie.get("title") or f"Movie #{movie['id']}"
            items.append({"id": movie["id"], "label": f"{title} ({year})" if year else title})
        random.shuffle(items)
        self._keep_uncached(agent, cfg, items, force, found, seen, limit)
```

new:

```
            year = movie.get("year", "")
            title = movie.get("title") or f"Movie #{movie['id']}"
            items.append({"id": movie["id"], "label": f"{title} ({year})" if year else title,
                          "qualityProfileId": movie.get("qualityProfileId")})
        random.shuffle(items)
        self._keep_uncached(agent, cfg, items, force, found, seen, limit, checked, round_keys, profiles)
```

- [ ] **Step 4: Tests laufen lassen**

Run: `cd <repo> && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: `586 passed, 2 skipped` (im G3-Worktree auf Stand Welle 1)

- [ ] **Step 5: Commit**

```bash
git add backend/skills/search_upgrades.py tests/test_g3_runner.py
git commit -m "feat: run upgrade searches through the checked search as well"
```

---

## Paket G4 — Modell und API (Welle 2)

Abnahme G4: `tests/test_g4_*.py` grün (`31 passed`); Gesamtsuite im Worktree `529 passed, 2 skipped`; alle Antworten von `/api/instances*` enthalten `checked_search_settings` vollständig (Test), kein Klartext-Schlüssel (bestehender Test `test_api_key_never_leaves_the_server` grün).

### Task G4.1: Instanzmodell mit Checked search und Sonarr-Sperre

**Files:**
- Modify: `backend/models/instance.py`
- Test: `tests/test_g4_models.py`

**Interfaces:**
- Consumes: G1.2 (`CheckedSearchMode`, `CheckedSearchSettings`)
- Produces: `CHECKED_SEARCH_MODE_MESSAGE`, `checked_mode_conflict`; `InstanceCreate.checked_search` (Standard `off`), `.checked_search_settings` (Standard: Voreinstellungen) und `.search_again_after_profile_change` (Standard an); `InstanceUpdate` mit allen drei Feldern optional (`None` = behalten) (Vertrag G4).

Der Modell-Validator prüft die Sonarr-Sperre nur, wenn `checked_search` im Request steht. Beim PUT ohne das Feld prüft die API gegen den gespeicherten Modus (G4.2).

- [ ] **Step 1: Failing test schreiben**

`tests/test_g4_models.py`:

```python
import pytest
from pydantic import ValidationError

from backend.checked_search.settings import CheckedSearchSettings
from backend.models.instance import CHECKED_SEARCH_MODE_MESSAGE, InstanceCreate, InstanceUpdate

BASE = {"name": "Sonarr", "type": "sonarr", "url": "http://sonarr:8989", "api_key": "k" * 32}


def test_create_defaults_to_off_with_default_settings():
    model = InstanceCreate(**BASE)
    assert model.checked_search == "off"
    assert model.checked_search_settings == CheckedSearchSettings()
    assert model.search_again_after_profile_change is True


def test_update_leaves_the_new_fields_unset():
    model = InstanceUpdate(**{k: v for k, v in BASE.items() if k != "api_key"})
    assert model.checked_search is None and model.checked_search_settings is None
    assert model.search_again_after_profile_change is None


@pytest.mark.parametrize("mode", ["smart", "season_packs", "show_batch"])
@pytest.mark.parametrize("checked", ["dry_run", "active"])
def test_sonarr_with_checked_search_allows_only_episodes(mode, checked):
    with pytest.raises(ValidationError) as info:
        InstanceCreate(**BASE, missing_mode=mode, checked_search=checked)
    assert CHECKED_SEARCH_MODE_MESSAGE in info.value.errors()[0]["msg"]


def test_episode_mode_and_radarr_are_fine():
    InstanceCreate(**BASE, missing_mode="episode", checked_search="active")
    InstanceCreate(**{**BASE, "type": "radarr"}, missing_mode="show_batch", checked_search="active")
    InstanceCreate(**BASE, missing_mode="show_batch", checked_search="off")


def test_settings_bounds_reach_the_user():
    with pytest.raises(ValidationError) as info:
        InstanceCreate(**BASE, checked_search_settings={"release_timeout_seconds": 5})
    assert "release_timeout_seconds must be between 10 and 600" in str(info.value)


def test_unknown_mode_is_refused():
    with pytest.raises(ValidationError):
        InstanceCreate(**BASE, checked_search="sometimes")
```

- [ ] **Step 2: Test laufen lassen, er muss scheitern**

Run: `cd <repo> && .venv/bin/python -m pytest tests/test_g4_models.py -q -p no:cacheprovider`
Expected: FAIL mit `ImportError: cannot import name 'CHECKED_SEARCH_MODE_MESSAGE'`

- [ ] **Step 3: Implementieren**

`backend/models/instance.py` — Edit, old:

```
from pydantic import BaseModel, field_validator, model_validator
```

new:

```
from pydantic import BaseModel, Field, field_validator, model_validator

from backend.checked_search.settings import CheckedSearchMode, CheckedSearchSettings
```

Edit, old:

```
NAME_MAX_LENGTH = 100
```

new:

```
NAME_MAX_LENGTH = 100

CHECKED_SEARCH_MODE_MESSAGE = (
    "Checked search works with single episodes only — set Missing Mode to Episode "
    "or switch checked search off"
)


def checked_mode_conflict(arr_type: str, checked_search: str | None, missing_mode: str) -> str | None:
    """Sonarr with checked search on allows only missing_mode 'episode'."""
    if arr_type == "sonarr" and checked_search not in (None, "off") and missing_mode != "episode":
        return CHECKED_SEARCH_MODE_MESSAGE
    return None
```

Edit, old:

```
    rows already in the database are never rejected when they are read."""

    @model_validator(mode="after")
    def _check_bounds(self):
        errors = []
```

new:

```
    rows already in the database are never rejected when they are read."""

    checked_search: Optional[CheckedSearchMode] = None
    checked_search_settings: Optional[CheckedSearchSettings] = None
    search_again_after_profile_change: Optional[bool] = None

    @model_validator(mode="after")
    def _check_bounds(self):
        errors = []
        conflict = checked_mode_conflict(self.type, self.checked_search, self.missing_mode)
        if conflict:
            errors.append(conflict)
```

Edit, old:

```
class InstanceCreate(_InstanceWrite):
    api_key: str
```

new:

```
class InstanceCreate(_InstanceWrite):
    api_key: str
    checked_search: CheckedSearchMode = "off"
    checked_search_settings: CheckedSearchSettings = Field(default_factory=CheckedSearchSettings)
    # Spec addendum: a cached title may be searched again after its quality
    # profile changed. Applies to the command search as well, hence not part
    # of checked_search_settings.
    search_again_after_profile_change: bool = True
```

Edit, old:

```
class InstanceUpdate(_InstanceWrite):
    api_key: Optional[str] = None
```

new:

```
class InstanceUpdate(_InstanceWrite):
    """checked_search / checked_search_settings / search_again_after_profile_change
    left out (None): the stored values stay. The Sonarr mode check then runs
    against the stored mode in the API (update_instance)."""

    api_key: Optional[str] = None
```

- [ ] **Step 4: Tests laufen lassen**

Run: `cd <repo> && .venv/bin/python -m pytest tests/test_g4_models.py tests/test_p1_models.py -q -p no:cacheprovider`
Expected: alle grün (`11` neue)

- [ ] **Step 5: Commit**

```bash
git add backend/models/instance.py tests/test_g4_models.py
git commit -m "feat: add checked-search fields to the instance model and lock Sonarr pack modes"
```

---

### Task G4.2: API — Instanzen, Reset, Liste, CSV

**Files:**
- Create: `backend/api/checked_search.py`
- Modify: `backend/api/instances.py`, `backend/main.py` (nur Import und `include_router`)
- Test: `tests/test_g4_api.py`

**Interfaces:**
- Consumes: G4.1, G2 (`db.instances.reset_dry_run`, `db.checked_search_log.query/count/iter_csv`, `db.activity.insert`), G1.2 (`CheckedSearchSettings.from_stored`)
- Produces: die HTTP-Routen der Tabelle „G4 liefert“; `public_instance` mit vollständigen Einstellungen.

Der Reset schreibt eine Zeile ins Aktivitätslog (`"Dry run reset — every title will be checked again"`, Skill `checked_search`). Die CSV-Route liegt auf `/api/checked-search.csv` (Router ohne Präfix, eingebunden mit `prefix="/api"`). Liste und CSV kennen zusätzlich `current_round` (Voreinstellung `false`, die Seite schickt `true`). Die Liste setzt je Zeile `settings_changed` (Entscheidung Daniel 01.10.2026): Die Zeile trägt einen Einstellungs-Fingerabdruck, und er ist nicht mehr der aktuelle ihrer Instanz (`CheckedSearchSettings.rules_fingerprint`, G1). Das rechnet die API und nicht `db.checked_search_log`, weil G2 in Welle 1 das Einstellungsmodell aus G1 noch nicht kennt. Der Ergebnis-Filter kennt auch `grab_uncertain`.

Löschen während einer geprüften Suche: `DELETE` wartet wie seit 5693201 höchstens `DELETE_WAIT_SECONDS` (15 s) auf das Ende der Suche. Ein laufendes `GET /release` ist nicht unterbrechbar und dauert bis zur „Release search timeout“ (120 s, bis 600 s); das 409 „still stopping“ ist dann richtig, wirkte aber wie ein Fehler. Bei `checked_search != 'off'` bekommt die Meldung deshalb einen Zusatz, der das erklärt.

- [ ] **Step 1: Failing test schreiben**

`tests/test_g4_api.py`:

```python
import csv
import io

import pytest
from fastapi.testclient import TestClient

from backend import database, db
from backend.checked_search.settings import CheckedSearchSettings
from backend.config import settings
from backend.models.instance import CHECKED_SEARCH_MODE_MESSAGE

PASSWORD = "correct horse battery"
HOST = "missingarr.test"
SECRET = "SUPERSECRETKEY1234567890"


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


class FakeOrchestrator:
    def __init__(self):
        self.calls = []
        self.stops_in_time = True

    def get_agent_state(self, instance_id):
        return {}

    def forget_instance(self, instance_id, wait_seconds=15.0):
        self.calls.append(("forget", instance_id))
        return self.stops_in_time

    def start_agent(self, instance_id):
        self.calls.append(("start", instance_id))

    def stop_agent(self, instance_id, abort_running=True, wait_seconds=0.0):
        self.calls.append(("stop", instance_id))

    def reload_agent(self, instance_id):
        self.calls.append(("reload", instance_id))

    def refresh_config(self, instance_id):
        self.calls.append(("refresh", instance_id))

    def stop_all(self):
        pass


@pytest.fixture
def api(client):
    client.app.state.orchestrator.stop_all()
    client.app.state.orchestrator = FakeOrchestrator()
    client.post("/login", data={"username": "admin", "password": PASSWORD, "next": "/"})
    return client


def body(**fields):
    data = {"name": "Sonarr", "type": "sonarr", "url": "http://sonarr:8989", "api_key": SECRET, "enabled": False}
    data.update(fields)
    return data


def log(instance_id, title, outcome="would_grab", mode="dry_run", **extra):
    entry = {"instance_id": instance_id, "run_id": None, "mode": mode, "skill": "search_missing", "arr_id": 1,
             "cache_key": f"mov:{title}", "title": title, "outcome": outcome}
    entry.update(extra)
    db.checked_search_log.insert(entry)


# ── Instances ────────────────────────────────────────────────────────────────

def test_create_with_checked_search_returns_every_setting(api):
    resp = api.post("/api/instances", json=body(type="radarr", checked_search="dry_run",
                                                checked_search_settings={"year_tolerance": 2}))
    assert resp.status_code == 201
    data = resp.json()
    assert data["checked_search"] == "dry_run"
    assert data["checked_search_settings"]["year_tolerance"] == 2
    assert data["checked_search_settings"]["release_timeout_seconds"] == 120
    assert data["dry_run_round_started_at"]
    assert data["search_again_after_profile_change"] is True


def test_search_again_after_profile_change_is_saved_and_kept(api):
    instance_id = api.post("/api/instances", json=body(search_again_after_profile_change=False)).json()["id"]
    assert api.get(f"/api/instances/{instance_id}").json()["search_again_after_profile_change"] is False
    assert api.put(f"/api/instances/{instance_id}", json=body(api_key="", interval_minutes=30)).status_code == 200
    assert db.instances.get_by_id(instance_id)["search_again_after_profile_change"] == 0


def test_delete_during_a_checked_search_explains_the_wait(api):
    checked = api.post("/api/instances", json=body(checked_search="dry_run")).json()["id"]
    plain = api.post("/api/instances", json=body(name="Plain")).json()["id"]
    api.app.state.orchestrator.stops_in_time = False
    resp = api.delete(f"/api/instances/{checked}")
    assert resp.status_code == 409
    assert "release search" in resp.json()["detail"]
    resp = api.delete(f"/api/instances/{plain}")
    assert resp.status_code == 409
    assert "release search" not in resp.json()["detail"]


def test_sonarr_pack_modes_are_refused_with_422(api):
    resp = api.post("/api/instances", json=body(missing_mode="season_packs", checked_search="dry_run"))
    assert resp.status_code == 422
    assert CHECKED_SEARCH_MODE_MESSAGE in resp.json()["detail"][0]["msg"]


def test_update_without_checked_fields_checks_the_stored_mode(api):
    instance_id = api.post("/api/instances", json=body(checked_search="active")).json()["id"]
    resp = api.put(f"/api/instances/{instance_id}", json=body(api_key="", missing_mode="show_batch"))
    assert resp.status_code == 422
    assert resp.json()["detail"] == CHECKED_SEARCH_MODE_MESSAGE
    assert db.instances.get_by_id(instance_id)["missing_mode"] == "episode"


def test_update_without_checked_fields_keeps_them(api):
    instance_id = api.post("/api/instances", json=body(
        checked_search="active", checked_search_settings={"check_suffix": False})).json()["id"]
    assert api.put(f"/api/instances/{instance_id}", json=body(api_key="", interval_minutes=30)).status_code == 200
    stored = db.instances.get_by_id(instance_id)
    assert stored["checked_search"] == "active"
    assert stored["checked_search_settings"]["check_suffix"] is False


def test_switching_off_frees_the_pack_modes(api):
    instance_id = api.post("/api/instances", json=body(checked_search="active")).json()["id"]
    resp = api.put(f"/api/instances/{instance_id}",
                   json=body(api_key="", checked_search="off", missing_mode="season_packs"))
    assert resp.status_code == 200


def test_reset_dry_run(api):
    instance_id = api.post("/api/instances", json=body(type="radarr", checked_search="dry_run")).json()["id"]
    database_value = "2026-01-01 00:00:00.000"
    with database.get_db() as conn:
        conn.execute("UPDATE instances SET dry_run_round_started_at=?", (database_value,))
    resp = api.post(f"/api/instances/{instance_id}/checked-search/reset-dry-run")
    assert resp.status_code == 200
    assert resp.json()["status"] == "reset"
    assert resp.json()["dry_run_round_started_at"] > database_value
    assert api.post("/api/instances/999/checked-search/reset-dry-run").status_code == 404


def test_reset_needs_a_session_and_the_same_origin(client):
    assert client.post("/api/instances/1/checked-search/reset-dry-run").status_code == 401
    client.post("/login", data={"username": "admin", "password": PASSWORD, "next": "/"})
    resp = client.post("/api/instances/1/checked-search/reset-dry-run", headers={"Origin": "http://evil.example"})
    assert resp.status_code == 403


# ── Log list and CSV ─────────────────────────────────────────────────────────

def test_list_filters_and_counts(api):
    instance_id = api.post("/api/instances", json=body(type="radarr")).json()["id"]
    log(instance_id, "Same", arr_pick="A", pick={"title": "A"})
    log(instance_id, "Different", arr_pick="A", pick={"title": "B"})
    log(instance_id, "Grabbed", outcome="grabbed", mode="active")
    resp = api.get("/api/checked-search", params={"limit": 2})
    assert resp.status_code == 200
    assert resp.headers["X-Total-Count"] == "3"
    assert len(resp.json()) == 2
    assert [r["title"] for r in api.get("/api/checked-search", params={"only_differences": "true"}).json()] == [
        "Different"]
    assert api.get("/api/checked-search", params={"mode": "active"}).headers["X-Total-Count"] == "1"
    assert api.get("/api/checked-search", params={"q": "same"}).headers["X-Total-Count"] == "1"


def test_list_and_csv_can_show_the_current_round_only(api):
    instance_id = api.post("/api/instances", json=body(type="radarr", checked_search="dry_run")).json()["id"]
    log(instance_id, "Old round", dry_run_round=0)
    assert api.post(f"/api/instances/{instance_id}/checked-search/reset-dry-run").status_code == 200
    log(instance_id, "New round", dry_run_round=1)
    assert api.get("/api/checked-search").headers["X-Total-Count"] == "2"
    resp = api.get("/api/checked-search", params={"current_round": "true"})
    assert [r["title"] for r in resp.json()] == ["New round"]
    csv_text = api.get("/api/checked-search.csv", params={"current_round": "true"}).text
    assert "New round" in csv_text and "Old round" not in csv_text


def test_list_marks_rows_checked_under_other_rule_settings(api):
    instance_id = api.post("/api/instances", json=body(type="radarr", checked_search="dry_run")).json()["id"]
    current = CheckedSearchSettings().rules_fingerprint("radarr")
    log(instance_id, "Same settings", settings_fingerprint=current, dry_run_round=1)
    log(instance_id, "Before the change", settings_fingerprint="0000000000000000", dry_run_round=1)
    log(instance_id, "Unknown", dry_run_round=1)
    rows = {r["title"]: r["settings_changed"] for r in api.get("/api/checked-search").json()}
    assert rows == {"Same settings": False, "Before the change": True, "Unknown": False}
    assert api.put(f"/api/instances/{instance_id}", json=body(
        type="radarr", api_key="", checked_search="dry_run",
        checked_search_settings={"year_tolerance": 2})).status_code == 200
    rows = {r["title"]: r["settings_changed"] for r in api.get("/api/checked-search").json()}
    assert rows == {"Same settings": True, "Before the change": True, "Unknown": False}
    assert api.get("/api/checked-search", params={"outcome": "grab_uncertain"}).status_code == 200


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"offset": -1}, {"mode": "maybe"},
                                    {"outcome": "lost"}, {"q": "x" * 201}])
def test_bad_parameters_are_refused(api, params):
    assert api.get("/api/checked-search", params=params).status_code == 422


def test_csv_download_uses_the_filter(api):
    instance_id = api.post("/api/instances", json=body(type="radarr")).json()["id"]
    log(instance_id, "Keep", outcome="no_clean_hit", candidates=[
        {"title": "Rel.2020", "indexer": "Idx", "score": 5, "size": 1, "quality": "WEBDL-1080p",
         "verdict": "reject", "reasons": ["year"], "notes": [], "chosen": False, "arr_choice": True}])
    log(instance_id, "Drop", outcome="would_grab")
    resp = api.get("/api/checked-search.csv", params={"outcome": "no_clean_hit"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert 'attachment; filename="checked-search.csv"' == resp.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(resp.text)))
    assert rows[0][:4] == ["time", "instance", "mode", "title"]
    assert [r[3] for r in rows[1:]] == ["Keep"]


def test_list_needs_a_session(client):
    assert client.get("/api/checked-search").status_code == 401
    assert client.get("/api/checked-search.csv").status_code == 401
```

- [ ] **Step 2: Test laufen lassen, er muss scheitern**

Run: `cd <repo> && .venv/bin/python -m pytest tests/test_g4_api.py -q -p no:cacheprovider`
Expected: FAIL (u. a. `404` für die neuen Routen, Antworten ohne vollständige `checked_search_settings`)

- [ ] **Step 3: Router für Liste und CSV**

`backend/api/checked_search.py`:

```python
from typing import Literal, Optional

from fastapi import APIRouter, Query, Response
from fastapi.responses import StreamingResponse

from backend import db
from backend.checked_search.settings import CheckedSearchSettings

router = APIRouter()

Mode = Literal["dry_run", "active"]
Outcome = Literal["grabbed", "would_grab", "no_clean_hit", "no_results", "error", "grab_failed", "grab_uncertain"]


def _filters(instance_id, mode, outcome, q, only_differences, current_round) -> dict:
    return {
        "instance_id": instance_id,
        "mode": mode,
        "outcome": outcome,
        "search": (q or "").strip() or None,
        "only_differences": only_differences,
        "current_round": current_round,
    }


@router.get("/checked-search")
def list_checked_search(
    response: Response,
    instance_id: Optional[int] = None,
    mode: Optional[Mode] = None,
    outcome: Optional[Outcome] = None,
    q: Optional[str] = Query(None, max_length=200),
    only_differences: bool = False,
    current_round: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Pre-filter log, one row per title; X-Total-Count = matches without limit/offset.
    current_round: dry-run rows of the running round only (active rows always)."""
    filters = _filters(instance_id, mode, outcome, q, only_differences, current_round)
    response.headers["X-Total-Count"] = str(db.checked_search_log.count(**filters))
    return _mark_settings_changes(db.checked_search_log.query(**filters, limit=limit, offset=offset))


def _mark_settings_changes(rows: list[dict]) -> list[dict]:
    """settings_changed: the row was checked under rule settings its instance
    no longer has — a dry run checks the title again (badge on the page)."""
    current: dict = {}
    for row in rows:
        instance_id = row.get("instance_id")
        if instance_id not in current:
            inst = db.instances.get_by_id(instance_id) if instance_id is not None else None
            current[instance_id] = (
                CheckedSearchSettings.from_stored(inst.get("checked_search_settings"))
                .rules_fingerprint(inst.get("type") or "") if inst else None
            )
        stored = row.get("settings_fingerprint")
        row["settings_changed"] = bool(stored and current[instance_id] and stored != current[instance_id])
    return rows


@router.get("/checked-search.csv")
def checked_search_csv(
    instance_id: Optional[int] = None,
    mode: Optional[Mode] = None,
    outcome: Optional[Outcome] = None,
    q: Optional[str] = Query(None, max_length=200),
    only_differences: bool = False,
    current_round: bool = False,
):
    """The same filter as the list, one line per checked release."""
    filters = _filters(instance_id, mode, outcome, q, only_differences, current_round)
    return StreamingResponse(
        db.checked_search_log.iter_csv(**filters),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="checked-search.csv"'},
    )
```

- [ ] **Step 4: Instanz-API**

`backend/api/instances.py` — Edit, old:

```
from backend.agents.orchestrator import TRIGGER_NOT_FOUND
from backend.models.instance import InstanceCreate, InstanceUpdate
```

new:

```
from backend.agents.orchestrator import TRIGGER_NOT_FOUND
from backend.checked_search.settings import CheckedSearchSettings
from backend.models.instance import InstanceCreate, InstanceUpdate, checked_mode_conflict
```

Edit, old:

```
    out["api_key_set"] = bool(inst.get("api_key"))
    out["api_key"] = API_KEY_MASK if out["api_key_set"] else ""
    return out
```

new:

```
    out["api_key_set"] = bool(inst.get("api_key"))
    out["api_key"] = API_KEY_MASK if out["api_key_set"] else ""
    # Every setting with its effective value, defaults filled in.
    out["checked_search"] = inst.get("checked_search") or "off"
    out["checked_search_settings"] = CheckedSearchSettings.from_stored(
        inst.get("checked_search_settings")).model_dump()
    out["search_again_after_profile_change"] = bool(inst.get("search_again_after_profile_change", 1))
    return out
```

Edit, old:

```
    if not db.instances.get_by_id(instance_id):
        raise HTTPException(404, "Instance not found")
    # Abort a running search and wait for it before the row (and its foreign
```

new:

```
    inst = db.instances.get_by_id(instance_id)
    if not inst:
        raise HTTPException(404, "Instance not found")
    # Abort a running search and wait for it before the row (and its foreign
```

Edit, old:

```
        raise HTTPException(409, "A search of this instance is still stopping — try deleting again in a moment.")
    db.instances.delete(instance_id)
```

new:

```
        message = "A search of this instance is still stopping — try deleting again in a moment."
        if (inst.get("checked_search") or "off") != "off":
            # GET /release cannot be interrupted; it ends with the release search timeout.
            message += (" A checked search waits for *arr's release search to finish "
                        "(up to the release search timeout) before it stops.")
        raise HTTPException(409, message)
    db.instances.delete(instance_id)
```

Edit, old:

```
        raise HTTPException(400, "The URL changed — enter the API key again so it is not sent to a new address.")
    inst = db.instances.update(instance_id, payload)
```

new:

```
        raise HTTPException(400, "The URL changed — enter the API key again so it is not sent to a new address.")
    # Left out of the request: the stored mode counts for the Sonarr check.
    effective = payload.get("checked_search") or existing.get("checked_search") or "off"
    conflict = checked_mode_conflict(payload["type"], effective, payload["missing_mode"])
    if conflict:
        raise HTTPException(422, conflict)
    inst = db.instances.update(instance_id, payload)
```

Edit, old: `@router.post("/{instance_id}/trigger")`
new:

```
@router.post("/{instance_id}/checked-search/reset-dry-run")
def reset_dry_run(instance_id: int):
    """Start a new dry-run round: every title is checked once more."""
    inst = db.instances.get_by_id(instance_id)
    if not inst:
        raise HTTPException(404, "Instance not found")
    started = db.instances.reset_dry_run(instance_id)
    db.activity.insert(instance_id, inst["name"], "info",
                       "Dry run reset — every title will be checked again", "checked_search")
    return {"status": "reset", "dry_run_round_started_at": started}


@router.post("/{instance_id}/trigger")
```

`backend/main.py` — Edit, old: `from backend.api import health, instances, activity, history, searched`
new: `from backend.api import health, instances, activity, history, searched, checked_search`

Edit, old: `app.include_router(searched.router, prefix="/api")`
new:

```
app.include_router(searched.router, prefix="/api")
app.include_router(checked_search.router, prefix="/api")
```

- [ ] **Step 5: Tests laufen lassen**

Run: `cd <repo> && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: `529 passed, 2 skipped` (im G4-Worktree auf Stand Welle 1)

- [ ] **Step 6: Commit**

```bash
git add backend/api/checked_search.py backend/api/instances.py backend/main.py tests/test_g4_api.py
git commit -m "feat: add the checked-search API: settings, Sonarr lock, reset, log list and CSV"
```

### Wellen-Abnahme 2

Im Haupt-Arbeitsbaum: `git merge --no-ff feat/checked-search-g3`, `git merge --no-ff feat/checked-search-g4`, dann
Run: `cd <repo> && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: `617 passed, 2 skipped`. Danach Worktrees und Zweige `g3`, `g4` entfernen.

---

## Paket G5 — Oberfläche (Welle 3, im Haupt-Arbeitsbaum)

Abnahme G5: `tests/test_g5_*.py` grün (`14 passed`; die zwei Node-Tests überspringen sich ohne Node); Gesamtsuite `631 passed, 2 skipped`; keine Serverdaten in Inline-JavaScript (die Tests prüfen alle `@`/`x-`/`:`/`on*`-Attribute mit einem feindlichen Instanznamen); Handprobe im Browser in Task Z.

### Task G5.1: Tooltips und Formularabschnitt „Checked search“

**Files:**
- Modify: `backend/tooltips.py`, `backend/main.py` (Import, `checked_search_form()`, Kontext der beiden Formular-Routen), `templates/instances/form.html`
- Test: `tests/test_g5_form.py`

**Interfaces:**
- Consumes: G1.2 (`GENERAL_FIELDS`, `RADARR_FIELDS`, `SONARR_FIELDS`, `FIELD_LABELS`, `SETTING_BOUNDS`, `CheckedSearchSettings`), G4 (`public_instance` liefert `checked_search` und vollständige `checked_search_settings`; Server-Sperre 422)
- Produces: `TOOLTIPS["checked_search"]`, `TOOLTIPS["cs_<feld>"]`, `TOOLTIPS["search_again_after_profile_change"]`; `checked_search_form()`; im Formular je Einstellung ein Eingabefeld `id="cs_<feld>"` mit `data-cs-field`/`data-cs-kind` und Info-Symbol; Kästchen „Search again after profile changes“ (`id="search_again_after_profile_change"`) im Abschnitt „Timing“ neben „Retry“, weil es den Such-Cache beider Suchwege betrifft und auch bei Checked search = Off sichtbar sein muss; JS-Funktion `checkedSearchSettings(form)`; Alpine-Methode `packsLocked()`.

Aufbau: Auswahl Off / Dry run / Active mit Info-Symbol; darunter (nur wenn nicht Off) die allgemeinen Einstellungen, die Radarr-Regeln (nur bei Typ Radarr) bzw. Sonarr-Regeln (nur bei Typ Sonarr) und einmal „Skip the release of the existing file“ (gilt für beide). Ausgeblendete Felder bleiben im Formular und werden mitgeschickt, damit die Werte der anderen App erhalten bleiben. Bei Sonarr mit Checked search ≠ Off sind die Modi Smart, Season Packs, Show Batch gesperrt und das Feld springt auf Episode; der Server lehnt sie zusätzlich mit 422 ab (G4).

- [ ] **Step 1: Failing test schreiben**

`tests/test_g5_form.py`:

```python
import html.parser
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import database, db
from backend.checked_search.settings import SETTING_BOUNDS, CheckedSearchSettings
from backend.config import settings
from backend.tooltips import TOOLTIPS

PASSWORD = "correct horse battery"
HOST = "missingarr.test"
NODE = shutil.which("node")
ROOT = Path(__file__).resolve().parent.parent


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
    data = {"name": "Radarr", "type": "radarr", "url": "http://127.0.0.1:9",
            "api_key": "SUPERSECRETKEY1234567890", "enabled": False}
    data.update(fields)
    return db.instances.create(data)


class Collector(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, {name: value or "" for name, value in attrs}))


def tags(text):
    collector = Collector()
    collector.feed(text)
    return collector.tags


def by_id(text):
    return {attrs["id"]: (tag, attrs) for tag, attrs in tags(text) if "id" in attrs}


def component_script(page, marker):
    at = page.index(marker)
    start = page.rindex("<script>", 0, at) + len("<script>")
    return page[start:page.index("</script>", at)]


def run_node(tmp_path, script, body):
    case = tmp_path / "case.js"
    case.write_text(
        "const vm = require('vm');\n"
        f"vm.runInThisContext({json.dumps(script)});\n"
        "const out = {};\n(async () => {\n" + body +
        "\nconsole.log(JSON.stringify(out));\n})().catch(e => { console.error(e); process.exit(1); });\n")
    result = subprocess.run([NODE, str(case)], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_every_setting_has_an_english_tooltip():
    for field in CheckedSearchSettings.model_fields:
        assert TOOLTIPS.get(f"cs_{field}"), field
    assert "Dry run" in TOOLTIPS["checked_search"] and "Active" in TOOLTIPS["checked_search"]
    assert "profile" in TOOLTIPS["search_again_after_profile_change"]


@pytest.mark.parametrize("stored", [True, False])
def test_search_again_after_profile_changes_is_in_the_form(client, stored):
    inst = make_instance(search_again_after_profile_change=stored)
    page = client.get(f"/instances/{inst['id']}/edit").text
    tag, attrs = by_id(page)["search_again_after_profile_change"]
    assert (tag, attrs["type"]) == ("input", "checkbox")
    assert ("checked" in attrs) is stored
    icons = [a["data-tooltip"] for t, a in tags(page) if t == "span" and "tooltip-icon" in a.get("class", "")]
    assert TOOLTIPS["search_again_after_profile_change"] in icons
    assert "search_again_after_profile_change: form.search_again_after_profile_change.checked" in page


@pytest.mark.parametrize("path_kind", ["new", "edit"])
def test_form_has_the_checked_search_section_with_info_icons(client, path_kind):
    path = "/instances/new" if path_kind == "new" else f"/instances/{make_instance()['id']}/edit"
    page = client.get(path).text
    assert "Checked search" in page
    elements = by_id(page)
    assert {a["value"] for t, a in tags(page) if t == "option" and a.get("value") in ("off", "dry_run", "active")} \
        == {"off", "dry_run", "active"}
    for field in CheckedSearchSettings.model_fields:
        tag, attrs = elements[f"cs_{field}"]
        assert attrs["data-cs-field"] == field
        if field in SETTING_BOUNDS:
            assert (attrs["min"], attrs["max"]) == tuple(str(v) for v in SETTING_BOUNDS[field])
    icons = [a["data-tooltip"] for t, a in tags(page) if t == "span" and "tooltip-icon" in a.get("class", "")]
    assert TOOLTIPS["checked_search"] in icons
    for field in CheckedSearchSettings.model_fields:
        assert TOOLTIPS[f"cs_{field}"] in icons, field
    assert page.count('id="cs_skip_existing_file"') == 1


def test_edit_form_shows_the_stored_settings(client):
    inst = make_instance(checked_search="dry_run", checked_search_settings={"year_tolerance": 3,
                                                                              "country_codes": ["US", "DE"]})
    elements = by_id(client.get(f"/instances/{inst['id']}/edit").text)
    assert elements["cs_year_tolerance"][1]["value"] == "3"
    assert elements["cs_country_codes"][1]["value"] == "US, DE"
    assert elements["cs_release_timeout_seconds"][1]["value"] == "120"


def test_pack_modes_are_locked_while_checked_search_is_on(client):
    page = client.get("/instances/new").text
    locked = {a["value"] for t, a in tags(page) if t == "option" and a.get(":disabled") == "packsLocked()"}
    assert locked == {"smart", "season_packs", "show_batch"}
    assert "Checked search works with single episodes only." in page


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_form_sends_every_setting_with_its_type(client, tmp_path):
    script = component_script(client.get("/instances/new").text, "function checkedSearchSettings(form)")
    script = script[:script.index("document.getElementById('instance-form')")]
    out = run_node(tmp_path, script, """
const fields = [
  { dataset: { csField: 'year_tolerance', csKind: 'int' }, value: '2' },
  { dataset: { csField: 'prefix_match', csKind: 'bool' }, checked: false },
  { dataset: { csField: 'country_codes', csKind: 'list' }, value: 'US, de;  CO' },
];
out.settings = checkedSearchSettings({ querySelectorAll: () => fields });
""")
    assert out["settings"] == {"year_tolerance": 2, "prefix_match": False, "country_codes": ["US", "de", "CO"]}
```

- [ ] **Step 2: Test laufen lassen, er muss scheitern**

Run: `cd <repo> && .venv/bin/python -m pytest tests/test_g5_form.py -q -p no:cacheprovider`
Expected: FAIL (fehlende Tooltips, `KeyError: 'cs_release_timeout_seconds'` beim Formular)

- [ ] **Step 3: Tooltips**

`backend/tooltips.py` — Edit, old:

```
    "quiet_end": "End of quiet hours (HH:MM). Force runs from the dashboard always bypass quiet hours.",
}
```

new:

```
    "quiet_end": "End of quiet hours (HH:MM). Force runs from the dashboard always bypass quiet hours.",
    # Checked search (0.9.0)
    "checked_search": (
        "Instead of telling *arr to search and grab, missingarr fetches the search results itself, "
        "checks every approved release with the pre-filter below and grabs only the first clean one.\n"
        "• Off: searches as before (search command, *arr grabs its first approved release)\n"
        "• Dry run: searches and checks, but grabs nothing and remembers nothing. Every title is checked "
        "once per round (a Force Run too) and again after its quality profile or the rules below changed; "
        "see the Pre-filter page, reset the round there.\n"
        "• Active: grabs the first release that passes every rule, for exactly this title. No clean release: "
        "the title counts as searched, Retry decides when it is searched again.\n"
        "A run pauses (searches nothing) while an indexer has Automatic Search and Interactive Search set "
        "differently in *arr.\n"
        "Sonarr: single episodes only — the pack modes are locked while this is on."
    ),
    "cs_release_timeout_seconds": "How long to wait for *arr's release search (GET /release runs the indexer search before it answers). 10 to 600 seconds. A timeout counts as a failed search; the title is tried again next run.",
    "cs_time_budget_minutes": "No new title is started once a run has been going this long (1 to 1440 minutes). The rest waits for the next run.",
    "cs_dry_run_max_releases": "Dry run only: check at most this many approved releases per title (1 to 1000). The rest are listed as unchecked.",
    "cs_search_again_after_days": "Active only: a title the checked search grabbed that is still in the Wanted list (missing, or cutoff unmet) after this many days is searched again, checked, whatever Retry says. 1 to 365 days. It takes the place of *arr's \"Redownload Failed from Interactive Search\" — switch that off in Radarr and Sonarr: a grab through the API counts as interactive.",
    "cs_year_tolerance": "Radarr rule a: the year in the release name may be this many years away from a year of the movie (year, secondary year and — if switched on — the years of its release dates). 0 to 10.",
    "cs_count_release_dates": "Radarr rule a: also accept the years of the cinema, digital and physical release as the movie's years.",
    "cs_veto_other_movie": "Radarr rule b: reject a release with a year that Radarr's /parse assigns to another movie of your library.",
    "cs_prefix_match": "Radarr rule c: a release title also fits when it starts with a name of the movie, or the other way round (both at least the minimum length).",
    "cs_prefix_min_length": "Radarr rule c: minimum length (letters and digits) of both titles for the prefix match. 1 to 50.",
    "cs_word_match": "Radarr rule c: a release title also fits when it contains every word of a name of the movie that has enough core words.",
    "cs_word_min_core_words": "Radarr rule c: a name counts for the word match only with at least this many core words (words other than the, a, an, der, die, das, and, und, of, le, la, les, el, il). 1 to 10.",
    "cs_no_year_needs_exact": "Radarr rule d: a release without a year must match a name of the movie exactly, or /parse must assign it to this movie.",
    "cs_skip_existing_file": "Never grab the release the existing file came from again (same scene name or file name). Protects upgrades from grabbing the same release in a loop.",
    "cs_veto_other_series": "Sonarr rule S1: reject a release that Sonarr's /parse assigns to another series of your library.",
    "cs_check_suffix": "Sonarr rule S2: a year or country code at the end of the parsed series title must fit the series — the year within the tolerance of the series year, the country code also at the end of the series title or an alternate title.",
    "cs_country_codes": "Sonarr rule S2: country codes that count as a suffix, separated by commas (two or three letters).",
    "cs_suffix_year_tolerance": "Sonarr rule S2: how many years a year suffix may be away from the series year. 0 to 10.",
    "cs_reject_days_before_air": "Sonarr rule S3: reject a release published more than this many days before the episode aired (a sign of a different numbering). 0 to 36500.",
    "cs_note_days_before_air": "Sonarr rule S3: releases published at least this many days (but not more than the reject limit) before the episode aired are only noted in the log. 0 to 36500.",
    "search_again_after_profile_change": (
        "A searched title stays in the Searched cache (see Retry). With this on, it may be searched again as soon "
        "as its quality profile changes in *arr — scores, qualities, custom formats or release profiles. "
        "Every run compares a fingerprint of the profiles; a changed custom format, release profile, quality "
        "size limit (Settings → Quality) or indexer setting (minimum age, maximum size, retention) counts as "
        "a change of every profile. Titles searched before 0.9.0 are released only by the next change. "
        "Off: cached titles stay blocked whatever the profile. A dry run always checks a title again after a "
        "profile change."
    ),
}
```

- [ ] **Step 4: Formular-Kontext in `main.py`**

`backend/main.py` — Edit, old:

```
from backend.api.instances import public_instance
from backend.models.instance import FIELD_BOUNDS
```

new:

```
from backend.api.instances import public_instance
from backend.checked_search.settings import (
    FIELD_LABELS, GENERAL_FIELDS, RADARR_FIELDS, SETTING_BOUNDS, SONARR_FIELDS, CheckedSearchSettings,
)
from backend.models.instance import FIELD_BOUNDS
```

Edit, old: `def template_ctx(request: Request, **extra) -> dict:`
new:

```
def checked_search_form() -> dict:
    """Field groups of the form section "Checked search". A rule both apps
    have (skip_existing_file) is rendered once, outside the app groups."""
    both = [f for f in RADARR_FIELDS if f in SONARR_FIELDS]
    defaults = CheckedSearchSettings().model_dump()
    return {
        "general": list(GENERAL_FIELDS),
        "radarr": [f for f in RADARR_FIELDS if f not in both],
        "sonarr": [f for f in SONARR_FIELDS if f not in both],
        "both": both,
        "labels": FIELD_LABELS,
        "bounds": SETTING_BOUNDS,
        "defaults": defaults,
        "kinds": {
            name: "bool" if isinstance(value, bool) else "list" if isinstance(value, list) else "int"
            for name, value in defaults.items()
        },
    }


def template_ctx(request: Request, **extra) -> dict:
```

Edit, old:

```
        template_ctx(request, instance=None, action="/api/instances", method="POST", bounds=FIELD_BOUNDS),
```

new:

```
        template_ctx(request, instance=None, action="/api/instances", method="POST", bounds=FIELD_BOUNDS,
                     cs=checked_search_form()),
```

Edit, old:

```
        template_ctx(request, instance=public_instance(inst), action=f"/api/instances/{instance_id}",
                     method="PUT", bounds=FIELD_BOUNDS),
```

new:

```
        template_ctx(request, instance=public_instance(inst), action=f"/api/instances/{instance_id}",
                     method="PUT", bounds=FIELD_BOUNDS, cs=checked_search_form()),
```

- [ ] **Step 5: Formular**

`templates/instances/form.html` — Edit, old:

```
            upgradesEnabled: false,

            showMissingMode() { return this.type === 'sonarr'; },
```

new:

```
            upgradesEnabled: false,
            checkedSearch: 'off',

            showMissingMode() { return this.type === 'sonarr'; },
            // Checked search handles single episodes only (Sonarr).
            packsLocked() { return this.type === 'sonarr' && this.checkedSearch !== 'off'; },
```

Edit, old:

```
    <div class="card" x-data="instanceForm('{{ instance.type if instance else 'sonarr' }}')"
         x-init="
             enabledCheck     = {{ 'true' if not instance or instance.enabled else 'false' }};
```

new:

```
    <div class="card" x-data="instanceForm('{{ instance.type if instance else 'sonarr' }}')"
         data-checked-search="{{ instance.checked_search if instance else 'off' }}"
         x-init="
             checkedSearch    = $el.dataset.checkedSearch || 'off';
             enabledCheck     = {{ 'true' if not instance or instance.enabled else 'false' }};
```

Edit, old:

```
                        <select class="form-control" id="missing_mode" name="missing_mode">
                            {% set mm = instance.missing_mode if instance else 'episode' %}
                            <option value="smart" {% if mm == 'smart' %}selected{% endif %}>Smart</option>
                            <option value="season_packs" {% if mm == 'season_packs' %}selected{% endif %}>Season Packs</option>
                            <option value="show_batch" {% if mm == 'show_batch' %}selected{% endif %}>Show Batch</option>
                            <option value="episode" {% if mm == 'episode' %}selected{% endif %}>Episode</option>
                        </select>
```

new:

```
                        <select class="form-control" id="missing_mode" name="missing_mode" x-ref="missingMode"
                                x-effect="if (packsLocked()) $refs.missingMode.value = 'episode'">
                            {% set mm = instance.missing_mode if instance else 'episode' %}
                            <option value="smart" :disabled="packsLocked()" {% if mm == 'smart' %}selected{% endif %}>Smart</option>
                            <option value="season_packs" :disabled="packsLocked()" {% if mm == 'season_packs' %}selected{% endif %}>Season Packs</option>
                            <option value="show_batch" :disabled="packsLocked()" {% if mm == 'show_batch' %}selected{% endif %}>Show Batch</option>
                            <option value="episode" {% if mm == 'episode' %}selected{% endif %}>Episode</option>
                        </select>
                        <div class="form-hint" x-show="packsLocked()">Checked search works with single episodes only.</div>
```

Edit, old:

```
                               value="{{ instance.retry_hours if instance else 0 }}">
                    </div>
                </div>
```

new:

```
                               value="{{ instance.retry_hours if instance else 0 }}">
                    </div>
                </div>
                <div class="form-group">
                    <label class="form-label" for="search_again_after_profile_change"
                           style="display:flex;align-items:center;gap:0.5rem;">
                        <input type="checkbox" id="search_again_after_profile_change"
                               name="search_again_after_profile_change"
                               {% if not instance or instance.search_again_after_profile_change %}checked{% endif %}>
                        Search again after profile changes
                        <span class="tooltip-icon" data-tooltip="{{ tooltips.search_again_after_profile_change }}">?</span>
                    </label>
                </div>
```

Edit, old: `            <!-- Submit -->`
new:

```
            <!-- Checked search (0.9.0) -->
            <div class="form-section">
                <div class="form-section-title">Checked search</div>
                <div class="form-group">
                    <label class="form-label" for="checked_search">
                        Checked search
                        <span class="tooltip-icon" data-tooltip="{{ tooltips.checked_search }}">?</span>
                    </label>
                    <select class="form-control" id="checked_search" name="checked_search" x-model="checkedSearch">
                        {% set csm = instance.checked_search if instance else 'off' %}
                        <option value="off" {% if csm == 'off' %}selected{% endif %}>Off</option>
                        <option value="dry_run" {% if csm == 'dry_run' %}selected{% endif %}>Dry run</option>
                        <option value="active" {% if csm == 'active' %}selected{% endif %}>Active</option>
                    </select>
                    <div class="form-hint" x-show="checkedSearch === 'dry_run'">
                        Dry run grabs nothing and remembers nothing — review the decisions on the Pre-filter page.
                    </div>
                </div>
                {% set cs_values = instance.checked_search_settings if instance else cs.defaults %}
                {% macro cs_field(name) %}
                    {% set kind = cs.kinds[name] %}
                    {% set value = cs_values.get(name, cs.defaults[name]) %}
                    <div class="form-group">
                        {% if kind == 'bool' %}
                        <label class="form-label" for="cs_{{ name }}" style="display:flex;align-items:center;gap:0.5rem;">
                            <input type="checkbox" id="cs_{{ name }}" data-cs-field="{{ name }}" data-cs-kind="bool"
                                   {% if value %}checked{% endif %}>
                            {{ cs.labels[name] }}
                            <span class="tooltip-icon" data-tooltip="{{ tooltips['cs_' ~ name] }}">?</span>
                        </label>
                        {% else %}
                        <label class="form-label" for="cs_{{ name }}">
                            {{ cs.labels[name] }}
                            <span class="tooltip-icon" data-tooltip="{{ tooltips['cs_' ~ name] }}">?</span>
                        </label>
                        {% if kind == 'list' %}
                        <input class="form-control" type="text" id="cs_{{ name }}" data-cs-field="{{ name }}"
                               data-cs-kind="list" value="{{ value | join(', ') }}">
                        {% else %}
                        <input class="form-control" type="number" id="cs_{{ name }}" data-cs-field="{{ name }}"
                               data-cs-kind="int" min="{{ cs.bounds[name][0] }}" max="{{ cs.bounds[name][1] }}"
                               step="1" value="{{ value }}">
                        {% endif %}
                        {% endif %}
                    </div>
                {% endmacro %}
                <div x-show="checkedSearch !== 'off'">
                    <div class="form-row">
                        {% for name in cs.general %}{{ cs_field(name) }}{% endfor %}
                    </div>
                    <div x-show="type === 'radarr'">
                        <div class="form-hint" style="margin-bottom:0.5rem;">Radarr rules (pre-filter V6)</div>
                        {% for name in cs.radarr %}{{ cs_field(name) }}{% endfor %}
                    </div>
                    <div x-show="type === 'sonarr'">
                        <div class="form-hint" style="margin-bottom:0.5rem;">Sonarr rules (S1–S4, not measured yet — check them in a dry run)</div>
                        {% for name in cs.sonarr %}{{ cs_field(name) }}{% endfor %}
                    </div>
                    {% for name in cs.both %}{{ cs_field(name) }}{% endfor %}
                </div>
            </div>

            <!-- Submit -->
```

Edit, old: `function normalizedUrl(value) {`
new:

```
// Every field of the section, hidden ones too: the settings of the other
// app keep their values.
function checkedSearchSettings(form) {
    const settings = {};
    form.querySelectorAll('[data-cs-field]').forEach(function (el) {
        const kind = el.dataset.csKind;
        if (kind === 'bool') settings[el.dataset.csField] = el.checked;
        else if (kind === 'list') settings[el.dataset.csField] = el.value.split(/[\s,;]+/).filter(Boolean);
        else settings[el.dataset.csField] = parseInt(el.value, 10);
    });
    return settings;
}

function normalizedUrl(value) {
```

Edit, old:

```
        quiet_end: form.quiet_end.value || null,
    };
```

new:

```
        quiet_end: form.quiet_end.value || null,
        search_again_after_profile_change: form.search_again_after_profile_change.checked,
        checked_search: form.checked_search.value,
        checked_search_settings: checkedSearchSettings(form),
    };
```

- [ ] **Step 6: Tests laufen lassen**

Run: `cd <repo> && .venv/bin/python -m pytest tests/test_g5_form.py tests/test_p5_form.py tests/test_p4_pages.py -q -p no:cacheprovider`
Expected: alle grün (`8` neue)

- [ ] **Step 7: Commit**

```bash
git add backend/tooltips.py backend/main.py templates/instances/form.html tests/test_g5_form.py
git commit -m "feat: add the Checked search section with info icons to the instance form"
```

---

### Task G5.2: Seite „Pre-filter“, Menü, Karte, History, Hilfe

**Files:**
- Create: `templates/checked_search.html`
- Modify: `backend/main.py` (Route `/checked-search`), `templates/base.html`, `templates/instances/card.html`, `templates/history.html`, `templates/help.html`
- Test: `tests/test_g5_pages.py`

**Interfaces:**
- Consumes: G2 (`db.checked_search_log.summary`), G4 (`GET /api/checked-search`, `GET /api/checked-search.csv`, `POST /api/instances/{id}/checked-search/reset-dry-run`, `public_instance`)
- Produces: Seite `/checked-search` mit Alpine-Komponente `checkedSearchPage()` (`load`, `reload`, `goTo`, `csvHref`, `resetDryRun(id, name)`, `toggle(id)`); Menüpunkt „Pre-filter“; Karten-Plakette „dry run“/„checked“ (`data-checked-badge`); History-Labels `grabbed: 'geladen'`, `no_hit: 'kein sauberer Treffer'`.

Die Seite zeigt oben die Zähler je Instanz, Modus und Ergebnis der **laufenden Runde** (serverseitig gerendert, `summary(current_round=True)`; aktive Zeilen zählen immer) und Knöpfe „Reset dry run“ für jede Instanz mit Checked search ≠ Off (Name nur in `data-name`). Die Seite fragt nicht regelmäßig nach: Nach einem erfolgreichen Reset entfernt `resetDryRun` die Zählerzeilen dieser Instanz im Modus Probelauf (`data-summary-instance`/`data-summary-mode` an jeder Zeile; die neue Runde hat noch keine) und lädt die Liste ab Seite 1 neu (`reload()`), sonst blieben Zeilen, Summe und Zähler der alten Runde stehen (Codex-Prüfung 0.9.0). Ein gescheiterter Reset ändert nichts. Darunter Filter (Text, Instanz, Modus, Ergebnis, „only differences“, „current round only“ — voreingestellt an), Tabelle (Time, Instance, Mode, Title, Outcome, *arr would grab, Filter grabs, Rejected), serverseitig geblättert wie die History; ein Titel, dessen Zeile unter einem inzwischen geänderten Profil geprüft wurde, trägt die Plakette „profile changed“ (Spec-Nachtrag); ein Klick auf eine Zeile klappt die Kandidaten auf (Release, Indexer, Score, Size, Quality, Verdict, Reasons, Notes, Markierung „*arr“ und „filter“). Der CSV-Link trägt den aktuellen Filter und hat `hx-boost="false"` (sonst lädt htmx die Datei per AJAX).

- [ ] **Step 1: Failing test schreiben**

`tests/test_g5_pages.py`:

```python
import html.parser
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import database, db
from backend.config import settings

PASSWORD = "correct horse battery"
HOST = "missingarr.test"
HOSTILE = "x'); alert(1);//\"><img src=x onerror=alert(2)>"
NODE = shutil.which("node")
ROOT = Path(__file__).resolve().parent.parent


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
    data = {"name": "Radarr", "type": "radarr", "url": "http://127.0.0.1:9",
            "api_key": "SUPERSECRETKEY1234567890", "enabled": False}
    data.update(fields)
    return db.instances.create(data)


class Collector(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, {name: value or "" for name, value in attrs}))


def tags(text):
    collector = Collector()
    collector.feed(text)
    return collector.tags


def script_attributes(text):
    """Attributes whose value the browser or Alpine runs as JavaScript."""
    return [(tag, name, value) for tag, attrs in tags(text) for name, value in attrs.items()
            if name.startswith(("on", "@", "x-", ":"))]


def component_script(page, marker):
    at = page.index(marker)
    start = page.rindex("<script>", 0, at) + len("<script>")
    return page[start:page.index("</script>", at)]


def run_node(tmp_path, script, body):
    case = tmp_path / "case.js"
    case.write_text(
        "const vm = require('vm');\n"
        "const calls = []; const toasts = []; const removed = [];\n"
        "globalThis.document = { querySelectorAll: s => [{ remove: () => removed.push(s) }] };\n"
        "globalThis.toast = (m, t) => toasts.push([m, t]);\n"
        "globalThis.isSessionExpired = () => false;\n"
        "globalThis.confirm = () => true;\n"
        "globalThis.apiFetch = async (url, options) => { calls.push([url, (options || {}).method || 'GET']);"
        " return { ok: true, headers: { get: () => '3' }, json: async () => [{ id: 1 }] }; };\n"
        f"vm.runInThisContext({json.dumps(script)});\n"
        "const out = {};\n(async () => {\n" + body +
        "\nconsole.log(JSON.stringify(out));\n})().catch(e => { console.error(e); process.exit(1); });\n")
    result = subprocess.run([NODE, str(case)], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_card_shows_the_mode(client):
    dry = make_instance(name="Dry", checked_search="dry_run")
    active = make_instance(name="Live", checked_search="active")
    off = make_instance(name="Off")
    assert ">dry run</span>" in client.get(f"/instances/{dry['id']}/card").text
    assert ">checked</span>" in client.get(f"/instances/{active['id']}/card").text
    assert "data-checked-badge" not in client.get(f"/instances/{off['id']}/card").text


def test_history_knows_the_new_item_statuses(client):
    page = client.get("/history").text
    assert "grabbed: 'geladen'" in page
    assert "no_hit: 'kein sauberer Treffer'" in page


def test_help_explains_checked_search(client):
    page = client.get("/help").text
    assert "Checked search (pre-filter)" in page
    assert "Checked Search Year Tolerance" in page


def test_prefilter_page_lists_counters_and_reset_buttons(client):
    inst = make_instance(name=HOSTILE, checked_search="dry_run")
    make_instance(name="Off instance")
    db.checked_search_log.insert({"instance_id": inst["id"], "mode": "dry_run", "skill": "search_missing",
                                  "title": "A", "outcome": "would_grab", "cache_key": "mov:1"})
    page = client.get("/checked-search").text
    assert 'href="/checked-search" class="nav-link active"' in page
    assert "would grab" in page
    assert "current round only" in page
    assert "profile changed" in page and "settings changed" in page
    assert '<option value="grab_uncertain">' in page
    for tag, name, value in script_attributes(page):
        assert "alert(" not in value, (tag, name, value)
    buttons = [a for t, a in tags(page) if t == "button" and "data-name" in a]
    assert [b["data-name"] for b in buttons] == [HOSTILE]
    assert buttons[0]["@click"] == "resetDryRun(Number($el.dataset.id), $el.dataset.name)"
    links = [a for t, a in tags(page) if t == "a" and a.get(":href") == "csvHref()"]
    assert links and links[0]["hx-boost"] == "false"


def test_counters_show_the_current_round(client):
    inst = make_instance(checked_search="dry_run")
    entry = {"instance_id": inst["id"], "mode": "dry_run", "skill": "search_missing", "cache_key": "mov:1"}
    db.checked_search_log.insert({**entry, "title": "A", "outcome": "no_clean_hit", "dry_run_round": 0})
    db.instances.reset_dry_run(inst["id"])
    db.checked_search_log.insert({**entry, "title": "A", "outcome": "would_grab", "dry_run_round": 1})
    page = client.get("/checked-search").text
    assert "would grab" in page
    assert "no clean hit" not in page.split("Download CSV", 1)[1].split("<template", 1)[0]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_prefilter_component_filters_pages_and_resets(client, tmp_path):
    script = component_script(client.get("/checked-search").text, "function checkedSearchPage()")
    out = run_node(tmp_path, script, """
const page = checkedSearchPage();
page.instance = '2'; page.mode = 'dry_run'; page.onlyDifferences = true; page.search = ' Thing ';
await page.load();
out.list = calls[0];
out.total = page.total;
out.csv = page.csvHref();
await page.resetDryRun(2, 'Radarr');
out.reset = calls[1];
""")
    assert out["list"] == ["/api/checked-search?instance_id=2&mode=dry_run&q=Thing&only_differences=true"
                           "&current_round=true&limit=50&offset=0", "GET"]
    assert out["total"] == 3
    assert out["csv"] == ("/api/checked-search.csv?instance_id=2&mode=dry_run&q=Thing&only_differences=true"
                          "&current_round=true")
    assert out["reset"] == ["/api/instances/2/checked-search/reset-dry-run", "POST"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_reset_refreshes_the_list_and_drops_the_old_rounds_counters(client, tmp_path):
    # The page does not poll: without a refresh the old round's rows, total
    # and counters would stay on screen after "Reset dry run".
    inst = make_instance(checked_search="dry_run")
    entry = {"instance_id": inst["id"], "skill": "search_missing", "cache_key": "mov:1", "title": "A"}
    db.checked_search_log.insert({**entry, "mode": "active", "outcome": "grabbed"})
    db.checked_search_log.insert({**entry, "mode": "dry_run", "outcome": "would_grab",
                                  "dry_run_round": inst["dry_run_round"]})
    page = client.get("/checked-search").text
    counters = [(a["data-summary-instance"], a["data-summary-mode"])
                for t, a in tags(page) if t == "tr" and "data-summary-instance" in a]
    assert counters == [(str(inst["id"]), "active"), (str(inst["id"]), "dry_run")]
    out = run_node(tmp_path, component_script(page, "function checkedSearchPage()"), """
const page = checkedSearchPage();
page.page = 4; page.total = 500; page.rows = [{ id: 7 }];
await page.resetDryRun(""" + str(inst["id"]) + """, 'Radarr');
out.calls = calls.slice(); out.removed = removed.slice();
out.page = page.page; out.total = page.total; out.rows = page.rows;
calls.length = 0; removed.length = 0;
globalThis.apiFetch = async (url, options) => { calls.push([url, options.method]); return { ok: false }; };
await page.resetDryRun(""" + str(inst["id"]) + """, 'Radarr');
out.failed = calls.slice(); out.failedRemoved = removed.slice(); out.toasts = toasts;
""")
    reset = [f"/api/instances/{inst['id']}/checked-search/reset-dry-run", "POST"]
    assert out["calls"] == [reset, ["/api/checked-search?current_round=true&limit=50&offset=0", "GET"]]
    assert out["removed"] == [f'[data-summary-instance="{inst["id"]}"][data-summary-mode="dry_run"]']
    assert (out["page"], out["total"], out["rows"]) == (1, 3, [{"id": 1}])
    assert out["failed"] == [reset] and out["failedRemoved"] == []
    assert out["toasts"] == [["Dry run reset for Radarr", "success"], ["Reset failed", "error"]]
```

- [ ] **Step 2: Test laufen lassen, er muss scheitern**

Run: `cd <repo> && .venv/bin/python -m pytest tests/test_g5_pages.py -q -p no:cacheprovider`
Expected: FAIL (`/checked-search` 404, keine Plakette, keine neuen Labels)

- [ ] **Step 3: Route in `main.py`**

`backend/main.py` — Edit, old: `@app.get("/help", response_class=HTMLResponse)`
new:

```
@app.get("/checked-search", response_class=HTMLResponse)
async def checked_search_page(request: Request):
    instances = [public_instance(i) for i in db.instances.get_all()]
    return templates.TemplateResponse(
        request, "checked_search.html",
        template_ctx(request, instances=instances, summary=db.checked_search_log.summary(current_round=True)),
    )


@app.get("/help", response_class=HTMLResponse)
```

- [ ] **Step 4: Seite**

`templates/checked_search.html`:

```html
{% extends "base.html" %}
{% block title %}Pre-filter — {{ app_name }}{% endblock %}

{% block content %}
<script>
var CHECKED_OUTCOMES = {
    grabbed: ['badge-online', 'grabbed'],
    would_grab: ['badge-scheduled', 'would grab'],
    no_clean_hit: ['badge-unknown', 'no clean hit'],
    no_results: ['badge-unknown', 'no results'],
    error: ['badge-offline', 'error'],
    grab_failed: ['badge-offline', 'grab failed'],
    grab_uncertain: ['badge-offline', 'grab uncertain']
};

function checkedSearchPage() {
    return {
        instance: '',
        mode: '',
        outcome: '',
        search: '',
        onlyDifferences: false,
        // After "Reset dry run" the old round's verdicts would mix with the new ones.
        currentRound: true,
        perPage: 50,
        page: 1,
        rows: [],
        total: 0,
        loading: true,
        open: {},
        _request: 0,

        get totalPages() { return Math.max(1, Math.ceil(this.total / this.perPage)); },
        filterParams() {
            const params = new URLSearchParams();
            if (this.instance) params.set('instance_id', this.instance);
            if (this.mode) params.set('mode', this.mode);
            if (this.outcome) params.set('outcome', this.outcome);
            if (this.search.trim()) params.set('q', this.search.trim());
            if (this.onlyDifferences) params.set('only_differences', 'true');
            if (this.currentRound) params.set('current_round', 'true');
            return params;
        },
        csvHref() { return '/api/checked-search.csv?' + this.filterParams(); },
        reload() { this.page = 1; return this.load(); },
        goTo(page) {
            this.page = Math.min(Math.max(1, page), this.totalPages);
            return this.load();
        },

        async load() {
            const request = ++this._request;
            this.loading = true;
            const params = this.filterParams();
            params.set('limit', this.perPage);
            params.set('offset', (this.page - 1) * this.perPage);
            try {
                const resp = await apiFetch(`/api/checked-search?${params}`);
                if (request !== this._request) return;
                this.rows = resp.ok ? await resp.json() : [];
                this.total = resp.ok ? Number(resp.headers.get('X-Total-Count') || this.rows.length) : 0;
                this.open = {};
            } catch (err) {
                if (!isSessionExpired(err)) toast('Could not load the pre-filter log', 'error');
            } finally {
                if (request === this._request) this.loading = false;
            }
        },

        async resetDryRun(id, name) {
            if (!confirm(`Start a new dry-run round for ${name}? Every title will be checked again.`)) return;
            try {
                const resp = await apiFetch(`/api/instances/${id}/checked-search/reset-dry-run`, { method: 'POST' });
                if (!resp.ok) {
                    toast('Reset failed', 'error');
                    return;
                }
                toast(`Dry run reset for ${name}`, 'success');
                // The page does not poll. The new round has no dry-run verdicts yet:
                // drop the old round's counters and reload the list.
                document.querySelectorAll(`[data-summary-instance="${id}"][data-summary-mode="dry_run"]`)
                    .forEach(row => row.remove());
                await this.reload();
            } catch (err) {
                if (!isSessionExpired(err)) toast('Reset failed', 'error');
            }
        },

        toggle(id) { this.open[id] = !this.open[id]; },
        outcomeClass(o) { return 'badge ' + (CHECKED_OUTCOMES[o] || ['badge-unknown'])[0]; },
        outcomeLabel(o) { return (CHECKED_OUTCOMES[o] || [null, o])[1]; },
        modeLabel(m) { return m === 'dry_run' ? 'dry run' : 'active'; },
        fmtTime(s) { return s ? s.replace('T', ' ').substring(0, 16) : '—'; },
        fmtSize(bytes) { return bytes ? (bytes / 1073741824).toFixed(2) + ' GiB' : '—'; },
        candidateStyle(c) {
            if (c.chosen) return 'font-weight:600;';
            return c.verdict === 'unchecked' ? 'color:var(--text-muted);' : '';
        }
    };
}
</script>

<div x-data="checkedSearchPage()" x-init="load()">

<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:1rem;flex-wrap:wrap;gap:0.75rem;">
    <h1 style="margin:0;font-size:1.4rem;font-weight:700;">Pre-filter</h1>
    <a class="btn btn-secondary btn-sm" hx-boost="false" :href="csvHref()" href="/api/checked-search.csv">Download CSV</a>
</div>

<!-- Counters per instance, mode and outcome (dry run: the running round) -->
<div class="card" style="margin-bottom:1rem;overflow:auto;">
    {% if summary %}
    <table class="table">
        <thead><tr><th>Instance</th><th>Mode</th><th>Outcome</th><th title="Dry run: the current round only">Titles</th></tr></thead>
        <tbody>
        {% for s in summary %}
        <tr data-summary-instance="{{ s.instance_id }}" data-summary-mode="{{ s.mode }}">
            <td>{{ s.instance_name }}</td>
            <td>{{ 'dry run' if s.mode == 'dry_run' else 'active' }}</td>
            <td>{{ s.outcome.replace('_', ' ') }}</td>
            <td>{{ s.n }}</td>
        </tr>
        {% endfor %}
        </tbody>
    </table>
    {% else %}
    <div style="padding:1.5rem;color:var(--text-muted);font-size:0.9rem;">
        Nothing checked yet. Switch "Checked search" to Dry run in an instance's settings.
    </div>
    {% endif %}
    <div style="display:flex;gap:0.5rem;flex-wrap:wrap;padding:0.75rem 1rem;border-top:1px solid var(--border);">
        {% for inst in instances if inst.checked_search != 'off' %}
        <button class="btn btn-secondary btn-sm" data-id="{{ inst.id }}" data-name="{{ inst.name }}"
                @click="resetDryRun(Number($el.dataset.id), $el.dataset.name)">
            Reset dry run: {{ inst.name }}
        </button>
        {% endfor %}
    </div>
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
    <select class="form-input" x-model="mode" @change="reload()">
        <option value="">All modes</option>
        <option value="dry_run">Dry run</option>
        <option value="active">Active</option>
    </select>
    <select class="form-input" x-model="outcome" @change="reload()">
        <option value="">All outcomes</option>
        <option value="grabbed">Grabbed</option>
        <option value="would_grab">Would grab</option>
        <option value="no_clean_hit">No clean hit</option>
        <option value="no_results">No results</option>
        <option value="error">Error</option>
        <option value="grab_failed">Grab failed</option>
        <option value="grab_uncertain">Grab uncertain</option>
    </select>
    <label style="font-size:0.85rem;display:flex;align-items:center;gap:0.35rem;">
        <input type="checkbox" x-model="onlyDifferences" @change="reload()"> only differences
    </label>
    <label style="font-size:0.85rem;display:flex;align-items:center;gap:0.35rem;"
           title="Dry run: only the verdicts since the last reset. Active rows are always shown.">
        <input type="checkbox" x-model="currentRound" @change="reload()"> current round only
    </label>
    <span style="font-size:0.82rem;color:var(--text-muted);" x-text="total + ' titles'"></span>
</div>

<div class="card" style="overflow:auto;">
    <template x-if="loading && rows.length === 0">
        <div style="text-align:center;padding:3rem 1rem;color:var(--text-muted);">Loading…</div>
    </template>
    <template x-if="rows.length > 0">
        <table class="table">
            <thead>
                <tr>
                    <th>Time</th><th>Instance</th><th>Mode</th><th>Title</th><th>Outcome</th>
                    <th>*arr would grab</th><th>Filter grabs</th><th>Rejected</th>
                </tr>
            </thead>
            <template x-for="row in rows" :key="row.id">
                <tbody>
                    <tr style="cursor:pointer;" @click="toggle(row.id)">
                        <td style="white-space:nowrap;font-size:0.78rem;color:var(--text-muted);" x-text="fmtTime(row.created_at)"></td>
                        <td style="white-space:nowrap;" x-text="row.instance_name"></td>
                        <td x-text="modeLabel(row.mode)"></td>
                        <td style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"
                            :title="row.title">
                            <span x-text="row.title"></span>
                            <span x-show="row.profile_changed" class="badge badge-unknown"
                                  title="Checked under a quality profile that has changed since — a dry run checks it again">profile changed</span>
                            <span x-show="row.settings_changed" class="badge badge-unknown"
                                  title="Checked under rule settings that have changed since — a dry run checks it again">settings changed</span>
                        </td>
                        <td><span :class="outcomeClass(row.outcome)" :title="row.error_message || ''"
                                  x-text="outcomeLabel(row.outcome)"></span></td>
                        <td style="max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:0.8rem;"
                            :title="row.arr_pick || ''" x-text="row.arr_pick || '—'"></td>
                        <td style="max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:0.8rem;"
                            :title="row.pick || ''" x-text="row.pick || '—'"></td>
                        <td x-text="row.rejected_count"></td>
                    </tr>
                    <tr x-show="open[row.id]">
                        <td colspan="8" style="background:rgba(0,0,0,0.15);">
                            <div x-show="row.error_message" style="font-size:0.8rem;color:var(--text-muted);margin-bottom:0.5rem;"
                                 x-text="row.error_message"></div>
                            <table class="table" style="font-size:0.78rem;">
                                <thead><tr><th>Release</th><th>Indexer</th><th>Score</th><th>Size</th><th>Quality</th><th>Verdict</th><th>Reasons</th><th>Notes</th></tr></thead>
                                <tbody>
                                    <template x-for="(c, i) in row.candidates" :key="i">
                                        <tr :style="candidateStyle(c)">
                                            <td style="word-break:break-all;">
                                                <span x-text="c.title"></span>
                                                <span x-show="c.arr_choice" class="badge badge-unknown" title="*arr would grab this one">*arr</span>
                                                <span x-show="c.chosen" class="badge badge-online" title="The filter picks this one">filter</span>
                                            </td>
                                            <td x-text="c.indexer"></td>
                                            <td x-text="c.score ?? '—'"></td>
                                            <td style="white-space:nowrap;" x-text="fmtSize(c.size)"></td>
                                            <td x-text="c.quality"></td>
                                            <td x-text="c.verdict"></td>
                                            <td x-text="(c.reasons || []).join(', ')"></td>
                                            <td x-text="(c.notes || []).join(', ')"></td>
                                        </tr>
                                    </template>
                                </tbody>
                            </table>
                        </td>
                    </tr>
                </tbody>
            </template>
        </table>
    </template>
    <template x-if="!loading && rows.length === 0">
        <div style="text-align:center;padding:3rem 1rem;color:var(--text-muted);">No titles match your filters.</div>
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

- [ ] **Step 5: Menü, Karte, History, Hilfe**

`templates/base.html` — Edit, old:

```
        <a href="/searched" class="nav-link {% if '/searched' in request.url.path %}active{% endif %}">Progressed</a>
```

new:

```
        <a href="/searched" class="nav-link {% if '/searched' in request.url.path %}active{% endif %}">Progressed</a>
        <a href="/checked-search" class="nav-link {% if '/checked-search' in request.url.path %}active{% endif %}">Pre-filter</a>
```

`templates/instances/card.html` — Edit, old:

```
            <span class="type-badge type-{{ inst.type }}">{{ inst.type }}</span>
```

new:

```
            <span class="type-badge type-{{ inst.type }}">{{ inst.type }}</span>
            {% if inst.checked_search == 'dry_run' %}
            <span class="badge badge-scheduled" data-checked-badge title="Checked search: dry run — nothing is grabbed, see Pre-filter">dry run</span>
            {% elif inst.checked_search == 'active' %}
            <span class="badge badge-online" data-checked-badge title="Checked search: only releases that pass the pre-filter are grabbed">checked</span>
            {% endif %}
```

`templates/history.html` — Edit, old:

```
        itemLabel(s) {
            return {
                completed: 'durchgelaufen',
                failed: 'gescheitert',
                submitted: 'offen',
                expired: 'nicht prüfbar'
            }[s] || s;
        }
```

new:

```
        itemLabel(s) {
            return {
                completed: 'durchgelaufen',
                failed: 'gescheitert',
                submitted: 'offen',
                expired: 'nicht prüfbar',
                grabbed: 'geladen',
                no_hit: 'kein sauberer Treffer'
            }[s] || s;
        },
        itemTitle(row) {
            if (row.command_id) return 'Befehl #' + row.command_id;
            if (row.command_status === 'grabbed') return 'Geprüfte Suche: sauberes Release geladen';
            if (row.command_status === 'no_hit') return 'Geprüfte Suche: kein Release hat den Vorfilter bestanden';
            return row.command_status === 'failed' ? 'Nicht angenommen' : '';
        }
```

Edit, old:

```
                                        'badge-scheduled': row.command_status === 'submitted',
                                        'badge-unknown': row.command_status === 'expired'
                                    }"
                                    :title="row.command_id ? 'Befehl #' + row.command_id : (row.command_status === 'failed' ? 'Nicht angenommen' : '')"
```

new:

```
                                        'badge-scheduled': row.command_status === 'submitted',
                                        'badge-unknown': row.command_status === 'expired' || row.command_status === 'no_hit',
                                        'badge-running': row.command_status === 'grabbed'
                                    }"
                                    :title="itemTitle(row)"
```

`templates/help.html` — Edit, old: `<!-- Fields reference -->`
new:

```
<!-- Checked search -->
<div class="card" style="margin-bottom:1rem;">
    <div class="card-header"><strong>Checked search (pre-filter)</strong></div>
    <div class="card-body" style="font-size:0.9rem;line-height:1.7;color:var(--text-secondary);">
        <p>Some indexers attach the searched movie's or series' ID to every result of an ID search, foreign titles included, and *arr then grabs a foreign title. With <strong>Checked search</strong> missingarr fetches the search results itself (<code>GET /api/v3/release</code>), checks every approved release with a pre-filter (name, year, <code>/parse</code>, release of the existing file; Sonarr: series, country/year suffix, publish date) and grabs only the first release that passes (<code>POST /api/v3/release</code>).</p>
        <p><strong>Dry run</strong> searches and checks but grabs nothing and remembers nothing. Review the decisions on the <a href="/checked-search">Pre-filter</a> page — "only differences" shows where the filter would take something else than *arr — or download them as CSV. Every title is checked once per round (a Force Run does not change that) and again after its quality profile or the rules changed ("profile changed", "settings changed"); "Reset dry run" starts a new round.</p>
        <p><strong>Active</strong> grabs the first clean release and names the title it is for, so a search for another title in between cannot redirect it. A title without a clean release counts as searched (Retry decides about the next attempt); a grabbed title that is still missing after <em>Search again if still missing after (days)</em> is searched again. A grab without a clear answer keeps the title blocked like a grab ("grab uncertain"). The History shows these titles as "geladen" or "kein sauberer Treffer". Sonarr: single episodes only.</p>
        <p>A checked run <strong>pauses</strong> — searches and remembers nothing, also when there would be nothing to search — while an indexer has Automatic Search and Interactive Search set differently in *arr (the release search asks the interactive ones), or while the indexer list cannot be read. The run counts as a success with the note "Checked search paused"; the activity log warns and names the indexer, and Last sync on the card keeps the last run that searched.</p>
    </div>
</div>

<!-- Fields reference -->
```

Edit (Feldnamen der Referenztabelle lesbar machen), old:

```
{{ key.replace('_', ' ').title() }}
```

new:

```
{{ (('checked search ' ~ key[3:]) if key.startswith('cs_') else key).replace('_', ' ').title() }}
```

- [ ] **Step 6: Tests laufen lassen**

Run: `cd <repo> && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: `631 passed, 2 skipped`

- [ ] **Step 7: Commit**

```bash
git add backend/main.py templates/checked_search.html templates/base.html templates/instances/card.html templates/history.html templates/help.html tests/test_g5_pages.py
git commit -m "feat: add the Pre-filter page, card badge and history labels for the checked search"
```

---

## Task Z: Version, README, Probe, Review, Push nach Freigabe (nach Welle 3, allein)

**Files:**
- Modify: `VERSION`, `README.md`, `tests/test_p6_docs.py` (nur die Versionsprüfung)
- Create: `tests/test_z_release.py`
- Scratch (nicht im Repo): `<scratchpad>/probe-e2e/fake_arr.py`, `pages.js`, `save.js`

**Interfaces:**
- Consumes: alles
- Produces: Branch `feat/checked-search` mit 0.9.0, geprüft; Push erst nach Daniels Freigabe.

- [ ] **Step 1: Failing tests**

`tests/test_z_release.py`:

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_readme_documents_checked_search():
    readme = (ROOT / "README.md").read_text()
    assert "### Checked search" in readme
    assert "### Profile changes" in readme
    assert "**Search again after profile changes**" in readme
    assert "Interactive Search" in readme   # GET /release asks other indexers than the command
    assert "**Search again if still missing after (days)**" in readme
    assert "Redownload Failed from Interactive Search" in readme
    assert "## Upgrading to 0.9.0" in readme
    assert "/api/checked-search.csv" in readme
    assert "downloadUrl" in readme   # the security note says it is never stored


def test_no_host_details_in_new_files():
    files = [ROOT / "README.md", *sorted((ROOT / "backend" / "checked_search").glob("*.py")),
             ROOT / "backend" / "db" / "checked_search_log.py", ROOT / "backend" / "skills" / "profiles.py",
             ROOT / "templates" / "checked_search.html", *sorted((ROOT / "tests").glob("test_g*_*.py"))]
    for path in files:
        text = path.read_text()
        for marker in ("/root/", "/home/", "/tmp/", "/mnt/", "/srv/"):
            assert marker not in text, f"{marker} in {path.name}"
```

`tests/test_p6_docs.py` — Edit (die Versionsprüfung zieht mit, siehe Global Constraints), old:

```
def test_version_is_0_8_0():
    assert (ROOT / "VERSION").read_text().strip() == "0.8.0"
```

new:

```
def test_version_is_0_9_0():
    assert (ROOT / "VERSION").read_text().strip() == "0.9.0"
```

Run: `cd <repo> && .venv/bin/python -m pytest tests/test_z_release.py tests/test_p6_docs.py -q -p no:cacheprovider`
Expected: `2 failed` (`test_readme_documents_checked_search`, `test_version_is_0_9_0`)

- [ ] **Step 2: Version und README**

`VERSION` komplett:

```
0.9.0
```

`README.md` — Edit, old:

```
- Configurable search intervals, rate limiting, quiet hours
```

new:

```
- Configurable search intervals, rate limiting, quiet hours
- Optional checked search: missingarr checks every release with a pre-filter and grabs only clean ones (dry run first)
```

Edit, old: `## Example: Typical Home Setup`
new:

```
### Checked search

Some indexers attach the searched movie's or series' ID to *every* result of an ID search, foreign titles included. Radarr and Sonarr then accept a foreign title as the searched one — "The Thing" (1982) gets `Das.Ding.aus.einer.anderen.Welt.1951…`, "Halloween" (1978) gets `Halloween.2018…`. With **Checked search** missingarr does not send the search command. It fetches the results itself (`GET /api/v3/release`), checks every approved release with a pre-filter and grabs only the first one that passes (`POST /api/v3/release`). Language and quality stay with your *arr profiles.

| Field | Default | Description |
|---|---|---|
| **Checked search** | `Off` | **Off**: search command as before. **Dry run**: search and check, grab nothing, remember nothing; each title once per round. **Active**: grab the first clean release. |
| **Release search timeout** | `120` s | How long to wait for the release search (10–600). |
| **Time budget per run** | `25` min | No new title is started after this (1–1440). |
| **Dry run: max releases checked** | `100` | Releases checked per title in a dry run (1–1000). |
| **Search again if still missing after (days)** | `7` | A title the checked search grabbed that is still in the Wanted list (missing, or cutoff unmet) after this many days is searched again, independent of *Retry* (1–365). Takes the place of *Redownload Failed from Interactive Search* in \*arr. |

Radarr rules: year within a tolerance of the movie's years (optionally including its release dates), veto when `/parse` names another movie of the library, title match (exact, prefix, word match), releases without a year need an exact title, and the release of the existing file is never grabbed again. Sonarr rules (single episodes only — the pack modes are locked while checked search is on): veto when `/parse` names another series, country/year suffix must fit the series, releases published long before the air date are rejected (shortly before: noted), and the release of the existing file is skipped. Every rule and limit is a setting of the instance; the info icons in the form explain each one.

Review a dry run on the **Pre-filter** page (filter "only differences" shows where the filter takes something else than \*arr; "current round only" hides the verdicts from before the last *Reset dry run*) or download it as CSV. Every title is checked once per round — a Force Run does not change that — and again when its quality profile or the rule settings change (the page marks older rows "profile changed" or "settings changed"). In active mode a title without a clean release counts as searched — *Retry* decides when it is searched again. The History shows these titles as "geladen" (grabbed) or "kein sauberer Treffer" (no clean hit); they need no command verification.

Things to know before you switch it on:

- `GET /api/v3/release` is an **interactive search** for Radarr and Sonarr. It asks the indexers that have *Interactive Search* enabled, not the ones with *Automatic Search* that the search command used. While any indexer has the two switches set differently (or the indexer list cannot be read), a checked run **pauses** — also a run that would find nothing to search: it searches and remembers nothing and ends as a success with the note "Checked search paused" in the History; the activity log warns and names the indexer (or why the list could not be read). *Last sync* on the card stays at the last run that searched, so a lasting pause shows there. Set both switches alike under *Settings → Indexers* (or in Prowlarr).
- The grab names its target: missingarr sends the movie (or the series and episodes) together with the quality and languages the search reported (`shouldOverride`). Radarr and Sonarr keep search results for 30 minutes per indexer and release, mapped to whichever search returned them last; without the target a search for another title in between could redirect the grab. A release the search did not map to exactly this title is never grabbed.
- A failing indexer does not make `GET /api/v3/release` fail: Radarr and Sonarr answer with what the other indexers found and skip an indexer they blocked after failures. So when a title ends without a clean release, missingarr waits a few seconds and reads the \*arr health checks (`GET /api/v3/health`). If they report an indexer with *Interactive Search* on as unavailable due to failures (or cannot be read), the title ends as an error ("indexer failure during search") and is not remembered: the next run searches it again. A permanently broken indexer therefore keeps those titles coming back until it is fixed or switched off.
- A release grabbed through `POST /api/v3/release` is recorded as an interactive grab. Radarr and Sonarr then skip their "matched by ID — Manual Import required" block and import it automatically. Switch off *Settings → Download Clients → Failed Download Handling → Redownload Failed from Interactive Search* in both: missingarr searches a grabbed title again itself once it is still missing after **Search again if still missing after (days)**. A foreign release that gets past the pre-filter (same title and year, see the known limits) is imported, not held back.
- A grab without a clear answer (timeout, connection lost after sending, server error) may be downloading: the title stays blocked like after a grab ("grab uncertain" on the Pre-filter page) — check the queue in \*arr, reset the cache on the Progressed page to retry sooner. A refused grab (for example the search result expired) leaves the title free.
- Season packs: in a search for one episode Sonarr rejects packs itself, except for specials (season 0); the checked search never takes a pack. Nor does it take a multi-episode release (one release for two or more episodes, which Sonarr approves in a search for either of them): the rules and the Searched cache would only cover the episode searched for. An episode that only exists as part of such a release needs the search command (Checked search Off).
- Sonarr upgrades: the checked search searches and remembers single episodes, the search command whole seasons. Back on the search command (Checked search Off), a season waits while one of its episodes is blocked after a checked grab (for *Search again if still missing after (days)*, or until its profile changes) — the season search would search that episode again.
- What \*arr approved during the search is what is grabbed: `POST /api/v3/release` downloads the release without a new decision by \*arr (no second profile check at the grab).
- A release search cannot be interrupted. Deleting an instance while it runs can answer "still stopping" for up to the *Release search timeout* — delete again afterwards.

The API: `GET /api/checked-search` (list, `X-Total-Count`, optional `current_round=true`), `GET /api/checked-search.csv`, `POST /api/instances/<id>/checked-search/reset-dry-run`.

### Profile changes

Radarr and Sonarr score every release with the quality profile in force when they search. Missingarr notices when a profile changes: at the start of every run it reads the quality profiles, custom formats and release profiles, the quality size limits (`GET /api/v3/qualitydefinition`) and the indexer settings (`GET /api/v3/config/indexer`), all without indexer load, and keeps a fingerprint per profile of what decides a score or whether \*arr approves a release: the qualities and their order, cutoff and scores, the conditions of the custom formats, the terms of the release profiles, the minimum and maximum size per quality, and *Minimum Age*, *Maximum Size* and *Retention* (Radarr also the hardcoded-subtitles settings). The preferred size and other settings that only sort the approved releases do not count. Names, labels and help texts do not count, nor does the order of unordered lists; a rule field that a future \*arr version adds counts once missingarr knows it. With **Search again after profile changes** (on by default) a title in the Searched cache may be searched again as soon as the fingerprint of its profile differs from the one it was searched under. A changed custom format, release profile, size limit or indexer setting counts as a change of every profile. Titles cached before 0.9.0 count as searched under the profiles as they were at the first run of 0.9.0 — the update alone releases nothing. If any of these cannot be read, nothing is released. A dry run always checks a title again after its profile changed; the Pre-filter page marks such rows "profile changed". Every detected change is named in the activity log.

## Example: Typical Home Setup
```

Edit, old:

```
- Missingarr does not follow redirects from Sonarr/Radarr. If the connection test reports a redirect, fix the URL.
```

new:

```
- Missingarr does not follow redirects from Sonarr/Radarr. If the connection test reports a redirect, fix the URL.
- Checked search keeps only titles and numbers of a release. `downloadUrl` (which carries your indexer's API key), `magnetUrl`, `infoUrl` and `guid` are never logged or stored; the `guid` (with the release's mapping, quality and languages) is held in memory for the one `POST /api/v3/release`.
```

Edit, old:

```
| **Retry (hours)** | `0` | How long a searched title stays in the Searched cache. `0` = never search it again automatically (reset the cache on the Progressed page to retry). A broader season or series search only blocks episodes that were already out (air date plus *Hours after release*) when it ran; season and series searches from before 0.8.0 do not block single episodes. |
```

new:

```
| **Retry (hours)** | `0` | How long a searched title stays in the Searched cache. `0` = never search it again automatically (reset the cache on the Progressed page to retry). A broader season or series search only blocks episodes that were already out (air date plus *Hours after release*) when it ran; season and series searches from before 0.8.0 do not block single episodes. |
| **Search again after profile changes** | `On` | A cached title may be searched again once its quality profile changed in \*arr (scores, qualities, custom formats, release profiles, quality size limits, indexer minimum age / maximum size / retention). See *Profile changes* below. |
```

Edit, old: `## Upgrading to 0.8.0`
new:

```
## Upgrading to 0.9.0

- Nothing changes until you switch **Checked search** on for an instance; it is off for every existing instance.
- Start with **Dry run** and review the Pre-filter page before switching to **Active**. A dry run searches your indexers like a normal run (one search per title), but over titles that were searched before as well.
- Before the first dry run, set *Automatic Search* and *Interactive Search* alike for every indexer in Radarr and Sonarr — a checked run pauses while they differ (see *Checked search*) — and switch off *Redownload Failed from Interactive Search* in both.
- With Checked search on, Sonarr only searches single episodes; Season Packs, Show Batch and Smart cannot be selected.
- **Search again after profile changes** is on for every instance. The first run of 0.9.0 records the profiles; titles already in the Searched cache stay blocked until a profile changes after that. Each run reads the quality profiles, custom formats, release profiles, quality size limits and indexer settings, and Sonarr also the series list (no indexer load).
- The pre-filter log keeps the history's retention (`HISTORY_RETENTION_DAYS`).

## Upgrading to 0.8.0
```

- [ ] **Step 3: Alles testen**

Run: `cd <repo> && .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: `633 passed, 2 skipped`

Run (lokal, Pfade nie ins Repo): `cd <repo> && MISSINGARR_VORFILTER_KORPUS=<korpus> .venv/bin/python -m pytest tests/ -q -p no:cacheprovider`
Expected: `635 passed` (die Runde-4-Metadaten liegen in `referenz/r4/`; fehlen sie, `634 passed, 1 skipped`).

Commit:

```bash
git add VERSION README.md tests/test_p6_docs.py tests/test_z_release.py
git commit -m "docs: version 0.9.0 and the checked search in the README"
```

- [ ] **Step 4: Ende-zu-Ende-Probe gegen ein nachgebildetes Radarr (Scratch-DB, nie Live)**

Mit dem Write-Werkzeug `<scratchpad>/probe-e2e/fake_arr.py` anlegen:

```python
"""Fake Radarr for the end-to-end probe of the checked search (scratch only).

Serves the endpoints missingarr uses; every release carries a downloadUrl
with a fake indexer key so the probe can check that it never lands anywhere.
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

KEY = "probe-arr-key"
INDEXER_KEY = "PROBEINDEXERKEY999"
MOVIES = {
    1: {"id": 1, "title": "Das Ding aus einer anderen Welt", "originalTitle": "The Thing", "year": 1982,
        "alternateTitles": [], "hasFile": False, "monitored": True, "inCinemas": "1982-06-25T00:00:00Z",
        "qualityProfileId": 1},
    2: {"id": 2, "title": "Halloween - Die Nacht des Grauens", "originalTitle": "Halloween", "year": 1978,
        "alternateTitles": [], "hasFile": False, "monitored": True, "qualityProfileId": 1},
}
PROFILES = [{"id": 1, "name": "HD", "formatItems": [{"format": 1, "name": "German", "score": 100}]}]
FORMATS = [{"id": 1, "name": "German", "specifications": []}]
INDEXERS = [{"id": 7, "name": "Probe", "enableAutomaticSearch": True, "enableInteractiveSearch": True}]


def release(title, guid, movie_id):
    return {"title": title, "guid": guid, "indexerId": 7, "indexer": "Probe", "approved": True,
            "customFormatScore": 100, "size": 4_000_000_000, "mappedMovieId": movie_id,
            "quality": {"quality": {"id": 7, "name": "Bluray-1080p"},
                        "revision": {"version": 1, "real": 0, "isRepack": False}},
            "languages": [{"id": 4, "name": "German"}],
            "movieTitles": [], "publishDate": "2026-01-01T00:00:00Z",
            "downloadUrl": f"http://127.0.0.1:9696/1/download?apikey={INDEXER_KEY}", "infoUrl": "http://x"}


RELEASES = {
    1: [release("Das.Ding.aus.einer.anderen.Welt.1951.German.DL.1080p.BluRay.x265-GRP", "guid-a", 1),
        release("The.Thing.1982.German.DL.1080p.BluRay.x264-GRP", "guid-b", 1)],
    2: [release("Halloween.2018.German.DL.1080p.BluRay.x264-GRP", "guid-c", 2)],
}
CACHE = set()   # (indexerId, guid) of every release a search returned, like *arr's 30-minute cache
PARSE = {
    "Das.Ding.aus.einer.anderen.Welt.1951.German.DL.1080p.BluRay.x265-GRP":
        {"parsedMovieInfo": {"movieTitles": ["Das Ding aus einer anderen Welt"], "year": 1951}},
    "The.Thing.1982.German.DL.1080p.BluRay.x264-GRP":
        {"parsedMovieInfo": {"movieTitles": ["The Thing"], "year": 1982}, "movie": {"id": 1}},
    "Halloween.2018.German.DL.1080p.BluRay.x264-GRP":
        {"parsedMovieInfo": {"movieTitles": ["Halloween"], "year": 2018}, "movie": {"id": 3}},
}
GRABS = []


class Handler(BaseHTTPRequestHandler):
    def _send(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.headers.get("X-Api-Key") != KEY:
            return self._send({"error": "unauthorized"}, 401)
        url = urlsplit(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path == "/api/v3/system/status":
            return self._send({"appName": "Radarr", "version": "6.4.4"})
        if url.path == "/api/v3/wanted/missing":
            records = list(MOVIES.values())
            size, page = int(q.get("pageSize", 10)), int(q.get("page", 1))
            return self._send({"totalRecords": len(records), "records": records[(page - 1) * size: page * size]})
        if url.path.startswith("/api/v3/movie/"):
            return self._send(MOVIES[int(url.path.rsplit("/", 1)[1])])
        if url.path == "/api/v3/qualityprofile":
            return self._send(PROFILES)
        if url.path == "/api/v3/customformat":
            return self._send(FORMATS)
        if url.path == "/api/v3/releaseprofile":
            return self._send([])
        if url.path == "/api/v3/qualitydefinition":
            return self._send([{"id": 1, "quality": {"id": 7, "name": "Bluray-1080p"}, "title": "Bluray-1080p",
                                "minSize": 0, "maxSize": None, "preferredSize": None}])
        if url.path == "/api/v3/config/indexer":
            return self._send({"id": 1, "minimumAge": 0, "maximumSize": 0, "retention": 0})
        if url.path == "/api/v3/indexer":
            return self._send(INDEXERS)
        if url.path == "/api/v3/release":
            answer = RELEASES.get(int(q["movieId"]), [])
            CACHE.update((r["indexerId"], r["guid"]) for r in answer)
            return self._send(answer)
        if url.path == "/api/v3/parse":
            return self._send(PARSE.get(q["title"], {"title": q["title"]}))
        if url.path == "/grabs":
            return self._send(GRABS)
        return self._send({"error": "not found"}, 404)

    def do_POST(self):
        if self.headers.get("X-Api-Key") != KEY:
            return self._send({"error": "unauthorized"}, 401)
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path == "/api/v3/release":
            if (body.get("indexerId"), body.get("guid")) not in CACHE:
                return self._send({"message": "Couldn't find requested release in cache"}, 404)
            if body.get("shouldOverride") and None in (body.get("movieId"), body.get("quality"),
                                                       body.get("languages")):
                return self._send({"message": "movieId, quality and languages are required"}, 400)
            GRABS.append(body)
            return self._send(body)
        return self._send({"error": "not found"}, 404)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
```

Run:

```bash
SCRATCH=<scratchpad>
test -d "$SCRATCH" || { echo "SCRATCH fehlt: $SCRATCH" >&2; exit 1; }
P="$SCRATCH/probe-e2e"
rm -f "$P"/missingarr.db* "$P/jar"
cd <repo>
python3 "$P/fake_arr.py" 18791 > "$P/fake.log" 2>&1 &
echo $! > "$P/fake.pid"
DATABASE_URL="$P/missingarr.db" AUTH_PASSWORD=probe-pass \
  .venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 18767 --timeout-graceful-shutdown 5 \
  > "$P/uvicorn.log" 2>&1 &
echo $! > "$P/uv.pid"
timeout 30 sh -c 'until curl -fsS http://127.0.0.1:18767/api/health >/dev/null 2>&1; do sleep 1; done'
curl -s -c "$P/jar" -o /dev/null -w 'login %{http_code}\n' -d 'username=admin&password=probe-pass&next=/' http://127.0.0.1:18767/login
curl -s -b "$P/jar" -H 'Content-Type: application/json' -o /dev/null -w 'create %{http_code}\n' \
  -d '{"name":"Probe Radarr","type":"radarr","url":"http://127.0.0.1:18791","api_key":"probe-arr-key","enabled":false,"checked_search":"dry_run","seconds_between_actions":0,"missing_per_run":20}' \
  http://127.0.0.1:18767/api/instances
curl -s -b "$P/jar" -X POST 'http://127.0.0.1:18767/api/instances/1/trigger?skill=search_missing&force=false'; echo
sleep 3
curl -s -b "$P/jar" 'http://127.0.0.1:18767/api/checked-search' | python3 -c "import json,sys; [print(r['mode'],r['outcome'],r['title'],'| arr:',r['arr_pick'],'| pick:',r['pick']) for r in json.load(sys.stdin)]"
curl -s -b "$P/jar" -X POST 'http://127.0.0.1:18767/api/instances/1/trigger?skill=search_missing&force=false'; echo
sleep 2
curl -s -b "$P/jar" -D - -o /dev/null 'http://127.0.0.1:18767/api/checked-search' | grep -i x-total
curl -s -b "$P/jar" 'http://127.0.0.1:18767/api/history?limit=5' | python3 -c "import json,sys; [print(r['status'],r['triggered_count'],r['error_message']) for r in json.load(sys.stdin)]"
curl -s -b "$P/jar" -X PUT -H 'Content-Type: application/json' -o /dev/null -w 'put %{http_code}\n' \
  -d '{"name":"Probe Radarr","type":"radarr","url":"http://127.0.0.1:18791","enabled":false,"checked_search":"active","seconds_between_actions":0,"missing_per_run":20}' \
  http://127.0.0.1:18767/api/instances/1
curl -s -b "$P/jar" -X POST 'http://127.0.0.1:18767/api/instances/1/trigger?skill=search_missing&force=false'; echo
sleep 3
curl -s -H 'X-Api-Key: probe-arr-key' http://127.0.0.1:18791/grabs | python3 -c "import json,sys; print('grabs', [(g['guid'], g['indexerId'], g.get('shouldOverride'), g.get('movieId'), g['quality']['quality']['id'], [l['id'] for l in g['languages']]) for g in json.load(sys.stdin)])"
curl -s -b "$P/jar" 'http://127.0.0.1:18767/api/history/items' | python3 -c "import json,sys; [print(r['title'],r['command_status'],r['status']) for r in json.load(sys.stdin)]"
curl -s -b "$P/jar" 'http://127.0.0.1:18767/api/checked-search.csv?mode=active'
curl -s -b "$P/jar" http://127.0.0.1:18767/api/instances/1 | python3 -c "import json,sys; d=json.load(sys.stdin); print('profiles', len(d['profile_fingerprints']), d['profile_fingerprints'] == d['profile_fingerprints_baseline'], d['search_again_after_profile_change'])"
.venv/bin/python -c "
import sqlite3, sys; c = sqlite3.connect(sys.argv[1])
print('cache', c.execute('SELECT cache_key, profile_fingerprint IS NOT NULL, history_item_id IS NOT NULL FROM searched_items ORDER BY cache_key').fetchall())" "$P/missingarr.db"
.venv/bin/python -c "
import sqlite3, sys; c = sqlite3.connect(sys.argv[1]); t = '\n'.join(c.iterdump())
print('secrets in db:', [s for s in ('PROBEINDEXERKEY999', 'guid-b', 'downloadUrl', 'apikey') if s in t])" "$P/missingarr.db"
grep -c -e PROBEINDEXERKEY999 -e guid-b "$P/uvicorn.log"
```

(`sleep` ist hier ein Shell-Befehl im Probeskript; wer das über ein Agenten-Werkzeug ausführt, das alleinstehendes `sleep` sperrt, lässt den Block als ein Skript laufen oder nutzt den Warte-Mechanismus des Werkzeugs.)

Expected der Reihe nach (bei der zweiten Überarbeitung am 01.10.2026 so beobachtet):
- `login 302`, `create 201`, `{"status":"triggered","skill":"search_missing"}`
- zwei Probelauf-Zeilen (Reihenfolge folgt der Suchreihenfolge `random`): `dry_run no_clean_hit Halloween - Die Nacht des Grauens (1978) | arr: Halloween.2018… | pick: None` und `dry_run would_grab Das Ding aus einer anderen Welt (1982) | arr: Das.Ding.aus.einer.anderen.Welt.1951… | pick: The.Thing.1982.German.DL.1080p.BluRay.x264-GRP`
- zweiter Probelauf prüft nichts Neues: `x-total-count: 2`; History: zwei Läufe `success 0 None`
- `put 200`, Trigger, Grabs genau `grabs [('guid-b', 7, True, 1, 7, [4])]` (Ziel festgelegt: Film 1, Qualität und Sprachen wie gemeldet)
- History-Items (beliebige Reihenfolge): `Halloween - Die Nacht des Grauens (1978) no_hit success` und `Das Ding aus einer anderen Welt (1982) grabbed success`
- CSV: Kopfzeile plus drei Zeilen (`reject, year`; `pass … yes,no`; Halloween `reject, year; other movie`)
- `profiles 1 True True` (ein Profil gelesen, Grundlinie beim ersten Lauf gesetzt, Einstellung an) und `cache [('mov:1', 1, 1), ('mov:2', 1, 1)]` (beide Cache-Einträge mit Fingerabdruck und schreibendem Item; so am 02.10.2026 beobachtet)
- `secrets in db: []` und `0`

- [ ] **Step 5: Seiten im Headless-Browser (wenn verfügbar), sonst Handprobe**

Mit dem Write-Werkzeug `<scratchpad>/probe-e2e/pages.js` anlegen:

```javascript
// Headless page check for the probe (scratch only): every page loads without
// JavaScript errors, the form locks the pack modes, the pre-filter rows open.
const { chromium } = require(process.env.PW_CORE);

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM });
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(`pageerror ${page.url()}: ${e.message}`));
  page.on('console', m => { if (m.type() === 'error') errors.push(`console ${page.url()}: ${m.text()}`); });
  const base = 'http://127.0.0.1:18767';
  await page.goto(base + '/login');
  await page.fill('input[name=username]', 'admin');
  await page.fill('input[name=password]', 'probe-pass');
  await Promise.all([page.waitForNavigation(), page.click('button[type=submit]')]);
  for (const path of ['/', '/instances', '/instances/new', '/instances/1/edit', '/history', '/logs', '/searched',
                      '/checked-search', '/help']) {
    await page.goto(base + path);
    await page.waitForTimeout(600);
  }
  await page.goto(base + '/checked-search');
  await page.waitForTimeout(800);
  const rows = await page.locator('tbody > tr[style*="cursor"]').count();
  await page.locator('tbody > tr[style*="cursor"]').first().click();
  await page.waitForTimeout(300);
  const visibleCandidates = await page.locator('td[colspan="8"] table tbody tr:visible').count();
  await page.goto(base + '/instances/new');
  await page.selectOption('#type', 'sonarr');
  await page.selectOption('#missing_mode', 'show_batch');
  await page.selectOption('#checked_search', 'dry_run');
  await page.waitForTimeout(300);
  const mode = await page.$eval('#missing_mode', el => el.value);
  const disabled = await page.$eval('#missing_mode option[value=season_packs]', el => el.disabled);
  const sonarrRuleVisible = await page.isVisible('#cs_check_suffix');
  const radarrRuleVisible = await page.isVisible('#cs_year_tolerance');
  console.log(JSON.stringify({ errors, rows, visibleCandidates, mode, disabled, sonarrRuleVisible, radarrRuleVisible }));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
```

und `<scratchpad>/probe-e2e/save.js`:

```javascript
// Saves the edit form once and reads the instance back (scratch only).
const { chromium } = require(process.env.PW_CORE);

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM });
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  const base = 'http://127.0.0.1:18767';
  await page.goto(base + '/login');
  await page.fill('input[name=username]', 'admin');
  await page.fill('input[name=password]', 'probe-pass');
  await Promise.all([page.waitForNavigation(), page.click('button[type=submit]')]);
  await page.goto(base + '/instances/1/edit');
  await page.fill('#cs_year_tolerance', '2');
  await page.uncheck('#cs_prefix_match');
  await page.click('#save-btn');
  await page.waitForTimeout(800);
  const toast = await page.locator('.toast').first().innerText();
  const data = await page.evaluate(async () => (await fetch('/api/instances/1')).json());
  console.log(JSON.stringify({ errors, toast, mode: data.checked_search, yt: data.checked_search_settings.year_tolerance,
    pm: data.checked_search_settings.prefix_match, codes: data.checked_search_settings.country_codes.length }));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
```

Run (mit laufender Probe aus Step 4; `PW_CORE` = Pfad zu einem vorhandenen `playwright-core`, `CHROMIUM` = Pfad zu einer Chromium-Binärdatei):

```bash
SCRATCH=<scratchpad>
test -d "$SCRATCH" || { echo "SCRATCH fehlt: $SCRATCH" >&2; exit 1; }
PW_CORE=<playwright-core> CHROMIUM=<chromium> timeout 90 node "$SCRATCH/probe-e2e/pages.js"
PW_CORE=<playwright-core> CHROMIUM=<chromium> timeout 60 node "$SCRATCH/probe-e2e/save.js"
```

Expected: `{"errors":[],"rows":4,"visibleCandidates":2,"mode":"episode","disabled":true,"sonarrRuleVisible":true,"radarrRuleVisible":false}` und `{"errors":[],"toast":"Instance saved!\n×","mode":"active","yt":2,"pm":false,"codes":21}`.

Ohne Headless-Browser dieselben Punkte von Hand im Browser: alle Seiten ohne Konsolenfehler; Pre-filter-Zeile klappt die Kandidaten auf; Sonarr + Dry run sperrt die Paket-Modi und springt auf Episode; nur die Regeln der gewählten App sind sichtbar; Speichern zeigt „Instance saved!“ und die geänderten Werte kommen zurück.

Danach beenden: `P=<scratchpad>/probe-e2e; kill -TERM "$(cat "$P/uv.pid")" "$(cat "$P/fake.pid")"`, dann `grep -c "Shutdown complete" "$P/uvicorn.log"` → `1`.

- [ ] **Step 6: Codex-Review und Schlüssel-Prüfung**

Codex-Review (Skill `codex:rescue`) nur auf den Code im Repo: Auftrag „Prüfe `git diff main...feat/checked-search` in `<repo>` gegen `docs/superpowers/specs/2026-10-01-checked-search-design.md`; lies keine Dateien außerhalb des Repos und nichts unter `data/`“. Befunde wie beim letzten Plan einzeln am Code prüfen, berechtigte als eigene Commits (`fix: …`) mit Test beheben, die Gesamtsuite danach grün.

Danach die Sitzungsdateien der heutigen Codex-Läufe auf Schlüssel prüfen, **ohne die echten Schlüssel zu kennen**: Die Radarr-, Sonarr- und Prowlarr-Schlüssel liegen nicht in der Geheimnisablage, und sie aus der Live-Datenbank oder der \*arr-Konfiguration zu holen verbieten die harten Regeln. Stattdessen nach Mustern suchen, nur Dateinamen ausgeben:

```bash
grep -rlE '[?&]apikey=[0-9a-fA-F]{20,}|X-Api-Key:[[:space:]]*[0-9a-fA-F]{20,}|"downloadUrl"[[:space:]]*:[[:space:]]*"https?://|magnet:\?xt=' <codex-sitzungsordner-der-heutigen-läufe>
```

Erwartet: keine Treffer. (`downloadUrl` allein trifft auch Quelltext und README, die das Wort nennen; gesucht wird deshalb nur ein Wert mit URL.) Bei einem Treffer: Datei nicht anzeigen, Daniel Bescheid geben. Soll doch mit den echten Schlüsseln geprüft werden, vorher Daniel fragen und die Quelle hier eintragen.

- [ ] **Step 7: Push nur nach Freigabe**

Daniel die Zusammenfassung geben (Tests, Probe, Review). Erst nach seiner Freigabe:

```bash
cd <repo>
.venv/bin/python -m pytest tests/ -q -p no:cacheprovider
git status --short docs/superpowers/plans/2026-10-01-checked-search.md   # erwartet: "?? …" (nie committet)
git log --oneline main..feat/checked-search -- docs/superpowers/plans/   # erwartet: leer
git push -u origin feat/checked-search
```

Kein `git tag`, kein Merge nach `main`. Einspielen, Probelauf mit Live-Daten und alle weiteren Schritte der Spec („Einführung“ 2–5) sind eigene Schritte mit Daniels Freigabe.

---

## Verhaltensänderungen für die Release-Notiz (0.9.0)

1. **Ohne Umschalten keine Änderung bei der Suche.** Jede bestehende Instanz hat `Checked search = Off` und sucht wie in 0.8.0 per Befehl. Neu für alle Instanzen sind nur Punkt 6 (Profiländerungen, gibt beim Update selbst nichts frei) und Punkt 16 (Befehlsprüfung gibt nur den eigenen Cache-Eintrag frei).
2. **Neuer Formularabschnitt „Checked search“** (Off / Dry run / Active) mit allen Regeln und Grenzwerten pro Instanz, jede Einstellung mit Info-Symbol. Angezeigt werden nur die Regeln der jeweiligen App.
3. **Probelauf (Dry run):** sucht wie ein normaler Lauf bei den Indexern (eine Suche pro Titel), lädt nichts, merkt nichts (kein Verlaufseintrag, kein Cache). Er übergeht den Such-Cache, also auch Titel, die früher schon gesucht wurden — dafür jeden Titel nur einmal pro Runde, Profilstand und Stand der Regel-Einstellungen, auch beim Force Run; ein Titel, dessen Laden oder Suche scheiterte, kommt im nächsten Probelauf wieder dran. „Reset dry run“ (Seite Pre-filter, API) startet eine neue Runde; das Umschalten auf Dry run startet ebenfalls eine. Ein Lauf, der vor dem Reset begann, schreibt in seine alte Runde. Der Lauf steht in der History als `success` mit `triggered_count` 0; das Aktivitätslog meldet „Dry run: N title(s) checked, M would grab“.
4. **Aktiv:** lädt das erste Release, das alle Regeln besteht, per `POST /api/v3/release` mit festgelegtem Ziel (`shouldOverride`: Film bzw. Serie und Folgen, Qualität und Sprachen wie gemeldet), damit eine Suche nach einem anderen Titel dazwischen den Grab nicht umlenkt; ein Release, das \*arr nicht genau diesem Titel zugeordnet hat, wird nie geladen. Ohne sauberes Release gilt der Titel als gesucht (`retry_hours` entscheidet). Ein geladener Titel bleibt „Search again if still missing after (days)“ (7) gesperrt, auch wenn `retry_hours` kürzer ist; steht er danach noch in der Wanted-Liste, wird er neu gesucht. Geladen wird, was \*arr bei der Suche freigegeben hat: `POST /release` lädt ohne neue Entscheidung von \*arr. Die History zeigt „geladen“ bzw. „kein sauberer Treffer“; diese Einträge brauchen keine Befehlsprüfung, der Lauf ist sofort fertig. Karte: „Confirmed / Sent“ zählt geladene und „kein sauberer Treffer“ gleich nach dem Lauf als bestätigt. Scheitert das Laden, wird kein zweites Release versucht. Lehnt \*arr ab (oder kam die Anfrage nie an), bleibt der Titel frei; ohne klare Antwort (Zeitüberschreitung, Abbruch nach dem Senden, 5xx) gilt er wie geladen („grab uncertain“, Titel gesperrt). Ein Grab, den die Datenbank nicht speichern konnte, bleibt im Speicher und sperrt seinen Titel, bis ein späterer Lauf ihn speichert. Ein Ladefehler vor der Suche ist ein `failed`-Eintrag: Ein Lauf mit Grab und Ladefehler endet als `partial`.
5. **Interaktive Suche:** `GET /api/v3/release` fragt die Indexer mit „Interactive Search“, nicht die mit „Automatic Search“. Weichen die beiden Schalter bei einem Indexer ab (oder ist die Indexer-Liste nicht lesbar), setzt der geprüfte Lauf aus, auch wenn er nichts zu suchen hätte: keine Suche, nichts gemerkt, Status `success` mit dem Hinweis „Checked search paused — …“, Warnung im Aktivitätslog; „Last sync“ auf der Karte bleibt beim letzten Lauf, der gesucht hat. Ein per `POST /release` geladenes Release gilt in \*arr als interaktiv geladen: Die Import-Sperre „matched by ID … Manual Import required“ greift nicht. „Redownload Failed from Interactive Search“ wird bei der Einführung in Radarr und Sonarr abgeschaltet; das neue Suchen übernimmt missingarr (Punkt 4).
6. **Profiländerungen (beide Suchwege, alle Instanzen):** Jeder Lauf liest Qualitätsprofile, Custom Formats, Release-Profile, die Größengrenzen der Qualitäten (`GET /api/v3/qualitydefinition`) und die Indexer-Einstellungen (`GET /api/v3/config/indexer`; Sonarr zusätzlich die Serienliste; keine Indexer-Last) und merkt sich je Profil einen Fingerabdruck der bewertungsrelevanten Felder (Namen, Beschriftungen, Hilfetexte und die Reihenfolge ungeordneter Listen zählen nicht). Mindest- und Höchstgröße je Qualität sowie „Minimum Age“, „Maximum Size“ und „Retention“ (Radarr auch die Einstellungen zu eingebrannten Untertiteln) entscheiden in \*arr über die Freigabe eines Treffers und gelten für alle Profile: Ihre Änderung wirkt wie eine Änderung jedes Profils; die bevorzugte Größe zählt nicht (sie sortiert nur). Mit „Search again after profile changes“ (neu, an, im Abschnitt Timing) darf ein gemerkter Titel wieder gesucht werden, sobald sich sein Profil ändert; Titel aus der Zeit vor 0.9.0 erst nach der ersten Änderung. Scheitert der Abruf (oder beim ersten Lauf das Speichern der Grundlinie), wird nichts freigegeben. Der Probelauf prüft nach einer Änderung immer erneut. Jede Änderung steht im Aktivitätslog („Quality profile changed: …“).
7. **Abbruch:** Instanz aus, Löschen oder Herunterfahren beendet den Lauf auch nach der Release-Suche, vor jedem `/parse` und vor dem Laden — nach dem Ausschalten startet kein Download mehr. Löschen während einer Release-Suche kann bis zur „Release search timeout“ mit 409 „still stopping“ antworten; die Meldung sagt das bei geprüfter Suche dazu.
8. **Rate-Limit:** Jede Release-Suche zählt als Aktion, auch wenn sie in die Wartezeit läuft oder die Verbindung nach dem Senden abbricht (\*arr fragt die Indexer weiter); zurückgegeben wird die Aktion nur, wenn die Anfrage \*arr nachweislich nicht erreicht hat (Verbindung abgelehnt, Name nicht auflösbar, Zeitüberschreitung beim Verbindungsaufbau) oder \*arr sie mit 3xx/4xx abgelehnt hat.
9. **Sonarr nur einzelne Folgen:** Mit Checked search ≠ Off sind Season Packs, Show Batch und Smart gesperrt (Formular) und werden vom Server mit `422` abgelehnt — auch ein `PUT`, das `checked_search` gar nicht mitschickt, wenn die gespeicherte Instanz schon geprüft sucht. Staffelpakete (Treffer mit `fullSeason`, bei Specials möglich) und Mehrfachfolgen-Releases (`mappedEpisodeInfo` mit mehr als einer Folge, Grund „multi-episode release“) werden nie geladen; der Grab nennt genau die gesuchte Folge.
10. **Sonarr-Upgrades** laufen im geprüften Modus je Folge (Cache-Schlüssel `upg:<folge>`) statt je Staffel. Der Schlüssel des anderen Suchwegs sperrt mit (Staffel bzw. Folge), damit ein Moduswechsel nichts früher freigibt. Zurück auf Off wartet die Staffelsuche, solange eine Folge der Staffel nach einem Grab der geprüften Suche gesperrt ist (Staffel-Halter `upg:sea-hold:<serie>:<staffel>`, „Search again if still missing after (days)“ bzw. bis zur Profiländerung); einzelne Folgen-Befehle gibt es dafür nicht.
11. **Neue Seite „Pre-filter“** (Spec: „Vorfilter“; englisch wie die übrige Oberfläche) mit Zählern der laufenden Runde, Filtern, „only differences“, „current round only“ (voreingestellt an), Plaketten „profile changed“ und „settings changed“, aufklappbaren Kandidaten und CSV-Download; Karte mit Plakette „dry run“/„checked“.
12. **Neue API:** `GET /api/checked-search` (mit `current_round`, je Zeile `settings_changed`), `GET /api/checked-search.csv`, `POST /api/instances/<id>/checked-search/reset-dry-run`. Alle Instanz-Antworten enthalten zusätzlich `checked_search`, `checked_search_settings` (vollständig), `dry_run_round`, `dry_run_round_started_at`, `search_again_after_profile_change`, `profile_fingerprints` und `profile_fingerprints_baseline`. Ein `PUT` ohne die neuen Felder lässt sie unverändert.
13. **Eigene Wartezeiten:** Release-Suche bis 120 s (einstellbar), Zeitbudget 25 min pro Lauf ab dessen Start (danach kein neuer Titel).
14. **Laufurteil sofort, wenn nichts mehr offen ist:** Ein Lauf, dessen Items schon alle ein Urteil haben, ist sofort fertig statt erst nach der nächsten Befehlsprüfung. Betrifft außer der geprüften Suche nur Läufe, bei denen \*arr keine Befehls-ID lieferte („nicht prüfbar“) — dasselbe Urteil, bis zu 2 Minuten früher.
15. **Hauspflege** löscht auch Vorfilter-Protokollzeilen älter als `HISTORY_RETENTION_DAYS`, außer den Probelauf-Zeilen der laufenden Runde (die Runde endet nur durch „Reset dry run“ bzw. für einzelne Titel durch eine Profil- oder Einstellungsänderung); die Logzeile nennt sie zusätzlich („; N pre-filter log row(s)“). Cache-Zeilen eines Grabs der geprüften Suche bleiben „Search again if still missing after (days)“ stehen, auch außerhalb von `retry_hours`.
16. **Befehlsprüfung (alle Instanzen):** Meldet \*arr einen Befehl als gescheitert (auch „orphaned“ nach einem Neustart), gibt missingarr den Cache-Eintrag nur frei, wenn er noch von genau diesem Befehl stammt (neue Spalte `searched_items.history_item_id`; Einträge aus 0.8.0: wenn seitdem keine neuere Suche denselben Titel gemerkt hat). Vorher konnte ein später gescheiterter alter Befehl die Sperre einer neueren Suche (oder eines Grabs der geprüften Suche) löschen, auch nachdem der Verlauf geleert oder von der Hauspflege gekürzt war.
17. **Plakette „profile changed“** vergleicht mit dem Profil, unter dem die Zeile geprüft wurde (neue Spalte `checked_search_log.profile_id`); auch das Löschen des letzten Profils zählt als Änderung.
18. **CSV-Download** liest einen Datenbankstand: Zeilen, die ein laufender Suchlauf während des Downloads schreibt, oder ein „Reset dry run“ währenddessen verdoppeln oder verlieren keine Zeile.
19. **Version 0.9.0.**

---

## Risiken

| Risiko | Wirkung | Gegenmaßnahme im Plan |
|---|---|---|
| Indexer-Last im Probelauf | Der Probelauf geht über den ganzen Rückstand, auch über schon gesuchte Titel | gleiche Last pro Titel wie bisher; begrenzt durch „Missing per run“ (Radarr 20 laut Spec-Einführung), Intervall, Rate-Limit (eine Aktion pro Titel, auch bei Zeitüberschreitung) und Zeitbudget |
| `GET /release` ist eine interaktive Suche | \*arr fragt die Indexer mit „Interactive Search“; weichen die Schalter von „Automatic Search“ ab, ändern sich Indexer-Last und Treffer, und ein Lauf würde Titel mit unvollständigen Treffern als gesucht merken | Lauf setzt aus, solange ein Indexer abweicht oder die Liste nicht lesbar ist (Entscheidung Daniel 01.10.2026; der sonntägliche Prüflauf kostet so nur ausgesetzte Läufe); Einführung: beide Schalter je Indexer gleich stellen (README „Upgrading to 0.9.0“). Ein Indexer, bei dem beide Schalter aus sind, wird von beiden Suchwegen nicht gefragt |
| Grab per `POST /release` gilt als interaktiv (`ReleaseSource=InteractiveSearch`) | Die Import-Sperre „matched by ID … Manual Import required“ (Radarr `CompletedDownloadService.cs:110`, Sonarr `:116`) entfällt: Ein fremdes Release, das der Filter durchlässt (bekannte Grenze „Noah“), wird automatisch importiert statt blockiert. Neu laden nach gescheitertem Download steuerte `AutoRedownloadFailedFromInteractiveSearch` (Voreinstellung an) | Probelauf vor Aktiv; Grenze in Spec, README und Release-Notiz genannt; „Redownload Failed from Interactive Search“ wird bei der Einführung abgeschaltet, missingarr sucht einen geladenen Titel nach „Search again if still missing after (days)“ selbst neu (Entscheidung Daniel) |
| Zwischenspeicher von \*arr (30 min, Schlüssel Indexer + `guid`, keine Verlängerung) | `POST /release` nach über 30 min → 404 → `grab_failed`, Titel bleibt frei. Eine Suche nach einem anderen Titel dazwischen ordnet denselben Treffer dem anderen Titel zu (Codex K1) | Ziel-Festlegung per `shouldOverride` (Test mit gemeinsamem Zwischenspeicher im Nachbau); der Abstand Suche → POST ist nicht begrenzt, `/parse` ist aber lokal und schnell — über 30 min kommt man nur mit sehr vielen freigegebenen Treffern, die alle in die `/parse`-Wartezeit laufen; Folge harmlos (`grab_failed`, Titel frei, Test „release expired“) |
| \*arr-Version ohne `shouldOverride` | Eine ältere Version ignoriert das Feld (unbekannte JSON-Felder) und lädt nach der Zuordnung ihres Zwischenspeichers | Quellcode Radarr 6.4 / Sonarr 4.0 geprüft; Einführung (Spec Schritt 4): laufende Versionen vor Active vergleichen |
| Keine klare Antwort auf `POST` (Zeitüberschreitung, Abbruch nach dem Senden, 5xx) | Der Download läuft vielleicht. Die Warteschlange von \*arr schützt nicht zuverlässig: `QueueSpecification` lehnt nur ab, wenn das Geladene den Cutoff erreicht oder das neue Release kein Upgrade ist, sie füllt sich erst nach dem Grab-Ereignis (5 s Entprellung, `DownloadMonitoringService`), und der Grab-Verlauf zählt bei Suchen nicht (`HistorySpecification`: „Skipping history check during search“) | `grab_uncertain`: Item `failed` mit Hinweis, Cache wie nach einem Grab, kein zweiter Kandidat (Codex K2); nach „Search again if still missing“ wieder frei oder per Cache-Reset |
| Datenbank verweigert das Schreiben nach einem Grab | Grab ohne Verlauf/Cache | `UnsavedCheckedGrab` im Speicher sperrt den Titel, der nächste Lauf speichert nach (Codex K2); Lauf endet mit `store_error`. Ein Neustart des Prozesses verliert die Liste (wie bei Befehlen seit 0.8.0): dann kann der Titel einmal zu früh frei werden |
| Löschen während einer Release-Suche | `DELETE` wartet 15 s, `GET /release` ist nicht unterbrechbar und dauert bis zur „Release search timeout“ (120 s, bis 600 s) → 409 „still stopping“ (richtig seit 5693201) | Meldung erklärt bei geprüfter Suche die Wartezeit; README: danach erneut löschen |
| Profil-Abruf je Lauf | drei kleine Abrufe, bei Sonarr zusätzlich die ganze Serienliste (`GET /api/v3/series`, bei großer Bibliothek einige MB) | Wartezeit 60 s; scheitert ein Abruf, gilt der gespeicherte Stand und nichts wird freigegeben; `includeSeries=true` an den Wanted-Listen wäre teurer (Serie je Folge) |
| Grobe Fingerabdrücke | Eine Änderung an einem Custom Format oder Release-Profil gibt die Titel **aller** Profile frei → einmalig mehr Suchen | so in der Spec gewollt („bewusst grob“); Einstellung abschaltbar; Rate-Limit und „per run“ begrenzen die Last. Reine Text-, Namens- und Reihenfolgeänderungen (Oberflächensprache, Update mit neuer Auswahlliste) zählen nicht mehr (Codex K6) |
| Neues Regel-Feld in einem künftigen \*arr-Update | ändert die Bewertung, aber nicht den Fingerabdruck (semantische Auswahl) | bewusste Lücke, im Docstring und README genannt; Feld in `fingerprint.py` aufnehmen |
| Titel ohne bekanntes Profil beim Schreiben | Cache-Eintrag ohne Fingerabdruck gälte wie ein Alt-Eintrag (Grundlinie) und könnte nach einer früheren Profiländerung sofort wieder frei werden (Codex K3) | geprüfte Suche nimmt das Profil aus dem geladenen Film bzw. der Serie, der Befehlsweg fragt die einzelne Serie, ein vorhandener Fingerabdruck wird nie durch `NULL` ersetzt; Restfall: auch der Einzelabruf scheitert oder das Profil ist neu und noch nicht im Stand — höchstens eine zusätzliche Suche |
| Staffel-Halter auf dem Befehlsweg (Codex-Runde 3, G3) | Nach dem Zurückschalten auf Off warten Sonarr-Upgrades einer Staffel bis zu „Search again if still missing after (days)“, wenn die geprüfte Suche dort zuletzt eine Folge geladen hat; freie Folgen derselben Staffel kommen so später dran | gewollt (Entscheidung Daniel 02.10.2026: lieber warten als die geladene Folge erneut suchen); der Halter endet mit dem Grab (N Tage, Profiländerung), Force Run und Cache-Reset übergehen ihn; nur Grabs (auch unklare) halten, `no_hit` nicht |
| CSV-Export in einer Lesetransaktion (Codex-Runde 3, G5) | Solange ein Download läuft, kann SQLite den WAL nicht über diesen Stand hinaus zurückschreiben (die WAL-Datei wächst während des Exports) | Export ist kurz und selten; Schreiber werden nicht blockiert; die Verbindung schließt auch bei abgebrochenem Download (`finally`) |
| Sonarr-Regeln ungemessen | Fehlurteile (z. B. Ländercode-Endungen, Nummerierung) | Probelauf schreibt jede greifende Regel einzeln mit; jede Regel abschaltbar; Ländercode-Liste einstellbar; Freigabe getrennt von Radarr |
| Gleicher Titel, gleiches Jahr | fremder Film bleibt unerkannt („Noah“) und wird jetzt auch importiert (siehe oben) | bekannte Grenze der Spec, Test dokumentiert sie |
| Unterschied Korpus-Test ↔ Betrieb bei `/parse`-Fehlern | Messung nahm bei 11 Fehlern die `movieTitles` des Treffers, der Betrieb verwirft den Kandidaten | im Zweifel nicht laden (Spec); Unterschied im Test-Docstring benannt |
| Runde 4 ohne Metadaten | zweiter Abgleich überspringt sich | Metadaten liegen seit 01.10.2026 in `referenz/r4/`; eigene Umgebungsvariable für einen anderen Ort |
| Probelauf-Runde | Reset während eines Laufs, Zeitumstellung, Force Run, Aufbewahrung | Runde als Zähler, beim Start festgehalten; Force Run übergeht die Runde nicht (Codex K8); die Hauspflege löscht keine Zeilen der laufenden Runde (Entscheidung Daniel, Codex-Runde 2 F6), die Zeilen einer langen Runde bleiben dafür über `HISTORY_RETENTION_DAYS` hinaus stehen. Ein laufender Probelauf prüft nach einem Reset seine restlichen Titel noch in der alten Runde (höchstens „per run“ Suchen) |
| Zeitstempel mit Millisekunden | gemischte Formate könnten beim Textvergleich stören | `created_at` des Protokolls und `dry_run_round_started_at` benutzen beide dasselbe Format; für die Runde wird nicht mehr nach Zeit verglichen; Aufbewahrung vergleicht mit `datetime(...)` (Präfix-sicher) |
| Worktree-Merge am Wellenende | ein Paket ändert entgegen der Zuständigkeit eine fremde Datei | Wellen-Abnahme merged nacheinander; jede Datei steht genau einmal in der Tabelle (`main.py` nur in verschiedenen Wellen) |
| Öffentliches Repo | Host-Details oder Bibliotheksdaten könnten in Tests oder im Plan landen | nur Spec-Beispiele und erfundene Titel; `tests/test_z_release.py` sucht nach Host-Pfaden in den neuen Dateien; Korpus nur per Umgebungsvariable; diese Plandatei wird nicht committet (Task Z Step 7 prüft es) |

## Bewusst nicht gemacht

- **Staffel- und Serienpakete bei Sonarr** (Nicht-Ziel der Spec): im geprüften Modus nur `episode`; Treffer mit `fullSeason` werden verworfen.
- **Sprach-, Qualitäts-, Laufzeit- oder Größenprüfung** (Nicht-Ziele bzw. offen in der Spec).
- **Textsuche statt ID-Suche**, **Schutz für Suchen außerhalb von missingarr** (Nicht-Ziele).
- **`seriesTitleInfo.year` für S2:** Sonarr setzt dort den ersten Zahlblock, nicht ein Jahr am Ende; `title_suffix` liest das Ende selbst (siehe G1.4).
- **`ß` als `s`, a/o/u und „and“ im Wortvergleich:** Die Normalisierung folgt der gemessenen Referenz V6 (Entscheidung Daniel 01.10.2026; die Spec beschreibt sie jetzt so). Nachgemessen hätte die erweiterte Lesart kein Urteil geändert (Codex K7).
- **Mehrfachfolgen-Releases bei Sonarr** (Entscheidung Daniel 01.10.2026): Ein Treffer, dessen `mappedEpisodeInfo` mehr als eine Folge nennt, fällt mit „multi-episode release“ weg. Unterstützung (jede Folge prüfen, laden und merken, schon gesammelte Folgen im Lauf überspringen) gibt es in 0.9.0 nicht. Eine Folge, die es nur als Teil einer Mehrfachfolge gibt, findet die geprüfte Suche nicht; dafür bleibt der Befehlsweg (Checked search Off).
- **Runde automatisch neu starten:** Nach einer vollständigen Probelauf-Runde findet ein Lauf nichts mehr („Nothing to search“), bis jemand „Reset dry run“ drückt oder sich ein Profil bzw. eine Regel-Einstellung ändert — gewollt, damit Daniel die Runde durchsehen kann. Auch die Aufbewahrung startet sie nicht neu: Die Hauspflege löscht keine Zeilen der laufenden Runde (Entscheidung Daniel 01.10.2026).
- **Force Run und kürzlich geladene Titel:** Der Force Run übergeht den Such-Cache wie seit 0.8.0, also auch gespeicherte und ungespeicherte Grabs der geprüften Suche (Codex-Runde 2 F2). Die Warteschlange von \*arr lehnt danach einen gleich guten oder schlechteren Treffer ab. Eine eigene Sperre für Grabs beim Force Run wäre eine neue Regel für alle Grabs, keine Lücke der Sicherung für ungespeicherte.
- **Laufenden Probelauf bei einem Reset abbrechen:** Ein Lauf, der vor dem Reset begann, prüft seine übrigen Titel noch und schreibt sie in die alte Runde (höchstens „per run“ Suchen). Eine Rundenprüfung vor jedem Titel spart das, kostet aber einen DB-Zugriff je Titel; nicht nötig, weil die Zeilen die neue Runde nicht verfälschen.
- **Abgleich unklarer Grabs mit Warteschlange und Verlauf von \*arr, Speichern über Neustarts:** Die Cache-Sperre („grab uncertain“) und die Liste ungespeicherter Grabs im Speicher reichen; ein Abgleich bräuchte weitere \*arr-Abfragen und eine eigene Tabelle (Codex K2 schlug beides vor).
- **Custom Formats und Punkte im Grab-Verlauf von \*arr:** Mit `shouldOverride` stammen sie aus dem Zwischenspeicher-Eintrag, also womöglich aus der Suche nach einem anderen Titel. Das betrifft nur Verlaufsdaten in \*arr, nicht das Ziel, die Qualität oder die Sprachen des Grabs.
- **Zeitgrenze bis zum POST (25 min nach der Suche):** Die Folge eines abgelaufenen Zwischenspeichers ist harmlos (`grab_failed`, Titel frei) und praktisch nicht erreichbar (Codex K9, optionaler Teil).
- **„Search again if still missing“ für die Upgrade-Quelle „monitored movies“:** Die Filmliste nennt jeden Film mit Datei; ob ein Grab dort noch fehlt, sagt sie nicht. Nur die Wanted-Listen (fehlend, Cutoff) geben geladene Titel nach N Tagen frei.
- **Eigener Status für abgebrochene Titel:** Ein Abbruch nach der Suche, vor einem `/parse` oder vor dem Laden hinterlässt keine Zeile; der Titel kommt im nächsten Lauf wieder dran. Der Rate-Slot bleibt belegt (die Suche lief).
- **Health-Check-Warnung bei abweichenden Indexer-Schaltern:** Der Runner prüft die Schalter bei jedem geprüften Lauf und setzt aus; ein eigener Health-Check (`health_check.py`, keinem Paket zugeteilt) wäre doppelt.
- **Profilwechsel eines Titels ohne Profiländerung als Plakette:** Ein Titel, der nur in ein anderes, unverändertes Profil verschoben wurde, bekommt auf der Seite keine Plakette „profile changed“ (die Zeile trägt einen noch gültigen Fingerabdruck); Cache und Probelauf geben ihn trotzdem frei, weil sein aktueller Fingerabdruck ein anderer ist.
- **Einzelne Folgen-Befehle statt der zurückgestellten Staffelsuche** (Codex-Runde 3, G3, Alternative): Entscheidung Daniel 02.10.2026 — die Staffel wartet, bis der Halter abläuft; Einzelbefehle kosteten mehr Indexer-Abfragen und bräuchten den Halter trotzdem, um die Staffel zu erkennen.
- **Staffel-Halter für die Fehlend-Suche in den Paket-Modi:** Mit geprüfter Suche sind Season Packs, Show Batch und Smart gesperrt. Wer auf Off **und** zugleich in einen Paket-Modus umschaltet, kann eine Staffel suchen, deren Folge die geprüfte Suche gerade geladen hat — wie ein Moduswechsel von `episode` auf einen Paket-Modus in 0.8.0. Codex hat das nicht genannt; die Entscheidung vom 02.10.2026 betrifft die Upgrades (siehe Offene Fragen).
- **Such-Cache von Radarr zurücksetzen, Seerr `preventSearch`, Einspielen:** Schritte der Spec-Einführung mit Daniels Freigabe, nicht Teil dieses Branches.

## Offene Fragen

Geklärt am 01.10.2026 (Daniel): Runde-4-Metadaten liegen in `referenz/r4/` (früher Frage 1); abweichende Indexer-Schalter lassen den geprüften Lauf aussetzen (früher Frage 2); „Redownload Failed from Interactive Search“ wird abgeschaltet, missingarr sucht nach N Tagen selbst neu (früher Frage 3). Frühere Pläne im öffentlichen Repo sind mit 9631d35 bereinigt (Platzhalter statt Host-Pfaden und Instanz-Adressen). Nach der zweiten Codex-Runde: ein ausgesetzter Lauf endet als `success` mit Hinweis, auch bei nicht lesbarer Indexer-Liste; Mehrfachfolgen-Releases fallen weg; die Hauspflege löscht keine Zeilen der laufenden Runde; es gilt der Stand der Suche (`POST /release` entscheidet nicht neu).

Zuletzt geklärt (Daniel, 01.10.2026): Ein Grab wird bei einer Profiländerung des Titels früher frei, so wie jeder Cache-Eintrag; die Warteschlangen-Prüfung von *arr verhindert doppelte gleichwertige Downloads. Nach der dritten Codex-Runde (02.10.2026): Auf dem Befehlsweg (Off) wartet eine Sonarr-Staffelsuche für Upgrades, solange eine Folge der Staffel nach einem Grab der geprüften Suche gesperrt ist; keine Einzelbefehle als Ersatz.

Entschieden (02.10.2026, nach Empfehlung, Daniel informiert): Der Staffel-Halter gilt nur für die Upgrade-Suche. Die Fehlend-Suche in Season Packs, Show Batch und Smart hält er nicht zurück; das entspricht einem Moduswechsel in 0.8.0, und mit geprüfter Suche sind diese Modi gesperrt. Keine offenen Fragen mehr.

## Kritik geprüft (Überarbeitung 01.10.2026)

Jeder Punkt der Durchsicht wurde am Code bzw. am Quellcode von Radarr 6.4 / Sonarr 4.0 geprüft.

| Punkt | Ergebnis |
|---|---|
| Blocker: Spec-Nachtrag „Profiländerungen erkennen“ fehlt, Ausgangsstand 73410b4 | **Übernommen.** Ausgangsstand e5712cf. G1.6 `fingerprint`, G2.1 sieben Spalten, G2.3/G2.5 Cache mit Fingerabdruck, G2.4 `store_profile_fingerprints`, G3.1/G3.2 `skills/profiles.py` und beide Skills, G4.1 Einstellung, G5 Kästchen, Tooltip und Plakette. Die Annahme der Spec „`series.qualityProfileId` steht in den Wanted-Listen“ stimmt für Sonarr nicht (`MissingController.cs:29`, `CutoffController.cs:33`: nur mit `includeSeries=true`); gelöst über einmal `GET /api/v3/series` pro Lauf statt `includeSeries` (Begründung in G3.1). Die Einstellung ist eine eigene Instanzspalte, nicht Teil von `checked_search_settings`. Die sieben Tests des Nachtrags stehen in G1.6, G2.5, G2.2 und G3.1. |
| Abbruch vor dem ersten `/parse` und vor dem `POST` nicht geprüft (zweimal genannt) | **Übernommen.** Prüfung nach der Suche, vor jedem `/parse` (auch dem ersten) und vor `grab`; Tests `test_abort_during_release_search_grabs_nothing` und `test_abort_after_the_last_parse_grabs_nothing`. |
| `dry_run_keys` zählt Fehlerzeilen (zweimal genannt) | **Übernommen.** `outcome != 'error'`; Tests `test_errors_do_not_count_for_the_round` (G2.2) und `test_dry_run_checks_a_title_again_after_an_error` (G3.1). |
| Interaktive Suche: andere Indexer, keine Import-Sperre, `AutoRedownloadFailedFromInteractiveSearch` (zweimal genannt) | **Übernommen** und am Quellcode bestätigt (`ReleaseSearchService.Dispatch`, `DownloadDecisionMaker.GetReleaseSource`, `CompletedDownloadService`, `RedownloadFailedDownloadService`). Beide Vorschläge kombiniert: Runner liest `GET /api/v3/indexer` einmal pro Lauf, verwirft Treffer ohne „Automatic Search“, `arr_pick` nur aus solchen Indexern, Warnung bei Abweichung; dazu Risiken, Release-Notiz, README, Offene Fragen 2 und 3. Ein eigener Health-Check wurde nicht gebaut (Bewusst nicht gemacht). **Überholt** in der zweiten Überarbeitung: Bei abweichenden Schaltern setzt der Lauf jetzt aus, „Redownload Failed from Interactive Search“ wird abgeschaltet (Entscheidungen Daniel, Tabelle unten). |
| Rate-Slot geht auch bei Zeitüberschreitung von `/release` zurück (zweimal genannt) | **Übernommen** in der genaueren Form: zurück nur bei Fehler vor der Suche, `ConnectionError` oder 3xx/4xx; bei `ReadTimeout`, 5xx und unlesbarer Antwort bleibt er belegt (`_Result.searched`). Tests für `ReadTimeout` (1) und `ConnectionError` (0); der Test „Fehler vor der Suche“ bleibt. **Nachgeschärft** in der zweiten Überarbeitung (K5): Nicht jede `ConnectionError` heißt „nicht gesendet“. |
| Sonarr-Staffelpakete bei Specials, S3-Beispiel ist ein Paket | **Übernommen**, am Quellcode bestätigt (`SingleEpisodeSearchMatchSpecification` lehnt Pakete bei `SingleEpisodeSearchCriteria` ab, `SpecialEpisodeSearchCriteria` nicht). Statt stillem Überspringen wird ein Treffer mit `fullSeason` als Kandidat mit Grund „season pack“ verworfen, ohne `/parse` — so bleibt sichtbar, was \*arr genommen hätte. Test `test_sonarr_season_packs_are_not_taken`; S3-Test mit `A.Better.Place.S01E03…`; Hinweis in G1.4 und README. |
| Löschen während `GET /release` → 409 | **Übernommen:** Risiken, README und Zusatz in der 409-Meldung bei geprüfter Suche (Test `test_delete_during_a_checked_search_explains_the_wait`). |
| Korpus-Test prüft nur Zahlen je Film | **Übernommen**, nachgemessen: je Kandidat `2477 / 2278 / 199`, Gründe `year 148, other movie 69, title 88, title without year 9`; die Abweichungen bei Veto (4), Regel d (7), Jahrestoleranz 2 (2), Präfixlänge 5/7 (1/2) fallen damit auf. **Nicht** zutreffend ist, dass `word_min_core_words=3` damit auffiele: Es ändert im Korpus kein einziges Kandidaten-Urteil (nachgemessen); der Wert ist durch den Regeltest mit `4` abgedeckt. |
| Testdaten aus Daniels Messmaterial | **Übernommen.** Global Constraint auf Spec-Beispiele und erfundene Titel eingeschränkt; ersetzt wurden Brewster McCloud, Alone in the Dark, Independence Day, Heat, Schöne Bescherung, Fast & Furious, Tom & Jerry, Amélie, The Office, 1923, The Farmer Wants a Wife, ein Alternativtitel von „The Guest“ sowie die Gruppen `HDSource`, `HiggsBoson`, `4SF` (→ `-GRP`); Indexer-Name und Hostname `prowlarr` in den Test-Treffern durch neutrale Werte. |
| Schlüssel-Prüfung der Codex-Sitzungen braucht Schlüssel, die nicht in der Ablage liegen | **Übernommen** (bestätigt: die Schlüsselablage des Servers enthält keine \*arr-/Prowlarr-Schlüssel). Muster-Suche ohne echte Schlüssel; `downloadUrl` nur als JSON-Wert mit URL, weil das Wort selbst in Code und README steht. |
| Seite filtert nicht nach der aktuellen Runde | **Übernommen:** `current_round` in `query/count/summary/iter_csv`, API und Kästchen „current round only“ (an); Zähler oben zeigen die laufende Runde. Tests in G2.2, G4.2, G5.2. |
| Karte zeigt nach aktivem Lauf „0 bestätigt“ | **Übernommen:** `finish_search_run` liest `last_verified` aus dem gerade beendeten Lauf; für Befehls-Läufe bleibt es 0 (bestehende Tests grün). |
| Schlüsseltest nur ohne Fehler, `magnetUrl` fehlt | **Übernommen:** Fixture mit `magnetUrl` (Passkey-artig), Test über fünf Abläufe inkl. Grab-, Such- und Ladefehler. |
| Plandatei mit Host-Details, Commit unklar | **Übernommen:** Plandatei wird nicht committet (Global Constraints, Task Z Step 7 prüft es). Eine Prüfung in `test_z_release.py` ist dann unnötig. Alte Pläne: Offene Frage 1. |

## Kritik geprüft (zweite Überarbeitung 01.10.2026: Codex-Durchsicht des Plans K1–K9, Entscheidungen Daniel)

Jeder Befund wurde am Plan-Code und am Quellcode von Radarr 6.4 / Sonarr 4.0 gegengeprüft (Widerlegung versucht), Proben nur in Wegwerf-Klonen im Scratchpad.

| Punkt | Ergebnis |
|---|---|
| K1 (hoch): Grab kann an einen anderen Titel gehen — \*arr hält Treffer unter Indexer + `guid`, eine überlappende Suche überschreibt die Zuordnung, `POST {guid, indexerId}` lädt für den Titel im Zwischenspeicher | **Bestätigt, übernommen.** POST mit `shouldOverride` und Ziel (Radarr `movieId`; Sonarr `seriesId` + gemeldete `episodeIds`), `quality`/`languages` unverändert wie gemeldet; Treffer ohne genau passende Zuordnung (oder ohne Qualität/Sprachen) werden verworfen (`not mapped to this title`). Nachbau mit gemeinsamem Release-Zwischenspeicher, je ein Überlappungs-Test für Radarr und Sonarr, Zuordnungs-Tests; Spec Ablauf 4, Risiken, README, Einführung (Versionen prüfen). Restfall Verlaufsdaten unter „Bewusst nicht gemacht“. |
| K2 (hoch): Grab ohne klare Antwort oder ungespeicherter Grab hinterlässt keinen Schutz; die Warteschlange von \*arr fängt einen zweiten Grab nicht sicher ab | **Bestätigt, übernommen** in der knappen Form: abgelehnt (nicht gesendet, 3xx/4xx) → `grab_failed`, Titel frei; sonst `grab_uncertain` (neues Protokoll-Ergebnis), Item `failed` **mit** Cache (`record_checked(cache=True)`); ungespeicherte Grabs als `UnsavedCheckedGrab` in der Liste der ungespeicherten Befehle, sperren und werden nachgespeichert. **Nicht übernommen:** Abgleich mit Warteschlange/Verlauf und Speichern über Neustarts (Bewusst nicht gemacht, Risiken). Tests: unklarer Grab sperrt, abgelehnter (404, 409, nicht gesendet) bleibt frei, Nachspeichern, Sperre während die DB verweigert. |
| K3 (mittel, eher niedrig): neuer Cache-Eintrag mit `NULL`-Fingerabdruck gilt als Grundlinie und wird sofort wieder frei | **Bestätigt, übernommen.** Geprüfte Suche nimmt den Fingerabdruck aus dem geladenen Film bzw. der Serie; Befehlsweg fragt bei nicht lesbarer Serienliste die einzelne Serie (`stored_fingerprint`); `COALESCE` im Cache-Upsert. Tests (aktiv, Probelauf, Befehlsweg, COALESCE in G2.3/G2.5). Spec unverändert (verlangte das schon), Text präzisiert. |
| K4 (mittel): Ladefehler neben einem Grab ergibt `success` statt `partial` | **Bestätigt, übernommen.** Ladefehler → Item `failed` ohne Cache; Test umbenannt (`…_is_a_failed_item`) und gemischter Lauf → `partial`. Spec-Widerspruch (Z. 50 gegen Z. 108) aufgelöst. Der Teil „verified counts bei partial“ trifft nicht zu (`finish_run` rechnet sie schon). |
| K5 (mittel): `ConnectionError` heißt nicht „nicht gesendet“ | **Bestätigt, übernommen.** `_refused`/`_not_sent`: zurück nur bei `ConnectTimeout` oder `MaxRetryError(NewConnectionError)` (auch Namensauflösung) bzw. 3xx/4xx; Reset/Close nach dem Senden, hängender Körper, `ReadTimeout`, 5xx behalten den Slot. Parametrisierter Test mit echten Fehlerformen (8 Fälle); dieselbe Einteilung entscheidet beim Grab zwischen abgelehnt und unklar. |
| K6 (mittel): Fingerabdruck über ganze Ressourcen ändert sich mit Oberflächensprache, Update, Mengen-Reihenfolge | **Bestätigt, übernommen** (Spec-Änderung, Entscheidung Daniel): semantische Auswahl, Mengen sortiert, Rangfolge der Qualitäten bleibt. Tests mit echter Ressourcenform, Text- und Reihenfolgeänderungen, jedem Regel-Feld. Bewusste Lücke (neue Regel-Felder) dokumentiert. |
| K7 (mittel, teilweise): Normalisierung weicht von der Spec ab | **Teilweise bestätigt** (Abweichung besteht, Folgen nicht: beide Beispiele gehen über den Präfix durch, die Spec-Lesart änderte auf R5/R4 kein Urteil). **Nicht nach Codex umgesetzt:** Entscheidung Daniel — verbindlich ist die Referenz V6, der Spec-Wortlaut wurde an die Referenz angepasst; Plantext korrigiert (die frühere Begründung „Korpus-Zahlen hängen daran“ stimmte nicht). |
| K8 (mittel): Force Run umgeht die Runde; Reset während eines Laufs ordnet dessen Zeilen der neuen Runde zu (auch Zeitumstellung) | **Bestätigt, übernommen** (Entscheidung Daniel): Runde als Zähler `dry_run_round` (Instanz und Protokollzeile), beim Start festgehalten (`run_checked(config=…)`); `force` übergeht die Runde nicht mehr. Tests: Force Run (Fehlend und Upgrades), Reset während des Laufs, ältere Runde außerhalb der aktuellen. Optionaler Abbruch bei Reset nicht gebaut (Bewusst nicht gemacht). |
| K9 (teilweise): Zeitbudget beginnt erst im Runner; Zwischenspeicher-Ablauf nicht abgesichert | **Teilweise bestätigt.** Übernommen: `started` vom Skill-Start, Test mit verbrauchtem Budget vor dem ersten Titel; Spec- und Risiken-Text zum unbegrenzten Abstand Suche → POST. **Nicht übernommen:** eigene 25-Minuten-Grenze vor dem POST (Folge harmlos, praktisch nicht erreichbar); Test für 404 deckt `test_a_refused_grab_leaves_the_title_free` ab. |
| Entscheidung: abweichende Indexer-Schalter → Lauf setzt aus | **Umgesetzt** (`indexer_pause`, damals `_indexer_pause`, `SubmitOutcome.paused`, Status `error` mit „Checked search paused — …“); ersetzt das Verwerfen von Treffern solcher Indexer und `REASON_INDEXER`. Auch eine nicht lesbare Indexer-Liste lässt den Lauf aussetzen (im Zweifel nicht suchen). Test mit drei Fällen. **Überholt** in der dritten Überarbeitung: Status `success` mit Hinweis, `last_sync` bleibt (Entscheidung Daniel, Tabelle unten). |
| Entscheidung: „Redownload Failed from Interactive Search“ aus, dafür „Search again if still missing after (days)“ | **Umgesetzt:** Einstellung (7, 1–365, Info-Symbol), `searched_items.grabbed_at`, `lookup_many(grab_release_days=)` nur für Wanted-Listen, unabhängig von `retry_hours`. Tests in G1.2, G2.3, G2.5, G3.1, G3.2; Spec Einführung Schritt 2. |
| Entscheidung: Regel-Einstellungen geändert → Probelauf prüft neu, „settings changed“ | **Umgesetzt:** `rules_fingerprint` (Regeln der App + `dry_run_max_releases`), je Protokollzeile `settings_fingerprint`, `round_blocks` vergleicht Profil und Einstellungen, API setzt `settings_changed`, Plakette auf der Seite. Tests in G1.2, G2.2, G3.1, G4.2, G5.2. |
| Entscheidung: Runde-4-Abgleich | **Umgesetzt und nachgerechnet:** Metadaten in `referenz/r4/`, Test liest den Vorgabe-Ordner (auch als `korpus/` übergeben) bzw. `MISSINGARR_VORFILTER_R4_META`; `gleich 61, anders 1, weg 4`, je Kandidat `453/440/13`; mit den Urteilen der Verprobung (nur im Scratchpad) 66 → 62, 0 fremd. |

## Kritik geprüft (dritte Überarbeitung 01.10.2026: zweite Codex-Runde F1–F9, Entscheidungen Daniel)

Jeder Befund wurde am Plan-Code, am Bestand von 0.8.0 und am Quellcode von Radarr 6.4 / Sonarr 4.0 gegengeprüft (Widerlegung versucht), Proben nur in Wegwerf-Kopien im Scratchpad.

| Punkt | Ergebnis |
|---|---|
| F1 (hoch, eher mittel): Grab-Sperre hängt an `retry_hours`, die Hauspflege löscht den Grab | **Teilweise bestätigt, übernommen.** Bestätigt (Probe): Der Zeitfenster-Filter in `lookup_many` warf eine Grab-Zeile nach `retry_hours` heraus, die Sperre war min(`retry_hours`, N Tage); `purge_expired` löschte sie stündlich. Jetzt lässt der Filter Grab-Zeilen bei `grab_release_days > 0` durch, `grab_due` entscheidet (G2.5), und `purge_expired(…, keep_grab_days=)` hält sie N Tage (G2.3, Wert roh aus `checked_search_settings`, auch bei Off). Tests: Grab mit `retry_hours=24` sperrt 7 Tage, `no_hit` wird nach `retry_hours` frei, ohne `grab_release_days` (monitored movies) gilt das Fenster, Hauspflege lässt den 2 Tage alten Grab stehen und löscht ihn nach 8 Tagen. **Widerlegt:** Freigabe nach einer Profiländerung ist kein Widerspruch, die Spec gibt jeden Eintrag unter geändertem Fingerabdruck frei (Text in G2.5 eindeutig, Offene Frage 1). Schwere nur mittel: Voreinstellung `retry_hours = 0`. |
| F2 (hoch): Force Run übergeht ungespeicherte Grabs | **Widerlegt.** 0.8.0 übergeht beim Force Run Cache und ungespeicherte Befehle gemeinsam (`search_missing.py`, `search_upgrades.py`: `hits = {} if force else {…}`); ein gespeicherter Grab verhält sich beim Force Run genauso, normale Läufe sperren ungespeicherte Grabs (Test `test_an_unsaved_grab_blocks_its_title_while_the_database_refuses`). Eine Ausnahme nur für ungespeicherte wäre widersprüchlich. Klarstellung in G3.1, Spec und „Bewusst nicht gemacht“. |
| F3 (hoch): Prüfung eines alten Befehls löscht die Sperre eines neueren Grabs | **Bestätigt (Probe: zweiter Grab), übernommen.** `resolve_item` löscht den Cache-Eintrag nur, wenn kein neueres Item desselben Schlüssels ihn geschrieben hat (Vergleich über die Item-ID; zählt Befehle und `grabbed`/`no_hit`/`failed` mit Schlüssel). Tests: altes Item + neuer Grab + `failed` direkt und über `VerifyCommandsSkill` mit „orphaned“ (Sperre bleibt, keine „released“-Meldung), Gegentest: ein alleinstehender gescheiterter Befehl gibt seinen Eintrag weiter frei. Schließt nebenbei denselben Wettlauf zwischen zwei Befehlen aus 0.8.0. Spec-Satz ergänzt. **Nachgeschärft** in der vierten Überarbeitung (G1): Besitz am Cache-Eintrag (`history_item_id`), weil `clear()`/`purge_old_runs()` die neueren Items löschen können. |
| F4 (hoch, eher mittel): Mehrfachfolgen werden nur für eine Folge geprüft und gemerkt | **Bestätigt, umgesetzt nach Entscheidung Daniel:** Treffer mit mehr als einer Folge in `mappedEpisodeInfo` fallen mit `multi-episode release` weg (ohne `/parse`), der POST nennt genau die gesuchte Folge; keine Mehrfachfolgen-Unterstützung in 0.9.0 („Bewusst nicht gemacht“, README). Test aktiv und Probelauf. |
| F5 (mittel, eher niedrig): alte `upg:sea:`-Einträge sperren im geprüften Modus nicht | **Teilweise bestätigt, übernommen statt nur dokumentiert.** `_keep_uncached` fragt bei Sonarr den Schlüssel des anderen Suchwegs mit ab (geprüft: Staffel; Befehl: Folge), mit demselben Fingerabdruck-Filter. Damit hält die Spec-Zusage „Das Update allein gibt nichts frei“ auch für Sonarr-Upgrades, in beiden Richtungen. Tests: Staffel-Eintrag aus 0.8.0 sperrt die Folge, nach Profiländerung frei, mit Einstellung aus weiter gesperrt; Folgen-Eintrag sperrt die Staffel auf dem Befehlsweg, eine freie Folge derselben Staffel löst `SeasonSearch` aus. Der Eintrag in „Bewusst nicht gemacht“ entfällt. **Nachgeschärft** in der vierten Überarbeitung (G3, Entscheidung Daniel): Nach einem Grab wartet die Staffel auf dem Befehlsweg (Staffel-Halter); nur nach `no_hit` löst eine freie Folge weiter `SeasonSearch` aus. |
| F6 (mittel): Aufbewahrung startet die Probelauf-Runde still neu | **Bestätigt, umgesetzt nach Entscheidung Daniel:** `purge_old` löscht nie Probelauf-Zeilen der laufenden Runde; ältere Runden und aktive Zeilen wie bisher. Tests: alte Zeile der laufenden Runde bleibt (`dry_run_keys` liefert sie weiter), nach einem Rundenwechsel wird sie gelöscht. |
| F7 (mittel, eher niedrig): Plakette „profile changed“ von einem unveränderten Zwillingsprofil verdeckt | **Bestätigt (Probe), übernommen.** Neue Spalte `checked_search_log.profile_id`, der Runner schreibt das Profil des geladenen Titels, `_row` vergleicht mit genau diesem Profil (gelöscht = geändert), Zeilen ohne ID wie bisher. Tests: zwei Profile mit gleichem Fingerabdruck, eines geändert; Runner schreibt `profile_id`. |
| F8 (niedrig): S3 rundet vor dem Vergleich ab | **Teilweise bestätigt, übernommen.** Das Abrunden war festgelegt, passte aber nicht zum Wortlaut „mehr als X Tage“. Jetzt genauer Vergleich der Zeitspannen, nur der Hinweis zeigt ganze Tage. Grenztest 365 Tage + 1 Stunde (verworfen) und genau 365 Tage (Hinweis). |
| F9 (niedrig): Release-Profil-ID im Fingerabdruck | **Bestätigt, übernommen.** `id` gestrichen (upstream entscheidet sie nichts; Spec nannte sie schon nicht), Test mit neu angelegtem Release-Profil. Die Custom-Format-ID bleibt (`formatItems.format`). |
| Entscheidung: ausgesetzter Lauf | **Umgesetzt:** Status `success` mit Hinweis „Checked search paused — …“ am Lauf, Warnung im Aktivitätslog; nicht lesbare Indexer-Liste mit eigenem Hinweistext. `last_sync` bleibt stehen (Begründung G3.1: die Karte zeigt so ein anhaltendes Aussetzen, ohne dass der Lauf als Fehler zählt). Test mit drei Fällen, Status, Warnstufe, `last_sync`. **Nachgeschärft** in der vierten Überarbeitung (G4): Die Prüfung steht vor dem Sammeln, auch Läufe ohne Titel setzen aus. |
| Entscheidung: Stand der Suche gilt | **Umgesetzt:** Spec-Satz „Radarr und Sonarr prüfen die Profile trotzdem“ richtiggestellt (`ReleaseController.DownloadRelease` → `DownloadService.DownloadReport` ohne neue Entscheidung, in beiden Quellbäumen); README und Release-Notiz nennen es. |

## Kritik geprüft (vierte Überarbeitung 02.10.2026: dritte Codex-Runde G1–G6, Entscheidung Daniel)

Jeder Befund wurde am Plan-Code, am Bestand von 0.8.0 und am Quellcode von Radarr 6.4 / Sonarr 4.0 gegengeprüft (Widerlegung versucht, nicht gelungen), Proben nur in Wegwerf-Kopien im Scratchpad. Keiner der sechs Befunde ist widerlegt.

| Punkt | Ergebnis |
|---|---|
| G1 (hoch, eher mittel): „Clear history“ hebt den Schutz der Grab-Sperre auf | **Bestätigt (Probe: nach `history.clear()` meldete die Befehlsprüfung für den alten Befehl „released“, die Sperre war weg; derselbe Weg über `purge_old_runs` bei kurzer Aufbewahrung), übernommen.** Die Besitzfrage hängt jetzt am Cache-Eintrag: neue Spalte `searched_items.history_item_id` (ohne Fremdschlüssel), gesetzt von `record_submission` und `record_checked` in derselben Transaktion; `resolve_item` löscht bei `failed` nur einen Eintrag, der noch diesem Item gehört; Einträge aus 0.8.0 (ohne Item-ID, ohne `grabbed_at`) behalten die bisherige Prüfung über neuere Items. Tests: Eintrag nennt sein Item, `clear()` und `purge_old_runs()` zwischen Grab und „orphaned“ (Sperre bleibt, keine „released“-Meldung), Eintrag aus 0.8.0 wird weiter frei, der Gegentest „eigener Eintrag wird frei“ bleibt. Der Satz „`clear()`, `purge_old_runs()` brauchen keine Änderung“ ist präzisiert (G2.3). Den Mindestschutz „nie mit `grabbed_at` löschen“ allein hätte ein späteres `no_hit` nicht geschützt. |
| G2 (hoch, eher mittel bis niedrig): gescheitertes erstes Speichern der Grundlinie gibt Alt-Einträge frei | **Bestätigt (Probe mit `database is locked`: der Alt-Eintrag wurde frei, auch bei Off), übernommen.** `profiles.refresh` setzt im Fehlerfall die Grundlinie dieses Laufs auf den gelesenen Stand, solange keine gespeichert ist; der nächste Lauf speichert sie (`COALESCE`). Test für Off und Active: erster Lauf mit gescheitertem Speichern sperrt weiter und warnt, zweiter Lauf speichert die Grundlinie und sperrt weiter. Spec Z. 148 und Docstring ergänzt. |
| G3 (hoch, eher mittel): eine geprüft geladene Folge schützt ihre Geschwister nicht vor einer Staffelsuche | **Bestätigt (Quellcode: `SeasonSearch` mit `missingOnly: false`, `HistorySpecification` überspringt Suchen, `ProcessDownloadDecisions` lädt jeden Treffer; der Plan-Test schrieb das Verhalten sogar fest), umgesetzt nach Entscheidung Daniel 02.10.2026:** Auf Off wartet die Staffelsuche für Upgrades, solange eine Folge der Staffel nach einem Grab (`grabbed`/`grab_uncertain`) gesperrt ist; keine Einzelbefehle. Umsetzung mit dem Staffel-Halter `upg:sea-hold:<serie>:<staffel>`, den der Grab in derselben Transaktion schreibt (`record_checked(hold_key=)`, auch ungespeichert und beim Nachspeichern); nur der Befehlsweg fragt ihn ab. Tests: Halter wird nur bei Grab/unklarem Grab geschrieben (G2.3); Off-Lauf mit `grabbed` und `grab_uncertain`, geladene Folge nicht mehr in der Cutoff-Liste und noch drin → keine Befehle, nach N Tagen `SeasonSearch`; Profiländerung gibt frei; im geprüften Modus hält der Grab die Geschwister nicht auf; ungespeicherter Grab hält ebenso; `no_hit` hält nur die eigene Folge (bisheriger Test umbenannt, Erwartung `SeasonSearch` bleibt dort richtig). Spec Z. 144 ergänzt. Gleichartiger Restfall in der Fehlend-Suche: Offene Frage 1. |
| G4 (mittel, eher niedrig bis mittel): Läufe ohne Titel umgehen die Indexer-Prüfung und rücken `last_sync` vor | **Bestätigt (Probe: fertige Runde und leere Liste → `success` ohne Hinweis, `last_sync` neu, kein Indexer-Abruf), übernommen.** Beide Skills lesen die Indexer-Liste nach dem Profilstand und vor dem Sammeln (`indexer_pause`, jetzt öffentlich), `run_checked(check_indexers=False)` liest sie dann nicht noch einmal. „per run“ = 0 prüft nicht (nichts zu tun ist kein geprüfter Lauf). Tests: fertige Probelauf-Runde, alles gesperrt, leere Wanted-Liste (Fehlend) und leerer Upgrade-Lauf, jeweils Status `success`, Hinweis, Warnung, `last_sync` unverändert, keine Release-Suche. Spec Z. 69 klargestellt. |
| G5 (mittel, eher niedrig): CSV mit `OFFSET`-Seiten verdoppelt oder verliert Zeilen bei gleichzeitigen Änderungen | **Bestätigt (Probe: Grenzzeile doppelt nach einer neuen Zeile; mit `current_round` und Reset fehlten alle Zeilen der Runde), übernommen.** `iter_csv` liest aus einer Lesetransaktion: eine Verbindung, `BEGIN`, eine Abfrage (`_SELECT`, gemeinsam mit `query`), `fetchmany`, `finally` schließt auch bei Abbruch. **Teilweise zutreffend:** Die Hauspflege löscht nur alte Zeilen am Ende der Sortierung und ließ keine Zeile aus; eine Lücke entstand nur durch Löschen davor (Instanz löschen). Tests: neue Zeile nach der ersten Seite (keine doppelte, keine neue), Reset nach der ersten Seite (alle Zeilen der Runde). |
| G6 (niedrig): Löschen des letzten Profils unterdrückt die Plakette | **Bestätigt (Quellcode: `QualityProfileService.Delete` sperrt nur benutzte Profile, Standardprofile erst beim nächsten Start), übernommen.** „Bekannt“ heißt jetzt Grundlinie gesetzt (`fingerprints_known` in `_SELECT`), nicht „aktueller Stand nicht leer“. Test: leerer Stand mit Grundlinie markiert Zeilen mit und ohne `profile_id`; die beiden bestehenden Plakettentests setzen jetzt auch die Grundlinie. Spec unverändert (verlangte das schon). |

## Selbstprüfung

- **Abdeckung:** Jeder Punkt der Spec einschließlich der Nachträge steht in der Tabelle „Spec → Paket → Task“ mit Task und Test. Fehlerfälle (Laden, Suche, `/parse`, POST abgelehnt und unklar, Speichern), Zeitbudget (auch vor dem ersten Titel verbraucht), Abbruch (zwischen Titeln, nach der Suche, vor `/parse`, vor dem POST), Rate-Limit (Zeitüberschreitung, Verbindungsfehler vor und nach dem Senden, 4xx, 5xx), Ziel-Festlegung (Überlappung Radarr und Sonarr, Zuordnung), Sonarr-Sperre, Staffelpakete, Indexer-Schalter (Aussetzen), Geheimnisse (auch in Fehlerpfaden), Probelauf-Runde (Zähler, Force Run, Reset während des Laufs, Regel-Einstellungen), Reset, aktuelle Runde, Hauspflege, Profiländerungen (Cache, Alt-Einträge, Einstellung aus, Probelauf, Abruf-Fehler, Sonarr über die Serienliste und ohne sie, Upgrades), „Search again if still missing“ (auch bei kurzem `retry_hours` und in der Hauspflege), neue Status in Aggregation/Verifikation/clear/purge/History, gemischter Lauf `partial`, Befehlsprüfung neben einem neueren Grab, Mehrfachfolgen, alte Staffel-Schlüssel bei Sonarr-Upgrades (beide Richtungen), Aufbewahrung der laufenden Runde, Plakette bei Zwillingsprofilen, S3-Grenze, neu angelegtes Release-Profil, ausgesetzter Lauf als `success` mit `last_sync` haben je einen Test; seit der vierten Überarbeitung auch Befehlsprüfung nach geleertem bzw. gekürztem Verlauf, gescheitertes erstes Speichern der Grundlinie, Staffel-Halter für Sonarr-Upgrades auf Off (Grab, unklarer Grab, ungespeichert, Ablauf, Profiländerung, geprüfter Modus), Aussetzen ohne Titel, CSV aus einem Datenbankstand und gelöschtes letztes Profil.
- **Dateizuständigkeit:** Jede geänderte Datei gehört genau einem Paket (neu: `fingerprint.py` → G1, `db/searched.py` → G2, `skills/profiles.py` → G3); `backend/main.py` ändern G4 (Welle 2) und G5 (Welle 3) nacheinander. Parallele Pakete (G1 ‖ G2, G3 ‖ G4) teilen keine Datei.
- **Namen:** `run_checked`, `CheckedTask`, `CheckedRunOutcome`, `record_checked`, `dry_run_keys`, `reset_dry_run`, `store_profile_fingerprints`, `lookup_many(fingerprints=…)`, `ProfileState`, `refresh_profiles`, `fingerprint_of`, `count_verified`, `checked_mode_conflict`, `CHECKED_SEARCH_MODE_MESSAGE`, `checked_search_form`, `checkedSearchSettings`, `checkedSearchPage`, `search_again_after_profile_change`, `search_again_after_days`, `rules_fingerprint`, `stored_fingerprint`, `UnsavedCheckedGrab`, `SubmitOutcome.paused`, `OUTCOME_GRAB_UNCERTAIN`/`grab_uncertain`, `REASON_TARGET`, `dry_run_round`, `settings_fingerprint`, `grabbed_at`, `grab_release_days`, `keep_grab_days`, `_grab_days`, `REASON_MULTI_EPISODE`/`multi-episode release`, `checked_search_log.profile_id`, `history_item_id`, `hold_key`/`upg:sea-hold:`, `_hold_key`, `indexer_pause`, `check_indexers`, `_SELECT`, `fingerprints_known` sind im Vertrag und in den Tasks gleich geschrieben.
- **Probe der vierten Überarbeitung (02.10.2026, nur in Wegwerf-Kopien im Scratchpad; Repo-Code, Live-Datenbanken, Container und \*arr unberührt):** Der fertige Plantext wurde per Skript auf eine frische Kopie von `feat/checked-search` (9631d35, Ausgangsstand `353 passed`) angewendet (32 ganze Dateien, 108 Edit-Paare, jede `old`-Stelle genau einmal gefunden). Gesamtsuite `633 passed, 2 skipped`; mit Korpus (Runde 5 und Runde 4) `635 passed`. Je Datei: G1 `10+8+24+14+23` und 2 übersprungen (mit Korpus `2 passed`), G2 `3+21+24+9+9` (database, log, history, instances, searched), G3 `88` (G3.1 `73`, vor der G3.2-Umsetzung `-k upgrade` → `14 failed, 1 passed`), G4 `11+20`, G5 `8+6`, Z `2`. Wellen einzeln: G1 allein `432 passed, 2 skipped`, G2 allein `419 passed`, Welle 1 `498 passed, 2 skipped`, G1+G2+G3.1 `571 passed, 2 skipped`, G1+G2+G3 `586 passed, 2 skipped`, G1+G2+G4 `529 passed, 2 skipped`, Welle 2 `617 passed, 2 skipped`, Welle 3 `631 passed, 2 skipped`. Gegenprobe: Jede neue Behebung (G1 Besitz in `resolve_item`, G1 Item-ID in `record_checked` und `record_submission`, G2, G3 Halter schreiben / auf dem Befehlsweg fragen / im geprüften Modus nicht fragen / ungespeichert sperren, G4 Fehlend und Upgrades zurück auf die Prüfung nur im Runner, G5 zurück auf `OFFSET`-Seiten, G6) wurde in einer Wegwerf-Kopie einzeln zurückgenommen; ihre Tests schlugen jedes Mal fehl, die übrigen Tests derselben Auswahl blieben grün (u. a. der Gegentest „eigener Eintrag wird frei“, die drei bisherigen Aussetz-Tests, die übrigen CSV-Tests). Ende-zu-Ende-Probe (Task Z Step 4) gegen die Wegwerf-Kopie wiederholt: alle dort genannten Ausgaben, Grab `[('guid-b', 7, True, 1, 7, [4])]`, CSV mit drei Kandidatenzeilen, `cache [('mov:1', 1, 1), ('mov:2', 1, 1)]`, `secrets in db: []`, `0`, Shutdown sauber. Browser-Probe (Step 5) nicht wiederholt: Seiten und Formular sind unverändert (Hilfetext und README nur Wortlaut), `test_g5_*` grün.
- **Probe der dritten Überarbeitung (01.10.2026, nur in Wegwerf-Kopien im Scratchpad; Repo-Code, Live-Datenbanken, Container und \*arr unberührt):** Der fertige Plantext wurde per Skript auf frische Klone von `feat/checked-search` (9631d35, Ausgangsstand `353 passed`) angewendet (32 ganze Dateien, 108 Edit-Paare, jede `old`-Stelle genau einmal gefunden). Gesamtsuite `614 passed, 2 skipped`; mit Korpus (Runde 5 und Runde 4) `616 passed`. Je Datei: G1 `10+8+24+14+23` und 2 übersprungen (mit Korpus `2 passed`), G2 `3+18+19+9+9` (database, log, history, instances, searched), G3 `77` (G3.1 `68`, vor der G3.2-Umsetzung `-k upgrade` → `8 failed, 1 passed`), G4 `11+20`, G5 `8+6`, Z `2`. Wellen einzeln: G1 allein `432 passed, 2 skipped`, G2 allein `411 passed`, Welle 1 `490 passed, 2 skipped`, G1+G2+G3 `567 passed, 2 skipped`, G1+G2+G4 `521 passed, 2 skipped`, Welle 2 `598 passed, 2 skipped`, Welle 3 `612 passed, 2 skipped`. Runde 4 mit den Urteilen der Verprobung unverändert `anders_richtig 1, gleich_richtig 61, weg_fremd 4` (66 → 62, 0 fremd). Gegenprobe: Jede neue Behebung (F1 Zeitfenster, F1 Hauspflege und ihr Aufruf, F3, F4, F5, F6, F7 Vergleich und Runner, F8, F9, Aussetzen als `success`, `last_sync`) wurde im Wegwerf-Klon einzeln zurückgenommen; ihre Tests schlugen jedes Mal fehl (die Gegentests „eigener Eintrag wird frei“ bzw. „aktive Zeilen werden gelöscht“ blieben wie gewollt grün). Ende-zu-Ende- und Browser-Probe (Task Z Step 4/5) nicht wiederholt: Die Änderungen betreffen Radarr-Grab, Seiten und Formular nicht (Hilfetext und README nur Wortlaut).
- **Probe der zweiten Überarbeitung (01.10.2026, nur in Wegwerf-Klonen im Scratchpad; Repo-Code, Live-Datenbanken, Container und \*arr unberührt):** Der fertige Plantext wurde per Skript auf frische Klone von `feat/checked-search` (e5712cf) angewendet (32 ganze Dateien, 102 Edit-Paare, jede `old`-Stelle genau einmal gefunden). Gesamtsuite `597 passed, 2 skipped`; mit Korpus (Runde 5 und Runde 4) `599 passed`. Je Datei: G1 `10+8+24+13+22` und 2 übersprungen (mit Korpus `2 passed`), G2 `3+15+14+9+8`, G3 `71` (G3.1 `65`, vor der G3.2-Umsetzung `-k upgrade` → `6 failed`), G4 `11+20`, G5 `8+6`, Z `2`. Wellen einzeln: G1 allein `430 passed, 2 skipped`, G2 allein `402 passed`, Welle 1 `479 passed, 2 skipped`, G1+G2+G3 `550 passed, 2 skipped`, G1+G2+G4 `510 passed, 2 skipped`, Welle 2 `581 passed, 2 skipped`, Welle 3 `595 passed, 2 skipped`. Runde 4 mit den Urteilen der Verprobung nachgerechnet (Scratchpad): `anders_richtig 1, gleich_richtig 61, weg_fremd 4` — 66 → 62, 0 fremd. Gegenprobe der neuen Tests: Je Behebung (K1 Ziel, K1 Zuordnung, K2 unklar/ungespeichert, K3 Lauf und Befehlsweg und `COALESCE`, K4, K5, K6, K8 Force Run, Einstellungs-Fingerabdruck, Aussetzen, „Search again“) wurde die Behebung im Wegwerf-Klon einzeln zurückgenommen; die zugehörigen Tests schlugen jedes Mal fehl. Ende-zu-Ende-Probe (Task Z Step 4) mit den dort genannten Ausgaben, Grab `('guid-b', 7, True, 1, 7, [4])`, `secrets in db: []`, Shutdown sauber. Die Headless-Browser-Probe (Step 5) wurde nicht wiederholt; Kästchen, Filter und Plaketten sind durch `test_g5_form.py` und `test_g5_pages.py` (inkl. Node-Test) abgedeckt.
