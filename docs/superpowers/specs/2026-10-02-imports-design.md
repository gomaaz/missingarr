# Imports: liegengebliebene manuelle Imports bestätigen oder verwerfen

**Datum:** 2026-10-02
**Status:** Entwurf, mit dem Betreiber abgestimmt (Teile 1–3 am 02.10.2026); Ergebnisse der Planprüfung und die Entscheidungen des Betreibers dazu eingearbeitet (02.10.2026)
**Version:** 0.10.0
**Betrifft:** neues Paket `backend/imports/`, neues Modul `backend/checked_search/import_check.py`, `backend/api/imports.py`, `backend/agents/base.py`, `backend/agents/orchestrator.py`, `backend/main.py`, `templates/imports.html`, `templates/base.html`, `templates/dashboard.html`, `templates/help.html`, `backend/tooltips.py`, `static/js/app.js`

## Problem

Radarr und Sonarr halten manche fertigen Downloads in der Warteschlange an, statt sie zu importieren (`trackedDownloadState = importBlocked`, Oberfläche „Manual Import Required“). Typische Gründe: Der Release wurde beim Grab nur über die ID zugeordnet, der Name passt zu keiner Serie („Unknown Series“), Staffel oder Folge sind ungültig, oder eine Datei wurde abgelehnt. Bisher muss man jeden solchen Download in der Oberfläche der App einzeln öffnen und bestätigen oder entfernen.

## Ziel

Eine Seite „Imports“ in missingarr zeigt alle liegengebliebenen Downloads aller eingeschalteten Instanzen auf einen Blick, mit dem Vorschlag der App, dem Grund des Anhaltens und einer eigenen Einschätzung mit den Regeln der geprüften Suche. Pro Download gibt es zwei Aktionen: „Importieren“ (den Vorschlag der App übernehmen) und „Verwerfen“ (aus dem Download-Client entfernen, wahlweise mit Sperrliste; was die App dann tut, steht unter „Ablauf Verwerfen“). Ein Zähler „Imports offen“ zeigt im Menü und auf dem Dashboard, ob etwas wartet.

## Nicht-Ziele

- Kein Zuordnen zu einem anderen Titel und keine Auswahl einzelner Dateien. Dafür bleibt die Oberfläche der App (Link pro Eintrag).
- Kein eigener automatischer Import. Ein Auto-Import des Betreibers außerhalb von missingarr bleibt, wie er ist.
- Keine Benachrichtigungen aus missingarr. Externe Skripte können auf `/imports` verlinken.
- Keine eigene Datenhaltung: Die Seite liest bei jedem Aufruf live aus den Apps. Gespeichert werden nur Zeilen im Aktivitätslog.
- Keine Downloads in anderen Zuständen (`downloading` mit Warnung, `failedPending`, `importing`).
- Keine Änderung an `radarr_rules`, `sonarr_rules` und `normalize`: Die Regeln der geprüften Suche werden nur aufgerufen.

## Entscheidungen (Betreiber, 02.10.2026)

| Punkt | Entscheidung |
|---|---|
| Umfang | Nur liegengebliebene Downloads der Warteschlange. |
| Bedienung | Seite in missingarr; die Telegram-Meldung mit Link kommt vom externen Auto-Import, nicht von missingarr. |
| Ansatz | Der Auto-Import bleibt unverändert zuständig; missingarr zeigt nur, was liegen bleibt. |
| Zuordnen | Nicht in der ersten Fassung. |
| Bestätigen | Vor „Importieren“ und „Verwerfen“ eine Bestätigung. |

## Welche Einträge

- Gelesen wird `GET /api/v3/queue` mit `page`, `pageSize=1000` bis zur letzten Seite und `includeUnknownSeriesItems=true` bzw. `includeUnknownMovieItems=true`. Ohne diesen Schalter fehlen Einträge ohne Serie oder Film, und „Unknown Series“ ist der häufigste Grund.
- Ein Eintrag zählt, wenn `trackedDownloadState == "importBlocked"` ist, oder `importPending` mit `trackedDownloadStatus == "warning"` (eine einzelne abgelehnte Datei bleibt sonst endlos in `importPending`). Einträge ohne `trackedDownloadState` und ohne `downloadId` (Releases, die ein Delay-Profil zurückhält) zählen nie.
- Sonarr liefert einen Warteschlangen-Eintrag pro Folge, alle mit derselben `downloadId`. Gebündelt wird pro `(Instanz, downloadId)`. Die Größe zählt pro Download einmal.
- Je Download werden gezeigt: Release-Name (`title`), Größe, Alter (`added`, kann fehlen), Download-Client, die Meldungen aus `statusMessages[].messages[]` und `errorMessage`.
- Nur eingeschaltete Instanzen werden gelesen. Eine ausgeschaltete erscheint als „pausiert, nicht gelesen“.

## Vorschlag der App

- Je Download `GET /api/v3/manualimport?downloadId=<id>&filterExistingFiles=true`, nie zusammen mit `movieId` oder `seriesId` (Sonarr übergeht dann die `downloadId`).
- Der Aufruf lässt die App jede Videodatei mit ffprobe untersuchen und kann über 30 Sekunden dauern. Er wird deshalb pro Eintrag einzeln nachgeladen (Zeitlimit 120 s), nicht beim Aufbau der Seite, und 60 Sekunden je `(Instanz, downloadId)` zwischengespeichert.
- Videokandidaten wie beim Auto-Import: Videoendung, nicht „sample“ im Namen, mindestens 50 MB.
- Gezeigt wird je Kandidat: Ziel (Film bzw. Serie mit Staffel und Folgen), Qualität, Sprachen, Release-Gruppe und die Einwände der App (`rejections[].reason`).
- Antwortet die App mit einer leeren Liste, liest missingarr die Warteschlange noch einmal: Die App antwortet auch für einen Download, den sie nicht mehr kennt, mit `[]`. Ist er weg, heißt es „schon erledigt“ (`409`) statt „keine Videodatei“.
- Nennt der Vorschlag bei Sonarr weniger Folgen als die Warteschlangen-Einträge des Downloads, zeigen Karte und Bestätigung einen Hinweis: Sonarr behält den Download nach dem Import. Das ist nur ein Hinweis, es sperrt nichts.

## „Importieren“ möglich nur wenn

Alle Bedingungen müssen gelten, sonst ist der Knopf gesperrt und nennt den Grund:

1. Es gibt mindestens einen Videokandidaten.
2. Jeder Videokandidat hat ein Ziel: Radarr `movie.id`; Sonarr `series.id`, `seasonNumber` und eine nicht leere `episodes`-Liste.
3. Jeder Videokandidat hat eine Qualität.
4. Kein Ziel kommt doppelt vor (zwei Dateien für denselben Film oder dieselbe Folge). Der Import-Befehl der App prüft das nicht; die zweite Datei würde die erste ersetzen.
5. Kein Kandidat hat einen Einwand der App. Der Import-Befehl übergeht alle Einwände, auch „Not an upgrade for existing … file“, und würde eine gleich gute oder bessere vorhandene Datei ersetzen. Wer trotzdem importieren will, tut das in der App.

Fehlen Ziel und Qualität und ist die Einwandliste leer, gilt das nicht als „keine Einwände“: Das ist die Rückfallantwort der App, wenn sie die Datei nicht auswerten konnte (Bedingung 2 sperrt).

## Eigene Einschätzung

Neues Modul `backend/checked_search/import_check.py`, nur lesend:

- `judge(arr_type, release_title, parse, movie, series, episodes, publish_date, settings) -> ImportVerdict` ist rein (ohne Netz). `check_import(get, arr_type, release_title, target, settings, *, download_id, cache) -> ImportVerdict` holt die Daten.
- `ImportVerdict(state: "fits" | "foreign" | "unknown", reasons, notes, details, error)`.
- Radarr: `GET /api/v3/parse?title=`, `GET /api/v3/movie/{id}`, dann `radarr_rules.evaluate` (V6) ohne die Rückfall-Titel der Releasesuche. Ohne `parsedMovieInfo` → `unknown` („Radarr kann den Namen nicht lesen“), nicht `foreign`.
- Sonarr: `GET /api/v3/parse?title=`, `GET /api/v3/series/{id}` (pro Seitenaufruf zwischengespeichert), `GET /api/v3/episode?episodeIds=…&includeEpisodeFile=true`, `GET /api/v3/history?downloadId=…&eventType=1` (nur für `data.publishedDate`). Dann `sonarr_rules.evaluate` je Folge, Ergebnisse zusammengefasst. Ohne `parsedEpisodeInfo` → `unknown`.
- Anpassungen im Adapter, nicht in den Regeln: „existing file“ wird zum Hinweis (der Download ist schon da); die Such-Schranken „not mapped to this title“, „season pack“ und „multi-episode release“ entfallen (das Ziel ist hier der Vorschlag der App); ohne Veröffentlichungsdatum entfällt S3 mit dem Hinweis „publish date unknown“.
- Zusätzlich bei Sonarr ein Abgleich als Hinweis: Staffel und Folgennummern aus `/parse` (`parsedEpisodeInfo.seasonNumber`, `episodeNumbers`, `absoluteEpisodeNumbers`) gegen die Folgen des Vorschlags.
- Anzeige: „passt“ (grün), „fremd?“ (gelb, mit Gründen und Einzelheiten), „nicht beurteilbar“ (grau, mit Grund). Bei Sonarr steht dazu „Regeln S1–S4, nicht gemessen; der Titel wird nur verglichen, wenn Sonarr den Namen einer Serie zuordnet“, damit „passt“ nicht mehr verspricht, als die Regeln leisten.
- Die Einschätzung sperrt „Importieren“ nicht. Bei „fremd?“ wiederholt die Bestätigung die Gründe und „Verwerfen“ ist hervorgehoben.
- Verlaufsdaten der App (`history.records[].data`) enthalten Download-Links mit Indexer-Schlüsseln. Aus ihnen wird nur `publishedDate` übernommen; `data` wird nie gespeichert, protokolliert, angezeigt oder zurückgegeben.

## Ablauf „Importieren“

1. Die Seite schickt `POST /api/imports/{instance_id}/import` mit `{download_id, proposal_key}`. `proposal_key` ist ein Hash über Pfade, Ziele und Qualitäten des angezeigten Vorschlags.
2. missingarr liest Warteschlange und Vorschlag neu (ohne Zwischenspeicher) und prüft die Bedingungen erneut:
   - Download nicht mehr blockiert oder weg → `409` „schon erledigt“.
   - anderer `proposal_key` → `409` „Vorschlag hat sich geändert, Seite neu laden“.
   - Bedingung verletzt → `409` mit Grund.
3. `GET /api/v3/command`: Läuft schon ein `ManualImport` mit einem dieser Pfade (`queued` oder `started`), → `409` „Import läuft schon“.
4. `POST /api/v3/command` mit `{"name": "ManualImport", "importMode": "auto", "files": [...]}`. Je Datei: `path`, `folderName`, `quality`, `languages`, `releaseGroup`, `indexerFlags`, `downloadId`, dazu Radarr `movieId`, Sonarr `seriesId`, `episodeIds`, `releaseType`. Das sind die Felder, die die Oberfläche der App selbst sendet. `importMode: auto` heißt bei SABnzbd verschieben.
5. Antwort an die Seite: Befehls-Id. Die Seite fragt `GET /api/imports/{instance_id}/commands/{command_id}` alle 3 Sekunden ab, höchstens 2 Minuten:
   - `completed` → Warteschlange neu lesen: Download weg → „importiert“. Steht er nach 30 s Nachlauf noch da: bei Sonarr „teilweise importiert, in der App prüfen“ (einige oder alle Dateien nicht importiert); bei Radarr „fehlgeschlagen: die App hat den Import ausgeführt, aber keine Datei importiert“. Radarr kennt keinen Teil-Import: Es nimmt den Download heraus, sobald eine Datei importiert ist; Fehler je Datei fängt es ab, der Befehl endet trotzdem `completed`.
   - `failed`, `aborted`, `cancelled` → „fehlgeschlagen“ mit `exception` bzw. `message`. Fehlt eine Quelldatei (etwa weil der Auto-Import gerade importiert hat), bricht der Befehl ab; Dateien davor können schon importiert sein.
   - `orphaned` oder `404` (die App hat neu gestartet oder den Befehl gelöscht) → „unbekannt, Warteschlange neu prüfen“.
   - nach 2 Minuten noch `queued` oder `started` → „läuft noch“. Befehle mit Plattenzugriff laufen in der App nacheinander, eigene Importe der App können vorgehen.
   - `result` wird nicht ausgewertet (live oft `unknown`).
6. Eine Zeile im Aktivitätslog (Skill `imports`): Release, Ziel, Befehls-Id, Ergebnis.

## Ablauf „Verwerfen“

1. `POST /api/imports/{instance_id}/discard` mit `{download_id, blocklist: true|false}`.
2. missingarr liest die Warteschlange neu und nimmt eine Warteschlangen-Id dieses Downloads (die App entfernt den ganzen Download, auch bei Sonarr mit mehreren Folgen-Einträgen).
3. `GET /api/v3/command`: Wartet oder läuft ein `ManualImport` mit einer Datei dieses Downloads (`body.files[].downloadId`, Status `queued` oder `started`), → `409` „Import läuft schon“, nichts wird gelöscht. Befehle mit Plattenzugriff laufen in der App nacheinander, ein gesendeter Import kann Minuten warten; `removeFromClient=true` würde ihm die Dateien wegnehmen.
4. `DELETE /api/v3/queue/{id}?removeFromClient=true&blocklist=<…>&skipRedownload=false&changeCategory=false`. `changeCategory` bleibt aus, SABnzbd unterstützt es nicht.
5. Was die App tut, überall mit demselben Text (Bestätigung, Info-Symbol, Hilfe, README, CHANGELOG, Aktivitätslog): Download und Dateien werden aus dem Download-Client entfernt. Mit Sperrliste markiert die App nur einen Release, den sie selbst gegriffen hat, als fehlgeschlagen, setzt ihn auf die Sperrliste und sucht vielleicht neu (Radarr nur für einen überwachten, verfügbaren Film; Sonarr nach seinen Einstellungen „Redownload Failed“). Ohne Grab-Verlauf tut die App nichts davon: Ein von Hand hinzugefügter Download wird nur entfernt. Ohne Sperrliste wird nur entfernt, eine neue Suche gibt es nicht. Der Standard ist „mit Sperrliste“.
6. `404` → „schon erledigt“. `removeFromClient=true` löscht die Dateien des Downloads.
7. Eine Zeile im Aktivitätslog.

## Zähler „Imports offen“

- `GET /api/imports/count` → `{total, per_instance: [{id, name, count, error}], checked_at}`.
- Je eingeschaltete Instanz zuerst `GET /api/v3/queue/status`. Sind dort alle Warn- und Fehlerschalter aus, ist die Zahl 0. Sonst die Warteschlange lesen und zählen wie oben.
- Ergebnis 60 Sekunden zwischengespeichert (pro Instanz, mit Sperre), nach jeder Aktion dieser Instanz verworfen. Menü und Dashboard teilen sich den Wert; das Menü fragt alle 60 Sekunden nach.
- Kurz nach einem Neustart einer App ist ihre Warteschlange leer, bis sie sich wieder füllt. Hat die App vor weniger als 2 Minuten gestartet (`GET /api/v3/system/status`, `startTime`), zeigt der Zähler „?“ statt 0. Ebenso „?“ statt 0, solange eine App nicht lesbar ist. Ist eine Zahl bekannt, fehlt aber eine App, nennt die Dashboard-Zeile das dazu.

## Seite

- `GET /imports` (Template `imports.html`), Menüpunkt „Imports“ mit Zähler nach „Pre-filter“.
- Ein Kasten pro Instanz. Ist eine App nicht erreichbar, zeigt nur ihr Kasten den Fehler.
- Pro Download eine Karte: Kopf mit Release, Größe, Alter, Gründen; darunter Vorschlag und Einschätzung, nachgeladen über `GET /api/imports/{instance_id}/proposal?download_id=…`.
- Link zur Warteschlange der App: gleicher Host wie die Seite, Port und Basispfad aus der Instanz-URL, Pfad `/activity/queue`. Damit funktioniert er, wenn die Ports der Apps am selben Host wie missingarr erreichbar sind; die README sagt das.
- Leer: „Nichts offen.“ Hat die App vor weniger als 2 Minuten gestartet, steht stattdessen „App gerade gestartet, ihre Warteschlange ist vielleicht noch nicht gefüllt“ (gleiche Prüfung wie beim Zähler).
- Bedienmuster wie auf der Pre-filter-Seite: Alpine-Komponente, `confirm()`, `apiFetch()`, Toast. Ids über `data-*`-Attribute, nie in JavaScript eingesetzt.
- Kommt auf „Importieren“ keine Antwort von missingarr (kein JSON, etwa eine `504` eines Reverse-Proxys, oder ein Netzfehler), meldet die Seite keinen Fehlschlag, sondern „keine klare Antwort, der Import läuft vielleicht noch, Warteschlange prüfen“: missingarr sendet den Befehl womöglich trotzdem.
- Hilfe-Karte „Imports“ und Info-Symbole (`imports_verdict`, `imports_discard_blocklist`).

Die Oberfläche ist englisch wie der Rest von missingarr. Die deutschen Begriffe dieser Spec entsprechen diesen Texten:

| Spec | Oberfläche |
|---|---|
| „Importieren“ / „Verwerfen“ | „Import“ / „Discard“ |
| Schalter „mit Sperrliste“ | „Blocklist and search again“ |
| „passt“ / „fremd?“ / „nicht beurteilbar“ | „fits“ / „foreign?“ / „cannot judge“ |
| „Nichts offen.“ | „Nothing open.“ |
| „App gerade gestartet …“ | „The app started less than 2 minutes ago — its queue may not be filled yet.“ |
| „pausiert, nicht gelesen“ | „Switched off — not read.“ |
| „Imports offen“ (Zähler) | Zahl am Menüpunkt „Imports“; Dashboard „N imports waiting“ |
| „keine klare Antwort …“ | „No clear answer — the import may still run in the app; check the queue“ |

## Aufbau im Code

| Datei | Inhalt |
|---|---|
| `backend/imports/__init__.py` | Paketbeschreibung: welche Module rein sind, welches mit den Apps spricht. |
| `backend/imports/entries.py` | rein: Warteschlange bündeln (`group_blocked`), Videofilter, Bedingungen für „Importieren“ (`assess`), `proposal_key`, Dateien für den Befehl. |
| `backend/imports/service.py` | Netz: Warteschlange, Vorschlag, Einschätzung, Import, Befehlsstatus, Verwerfen, Zähler-Zwischenspeicher. Bekommt einen Agenten (`http_get`, `http_post`, `http_delete`, `log`, `config`). |
| `backend/checked_search/import_check.py` | Einschätzung (siehe oben). |
| `backend/api/imports.py` | Router `/api/imports`: Liste, Vorschlag, Import, Befehlsstatus, Verwerfen, Zähler. Normale `def`-Routen (laufen im Threadpool, `requests` blockiert). `download_id` im JSON-Körper bzw. als Query-Parameter, nicht im Pfad. |
| `backend/agents/base.py` | `http_delete(path, params=None, timeout=10)`: ohne Weiterleitungen, gleiche Fehlerprüfung wie `http_post`, leere Antwort erlaubt. |
| `backend/agents/orchestrator.py` | `detached_agent(config)`: Agent nur zum Leihen der HTTP- und Log-Methoden, wie in `housekeeping()`, nie gestartet. |
| `backend/main.py` | Router einbinden, Seite `/imports`. |
| Templates, `app.js`, `tooltips.py` | Seite, Menü, Dashboard, Hilfe. |

Fehler der Apps werden fast wie beim Verbindungstest übersetzt: Zeitüberschreitung → 504, keine Verbindung → 503, 401/403 → 502 „Invalid API key“, Weiterleitung → 502, sonstiger HTTP-Fehler → 502 „HTTP <code> from instance“. Anders als beim Verbindungstest wird eine 401/403 der App nicht zu 401: `apiFetch()` hält jede 401 für eine abgelaufene Sitzung von missingarr und schickt zur Anmeldung, die Seite liefe in eine Schleife (Entscheidung des Betreibers, 02.10.2026). Fehlertexte enthalten nie den Schlüssel (er steht im Header).

## Sicherheit

- Seite und `/api/imports/*` stehen hinter der Anmeldung (`AuthMiddleware`), Aktionen sind POST und damit vom Herkunftsschutz (`CSRFMiddleware`) erfasst.
- Instanzen gehen nur über `public_instance()` an Templates; Schlüssel erscheinen nie.
- missingarr vertraut keinem Vorschlag aus dem Browser: Vor jedem Import liest es Warteschlange und Vorschlag selbst neu und baut den Befehl daraus.
- Verlaufsdaten der Apps werden nie weitergegeben (siehe Einschätzung).

## Tests

- `tests/test_h1_imports_entries.py`: Bündeln (Sonarr mit mehreren Folgen-Einträgen, Radarr ein Eintrag, Delay-Einträge ohne `downloadId`, fehlende Felder), Videofilter, jede Sperr-Bedingung, Rückfallantwort ohne Ziel, `proposal_key` stabil, Befehlsdateien je App.
- `tests/test_h2_import_check.py`: reine Fälle Radarr (fremdes Jahr, fremder Film, Treffer, vorhandene Datei als Hinweis, nicht lesbarer Name) und Sonarr (passt mit Hinweis, andere Serie, zu früh veröffentlicht, ohne Datum, mehrere Folgen, falsche Folgennummer als Hinweis); Abruf mit nachgebauten Apps: genaue Pfade und Anzahl der Anfragen, fehlender Verlauf nur als Hinweis, `repr` enthält nie Verlaufsdaten.
- `tests/test_h3_imports_service.py`: nachgebaute Apps für Warteschlange, Vorschlag, Befehl und Löschen; Neu-Lesen vor dem Import, `409` bei weg/geändert/laufend, Befehlsstatus-Abbildung (Sonarr „teilweise“, Radarr „fehlgeschlagen“ nach dem Nachlauf), Verwerfen mit und ohne Sperrliste, `409` beim Verwerfen während eines Imports dieses Downloads, `404` als „schon erledigt“, leerer Vorschlag eines verschwundenen Downloads, Hinweis bei weniger Folgen, Zähler mit Vorprüfung und Zwischenspeicher (auch kein veralteter Wert nach einer Aktion).
- `tests/test_h4_imports_api.py`: Anmeldung (401 ohne Sitzung), Herkunftsschutz (403), keine Schlüssel in Antworten, eine App down → die andere bleibt sichtbar, App gerade gestartet, Seite rendert, Menüeintrag.
- Bestehende Suite bleibt grün; `tests/test_p6_docs.py` auf 0.10.0.

## Einführung

1. Bau nach Plan auf `feat/imports`, Plan vorher von Codex prüfen lassen.
2. CHANGELOG (0.10.0), README (Abschnitt „Imports“, mit dem Hinweis: hinter einem Reverse-Proxy braucht `/api/imports/` ein Lesezeitlimit von mindestens 180 s), VERSION; Push-Prüfung; Tag `v0.10.0`; GitHub-Release.
3. Einspielen nur mit Freigabe des Betreibers, mit Sicherung wie bei 0.9.0. Keine Datenbank-Änderung.
4. Danach von außen: Meldung des Auto-Imports mit Link auf `/imports`, dann die App-eigene Meldung „On Manual Interaction Required“ abschalten (nicht Teil dieses Repos).

## Risiken

| Risiko | Umgang |
|---|---|
| Import und externer Auto-Import gleichzeitig | Neu-Lesen und Befehlsprüfung direkt vor dem Senden; fehlende Quelldatei lässt den Befehl scheitern, die Seite meldet das. |
| Verwerfen, während ein gesendeter Import noch in der App wartet | Befehlsprüfung vor dem Löschen, `409` „Import läuft schon“. |
| Import ersetzt eine bessere Datei | Gesperrt, sobald die App einen Einwand hat. |
| Langsame Vorschläge (ffprobe) | Einzeln nachgeladen, 120 s Zeitlimit, 60 s Zwischenspeicher. |
| Reverse-Proxy bricht lange Anfragen ab (oft nach 60 s) | README und CHANGELOG: Lesezeitlimit von mindestens 180 s für `/api/imports/`; ohne klare Antwort meldet die Seite „Import läuft vielleicht noch“, nicht „fehlgeschlagen“. |
| Sonarr-Einschätzung zu schwach | Als Hinweis beschriftet, sperrt nichts. |
| Link zur App falsch, wenn die App nicht am selben Host erreichbar ist | In der README beschrieben; Zuordnen in der App bleibt der Weg. |
| Leere Warteschlange nach App-Neustart | Zähler zeigt „?“ in den ersten 2 Minuten, der Kasten der App auf der Seite sagt „gerade gestartet“ statt „Nichts offen.“. |
| Veralteter Zähler, wenn ein Lesen vor einer Aktion begann | Ein Lesen, während dessen die Instanz verworfen wurde, speichert nichts. |
