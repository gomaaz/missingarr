# Geprüfte Suche: Namensvetter, Folgentitel und abweichende Zählung (Sonarr)

**Datum:** 2026-10-07
**Status:** mit dem Betreiber abgestimmt (07.10.2026: „Passt, alle 3 Regeln an“)
**Version:** 0.12.0
**Betrifft:** `backend/checked_search/` (`sonarr_rules.py`, neues Modul `episode_titles.py`, `settings.py`, `verdict.py`, `runner.py`), `backend/tooltips.py`, `README.md`, `CHANGELOG.md`, `VERSION`

## Problem

In der ersten Nacht mit „Active“ (07.10.2026) hat die geprüfte Suche 73 Releases geladen, 13 davon für die falsche Folge. Alle 13 hat der Vorfilter als sauber durchgelassen.

1. **Namensvetter (2 Grabs).** Für die Netflix-Serie „Nemesis (2026)“ kam `Nemesis.S01E05.1080p.DSNP.WEB-DL…` (Disney+, die niederländische Serie „Nemesis (2024)“). Ein Indexer hatte das Release mit der tvdbId der gesuchten Serie gestempelt, Sonarr ordnete es per ID zu (`seriesMatchType: Id`). `/parse` ordnet einen Namen ohne Jahr keiner Serie zu, wenn alle Serien dieses Titels in der Bibliothek einen Zusatz tragen (`nemesis2026`, `nemesis2024`, `nemesisau`): `series` ist `null`. S1 greift deshalb nicht (es braucht eine *andere* Serie aus `/parse`), S2 findet keinen Zusatz im Namen, und eine Sonarr-Fassung von „Releases without year need an exact title“ gibt es nicht. `parse_from_resource` liest `seriesTitleInfo.year` gar nicht aus.
2. **Abweichende Zählung (11 Grabs).** Eine Release-Gruppe zählt bei „CatDog“ halbe Stunden (zwei Geschichten je Datei), TVDB und Sonarr zählen jede 15-Minuten-Geschichte einzeln. Für die Serie gibt es kein XEM-Mapping, Sonarr ordnet die Nummer wörtlich zu. Die meisten Releases tragen keinen Folgentitel (`CatDog.1998.S01E14.1080p…`). Wo einer steht, zeigt er die andere Zählung (`CatDog.1998.S01E20.All.About.Cat.Trespassing…` = TVDB S01E39 + S01E40). Der Vorfilter schaut den Folgentitel nie an (offener Befund B5 aus dem Debug-Bericht vom 03.10.2026).

## Ziel

Drei neue Sonarr-Regeln, jede als Schalter mit Info-Symbol wie S1–S4, Voreinstellung an. Gemessen an allen Sonarr-Zeilen der Probeläufe vom 02.–07.10.2026 und der Active-Nacht (898 gewählte Releases, 3.223 Paare aus Release und Folge) hätten sie 12 der 13 Fehlgriffe verhindert, mit dem Auslöser „zwei Titel“ rechnerisch alle 13, ohne Fehlsperre unter den gewählten Releases.

## Nicht-Ziele

- Keine Prüfung von Laufzeit oder Dateigröße (unzuverlässig, Nicht-Ziel der Spec vom 01.10.2026).
- Keine Mehrfachfolgen-Releases. Serien, deren Releases richtig nur als Doppelfolge vorliegen, bleiben über die geprüfte Suche kaum ladbar.
- Keine Sprachprüfung (bleibt bei den Profilen).
- Die Imports-Seite (`import_check.py`) ruft `sonarr_rules.evaluate` weiter ohne Serienliste und Folgenliste auf; die neuen Regeln wirken dort nicht.
- Radarr bleibt unverändert.

## Entscheidungen (Betreiber, 07.10.2026)

| Punkt | Entscheidung |
|---|---|
| Umfang | S5, S6 und S7 wie unten, alle drei Voreinstellung an |
| Länderliste | `IL` kommt in die Voreinstellung der Ländercodes (die israelische Fassung „Love Island IL“ fiel sonst durch S2) |
| Probelauf | kein neuer Pflicht-Probelauf; die Regeln laufen sofort im aktiven Modus, die Seite Pre-filter zeigt, was sie sperren |

## Regeln

### S5, Namensvetter

Ein Release fällt mit Grund `other series by name` weg, wenn alle vier Bedingungen zutreffen:

1. `/parse` nennt keine Serie (`series` fehlt oder ist `null`).
2. `parsedEpisodeInfo.seriesTitleInfo.year` ist 0 oder fehlt.
3. Der geparste Serientitel trägt keinen Zusatz: `title_suffix(seriesTitle, country_codes)` ergibt `(0, "")`.
4. Eine kompakte Schreibweise des geparsten Serientitels (`normalize.variants`) ist der **Grundtitel** einer **anderen** Serie der Bibliothek. Grundtitel einer Serie: ihr Titel und jeder Alternativtitel, jeweils ohne den Zusatz am Ende, und zusätzlich ihr voller Titel. Als Zusatz zählt nur, was als Zusatz geschrieben ist (Nachtrag nach der Prüfung, siehe unten): ein Ländercode der Instanz in Großbuchstaben oder in Klammern (`Some Show US`, `Some Show (AU)`, nicht `Among Us`), ein Jahr in Klammern oder höchstens ein Jahr vom Jahr der Serie entfernt (`Lost in 1949` bleibt ganz); höchstens je eins, das erste Wort bleibt immer. Abgeschnitten wird am Originaltext, damit Umlaut- und `&`-Schreibweisen über `variants` erhalten bleiben (`Alex & CO` → `alexund`, nicht `alex`). Die Zielserie selbst zählt nicht. Präfix- oder Wortvergleiche gibt es nicht („My Royal Nemesis“ ist kein Namensvetter von „Nemesis“).

Ausnahme: Steht das Jahr der Zielserie (`series.year`) im Releasenamen als eigenes Wort (zwischen Trennzeichen `.`, ` `, `_`, `-`, `(`, `)`, `[`, `]`), und hat keiner der gefundenen Namensvetter dieses Jahr, fällt das Release nicht weg (`Nemesis.S01E03.2026.1080p…` für „Nemesis (2026)“).

Einstellung: „Veto when the title also fits another series“ (`veto_namesake`, an).

Gemessen: 5 gewählte Releases gesperrt, alle richtig (Nemesis S01E01, E02, E03, E05); unter 7.255 bestandenen Zeilen 72 gesperrt, davon 56 vermutlich richtige Netflix-Releases ohne Jahr, die nie gewählt waren, weil ein Release mit Jahr davor stand. Reichweite: Nur in Titelgruppen, bei denen `/parse` für den bloßen Grundtitel keine Serie liefert (17 von 193 Gruppen mit gleichem Grundtitel in der Bibliothek).

### S6, Folgentitel

Steht im Releasenamen hinter `SxxEyy` ein Folgentitel, wird er mit den Folgentiteln der Serie verglichen. Das Release fällt mit Grund `other episode by title` weg, wenn der Titel eindeutig zu einer **anderen** Folge derselben Serie passt und nicht zur Zielfolge. Passt der Titel zu keiner Folge (deutscher Titel gegen englische TVDB-Titel, Zusatztexte wie „Weekly Recap“), gibt es nur den Hinweis `episode title fits no episode` und keine Sperre.

Ablauf (reine Funktionen in `backend/checked_search/episode_titles.py`, übernommen aus dem gemessenen Prototyp):

1. **Titelteil:** Text zwischen `SxxEyy` (auch `SxxEyyEzz`, `SxxEyy-zz`) und dem ersten Qualitäts-, Quellen- oder Sprach-Token (Auflösung `\d{3,4}[pi]`, `WEB-DL`, `BluRay`, `x264`, `GERMAN`, `MULTi`, Dienstkürzel wie `AMZN`, `NF`, `DSNP` …). Ohne solches Token wird ein Gruppen-Anhang `-GRP` am letzten Wort abgeschnitten. Kein `SxxEyy` im Namen: die Regel prüft nichts.
2. **Wörter:** ASCII, klein, Umlaute als ae/oe/ue/ss, Apostrophe entfernt, `&` als „and“; nur Wörter aus Buchstaben mit mindestens 2 Zeichen, ohne Stoppwörter (englisch, deutsch, französisch, spanisch, italienisch: Artikel, Präpositionen, „and/und/et“), ohne Platzhalterwörter („episode“, „folge“, „part“, „teil“, „chapter“, „tba“, „finale“ …) und ohne Wörter des Serientitels (ab 4 Buchstaben, auch mit einem Buchstaben weniger am Ende).
3. **Ohne Prüfung bleiben:** kein Titelteil, nur Platzhalter („Episode 20“), weniger als 2 Wörter, Zielfolge mit Platzhaltertitel („TBA“, „Episode 5“).
4. **Passung:** Zwei Wörter gelten als gleich, wenn sie gleich sind oder beide mindestens 5 Zeichen haben und `difflib.SequenceMatcher(...).ratio() >= 0.85`. Gewicht eines Wortes: `log((n + 1) / (df + 0.5))` über die Folgentitel der Serie (seltene Wörter zählen mehr). Passung eines Folgentitels zum Release = das größere von „Anteil des Folgentitels, der im Release steht“ und „Anteil des Releasetitels, der im Folgentitel steht“ (gewichtet). Steht der zusammengezogene Zieltitel (mindestens 8 Zeichen) im zusammengezogenen Releasetitel oder umgekehrt, gilt die Zielfolge als passend.
5. **Entscheidung:**
   - Passung zur Zielfolge mindestens 0,5 → passt (keine Sperre).
   - Sonst für jede andere Folge der Serie: Steht ihr ganzer Titel im Release (Anteil ≥ 0,99), zählt sie als Kandidat. Bester Kandidat: die meisten „spezifischen“ Wörter (mindestens 4 Zeichen, in höchstens 2 Folgentiteln der Serie), dann die meisten Treffer. „Erklärt“ = Anteil der Releasewörter, die in den Titeln aller Kandidaten vorkommen.
   - Sperre (`other episode by title`), wenn der beste Kandidat mindestens 1 spezifisches Wort hat **und** (mindestens 2 Treffer **oder** ein spezifisches Wort mit mindestens 6 Zeichen) **und** „erklärt“ mindestens 0,5 ist.
   - Kandidat ohne diese Stärke → keine Sperre, kein Hinweis.
   - Kein Kandidat und keine Passung → Hinweis `episode title fits no episode`.
6. **Zwei Titel:** Lässt sich der Titelteil an einem Bindestrich zwischen zwei Wörtern (`Sent.You-Dogs.Strange`) in zwei Teile mit je mindestens 2 Wörtern trennen, wird zusätzlich jeder Teil für sich nach 3.–5. geprüft. Passt keiner der beiden Teile zur Zielfolge und ergibt einer `other episode by title`, fällt das Release mit diesem Grund weg.

Daten: Folgenliste der Serie (`GET /api/v3/episode?seriesId=<id>`, `seasonNumber`, `episodeNumber`, `title`), einmal je Serie und Lauf, nur geladen, wenn ein Release des Titels einen prüfbaren Titelteil hat. Specials (Staffel 0) zählen mit.

Einstellung: „Reject when the episode title names another episode“ (`check_episode_title`, an).

Gemessen: 12 gewählte Releases gesperrt, alle richtig (CatDog, American Dad mit Szene-Zählung, „Regular Show: The Lost Tapes“ nach TVDB-Umnummerierung); 0 Fehlsperren. In der Sonarr-Historie (17.105 Grabs) träfe die Sperre 2,2 %, ohne bekannte Fehlsperre.

### S7, Zählung zweifelhaft

Ergibt S6 für mindestens ein Release der Ergebnisliste `other episode by title`, zeigt das, dass Releases dieser Liste für die gesuchte Nummer eine andere Folge enthalten können. Mit zählen nur Releases, die die Regeln erreichen (dieser Folge zugeordnet, kein Staffelpaket, keine Mehrfachfolge): Eine Doppelfolge, die nach ihrem anderen Teil benannt ist, beweist keine andere Zählung (Nachtrag nach der Prüfung). Dann fällt jedes Release **ohne prüfbaren Titel** (kein Titelteil oder nur Platzhalter, und auch zwischen `SxxEyy` und dem ersten Quellen- oder Codec-Token wie `WEB-DL`, `1080p`, `x264` steht außer Qualitäts- und Sprachwörtern kein Titelwort; `Spanish.Fry` oder `Web.of.Lies` zählen als Titel) dieser Liste mit Grund `episode numbering in doubt` weg, wenn alle seine Sprachen auch Sprachen des belastenden Releases sind. Sprachen: `languages[].name` aus `GET /api/v3/release`. Hat eines der beiden Releases keine oder nur „Unknown“ als Sprache, greift S7 für dieses Paar nicht. So bleiben deutsche und Dual-Language-Releases frei, wenn ein englisches Release die andere Zählung zeigt.

S7 wertet die ganze Liste aus, bevor das erste saubere Release gewählt wird (eine reine Vorabprüfung der Releasenamen gegen die Folgenliste, ohne zusätzliche Anfragen).

Einstellung: „Reject untitled releases when another release shows a different numbering“ (`untitled_after_other_episode`, an). Wirkt nur, wenn S6 an ist.

Gemessen: zusammen mit S6 in der Active-Nacht 10 von 11 CatDog-Fehlgriffen gesperrt (S6 allein: 1); in allen Daten nur CatDog-Listen und eine Regular-Show-Liste betroffen.

## Umsetzung

- `EpisodeParse` bekommt `year` (aus `parsedEpisodeInfo.seriesTitleInfo.year`).
- `sonarr_rules.evaluate(info, release_title, publish_date, parse, settings, context=None)`: `context` ist ein neues, optionales Objekt mit dem Namensvetter-Index, der Folgenliste und dem S7-Befund der Liste. Ohne `context` (Imports-Seite, alte Aufrufe) verhält sich `evaluate` genau wie bisher.
- `EpisodeInfo` bekommt die Folgen-Koordinaten der Zielfolge (`season_number`, `episode_number`) und ihren Titel (`episode_title`), aus `GET /api/v3/episode/{id}`.
- Namensvetter-Index: aus `GET /api/v3/series` (alle Serien mit Titel, Alternativtiteln, Jahr) ein Wörterbuch kompakter Grundtitel → Serien-IDs und Jahre. Geladen nur, wenn ein Release die Bedingungen 1–3 erfüllt; im Speicher je Instanz 24 Stunden gehalten (ein Neustart oder das Speichern der Instanz leert ihn). Die Liste ist bei 11.000 Serien rund 39 MB groß und braucht etwa 13 s. Ist sie nicht lesbar, gilt das betroffene Release als ungeprüft (`parse error`, wie ein Fehler von `/parse`): Es wird nicht geladen, und findet sich kein anderes sauberes Release, endet der Titel als Fehler mit Pause.
- Folgenliste: je Serie einmal pro Lauf (im `_TitleCheck` zwischengespeichert). Nicht lesbar: alle Releases des Titels mit prüfbarem Titelteil gelten als ungeprüft (`parse error`), S7 greift nicht.
- Neue Gründe in `verdict.py`: `REASON_NAMESAKE = "other series by name"`, `REASON_OTHER_EPISODE = "other episode by title"`, `REASON_NUMBERING = "episode numbering in doubt"`. Hinweis: `"episode title fits no episode"`.
- Einstellungen in `SONARR_FIELDS` (verändert den Regel-Fingerabdruck von Sonarr; ein Probelauf beginnt dadurch für jeden Titel neu, im aktiven Modus ohne Wirkung), Labels in `FIELD_LABELS`, Tooltips `cs_veto_namesake`, `cs_check_episode_title`, `cs_untitled_after_other_episode`.
- Wie bei S1–S4 schreibt der Probelauf jede Regel mit, die greift.

## Tests

- `tests/test_i1_namesake.py`: Bedingungen 1–4 einzeln, Ausnahme mit Jahr, Zielserie zählt nicht, Alternativtitel, Umlaut-Schreibweise, kein Präfix-Treffer, `veto_namesake` aus, ohne `context` unverändert.
- `tests/test_i2_episode_titles.py`: Titelteil (mit und ohne Qualitäts-Token, Gruppen-Anhang, Mehrfachnummern), Platzhalter, zu kurz, passt (auch zusammengezogen), andere Folge, schwacher Kandidat, kein Treffer = Hinweis, zwei Titel, deutsche Titel nur Hinweis, Serienwörter zählen nicht.
- `tests/test_i3_numbering.py`: S7 sperrt titellose Releases gleicher Sprache, lässt andere Sprachen und „Unknown“ frei, wirkt nur mit S6 an, wirkt vor der Wahl des ersten sauberen Releases.
- `tests/test_i4_runner_context.py`: Namensvetter-Index nur bei Bedarf geladen und 24 h gehalten, Folgenliste einmal je Serie und Lauf, Lesefehler → `parse error`, Imports-Seite unverändert.
- Alle Testdaten erfunden („Some Show“, „Some Show (2020)“, „Some Show (AU)“).

## Einführung

Nach dem Einspielen: in beiden Sonarr-Instanzen `IL` in die gespeicherten Ländercodes aufnehmen (gespeicherte Werte überschreiben die Voreinstellung). Serien mit bekannt abweichender Zählung, die vor 0.12.0 aus der Überwachung genommen wurden, bleiben so, bis die Seite Pre-filter zeigt, dass S6 und S7 ihre Releases zuverlässig sperren.

## Nachtrag nach der Prüfung (07.10.2026)

Eine Prüfung aus fünf Blickwinkeln mit Gegenprüfung fand drei Lücken im Verhalten; alle drei sind behoben, die Messung über die echten Listen ergibt danach dieselben Sperren wie vorher (S6: 12 gewählte Releases, S7: nur CatDog-Listen):

1. **S7-Zweifel aus Releases, die die Regeln nie sehen.** Eine Doppelfolge (`S01E02E03`), die nach ihrem ersten Teil benannt ist, galt bei der Suche nach dem zweiten Teil als „andere Folge“ und sperrte das richtige Release ohne Titel. Jetzt zählen nur Releases, die die Tore des Runners passieren (`_TitleCheck.gate`, dieselben Tore wie in `verdict`).
2. **Titel, die mit einem Qualitäts- oder Sprachwort beginnen** (`Spanish.Fry`, `Real.Cats.Wear.Plaid`, `Web.of.Lies`), galten als titellos und konnten von S7 gesperrt werden. Jetzt ist ein Release nur titellos, wenn auch vor dem ersten Quellen- oder Codec-Token kein Titelwort steht. S6 selbst vergleicht wie gemessen.
3. **S5 schnitt normale Wörter als Zusatz ab** (`Killing It (2022)` → `killing`, `Among Us` → `among`, `Alex & Co` → `alex`). Jetzt zählt nur ein als Zusatz geschriebener Ländercode oder ein Jahr wie oben bei S5 beschrieben. Nemesis wird weiter gesperrt (5 gewählte Releases).

Dazu: Tooltip und CHANGELOG nennen die zweite Bedingung der Jahres-Ausnahme, und Schutztests für Squeeze, Zwei-Titel-Regel, die Kategorien `other_weak`/`target_generic`/`too_short`, „Unknown“ und S6 aus.
