# Upgrade-Suche: überwachte Folgen als Quelle für Sonarr

**Datum:** 2026-10-07
**Status:** mit dem Betreiber abgestimmt (07.10.2026: „Beides“, also neue Upgrade-Quelle für Sonarr bauen; Einspielen erst nach seinem Go)
**Version:** 0.13.0
**Betrifft:** `backend/skills/search_upgrades.py`, `backend/skills/profiles.py`, `backend/tooltips.py`, `templates/instances/form.html`, `README.md`, `CHANGELOG.md`, `VERSION`

## Problem

Die Upgrade-Suche nimmt bei Sonarr nur die Liste „Cutoff Unmet“ (`GET /api/v3/wanted/cutoff`). Diese Liste wertet nur die Qualität, nicht die Punkte der Custom Formats. Bei Profilen, deren Qualitätsgrenze eine Gruppe ist (z. B. „HD“ mit 720p und 1080p) und in denen die Punkte entscheiden, steht dort keine Datei, die die Qualitätsgrenze erreicht hat, auch wenn sie weit unter dem Punkte-Endstand (`cutoffFormatScore`) liegt.

Beispiel vom 07.10.2026: Neun Folgen einer Serie lagen als englische WEB-DL-Dateien mit 4.110 Punkten vor. Eine deutsche DL-Fassung mit 16.250 Punkten stand schon vorher beim Indexer, war in der Suche freigegeben und hätte die Datei sofort ersetzt. RSS sieht ein Release nur beim Erscheinen, die Cutoff-Liste enthielt die Folgen nicht (nur 9 SD-Folgen insgesamt), also suchte niemand danach. In einer Stichprobe von 300 Serien lagen 108 solche Dateien in HD an überwachten Folgen.

Die Einstellung „Upgrade Source“ gilt heute nur für Radarr. Sonarr nimmt fest die Cutoff-Liste (`_collect_candidates`: `sources = ("cutoff",)`), das Feld ist im Formular für Sonarr ausgeblendet.

## Ziel

Sonarr bekommt dieselbe Einstellung wie Radarr. „Monitored Items Only“ heißt bei Sonarr: überwachte Folgen mit Datei, deren Datei unter dem Punkte-Endstand ihres Qualitätsprofils liegt. Die niedrigsten Punkte kommen zuerst dran. So landen Dateien, die nur als Ersatz geladen wurden (in der Praxis meist eine Fassung ohne die bevorzugte Sprache), vorn in der Upgrade-Suche.

## Nicht-Ziele

- Keine Sprachprüfung in missingarr. Welche Fassung besser ist, entscheiden weiter die Profile von Sonarr; missingarr wählt nur, welche Folgen gesucht werden.
- Radarr bleibt unverändert (die Liste der überwachten Filme mit Datei ist klein, Radarr wertet bei der Suche selbst).
- Keine neue Einstellung außer der vorhandenen „Upgrade Source“. Die Zahl der je Lauf gelesenen Serien ist eine Konstante.

## Entscheidungen

| Punkt | Entscheidung |
|---|---|
| Einstellung | Sonarr beachtet `upgrade_source` wie Radarr: `wanted_list_only` = Cutoff-Liste, `monitored_items_only` = neue Quelle, `both` = beide. |
| Kandidat | Folge überwacht (`episode.monitored`), Serie überwacht (`series.monitored`), Datei vorhanden (`hasFile` und `episodeFile`), Profil der Serie erlaubt Upgrades (`upgradeAllowed`), `episodeFile.customFormatScore` kleiner als `cutoffFormatScore` des Profils. Fehlt der Wert der Datei oder ist das Profil unbekannt, ist die Folge kein Kandidat. |
| Reihenfolge | Aufsteigend nach den Punkten der Datei; bei Gleichstand zufällig. |
| Lesen | Einmal je Lauf `GET /api/v3/series` (Timeout `PROFILE_TIMEOUT`), daraus überwachte Serien mit `statistics.episodeFileCount > 0` und einem Profil, das Upgrades erlaubt und einen `cutoffFormatScore` hat; zufällige Reihenfolge. Je Serie `GET /api/v3/episode?seriesId=<id>&includeEpisodeFile=true`, höchstens `MONITORED_SERIES_BUDGET = 10` Serien je Lauf. Das Lesen endet früher, sobald mindestens `max(per_run * 20, 20)` ungecachte Kandidaten gesammelt sind: Gezählt wird je gelesener Folgenliste nach demselben Filter wie in `_keep_uncached` (Cache, Halteschlüssel, Warteschlange, Fehlerpause, Probelauf-Runde, schon gesehene Schlüssel), wie bei der Cutoff-Liste (A5). Eine Serie, deren Kandidaten alle im Cache stehen, beendet das Lesen also nicht. Danach über alle gelesenen Serien sortieren, dann wie bisher durch `_keep_uncached`. (Nachtrag Review 07.10.2026) |
| Profilwerte | `refresh()` liest `/api/v3/qualityprofile` schon jeden Lauf. `ProfileState` merkt sich daraus je Profil-ID `cutoffFormatScore` und `upgradeAllowed` (neue Felder, ohne Antwort leer). |
| Kandidat-Eintrag | wie bei der Cutoff-Liste: `id` (Folge), `label` „Serie SxxEyy – Titel“, `series_id`, `season_number`, `qualityProfileId` (der Serie). Damit gelten Cache-Schlüssel, Staffelsuche auf dem Befehlsweg, Halteschlüssel und geprüfte Suche unverändert. |
| Cache | Die neue Quelle zählt nicht als Wanted-Liste (`wanted_list=False`), wie „monitored“ bei Radarr: ein Grab der geprüften Suche sperrt nach `retry_hours`, eine leere Suche wird nach „Search again if still missing after (days)“ wieder frei. Bewusst so, nicht weil die Liste es nicht wüsste: Eine Folge bleibt in der Quelle, solange ihre Datei unter dem Endstand liegt. Mit Retry 0 wird ein gescheiterter Upgrade-Grab aus dieser Quelle nicht wiederholt (Review 07.10.2026, Entscheidung beim Betreiber offen). |
| Fehler | Bezeichnung der Quelle in Meldungen bei Sonarr „monitored episodes“, bei Radarr weiter „monitored movies“. Scheitert eine einzelne Folgenliste, wird sie übersprungen und als Hinweis gemeldet; scheitert die Serienliste, scheitert die Quelle wie bisher. Nachtrag Review: Konnte keine einzige Folgenliste gelesen werden, scheitert die Quelle mit der letzten Ausnahme (wie `_collect_cutoff` bei der ersten Seite). Wurden die Qualitätsprofile in diesem Lauf nicht gelesen (`upgrade_allowed` leer), scheitert die Quelle vor der Serienliste mit „quality profiles could not be read“; allein ist der Lauf ein Fehler (A7), mit „both“ ein Hinweis. |
| Oberfläche | Das Feld „Upgrade Source“ erscheint für Sonarr und Radarr, sobald Upgrades an sind. Tooltip und Hinweis nennen beide Bedeutungen von „Monitored Items Only“. |
| Bestehende Instanzen | Die gespeicherte Voreinstellung ist `monitored_items_only`. Eine Sonarr-Instanz wechselt beim Update also von der Cutoff-Liste auf die neue Quelle. CHANGELOG und README sagen das unter „Upgrading to 0.13.0“ und empfehlen „Both“, wer die Cutoff-Liste behalten will. Keine Migration. |

## Tests

Neue Testdatei `tests/test_j1_sonarr_upgrade_source.py` (keine `conftest.py`, Hilfen aus vorhandenen Testdateien importieren, erfundene Serien):

- Quellenwahl für Sonarr je Einstellung (`wanted_list_only`, `monitored_items_only`, `both`) und Voreinstellung.
- Filter: Serie nicht überwacht, Folge nicht überwacht, keine Datei, Punkte gleich oder über dem Endstand, `upgradeAllowed` false, unbekanntes Profil, fehlender Dateiwert, Serie ohne Dateien wird nicht gelesen.
- Reihenfolge: niedrigste Punkte zuerst, auch über mehrere Serien.
- Lesegrenze: höchstens 10 Folgenlisten je Lauf; frühes Ende bei genug ungecachten Kandidaten (Grenzfall 19/20 bei per_run 1, 40 bei per_run 2); eine ganz gecachte Serie beendet das Lesen nicht.
- Fehler: Serienliste scheitert → Quelle scheitert mit „monitored episodes“; eine Folgenliste scheitert → übersprungen, Hinweis; keine Folgenliste gelesen → Quelle scheitert; Profile nicht gelesen → Quelle scheitert ohne Serienliste.
- Mit „both“ kommt eine Folge aus beiden Quellen nur einmal vor.
- Geprüfte Suche: Kandidaten werden zu Folgen-Tasks mit `upg:<id>`.
- `ProfileState` merkt sich `cutoffFormatScore` und `upgradeAllowed`.
- Tooltip, Formular (Feld für Sonarr sichtbar), README, CHANGELOG, VERSION 0.13.0.

Die ganze Testsuite muss grün bleiben.
