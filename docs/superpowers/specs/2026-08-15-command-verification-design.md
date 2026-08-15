# Verifizierte Suchprotokolle

**Datum:** 2026-08-15
**Status:** Entwurf zur Umsetzung
**Betrifft:** `backend/skills/`, `backend/agents/base.py`, `backend/db/`, `backend/database.py`, `templates/`

## Problem

Missingarr protokolliert eine Suche als erfolgt, sobald der HTTP-Aufruf an Sonarr/Radarr
nicht geworfen hat. Was danach in \*arr mit dem Befehl geschieht, erfährt die Anwendung nie.
Die Anzeige behauptet damit mehr, als sie belegen kann.

### Nachgewiesene Befunde

Beide Befunde wurden am 2026-08-15 gegen die laufende Instanz (v0.6.13, Sonarr 172.20.10.33,
Radarr 172.20.10.22) verifiziert, nicht aus dem Code abgeleitet.

**B1 — Die gespeicherte Entitäts-ID ist bei Serien- und Staffelsuchen falsch.**

`search_missing.py:200` schreibt pauschal `record.get("id")` in `search_history_items.arr_id`.
Das ist immer die Episoden-ID des auslösenden Datensatzes — auch dann, wenn `_sonarr_search`
einen `SeriesSearch` mit einer Serien-ID oder einen `SeasonSearch` mit Serien- plus
Staffelnummer abgesetzt hat.

Beleg aus Lauf #9738:

| missingarr speicherte | an Sonarr ging | Prüfung |
|---|---|---|
| `arr_id=2813054`, `item_type=series` | `SeriesSearch seriesId=44565` | `/episode/2813054` → Episode S1E1 der Serie 44565; `/series/2813054` → HTTP 404 |
| `arr_id=2642599`, `item_type=series` | `SeriesSearch seriesId=41697` | `/episode/2642599` → Episode S1E4 der Serie 41697; `/series/2642599` → HTTP 404 |

Betroffen sind **7.447 von 12.357** Einträgen (6.715 `series` + 732 `season`, 60 %).
Der Titel ist jeweils korrekt, nur die ID zeigt ins Leere. `searched_items.cache_key`
ist davon **nicht** betroffen (`ser:44565` ist korrekt) — die Dedup-Logik arbeitet richtig.

**B2 — Erfolg wird beim Absenden gebucht, nicht beim Durchlaufen.**

`_sonarr_search`, `_radarr_search` und `_trigger_upgrade` verwerfen die Antwort von
`agent.http_post()` und geben unbedingt `True` zurück. Die Antwort enthält
`{"id": …, "status": "queued"}` — die Befehls-ID, der einzige Beleg, geht verloren.

Beleg: Lauf #9738 wurde um 13:40:32 als `success` mit `triggered_count=4` geschlossen.
Der vierte Befehl `cmd#3588728` lief in Sonarr erst um 13:40:40 durch — acht Sekunden
nachdem missingarr den Lauf bereits als erfolgreich abgelegt hatte.

**Verschärfend:** Beide Instanzen laufen mit `retry_hours=0`. In `db/searched.py:36`
bedeutet das eine Abfrage *ohne* Zeitfenster — ein einmal vermerktes Item ist dauerhaft
gesperrt. Eine Fehlbuchung wäre nicht selbstheilend.

### Was heute bereits stimmt

Nicht alles ist kaputt, und das begrenzt den Umfang:

- `http_post()` ruft `raise_for_status()`. Von \*arr abgelehnte Befehle (HTTP 4xx/5xx)
  werfen, werden in `_trigger_search` gefangen und erzeugen **keinen** Eintrag.
  Belegt durch die 29 Fehlläufe vom 2026-07-17: alle mit `triggered_count=0`.
- Der Live-Mitschnitt des Laufs #9738 zeigt: alle vier protokollierten Suchen sind
  in Sonarr eingegangen und mit `status=completed, result=successful` durchgelaufen.
  Titel, Typ und Zuordnung stimmen.

Die offene Lücke ist ausschließlich der Fall **„von \*arr angenommen, danach dort
gescheitert"**. Den sieht missingarr derzeit nicht.

### Empirische Randbedingung für das Design

`GET /api/v3/command/{id}` liefert auch **56 Minuten** nach Abschluss noch HTTP 200 —
die Einzelabfrage überlebt weit länger als die rund fünfminütige Queue von
`GET /api/v3/command`. Aber: `result` stand zu diesem Zeitpunkt auf `unknown`,
während der Live-Mitschnitt kurz nach Abschluss `successful` zeigte.

**Folgerung: `status` ist maßgeblich, `result` nur eine Zugabe.** `status` überlebt,
`result` verfällt. Damit wird die Verifikation zeitlich unkritisch — ein verzögerter
Durchlauf liefert weiterhin eine belastbare Aussage statt einer falschen.

## Ziel

Jeder Eintrag, den die Oberfläche zeigt, trägt einen nachprüfbaren Beleg: die Befehls-ID
aus \*arr und deren tatsächlichen Ausgang. Was nicht belegt ist, wird auch nicht als
belegt dargestellt.

## Nicht-Ziele

- Die 7.447 falschen `arr_id` in Altzeilen rückwirkend reparieren. Möglich wäre es
  (Episode → `seriesId` über die API), kostet aber 7.447 Abfragen für Daten, die
  nirgends angezeigt werden.
- Verfolgen, ob eine Suche ein *Release gefunden* hat. Ein durchgelaufener
  `SeriesSearch` ohne Treffer ist kein Fehler. Verifiziert wird die Ausführung,
  nicht der Fund.
- Umbau der UI über die neue Statusspalte hinaus.

## Entwurf

### 1. Datenmodell

Erweiterung über das bestehende Migrationsmuster in `database.py:132-140`
(`ALTER TABLE` in einer Liste, Fehler werden geschluckt).

`search_history_items`:

| Spalte | Typ | Bedeutung |
|---|---|---|
| `command_id` | INTEGER | Befehls-ID aus der \*arr-Antwort. `NULL` bei Altzeilen. |
| `command_status` | TEXT | `submitted` → `completed` / `failed` / `expired`; `legacy` für Altzeilen |
| `cache_key` | TEXT | zum gezielten Entsperren bei Fehlschlag |
| `verified_at` | TEXT | Zeitpunkt der Nachprüfung, `NULL` solange offen |
| `created_at` | TEXT | Entstehung des Items — Bezugspunkt der 24-Stunden-Grenze |

Ein Item, das mangels `command_id` sofort als `expired` angelegt wird, ist zu keinem
Zeitpunkt offen und bekommt `verified_at` deshalb schon beim Einfügen gesetzt.

`search_history`:

| Spalte | Typ | Bedeutung |
|---|---|---|
| `verified_count` | INTEGER | Anzahl bestätigt durchgelaufener Befehle |

Migration setzt für alle Bestandszeilen `command_status='legacy'`. Ohne das würden
Altzeilen mit dem Default `submitted` in die Verifikation laufen und dort mangels
`command_id` dauerhaft hängen.

`arr_id` behält seinen Namen, ändert aber die Bedeutung: künftig die **tatsächlich
gefeuerte** ID. In Verbindung mit `item_type` ist sie damit eindeutig auflösbar
(`series` → `/api/v3/series/{id}`, `episode` → `/api/v3/episode/{id}`,
`movie` → `/api/v3/movie/{id}`). Bei `season` ist die Serien-ID gespeichert;
die Staffelnummer steckt bereits im `cache_key` (`sea:{series}:{season}`).

### 2. Rückgabewert des Trigger-Pfads

`_trigger_search` gibt heute `(success, title, item_type, stored_key)` zurück. Mit
Entitäts-ID und Befehls-ID würde daraus ein Sechser-Tupel — an sechs Rückgabestellen
allein in `_sonarr_search` eine offene Fehlerquelle.

Stattdessen ein Wertobjekt in `backend/skills/base.py`:

```python
@dataclass(frozen=True)
class SearchResult:
    ok: bool
    title: str = ""
    item_type: str = ""
    cache_key: str = ""
    arr_id: int | None = None      # tatsächlich gefeuerte Entitäts-ID
    command_id: int | None = None  # Beleg aus der *arr-Antwort
```

Betroffen: `_sonarr_search` (6 Rückgabestellen), `_radarr_search`, `_trigger_search`,
`_trigger_upgrade` in `search_upgrades.py`. `http_post()` gibt die Antwort bereits
zurück — es genügt, sie nicht mehr wegzuwerfen:

```python
resp = agent.http_post("/api/v3/command", {"name": "SeriesSearch", "seriesId": series_id})
return SearchResult(True, label, "series", f"ser:{series_id}", series_id, resp.get("id"))
```

Fehlt `id` in der Antwort, bleibt `command_id=None` und der Eintrag wird sofort als
`expired` geführt — nicht als bestätigt.

### 3. Verifikation als eigener Skill

Neu: `backend/skills/verify_commands.py`, über `build_skills()` in jedem Agenten
registriert, Intervall 2 Minuten. Ein Durchlauf:

1. Offene Items der eigenen Instanz holen: `command_status='submitted'`, `command_id IS NOT NULL`,
   höchstens 50 pro Durchlauf.
2. Je Item `GET /api/v3/command/{command_id}`.
3. Abbilden:

   | \*arr liefert | `command_status` |
   |---|---|
   | `status=completed` | `completed` |
   | `status=failed` / `aborted` / `cancelled` | `failed` |
   | `status=queued` / `started` | bleibt `submitted` (nächster Durchlauf) |
   | HTTP 404 | `expired` |
   | Netzwerkfehler | unverändert, nächster Durchlauf |

   `aborted` und `cancelled` zählen bewusst als Fehlschlag: ein abgebrochener Befehl
   hat nicht gesucht. Das ist derselbe belegte Sachverhalt wie `failed` und rechtfertigt
   dieselbe Folge, die Freigabe im Cache. Der Unterschied zu `expired` bleibt gewahrt —
   dort ist der Ausgang unbekannt, hier ist er bekannt und negativ.

4. `verified_at` setzen. Danach das Aggregat (Abschnitt 5) neu berechnen für
   **alle** Läufe der Instanz, die noch auf `pending` stehen und kein `submitted`-Item
   mehr haben. Diese Formulierung — statt „Läufe, die in diesem Durchlauf berührt
   wurden" — schließt Läufe ein, deren Items schon beim Anlegen `expired` waren und
   die daher nie durch die Verifikation liefen.

Ein Item, das **24 Stunden nach seiner eigenen Entstehung** noch `submitted` ist, wird
auf `expired` gesetzt. Ohne diese Grenze sammeln sich Karteileichen, die bei jedem
Durchlauf erneut abgefragt werden. Maßgeblich ist das Alter des Items, nicht der Beginn
seines Laufs: bei `missing_per_run=600` und `seconds_between_actions=2` liegen zwischen
erstem und letztem Item eines Laufs über zwanzig Minuten. Dafür trägt jedes Item ein
eigenes `created_at`.

Der Skill läuft auch, wenn die Suche des Agenten deaktiviert ist — sonst blieben
Einträge nach dem Abschalten für immer offen.

### 4. Selbstheilung bei Fehlschlag

Bei `failed` wird der `cache_key` aus `searched_items` gelöscht, damit der Titel
wieder gefunden wird. Wegen `retry_hours=0` ist das zwingend: ohne Löschen bliebe
er dauerhaft gesperrt, obwohl nie gesucht wurde.

Gelöscht wird **ohne** Prüfung, ob inzwischen ein neuerer Erfolg denselben Schlüssel
belegt hat. Die Asymmetrie ist beabsichtigt: zu viel löschen kostet eine überflüssige
Suche, zu wenig löschen reißt ein dauerhaftes Loch in die Bibliothek.

Bei `expired` wird **nicht** gelöscht. Unbekannter Ausgang ist kein belegter Fehlschlag;
die Wiederholung würde sonst bei jeder Netzwerkstörung anspringen.

### 5. Lauf-Status folgt der Verifikation

`search_history.status` bedeutet künftig den bestätigten Ausgang, nicht das Absenden:

| Wert | Bedingung |
|---|---|
| `error` | Der Lauf selbst warf eine Ausnahme (unverändert) |
| `success` | Alle Items `completed` — oder der Lauf hat gar keine Items |
| `pending` | Mindestens ein Item noch `submitted` |
| `partial` | Nichts mehr offen, mindestens eines `completed`, mindestens eines nicht |
| `failed` | Nichts mehr offen, keines `completed`, mindestens eines `failed` |
| `unverified` | Nichts mehr offen, keines `completed`, keines `failed` — also nur `expired` |

`unverified` ist bewusst von `failed` getrennt. Ein Befehl, dessen Ausgang \*arr nicht
mehr kennt, ist kein belegter Fehlschlag. Beides in einen Topf zu werfen hieße, eine
Behauptung durch eine andere zu ersetzen — genau der Fehler, den diese Änderung behebt.

`finish_run()` schreibt `pending`, sobald Items vorhanden sind, sonst direkt `success`.
`verify_commands` rechnet das Aggregat nach jedem Durchlauf neu und setzt
`verified_count` auf die Zahl der `completed`-Items.

`triggered_count` behält seine Bedeutung „abgeschickt". Die beiden Zahlen nebeneinander
sind die Aussage: *4 abgeschickt, 4 bestätigt*.

### 6. Sperre auf Skill-Ebene ziehen

`base.py:160-169` sperrt über `self.state["status"]`, das sich alle Skills eines Agenten
teilen — der Kommentar sagt „same skill", der Code sperrt global. Nachweisbare Folge:
stündlich `Already running — skipping duplicate trigger` für den `health_check`,
jeweils zehn Sekunden nach Beginn des Suchlaufs (166 Vorkommen im Log).

Unverändert würde die Sperre den neuen `verify_commands` genauso wegdrücken, sobald
eine Suche läuft — bei 2-Minuten-Takt gegen mehrminütige Läufe regelmäßig.

Deshalb: eigenes `threading.Lock` je Skillname statt eines gemeinsamen Zustands.
`state["status"]` bleibt als Anzeigewert erhalten und wird weiterhin von den
Suchskills gesetzt — der `health_check` und `verify_commands` fassen ihn nicht an,
damit die Dashboard-Anzeige nicht flackert.

### 7. Anzeige

**History** (`templates/history.html:127-131`): Die Badge-Zuordnung kennt heute nur
`success` / `error` / `running`. Ergänzen um:

| Status | Badge | Text |
|---|---|---|
| `success` | `badge-online` | bestätigt |
| `pending` | `badge-scheduled` | offen |
| `partial` | `badge-error` | teilweise |
| `failed` | `badge-offline` | gescheitert |
| `unverified` | `badge-unknown` | nicht prüfbar |
| `error` | `badge-offline` | Fehler |

Je Item zusätzlich der eigene Zustand. Altzeilen (`legacy`) zeigen einen neutralen
Strich statt einer Behauptung.

**Dashboard** (`templates/instances/card.html:109-113`): Die Kachel `last_triggered`
zeigt künftig `bestätigt / abgeschickt`, also `4 / 4`. Der Agent führt dafür
`state["last_verified"]` mit, das `verify_commands` nach jedem Durchlauf für den
jüngsten Lauf der Instanz aktualisiert. `static/js/app.js:220` muss beide Werte lesen.

Nach einem Neustart des Containers stehen die In-Memory-Werte auf 0, bis der erste
Lauf durch ist — unverändertes Verhalten, hier nicht behandelt.

## Datenfluss

```
Suchlauf
  └─ _trigger_search → SearchResult(ok, …, arr_id, command_id)
       └─ insert_item(command_status='submitted', command_id, cache_key, arr_id)
  └─ finish_run(status='pending')

alle 2 min: verify_commands
  └─ offene Items der Instanz
       └─ GET /api/v3/command/{id}
            ├─ completed → command_status='completed'
            ├─ failed    → command_status='failed'  +  searched_items-Zeile löschen
            └─ 404       → command_status='expired'
  └─ Lauf-Aggregat neu berechnen → success | partial | failed | pending
  └─ state["last_verified"] aktualisieren
```

## Testbarkeit

Das Projekt hat heute keine Tests. Diese Änderung ist der falsche Ort, um eine
Testinfrastruktur einzuführen — aber die Kernlogik wird so geschnitten, dass sie
ohne laufende \*arr-Instanz prüfbar ist:

- Die Abbildung \*arr-Antwort → `command_status` als reine Funktion.
- Die Aggregation Item-Zustände → Lauf-Status als reine Funktion.

Beides ohne HTTP und ohne Datenbank aufrufbar. Ob dafür Tests geschrieben werden,
entscheidet der Implementierungsplan.

Verifikation der fertigen Umsetzung erfolgt wie bei dieser Analyse: Mitschnitt der
\*arr-Command-Queue während eines echten Laufs, danach Abgleich mit
`search_history_items`. Erwartung: für jedes Item eine `command_id`, die in \*arr
existiert, mit übereinstimmendem `arr_id` und Befehlstyp.

## Risiken

| Risiko | Einschätzung |
|---|---|
| Zusätzliche API-Last | Ein GET je abgeschicktem Befehl. Bei Sonarr (4/Stunde) vernachlässigbar. Bei Radarr steht `missing_per_run=600` bei `rate_cap=999999999` — dort wäre ein Lauf mit vielen Treffern spürbar. Deckel von 50 Items je Durchlauf begrenzt das. |
| `result` verfällt | Umgangen, indem `status` maßgeblich ist. |
| Löschen aus `searched_items` löst Neusuche aus | Beabsichtigt. Nur bei belegtem `failed`, nicht bei `expired`. |
| Umbau der Sperre bricht bestehendes Verhalten | Betrifft alle Skills. Nach dem Umbau muss geprüft werden, dass zwei Suchläufe derselben Instanz sich weiterhin ausschließen und der Force-Trigger seine 90-Sekunden-Wartezeit behält. |
| Radarr-Instanz mit `per_run=600` | Ein einzelner Lauf kann sehr viele Items erzeugen. Bei der Umsetzung prüfen, ob der 50er-Deckel je Verifikationsdurchlauf ausreicht, um nicht dauerhaft hinterherzulaufen. |

## Offene Frage für die Umsetzung

Der 2-Minuten-Takt ist geschätzt, nicht gemessen. Im beobachteten Lauf waren Befehle
nach 2–14 Sekunden durch. Sollte sich zeigen, dass `SeriesSearch` auf großen Serien
deutlich länger braucht, ist der Takt unkritisch — `status` verfällt nicht, die
Bestätigung kommt dann eben einen Durchlauf später.
