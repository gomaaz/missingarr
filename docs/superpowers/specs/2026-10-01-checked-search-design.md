# Geprüfte Suche (Vorfilter) für Radarr und Sonarr

**Datum:** 2026-10-01
**Status:** Entwurf, mit Daniel abgestimmt, Durchsicht der Spec offen
**Version:** 0.9.0
**Betrifft:** `backend/skills/`, neues Paket `backend/checked_search/`, `backend/db/`, `backend/database.py`, `backend/models/instance.py`, `backend/api/`, `templates/`, `static/js/`, `backend/tooltips.py`

## Problem

Ein Indexer (in Radarr als „Scenenzb (Prowlarr)“, liefert den Großteil aller Grabs) hängt bei einer Suche **per ID** die gesuchte tmdbId/imdbId bzw. tvdbId an **jeden** Treffer, auch an fremde Filme und Serien. Radarr ordnet einen Treffer bei einer Suche dem gesuchten Film zu, sobald diese ID passt, ohne Titel- und Jahresprüfung (Radarr 6.4 `ParsingService.cs:243–246`, bei gleichem Jahr reicht die ID allein: `:166–169`). Sonarr ordnet zuerst nach Titel, dann nach der gestempelten tvdbId zu (`ParsingService.cs:390–406`). RSS ist nicht betroffen.

Folge: Die bisherigen Such-Befehle `MoviesSearch` und `EpisodeSearch` laden den ersten angenommenen Treffer, auch wenn er ein fremder Film ist. In einer Stichprobe von 1.000 zufälligen Filmen hätten 7,2 % der Filme mit Treffern einen fremden Film geladen, etwa „The Thing“ (1982) ← `Das.Ding.aus.einer.anderen.Welt.1951…`, „Halloween“ (1978) ← `Halloween.2018…`, „John Carter“ ← `Carter 2022…`. Bei Sonarr sind dieselben Fehler belegt, aber an kleinen Stichproben: falsche Serie über umbenannte Titel, gestempelte tvdbId (`The.Guest.CO.2025…` für die koreanische Serie „The Guest“), falsche Folge durch abweichende Nummerierung (Release lange vor der Sendung der Folge).

## Ziel

missingarr sucht nicht mehr per Befehl, sondern ruft die Suchergebnisse selbst ab, prüft jeden Treffer mit einem Vorfilter und lädt nur den ersten sauberen. Ein Probelauf ohne Laden ist Pflicht, damit Daniel die Entscheidungen vorher durchsehen kann. Alle Regeln und Grenzwerte stellt Daniel pro Instanz selbst ein.

## Nicht-Ziele

- Keine Staffel- oder Serienpakete bei Sonarr. Die geprüfte Suche arbeitet nur mit einzelnen Folgen (Modus `episode`).
- Keine Sprach- oder Qualitätsprüfung. Das bleibt bei den Profilen von Radarr und Sonarr.
- Keine Prüfung von Laufzeit oder Dateigröße gegen den Film (aus der Gegenprüfung vorgeschlagen, offen).
- Keine Textsuche statt der ID-Suche.
- Kein Schutz für Suchen außerhalb von missingarr (Seerr, Handsuche, Radarrs eigene Suchen).

## Entscheidungen (Daniel, 01.10.2026)

| Punkt | Entscheidung |
|---|---|
| Durchsicht des Probelaufs | eigene Seite „Vorfilter“ und CSV-Download |
| Umfang | fehlende Titel **und** Upgrades, Radarr und Sonarr |
| Sonarr | nur einzelne Folgen; mit geprüfter Suche sind die Modi `season_packs`, `show_batch`, `smart` gesperrt |
| Kein sauberer Treffer | wie bisher: der Titel gilt als gesucht, `retry_hours` entscheidet über eine neue Suche |
| Tempo | Radarr im Probelauf und danach 20 Titel pro Lauf (Einstellung „Missing per run“, von Daniel gesetzt) |
| Regeln und Grenzwerte | alle pro Instanz in der Oberfläche einstellbar, jede Einstellung mit Info-Symbol; die Werte unten sind nur Voreinstellungen |

## Ablauf pro Titel

Die Kandidatenauswahl bleibt, wie sie ist: Wanted-Liste bzw. Cutoff-Liste, Release-Fenster, Reihenfolge, Rate-Limit, Abbruchsignal. Nur das Absenden ändert sich, wenn die Instanz `checked_search` auf `dry_run` oder `active` hat.

1. **Daten des Titels:** Radarr `GET /api/v3/movie/<id>`. Sonarr `GET /api/v3/episode/<id>` und `GET /api/v3/series/<seriesId>`. Daraus: Namen, Jahre, Daten, vorhandene Datei.
2. **Suche:** Radarr `GET /api/v3/release?movieId=<id>`, Sonarr `GET /api/v3/release?episodeId=<id>`. Das löst die Indexer-Suche aus, mit derselben Last wie der bisherige Befehl, und zählt als eine Aktion gegen das Rate-Limit. Eigene Wartezeit: bis „Release search timeout“ (Voreinstellung 120 s) statt der üblichen 10 s.
3. **Kandidaten:** nur Treffer mit `approved == true`, in der gelieferten Reihenfolge. Das ist die Rangfolge von Radarr bzw. Sonarr. Der erste davon ist der Treffer, den die alte Suche genommen hätte.
4. **Prüfung:** Jeder Kandidat wird per `GET /api/v3/parse?title=<release>` zerlegt und durch die Regeln der App geschickt (unten). `/parse` erzeugt keine Indexer-Last.
   - **Aktiv:** Der erste Kandidat, der alle Regeln besteht, wird per `POST /api/v3/release` mit `{"guid", "indexerId"}` aus derselben Suche geladen (Radarr und Sonarr halten die Treffer 30 Minuten). Danach wird nicht weiter geprüft.
   - **Probelauf:** Alle Kandidaten werden geprüft, höchstens „Dry run: max releases checked“ (Voreinstellung 100). Es wird nichts geladen.
5. **Protokoll:** eine Zeile pro Titel im Vorfilter-Protokoll (unten).

Fehler:
- `GET /movie`, `/episode`, `/series` oder `/release` scheitert oder läuft in die Wartezeit: Ergebnis „Fehler“, Titel nicht gemerkt, zählt als gescheiterte Einreichung des Laufs (Laufstatus wie seit 0.8.0).
- `/parse` scheitert für einen Kandidaten: Der Kandidat fällt mit Grund „parse error“ weg (im Zweifel nicht laden).
- `POST /release` scheitert: Ergebnis „Fehler beim Laden“, Verlaufseintrag `failed`, kein Cache-Eintrag. Es wird **kein** weiterer Kandidat versucht, damit ein Zeitüberschreitungs-Fehler nach einem doch erfolgten Grab nicht zu einem zweiten Download führt.
- Zeitbudget: Ist „Time budget per run“ (Voreinstellung 25 min) erschöpft, beginnt der Lauf keinen neuen Titel mehr. Das Abbruchsignal (Instanz aus, löschen, Herunterfahren) wird zwischen zwei Titeln und zwischen zwei `/parse`-Aufrufen geprüft.

## Regeln Radarr (V6, gemessen)

Namen des Films: `title` (bei `movieInfoLanguage` Deutsch die deutsche Übersetzung, sonst der Grundtitel), `originalTitle`, `alternateTitles[].title`. Jahre: `year`, `secondaryYear`, Jahr von `inCinemas`, `digitalRelease`, `physicalRelease`. Release: `parsedMovieInfo.movieTitles`, `parsedMovieInfo.year`, `movie.id` aus `/parse`.

Normalisieren: Kleinbuchstaben, Akzente entfernen, Umlaute sowohl als ae/oe/ue/ss als auch als a/o/u/s, `&` als „and“ und als „und“. Für den exakten Vergleich alles außer Buchstaben und Ziffern entfernen, für den Wortvergleich in Wörter zerlegen.

| Regel | Prüfung | Einstellung (Voreinstellung) |
|---|---|---|
| a, Jahr | Release-Jahr höchstens N Jahre neben einem Jahr des Films. Film ohne `year` und `secondaryYear`: nur Releases ohne Jahr. | „Year tolerance“ (1); „Count release dates as years“ (an: Kinostart, digital, physisch zählen mit) |
| b, Veto | Release hat ein Jahr und `/parse` ordnet es einem **anderen** Film der Bibliothek zu → verwerfen | „Veto when /parse names another movie“ (an) |
| c, Titel | mindestens eins: ein Release-Titel gleich einem Namen; `/parse` ordnet genau diesen Film zu; einer beginnt mit dem anderen, beide mindestens N Zeichen; alle Wörter eines Namens mit mindestens M Kernwörtern (ohne the/a/an/der/die/das/and/und/of/le/la/les/el/il) stehen im Release-Titel | „Prefix match“ (an), „Prefix minimum length“ (6), „Word match“ (an), „Word match minimum core words“ (2) |
| d, ohne Jahr | Release ohne Jahr: Titel exakt oder `/parse` ordnet genau diesen Film zu | „Releases without year need an exact title“ (an) |
| 1a, Datei | Release ist dieselbe Release wie die vorhandene Datei (Szenenname, Dateiname ohne Endung) → verwerfen | „Skip the release of the existing file“ (an) |

Gemessen mit den Namen, die die API liefert (Nachbau der Referenz, Runde 5, 1.000 Filme): 0 fremde Filme geladen (vorher 31), 15-mal der richtige statt eines fremden, 16 laden nichts mehr. Verloren gehen 2 harmlose und 2 unklare Picks wie in der Referenz, dazu „Bāhubali 2“, weil die API keinen englischen Titel liefert. Bekannte Grenze: gleicher Titel und gleiches Jahr (Kurzfilm „Noah“ 2013 gegen „Noah“ 2014) bleibt unerkannt.

## Regeln Sonarr (S1–S4, noch nicht gemessen)

Daten: Serie `title`, `alternateTitles[].title`, `year`; Folge `airDateUtc`, vorhandene Datei (`episodeFile.sceneName`, `relativePath`); Release `publishDate`, `title`; `/parse`: `series.id`, `parsedEpisodeInfo.seriesTitle`, `parsedEpisodeInfo.seriesTitleInfo.year`.

| Regel | Prüfung | Einstellung (Voreinstellung) |
|---|---|---|
| S1, Veto | `/parse` ordnet das Release einer **anderen** Serie der Bibliothek zu → verwerfen | „Veto when /parse names another series“ (an) |
| S2, Zusatz | Steht im geparsten Serientitel am Ende ein Jahr oder ein Ländercode aus der Liste, muss er passen: Jahr höchstens N neben dem Serienjahr, Ländercode auch am Ende des Serientitels oder eines Alternativtitels → sonst verwerfen | „Check country/year suffix“ (an), „Country codes“ (AU, US, UK, GB, DE, CO, CA, NZ, FR, ES, IT, NL, SE, DK, NO, JP, KR, MX, BR, AR, IN), „Suffix year tolerance“ (1) |
| S3, zu früh | `publishDate` mehr als X Tage vor `airDateUtc` → verwerfen; zwischen Y und X Tagen nur Hinweis im Protokoll | „Reject releases published this many days before air date“ (365), „Note releases published this many days before air date“ (14) |
| S4, Datei | Release ist die Release der vorhandenen Datei → verwerfen | „Skip the release of the existing file“ (an) |

Weil S1–S4 nicht gemessen sind, schreibt der Probelauf bei jedem Kandidaten **alle** Regeln mit, die greifen würden, auch wenn schon eine frühere ihn verwirft. So lässt sich jede Regel einzeln beurteilen.

## Einstellungen in der Oberfläche

Neuer Abschnitt „Checked search“ im Instanzformular. Jede Einstellung hat ein Info-Symbol mit Erklärung (Texte in `backend/tooltips.py`, englisch wie die übrige Oberfläche). Angezeigt werden nur die Regeln der jeweiligen App.

- „Checked search“: Off / Dry run / Active. Erklärt: was der Probelauf tut (nichts laden, nichts merken, jeder Titel einmal pro Runde), was Aktiv tut.
- Allgemein: „Release search timeout“ (120 s, 10–600), „Time budget per run“ (25 min, 1–1440), „Dry run: max releases checked“ (100, 1–1000).
- Radarr: die Einstellungen aus der Tabelle oben.
- Sonarr: die Einstellungen aus der Tabelle oben.
- Ist „Checked search“ nicht Off, sperrt das Formular bei Sonarr die Modi außer `episode`, und der Server lehnt sie mit 422 ab.

Die Einstellungen liegen als eine JSON-Spalte `checked_search_settings` in `instances`, geprüft durch ein Pydantic-Modell mit den Voreinstellungen. Fehlende Schlüssel bekommen die Voreinstellung, damit spätere neue Regeln ohne Migration dazukommen.

## Daten

- `instances.checked_search TEXT NOT NULL DEFAULT 'off'` mit `CHECK (checked_search IN ('off','dry_run','active'))`, `instances.checked_search_settings TEXT NOT NULL DEFAULT '{}'`, `instances.dry_run_round_started_at TEXT` (Beginn der aktuellen Probelauf-Runde). Migration per `PRAGMA table_info`, idempotent, wie seit 0.8.0.
- Neue Tabelle `checked_search_log`: `id`, `instance_id` (FK, `ON DELETE CASCADE`), `run_id` (FK auf `search_history`, `ON DELETE SET NULL`), `mode` (`dry_run`/`active`), `skill` (`search_missing`/`search_upgrades`), `arr_id`, `cache_key`, `title`, `created_at`, `outcome` (`grabbed`, `would_grab`, `no_clean_hit`, `no_results`, `error`, `grab_failed`), `arr_pick` (Release, das \*arr selbst genommen hätte), `pick` (Release des Filters), `pick_indexer`, `pick_score`, `pick_size`, `pick_quality`, `candidates` (JSON-Liste je geprüftem Kandidaten: Release-Titel, Indexer, Punkte, Größe, Qualität, Urteil, Gründe, Hinweise), `error_message`. Indizes auf `(instance_id, created_at)` und `(instance_id, mode, cache_key)`.
- Aufbewahrung: dieselbe Hauspflege wie für die History (`HISTORY_RETENTION_DAYS`).
- **Nie gespeichert oder geloggt:** `downloadUrl`, `guid` außer für den einen POST, `infoUrl`, Header. Die Treffer enthalten in `downloadUrl` den Prowlarr-Schlüssel.

## Verlauf, Cache und Verifikation

- **Probelauf:** kein Verlaufseintrag, kein Cache-Eintrag. Der Lauf selbst erscheint in `search_history` wie gewohnt (Status `success`, `triggered_count` 0, ohne Fehlertext); was er geprüft hat, zeigen Aktivitätslog („Dry run: N titles checked, M would grab“) und die Seite „Vorfilter“. Kandidaten für den Probelauf übergehen den Such-Cache, damit auch früher gesuchte Titel geprüft werden; stattdessen fallen Titel weg, die in der aktuellen Runde schon ein Protokoll mit `mode = 'dry_run'` haben. „Reset dry run“ setzt `dry_run_round_started_at` auf jetzt.
- **Aktiv, geladen:** Verlaufseintrag mit `command_status = 'grabbed'` und Cache-Eintrag in einer Transaktion (wie `record_submission`). `POST /release` antwortet synchron, es gibt keine Befehls-ID; die Befehlsprüfung alle 2 Minuten lässt diese Einträge aus.
- **Aktiv, kein sauberer Treffer oder keine Treffer:** Verlaufseintrag `command_status = 'no_hit'` und Cache-Eintrag (der Titel gilt als gesucht).
- **Aktiv, Fehler:** Verlaufseintrag `failed` bzw. kein Eintrag bei Fehlern vor der Suche, kein Cache-Eintrag.
- Laufstatus: `grabbed` und `no_hit` zählen als erledigt; der Lauf endet ohne Wartezeit auf die Verifikation. Die History zeigt neue Plaketten „geladen“ und „kein sauberer Treffer“.
- Upgrades (Cutoff-Liste) laufen genauso; dort ist Regel 1a bzw. S4 wichtig.

## Profiländerungen erkennen (Nachtrag, Daniel 01.10.2026)

Radarr und Sonarr bewerten die Treffer bei jeder Suche mit dem gerade gültigen Profil; dafür muss missingarr nichts wissen. Zwei Folgen einer Profiländerung fängt missingarr aber selbst ab: Titel, die vorher schon gesucht wurden, und veraltete Probelauf-Einträge.

- **Fingerabdruck:** Zu Beginn jedes Suchlaufs holt missingarr `GET /api/v3/qualityprofile`, `GET /api/v3/customformat` und `GET /api/v3/releaseprofile` (keine Indexer-Last). Pro Qualitätsprofil bildet es einen Fingerabdruck: SHA-256 über das kanonische JSON (sortierte Schlüssel) aus dem Profil, allen Custom Formats und allen Release-Profilen, gekürzt auf 16 Hex-Zeichen. Jede Änderung an Punkten, Qualitäten, Formaten oder Release-Profilen ändert ihn (bewusst grob: eine Formatänderung trifft alle Profile). Die aktuelle Zuordnung Profil-ID → Fingerabdruck steht pro Instanz in `instances.profile_fingerprints` (JSON). Scheitert der Abruf, gilt der zuletzt gespeicherte Stand, und es wird nichts freigegeben.
- **Profil eines Titels:** Radarr `movie.qualityProfileId`, Sonarr `series.qualityProfileId` (beide stehen schon in den Wanted- bzw. Cutoff-Listen).
- **Such-Cache:** `searched_items` bekommt die Spalte `profile_fingerprint`. Beim Merken eines Titels wird der Fingerabdruck seines Profils mitgeschrieben (alter und geprüfter Suchweg). Ein Eintrag sperrt nur, solange sein Fingerabdruck dem aktuellen Fingerabdruck des Profils des Titels entspricht. Einträge ohne Fingerabdruck (aus der Zeit vor 0.9.0) gelten als unter dem Stand gesucht, der beim ersten Lauf von 0.9.0 gespeichert wird (`instances.profile_fingerprints_baseline`, einmal gesetzt). Das Update allein gibt also nichts frei, erst die nächste Profiländerung.
- **Probelauf:** Das Vorfilter-Protokoll speichert den Fingerabdruck je Eintrag (`profile_fingerprint`). Ein Titel gilt in der laufenden Runde nur als geprüft, wenn sein Eintrag den aktuellen Fingerabdruck trägt; nach einer Profiländerung prüft der Probelauf ihn also erneut. Die Seite „Vorfilter“ zeigt bei Einträgen mit veraltetem Fingerabdruck die Plakette „profile changed“.
- **Einstellung:** „Search again after profile changes“ (an), mit Info-Symbol: Ist sie aus, sperren Cache-Einträge wie bisher unabhängig vom Profil; der Probelauf berücksichtigt Profiländerungen immer.
- **Protokoll:** Erkennt ein Lauf einen geänderten Fingerabdruck, schreibt er ins Aktivitätslog, welche Profile sich geändert haben (Name, alt → neu gekürzt).
- **Tests:** Fingerabdruck stabil bei gleicher Reihenfolge und anderer Schlüsselreihenfolge, anders bei geänderter Punktzahl; Cache sperrt bei gleichem und gibt frei bei geändertem Profil; Alt-Einträge sperren bis zur ersten Änderung nach der Grundlinie; Einstellung aus sperrt immer; Probelauf prüft nach Änderung erneut; Abruf-Fehler gibt nichts frei.

## Oberfläche

- **Menüpunkt „Vorfilter“:** oben Zähler je Ergebnis und Instanz; darunter eine Tabelle je Titel (Zeit, Instanz, Modus, Titel, Ergebnis, „\*arr would grab“, „Filter grabs“, Zahl verworfener Kandidaten), serverseitig gefiltert und geblättert wie die History. Filter: Instanz, Modus, Ergebnis, Text, „only differences“ (Filter nimmt etwas anderes als \*arr oder verwirft). Eine Zeile klappt die Kandidaten mit Urteil, Gründen und Hinweisen auf. Knopf „Reset dry run“ je Instanz.
- **CSV-Download** mit dem aktuellen Filter: eine Zeile pro geprüftem Kandidaten (Zeit, Instanz, Modus, Titel, Release, Indexer, Punkte, Größe, Qualität, Urteil, Gründe, gewählt ja/nein, \*arr hätte genommen ja/nein).
- **Karte:** Plakette „dry run“ bzw. „checked“.
- **API:** `GET /api/checked-search` (Liste mit `X-Total-Count`), `GET /api/checked-search.csv`, `POST /api/instances/<id>/checked-search/reset-dry-run`.

## Aufbau im Code

- `backend/checked_search/normalize.py`: Normalisieren und Zerlegen, rein.
- `backend/checked_search/radarr_rules.py`, `sonarr_rules.py`: Regeln als reine Funktionen, Eingabe sind einfache Datenklassen (Film bzw. Serie/Folge, Release, Parse-Ergebnis, Einstellungen), Ausgabe Urteil mit Gründen und Hinweisen. Kein Netz, keine Datenbank.
- `backend/checked_search/runner.py`: Ablauf pro Titel (Abrufen, Prüfen, Laden, Protokoll) über die HTTP-Methoden des Agenten.
- `backend/db/checked_search_log.py`: Protokoll schreiben, abfragen, CSV.
- `search_missing.py` und `search_upgrades.py` rufen bei `checked_search != 'off'` den Runner statt des Befehls auf.

## Tests

- Regeln einzeln mit öffentlichen Beispielen: „The Thing“ ← `Das.Ding.aus.einer.anderen.Welt.1951…` (verworfen, Jahr), `Halloween.2018…` für „Halloween“ (1978) (verworfen), `Carter.2022…` für „John Carter“ (verworfen), Umlaut-Schreibweisen, Release ohne Jahr, Veto, Präfix, Wortabdeckung, „Noah“ als bekannte Grenze (geht durch). Sonarr: `The.Guest.CO.2025.S01E10…` (verworfen, S2), Release lange vor der Sendung (verworfen, S3), `A.Better.Place.S01…` 14 Tage vorher (Hinweis, nicht verworfen), Datei-Release (S4), Veto (S1). Jede Einstellung ändert das Urteil wie beschrieben.
- Ablauf mit nachgebildetem Radarr/Sonarr: Probelauf (nichts geladen, nichts gemerkt, jeder Titel einmal pro Runde, Reset), aktiv (erster sauberer geladen, Cache und Verlauf in einer Transaktion), kein sauberer Treffer, keine Treffer, Fehler bei Suche, `/parse` und Laden, Zeitbudget, Abbruch, Rate-Limit, Sperre der Sonarr-Modi.
- Ein Test durchsucht Protokoll, Verlauf und Aktivitätslog nach `downloadUrl`, `apikey` und den Testschlüsseln: nichts darf auftauchen.
- Lokaler Abgleich mit dem Verprobungs-Korpus: Der Nachbau muss auf Runde 5 genau die gemessenen Zahlen liefern. Das Korpus liegt nicht im Repo; der Test liest den Pfad aus `MISSINGARR_VORFILTER_KORPUS` und überspringt sich ohne ihn.
- Codex-Review am Ende, nur auf den Code im Repo, nie auf Datenordner; danach die Codex-Sitzungen auf bekannte Schlüssel prüfen.

## Einführung

Jeder Schritt mit Daniels Freigabe:

1. 0.9.0 bauen, testen, reviewen, Branch pushen. Tag und Einspielen erst nach Freigabe.
2. Nach dem Einspielen: Radarr „Missing per run“ 20, „Checked search“ Dry run, Instanz einschalten. Sonarr „Checked search“ Dry run (in dieser Zeit lädt missingarr bei Sonarr nichts, RSS läuft weiter).
3. Durchsicht auf der Seite „Vorfilter“, vor allem „only differences“. Einstellungen bei Bedarf anpassen, Probelauf zurücksetzen, erneut prüfen.
4. Freigabe Radarr: „Checked search“ Active. Vorher entscheiden, ob der Such-Cache von Radarr zurückgesetzt wird (sonst bleiben Filme, die die alte Suche schon gesucht hat, mit `retry_hours = 0` außen vor). Sonarr folgt, wenn S1–S4 kalibriert sind.
5. In Seerr die Radarr-Suche beim Hinzufügen abschalten (`preventSearch`), damit neue Wünsche über missingarr laufen.

## Risiken

- Last auf den Indexer: dieselbe wie bisher pro Titel, aber im Probelauf über den ganzen Rückstand. Begrenzt durch „Missing per run“, Intervall, Rate-Limit und Zeitbudget.
- Zwischenspeicher von \*arr (30 Minuten): Liegen zwischen Suche und `POST /release` mehr als 30 Minuten, schlägt das Laden fehl („Fehler beim Laden“, Titel bleibt frei). Im Ablauf liegen nur die `/parse`-Aufrufe dazwischen.
- `POST /release` zählt in \*arr als Handsuche; Radarr und Sonarr prüfen die Profile trotzdem.
- Sonarr-Regeln sind ungemessen; deshalb erst Probelauf, Freigabe getrennt von Radarr.
