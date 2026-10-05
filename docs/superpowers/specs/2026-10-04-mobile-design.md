# Handy-Ansicht: missingarr am Handy bedienbar machen

**Datum:** 2026-10-04
**Status:** Entwurf, mit dem Betreiber abgestimmt (Umfang, Navigation, Tabellen, Ansatz und Abschnitte 1–4 am 04.10.2026)
**Version:** 0.11.0
**Betrifft:** `templates/base.html`, `templates/instances/card.html`, `templates/logs.html`, `templates/checked_search.html`, `templates/history.html`, `templates/imports.html`, `templates/searched.html`, `templates/instances/list.html`, `templates/instances/form.html`, `templates/help.html`, `templates/login.html`, `static/css/app.css`, `static/js/app.js`, neue Tests, neues Skript `scripts/mobile_check.py`, `CHANGELOG.md`, `README.md`, `VERSION`

## Problem

Gemessen am 04.10.2026 mit einem Handy-Viewport von 390 × 844 px (Chromium, `is_mobile`, angemeldet):

- Jede Seite ist 1.062 bis 1.079 px breit (`document.documentElement.scrollWidth`) und lässt sich seitlich verschieben. Ursache ist die Navigationsleiste: Acht Links, Version, GitHub und Abmelden stehen in einer Zeile. „Logs“, „Help“, die Version und das Abmelden erreicht man nur durch Wischen.
- Tabellen sind abgeschnitten. Auf „Logs“ sieht man Zeit, Instanz und Stufe, die Meldung liegt außerhalb des Bildschirms. Auf „Pre-filter“ und „History“ endet der Titel nach wenigen Buchstaben.
- Im Kopf der Dashboard-Karten überlappen sich Typ, „dry run“, „WAIT“ und „Test“. Der Name der Instanz ist nicht zu sehen.
- Viele Bedienelemente sind kleiner als 44 × 44 px (`.btn-sm`, Auswahlfelder, Seitenzahlen, Haken).
- Die „?“-Hilfen öffnen sich nur beim Überfahren mit der Maus (`[data-tooltip]:hover`). Am Handy gibt es kein Überfahren.

Gut nutzbar sind schon die Seiten Imports (ohne offene Einträge), Progressed und das Bearbeiten-Formular. Das Formular stellt seine Felder unter 768 px schon einspaltig.

## Ziel

Am Handy lassen sich die vier Seiten, die der Betreiber unterwegs braucht, bequem lesen und bedienen: Dashboard, Imports, Pre-filter und Logs. Dazu gehören nachsehen, eine Suche anstoßen, eine Instanz ein- oder ausschalten und einen Import bestätigen oder verwerfen. Alle übrigen Seiten laufen am Handy nicht mehr seitlich über, und alles darauf ist antippbar. Am PC ändert sich nichts.

## Nicht-Ziele

- Keine Änderung der Darstellung über 768 px Breite.
- Kein neues Aussehen: Farben, Schrift, dunkles Thema und Orange als Akzent bleiben. Das Projekt hat seinen eigenen Stil; hier geht es um die Bedienung am Handy.
- Keine Änderung an Backend, API, Datenbank oder Live-Aktualisierung.
- Keine eigenen Handy-Vorlagen und keine Weiche nach Gerät.
- Keine installierbare App (PWA), kein Offline-Betrieb, keine neuen Bibliotheken im Frontend.

## Entscheidungen (Betreiber, 04.10.2026)

| Punkt | Entscheidung |
|---|---|
| Nutzung | „Nachsehen und kurz eingreifen“: Dashboard, Imports, Pre-filter und Logs bekommen echte Handy-Ansichten, alle anderen Seiten werden nur repariert (kein Überlauf, alles antippbar, Tabellen als Listen). |
| Navigation | Schwebende Leiste unten mit vier Zielen und „More“: Dashboard, Imports (mit Zähler), Pre-filter, Logs. „More“ öffnet ein Blatt von unten mit Instances, History, Progressed, Help, GitHub, Version und „Sign out“. |
| Tabellen | Am Handy wird jede Zeile ein kompakter Listeneintrag (oben Meldung oder Titel, darunter Zeit · Instanz …, rechts die Pille). Antippen klappt Details auf, wo es heute schon Details gibt. |
| Ansatz | Eine Handy-Schicht im CSS, die nur bis 768 px wirkt. Dieselben Elemente (dasselbe DOM) werden am Handy anders angeordnet. |
| Aussehen | Bleibt wie heute (siehe Nicht-Ziele). |
| Abschnitte 1–4 | Rahmen, Dashboard-Karten, Listen und Filter, Imports-Karten und übrige Seiten wie unten beschrieben. |
| Codex | Keine Codex-Prüfung: reine Oberfläche, ohne Nebenläufigkeit, ohne Schreiben in andere Dienste. |
| Einspielen | Erst nach Durchsicht der Handy-Fotos aller Seiten und ausdrücklicher Freigabe. |

## Grundsatz

- **Grenze:** `@media (max-width: 768px)`, dieselbe Grenze, die `app.css` heute schon nutzt. Darüber gilt die heutige Darstellung unverändert.
- **Ein DOM, zwei Darstellungen:** Tabellen, Filter und Knöpfe werden nicht doppelt ausgegeben. Die Handy-Schicht ordnet sie per CSS um. So bleiben Alpine-Bindungen, htmx-Abfragen und die Live-Aktualisierung unverändert. Einzige neue Elemente: die Leiste unten mit dem „More“-Blatt, je ein Knopf „Filter“ auf den Seiten mit Filtern und eine Seitenanzeige „Page x / y“ beim Blättern. Sie sind über 768 px ausgeblendet.
- **Tippflächen:** Jedes sichtbare Bedienelement ist am Handy mindestens 44 × 44 px groß. Das gilt für Knöpfe, Links in Leiste und Blatt, Auswahlfelder, Eingaben, Haken samt Beschriftung und Seitenknöpfe. Ausnahme sind Links im Fließtext, etwa auf der Help-Seite.
- **Kein Überlauf:** Keine Seite ist breiter als der Bildschirm. Lange Namen und Releasenamen brechen um (`overflow-wrap: anywhere`), statt die Seite zu verbreitern.
- **Eingaben:** Eingabefelder und Auswahlfelder haben am Handy 16 px Schrift. Bei kleinerer Schrift vergrößert Safari auf dem iPhone die Seite beim Antippen.

## 1 Rahmen (`base.html`, `app.css`, `app.js`)

### Kopf

Am Handy zeigt die obere Leiste nur Logo, Name und Version. `.nav-links`, der GitHub-Knopf und das Abmelden sind ausgeblendet. Die Leiste wird 48 px hoch und bleibt oben stehen (`position: sticky` wie heute).

### Leiste unten

- Ein `<nav class="tabbar" aria-label="Main">` nach `<main>`, nur am Handy sichtbar.
- Sie schwebt mit 8 px Abstand zu den Rändern und hat abgerundete Ecken (14 px). Hintergrund ist `--bg-secondary`, der Rand `--border`, dazu ein leichter Schatten.
- Fünf gleich breite Ziele, je mindestens 44 px hoch, mit Symbol und Wort darunter: **Dashboard** (`/`), **Imports** (`/imports`), **Pre-filter** (`/checked-search`), **Logs** (`/logs`) und **More** (Knopf, kein Link).
- Symbole: feine Umrisse, 20 px groß, 1,8 px Strich, als Inline-SVG wie das Logo heute.
- Imports trägt denselben Zähler wie der Menüpunkt oben: ein zweites `<span class="badge badge-error" data-imports-count hidden>`. `updateImportsCount` füllt schon heute alle `[data-imports-count]`, es braucht keine neue Abfrage. `startImportsCount()` wird weiter nur einmal je Seite gestartet.
- Das aktive Ziel ist eine schwach orange getönte Pille (Fläche etwa 12 % `--accent`, Wort und Symbol in `--accent`) mit `aria-current="page"`. Aktiv ist ein Ziel nach denselben Pfadregeln wie oben (`/` genau, sonst „Pfad enthält“).
- **More** gilt als aktiv, wenn die Seite eines der Ziele im Blatt ist: `/instances…`, `/history`, `/searched`, `/help`.
- Platz für den Home-Balken am iPhone: Das Viewport-Meta bekommt `viewport-fit=cover`, der Abstand nach unten ist `calc(8px + env(safe-area-inset-bottom))`.
- `<main>` bekommt am Handy so viel Abstand nach unten, dass die Leiste keinen Inhalt verdeckt (Höhe der Leiste plus Abstand plus `env(safe-area-inset-bottom)`). Toasts erscheinen am Handy oberhalb der Leiste.

### Blatt „More“

- Ein Blatt von unten (`role="dialog"`, `aria-modal="true"`, Titel „More“) über einer abgedunkelten Fläche.
- Inhalt: Listenzeilen von je mindestens 48 px für Instances, History, Progressed, Help und GitHub. GitHub öffnet einen neuen Tab und zeigt ein kleines Pfeil-Symbol. Darunter, abgesetzt: die Version (`v{{ version }}`) und „Sign out“, wieder als `<form method="post" action="/logout" hx-boost="false">` wie heute. Die aktive Seite ist in der Liste wie in der Leiste markiert.
- Schließen per Tipp auf die abgedunkelte Fläche, per ✕-Knopf oben rechts oder mit Escape. Beim Öffnen bekommt der erste Link den Fokus, beim Schließen kehrt der Fokus zu „More“ zurück. Solange das Blatt offen ist, scrollt die Seite dahinter nicht.
- Zustand in Alpine (`x-data` am Blatt oder ein kleiner Store `$store.sheet`). Nach einem Seitenwechsel per `hx-boost` ist das Blatt geschlossen, weil `base.html` neu gerendert wird.
- Bewegung: Das Blatt gleitet in höchstens 200 ms hoch. Bei `prefers-reduced-motion: reduce` erscheint es ohne Bewegung.

### „?“-Hilfen

- `.tooltip-icon` bekommt `tabindex="0"` und `role="button"`, damit es antippbar ist und den Fokus bekommt.
- Die Hilfe erscheint bei `:hover` (wie heute) und zusätzlich bei `:focus`.
- Am Handy erscheint der Text als feste Box über die volle Breite: links und rechts 16 px Abstand, direkt über der Leiste unten (`position: fixed`). So läuft er nie aus dem Bildschirm. Ein Tipp daneben schließt sie, weil das Symbol dann den Fokus verliert.
- Die 21 Hilfen im Formular und die 2 auf der Imports-Seite nutzen dasselbe Muster. Dafür reicht CSS in `app.css` plus `tabindex` und `role` an jedem `.tooltip-icon`.

## 2 Dashboard-Karten (`instances/card.html`)

- **Kopf in zwei Zeilen:**
  - Zeile 1: Typ, Name (#Id), „dry run“ bzw. „checked“ und der Zustand (WAIT, RUNNING, QUIET, OFF, ERROR) rechts. Der Name kürzt mit „…“ und bleibt mindestens teilweise sichtbar.
  - Zeile 2: die vier Knöpfe Test, ON/OFF, FORCE und Edit, als Raster aus vier gleich breiten Spalten, je 44 px hoch.
- Am PC bleibt der Kopf einzeilig wie heute. Umgesetzt mit `flex-wrap` bzw. Grid nur in der Handy-Schicht.
- Die Zeile mit Verbindungszustand und URL bleibt. Die URL bricht um.
- Der Countdown wird am Handy kleiner (2 rem statt 2,5 rem). Rate window, Kennzahlen (zwei Spalten wie heute) und „Recent Actions“ bleiben.
- Die Knöpfe „Missing“, „Upgrades“ und „Force Upgrades“ werden 44 px hoch.
- `.instance-grid` hat am Handy eine Spalte. `.main` hat am Handy 16 px Rand statt 24 px. Heute erzwingt `minmax(340px, 1fr)` bei 390 px fast die volle Breite und läuft mit dem Rand über.
- Kopf der Seite: „Dashboard“, die Zahl der Instanzen und der Hinweis auf offene Imports bleiben. „Add Instance“ bleibt rechts daneben, 44 px hoch.
- `updateCardState` (Live-Aktualisierung alle 5 s) findet seine Elemente weiter über die `data-`-Attribute. Die Reihenfolge im DOM ändert sich nicht.

## 3 Listen (`.table-stack`)

### Muster

Eine Tabelle mit der Klasse `table-stack` wird am Handy zur Liste. Jede Zelle bekommt eine Rolle als Klasse:

| Klasse | Platz am Handy |
|---|---|
| `c-main` | Zeile 1, volle Breite, normale Schrift, bricht um (Meldung, Titel, Releasename) |
| `c-pill` | Zeile 1 rechts oben (Stufe, Ergebnis, Status) |
| `c-meta` | Zeile 2, klein und gedämpft. Mehrere `c-meta`-Zellen stehen in einer Zeile, mit Abstand dazwischen (ein Trennzeichen bräuchte eine Markierung der ersten Zelle) |
| `c-extra` | eigene kleine Zeile unter der Meta-Zeile, mit Beschriftung aus `data-label` (etwa „*arr: …“) |
| `c-hide` | am Handy ausgeblendet |

Ohne Rolle gilt eine Zelle als `c-meta`. Umsetzung: `thead` ausgeblendet; `tr` als Flexbox mit Umbruch und `order` (erst `main` und `pill`, dann ein Zeilenumbruch per `tr::after`, dann die Meta-Zellen, dann jede `extra`-Zelle auf eigener Zeile); Zeilen durch die heutige feine Linie getrennt (keine Streifen). Die Hintergründe für Warnung und Fehler auf „Logs“ (`log-row-warn`, `log-row-error`) bleiben. Leere-Zeilen-Meldungen (`colspan`) stehen über die volle Breite.

### Zuordnung

| Seite und Tabelle | `c-main` | `c-pill` | `c-meta` | `c-extra` | `c-hide` |
|---|---|---|---|---|---|
| Logs | Message (umbrechend statt abgeschnitten) | Level | Time, Instance, Skill | – | – |
| Pre-filter, Zähler oben | Outcome | Titles (als Zahl, ohne Pillenform) | Instance, Mode | – | – |
| Pre-filter, Titel | Title (mit „profile changed“/„settings changed“) | Outcome | Time, Instance, Mode | *arr would grab, Filter grabs, Rejected | – |
| Pre-filter, Kandidaten (aufgeklappt) | Release (mit „*arr“/„filter“) | Verdict | Indexer, Score, Size, Quality | Reasons, Notes | – |
| History | Title | Status | Time, Type, Instance, *arr, Search | Verified | – |
| Imports, Vorschlag | File | – | Target, Quality, Languages, Group | Objections of the app (gelb) | – |
| Progressed, Einträge | Title | – | Searched At, Type | – | – |
| Instances | Name | Type | URL | – | – |

Auf der Instances-Liste bleiben „Edit“ und „Delete“ als Knöpfe von je 44 px in einer eigenen Zeile. Die Help-Tabelle (zwei Spalten) bleibt eine Tabelle und bekommt nur Umbruch. Läuft sie dennoch über, scrollt sie in ihrem Kasten (`overflow-x: auto`), nie die Seite.

### Aufklappen auf „Pre-filter“

Antippen eines Titels klappt wie heute die Kandidaten auf (`toggle(row.id)`). Am Handy wird die Detailzeile (`colspan="8"`) zu einem eingerückten Block unter dem Eintrag. Die Kandidaten darin folgen dem Muster oben. Ein kleines Dreieck rechts zeigt, ob der Eintrag auf- oder zugeklappt ist.

## 4 Filter-Blatt (`.filter-bar`)

- Die Filterzeilen von Logs, Pre-filter und History bekommen die Klasse `filter-bar`. Am PC bleiben sie, wie sie sind.
- Am Handy ist die Filterzeile ausgeblendet. An ihrer Stelle steht ein Knopf **„Filter“**. Sind Filter aktiv, zeigt er ihre Zahl („Filter · 2“). Aktiv heißt: ein Wert weicht vom Standard der Seite ab (etwa Instanz nicht „All“, Haken anders als beim Laden).
- Antippen öffnet dieselbe Filterzeile als Blatt von unten, mit Titel „Filter“ und ✕. Die Elemente stehen darin untereinander über die volle Breite. Unten steht ein Hauptknopf, der das Blatt schließt: auf Pre-filter und History „Show N titles“ bzw. „Show N items“ mit der aktuellen Trefferzahl, auf Logs „Done“. Filter wirken wie heute sofort beim Ändern.
- Schließen, Fokus, Escape und Scroll-Sperre wie beim Blatt „More“.
- **Was draußen bleibt:**
  - Logs: „Live“ steht neben „Filter“. „Debug“, „Clear view“ und „Clear DB“ (rot) wandern ins Blatt, unter die Auswahlfelder.
  - Pre-filter und History: das Suchfeld steht über volle Breite über dem Knopf, die Trefferzahl daneben.
  - Pre-filter: „Download CSV“ bleibt im Kopf der Seite.
  - Progressed: Die beiden Auswahlfelder über der Eintragsliste werden nur 44 px hoch, kein Blatt.

## 5 Blättern

Logs und Progressed zeigen heute „← Prev“, bis zu fünf Seitenzahlen und „Next →“. Am Handy sind die Seitenzahlen ausgeblendet, dafür steht dazwischen „Page x / y“. Pre-filter und History zeigen schon heute nur «, „Page x of y“ und ». Bei allen vier sind die Knöpfe am Handy 44 px groß. Die Logik zum Blättern bleibt.

## 6 Imports-Karten (`imports.html`)

- Releasename oben über die volle Breite (bricht schon heute um), darunter Größe · Alter · Download-Client und die Meldungen der App.
- **„Import“ und „Discard“** stehen darunter als zwei gleich breite Knöpfe von je 44 px Höhe über die volle Breite der Karte. Am PC bleiben sie rechts. Gesperrt und Titel („Import is locked …“) wie heute. Die Rückfrage vor beiden Aktionen bleibt.
- Der Haken „Blocklist and search again“ ist samt Beschriftung eine Tippfläche von mindestens 44 px Höhe. Das „?“ daneben folgt dem Muster aus Abschnitt 1.
- Der Vorschlag der App folgt dem Listenmuster (Abschnitt 3). Hinweise in Gelb („Import is locked“, Abdeckung) und das Urteil von missingarr stehen darunter über die volle Breite. Heute haben sie `min-width:200px` und stehen neben dem Urteil.
- Kopf der Instanz: Name, Typ und „Queue in Radarr/Sonarr“ bleiben in einer Zeile, der Link-Knopf wird 44 px hoch. „Reload“ oben ebenso.

## 7 Übrige Seiten

- **Instances:** Liste nach Abschnitt 3, „Add Instance“ 44 px hoch.
- **Bearbeiten-Formular:** schon einspaltig. Neu sind Eingaben mit 16 px Schrift und 44 px Höhe, antippbare „?“-Hilfen sowie „Save Instance“ und „Cancel“ als zwei gleich breite Knöpfe über die volle Breite. Die Schalter oben („Enabled“, „Missing“, „Upgrades“) und „Test Connection“ werden 44 px hoch. Der Link „← Instances“ oben bekommt eine Tippfläche von 44 px Höhe.
- **Progressed:** Karten bleiben. „Reset“, „Show items“ und „Reset All“ werden 44 px hoch, die Eintragsliste folgt Abschnitt 3.
- **Help:** Fließtext bleibt. Die Link-Knöpfe am Ende umbrechen in mehrere Zeilen, Code und Tabelle scrollen höchstens in ihrem Kasten.
- **Login:** läuft nicht über (gemessen). Am Handy werden die beiden Felder 44 px hoch mit 16 px Schrift, „Sign in“ 44 px hoch, und „Remember me for 30 days“ wird samt Haken eine Tippfläche von 44 px Höhe. Die Seite hat keine Leiste unten, sie erweitert `base.html` nicht.

## 8 Prüfung

### Tests (pytest, ohne Browser)

Neue Testdatei `tests/test_m1_mobile.py`, mit dem Muster aus `test_g5_pages.py` (`client`, `tags`):

1. Jede angemeldete Seite (`/`, `/instances`, `/instances/new`, `/history`, `/searched`, `/checked-search`, `/imports`, `/logs`, `/help`) enthält genau eine `nav.tabbar` mit den vier Links `/`, `/imports`, `/checked-search`, `/logs` und einem Knopf „More“.
2. Das aktive Ziel trägt `aria-current="page"`: `/logs` → Logs. `/history` → More. `/instances/1/edit` → More.
3. Das Blatt „More“ enthält die Links `/instances`, `/history`, `/searched`, `/help`, den GitHub-Link mit `target="_blank"`, die Version und ein Formular `POST /logout` mit `hx-boost="false"`.
4. Es gibt zwei `[data-imports-count]` auf jeder Seite, aber nur einen Aufruf von `startImportsCount()`.
5. Das Viewport-Meta enthält `viewport-fit=cover`.
6. Logs, Pre-filter, History, Imports, Progressed und Instances haben `table-stack`-Tabellen. Jede Zelle der Zeilenvorlagen hat genau eine Rollenklasse aus `c-main`, `c-pill`, `c-meta`, `c-extra`, `c-hide`, oder keine. Es gibt höchstens ein `c-main` und ein `c-pill` je Zeile.
7. Logs, Pre-filter und History haben eine `.filter-bar` und einen Knopf „Filter“. Die Zählfunktion für aktive Filter liefert 0 beim Laden und 1 nach einer geänderten Auswahl. Geprüft per Node mit `run_node`, wie die bestehenden Komponententests.
8. Jedes `.tooltip-icon` hat `tabindex="0"` und `role="button"`.
9. Der Login-Seite fehlt die Leiste.

Alle bestehenden Tests bleiben grün.

### Browser-Prüfung (`scripts/mobile_check.py`)

Ein Skript für Entwickler, nicht Teil von `pytest` und nicht im Docker-Image:

- Es braucht `playwright` mit Chromium. Installation steht im Kopf des Skripts. Es kommt nicht in `requirements-dev.txt`, damit `pytest` ohne Browser läuft.
- Es startet missingarr auf `127.0.0.1` mit einer leeren temporären Datenbank, festem Passwort und Testdaten. Dazu gehören zwei Instanzen (aus, damit niemand angefragt wird), Log-Zeilen mit allen Stufen und einer sehr langen Meldung, History-Einträge, Pre-filter-Zeilen mit Kandidaten und langen Releasenamen sowie Einträge für Progressed. Für Imports beantwortet es die Abrufe des Browsers selbst (Playwright `page.route`): `GET /api/imports` mit einer Radarr-Instanz und einem Download mit langem Releasenamen und zwei Meldungen, `GET /api/imports/<id>/proposal` mit einem Vorschlag aus zwei Dateien (eine davon kein Video) samt Einwänden und Urteil, `GET /api/imports/count` mit 1. Die Formen der Antworten übernimmt es aus den Tests `test_h4_imports_api.py`. Import und Verwerfen werden nicht ausgelöst.
- Es öffnet jede Seite bei 390 × 844 px (`is_mobile`, `has_touch`) und bei 1280 × 800 px.
- **Fehler, wenn bei 390 px:**
  - `scrollWidth > innerWidth` ist (seitlicher Überlauf),
  - ein sichtbares Bedienelement (`a.btn`, `button`, `input`, `select`, `label` mit Haken, Ziele der Leiste) kleiner als 44 × 44 px ist, ausgenommen Links im Fließtext,
  - die Leiste fehlt oder Inhalt am Seitenende verdeckt.
- Es öffnet das Blatt „More“ und auf einer Seite das Filter-Blatt und prüft, dass Escape beide schließt. Auf Pre-filter klappt es einen Eintrag auf.
- Bei 1280 px prüft es, dass Leiste, Filter-Knopf und Seitenanzeige unsichtbar sind und die obere Navigation sichtbar ist.
- Es legt von jeder Seite ein Foto in beiden Breiten ab, im Ordner aus dem ersten Argument.

### Abnahme

Nach dem Bau fotografiert der Entwickler alle Seiten der laufenden Testinstanz bei 390 px und legt die Fotos dem Betreiber zur Durchsicht vor. Erst danach wird das Release gebaut und nach Freigabe eingespielt.

## 9 Release

- Version 0.11.0: `VERSION`, `CHANGELOG.md` (Added: Handy-Ansicht. Changed: „?“-Hilfen auch per Tippen und Tastatur), README (Abschnitt zur Oberfläche: am Handy Leiste unten).
- GitHub-Release mit den Notes aus dem Changelog. Vor dem Push laufen die Push-Prüfung und der `pre-push`-Hook.
- Eingespielt wird erst nach Freigabe des Betreibers.
- Rückweg: Image 0.10.1. Datenbank und API ändern sich nicht.

## Restrisiken

- **Andere Browser:** Ältere Android-Browser ohne `env(safe-area-inset-bottom)` nehmen 0 als Wert. Das ist unschädlich, die Leiste hat dann nur 8 px Abstand. Das Muster nutzt bewusst kein `:has()` und keine Container-Abfragen, damit es auch in älteren Browsern greift.
- **Blatt und Live-Aktualisierung:** Die Live-Logs fügen Zeilen hinzu, während das Filter-Blatt offen sein kann. Das ist gewollt und harmlos, die Liste liegt hinter der abgedunkelten Fläche.
- **Prüfung im echten Gerät:** Das Skript prüft Chromium mit Handy-Viewport. Safari auf dem iPhone ist nicht automatisch geprüft, der Betreiber sieht die Seite nach dem Einspielen einmal am eigenen Handy an.
