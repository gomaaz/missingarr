# missingarr 0.12.0: Namensvetter, Folgentitel und abweichende Zählung Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Die geprüfte Suche für Sonarr bekommt drei neue Regeln (S5 Namensvetter, S6 Folgentitel, S7 Zählung zweifelhaft), die die 13 falschen Grabs der ersten Active-Nacht verhindert hätten, dazu `IL` als Ländercode; Version 0.12.0.

**Architecture:** Die Regeln sind reine Funktionen: S5 in `sonarr_rules.py` (Index der Grundtitel aller Serien), S6 als neues Modul `episode_titles.py` (Titelteil des Namens gegen die Folgentitel der Serie), S7 in `sonarr_rules.evaluate` über einen neuen, optionalen `RuleContext`. Der Runner (`_TitleCheck`) füllt den Kontext: Serienliste nur bei Bedarf und einen Tag je Instanz im Speicher, Folgenliste einmal je Serie und Lauf, S7-Vorabprüfung der ganzen Ergebnisliste vor der Wahl. Ohne Kontext (Imports-Seite) entscheidet `evaluate` wie bisher.

**Tech Stack:** Python 3.12, `pytest`, `difflib` und `math` aus der Standardbibliothek; keine neue Abhängigkeit.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-10-07-filter-gaps-design.md` (mit dem Betreiber abgestimmt am 07.10.2026: „Passt, alle 3 Regeln an“).
- Ausgangsstand: Branch `feat/0.12.0-filter` auf `main` (0.11.1) mit dem Commit von Spec und Plan.
- Gründe genau: `"other series by name"`, `"other episode by title"`, `"episode numbering in doubt"`, Hinweis `"episode title fits no episode"`.
- Einstellungen genau: `veto_namesake`, `check_episode_title`, `untitled_after_other_episode`, alle `True`; Labels „Veto when the title also fits another series“, „Reject when the episode title names another episode“, „Reject untitled releases when another release shows a different numbering“; Tooltips `cs_<feld>`.
- `IL` kommt ans Ende von `DEFAULT_COUNTRY_CODES`.
- Ohne `context` verhält sich `sonarr_rules.evaluate` genau wie in 0.11.1. `import_check.py` bleibt unverändert. Radarr bleibt unverändert.
- Serienliste `GET /api/v3/series` mit Timeout 120 s, Zwischenspeicher 24 h je (Datenbank, Instanz, `updated_at`, Ländercodes); Folgenliste `GET /api/v3/episode?seriesId=<id>` einmal je Serie und Lauf. Lesefehler → Release `parse error`.
- Tests nur in den neuen Dateien `tests/test_i1_namesake.py` bis `tests/test_i4_runner_context.py`. Bestehende Testdateien ändern sich nur, wo ein Task es mit Code vorgibt: `tests/test_g1_sonarr_rules.py` (Task 1), `tests/test_p6_docs.py` und `tests/test_z_release.py` (Task 3). Neue Dateien holen Helfer aus bestehenden Testdateien, nie Testfunktionen. Keine `conftest.py`.
- Testbefehle laufen im Repo-Wurzelverzeichnis: `.venv/bin/python -m pytest <dateien> -q -p no:cacheprovider`. Alle Tests laufen offline. Die Live-Datenbank und die Sicherungen werden nie geöffnet.
- Dateien schreiben mit dem Write- bzw. Edit-Werkzeug, nie per Heredoc.
- Codeblöcke: „**Neue Datei** `pfad`“ heißt ganze Datei mit genau diesem Inhalt. „**Ersetze in** `pfad`“ mit einem Block, danach „**durch:**“ mit einem Block heißt: der erste Block kommt in der Datei genau einmal vor und wird durch den zweiten ersetzt; die Blöcke einer Datei gelten in der angegebenen Reihenfolge. „**Hänge an** `pfad`“ heißt: der Block kommt ans Dateiende (nach zwei Leerzeilen).
- Öffentliches Repo: keine Hostnamen, keine IPs außer `127.0.0.1`, keine absoluten Host-Pfade, keine Personennamen, keine echten Indexer- oder Gruppennamen, keine echten Serien aus der Bibliothek (Testdaten: „Some Show“, „Paw Friends“).
- Code, Bezeichner, Testnamen, Kommentare, Oberfläche und Commit-Nachrichten englisch; ein Commit je Task mit den zwei Trailer-Zeilen aus dem Commit-Schritt; `git add` nur mit den Dateien des Tasks, nie `-A`.
- Nichts taggen, nichts pushen, nichts einspielen (das entscheidet der Betreiber).

## Dateien

| Datei | Task | Aufgabe |
|---|---|---|
| `backend/checked_search/verdict.py` | 1 | drei Gründe, ein Hinweis |
| `backend/checked_search/settings.py` | 1 | drei Schalter, Labels, `IL` |
| `backend/tooltips.py` | 1 | drei Tooltips |
| `backend/checked_search/episode_titles.py` (neu) | 1 | S6: Titelteil, Wörter, Passung, Entscheidung |
| `backend/checked_search/sonarr_rules.py` | 1 | `EpisodeInfo`/`EpisodeParse` erweitert, `RuleContext`, S5, S6/S7 in `evaluate` |
| `backend/checked_search/runner.py` | 2 | Serienliste, Folgenliste, S7-Vorabprüfung, Kontext je Release |
| `README.md`, `CHANGELOG.md`, `VERSION` | 3 | Version 0.12.0 |

---

### Task 1: Regeln S5–S7 als reine Funktionen

**Files:**
- Create: `backend/checked_search/episode_titles.py`
- Modify: `backend/checked_search/verdict.py`, `backend/checked_search/settings.py`, `backend/tooltips.py`, `backend/checked_search/sonarr_rules.py`
- Test: `tests/test_i1_namesake.py`, `tests/test_i2_episode_titles.py` (neu), `tests/test_g1_sonarr_rules.py` (eine Zeile)

**Interfaces:**
- Produces (für Task 2):
  - `episode_titles.needs_episode_list(release: str, series_title: str) -> bool`, `episode_titles.is_untitled(release, series_title) -> bool`, `episode_titles.episode_list(resources) -> tuple[tuple[int, int, str], ...]`, `episode_titles.judge(release, target: tuple[int, int], target_title: str, series_title: str, episodes) -> str`, Konstante `episode_titles.OTHER_EPISODE`.
  - `sonarr_rules.EpisodeInfo(..., season_number: int | None = None, episode_number: int | None = None, episode_title: str = "")`, gefüllt von `episode_from_resources`.
  - `sonarr_rules.EpisodeParse(series_id, series_title, year: int = 0)`, gefüllt von `parse_from_resource`.
  - `sonarr_rules.RuleContext(namesake_index: dict | None = None, episodes: tuple | None = None, release_languages: frozenset = frozenset(), doubt_languages: tuple = ())`.
  - `sonarr_rules.namesake_index(series_list, codes) -> dict[str, frozenset[tuple[int, int]]]`, `sonarr_rules.needs_namesake_index(parse, settings) -> bool`, `sonarr_rules.evaluate(info, release_title, publish_date, parse, settings, context=None) -> Verdict`.
  - `CheckedSearchSettings.veto_namesake`, `.check_episode_title`, `.untitled_after_other_episode`.

- [ ] **Step 1: Tests schreiben**

**Neue Datei** `tests/test_i1_namesake.py`:

```python
"""0.12.0, Sonarr rule S5: a name without year that /parse maps to no series
and that is also the title of another series of the library (pure functions,
invented series)."""
from datetime import datetime, timezone

from backend.checked_search import sonarr_rules as sr
from backend.checked_search.settings import DEFAULT_COUNTRY_CODES, CheckedSearchSettings
from backend.checked_search.verdict import REASON_NAMESAKE

CODES = DEFAULT_COUNTRY_CODES
LIBRARY = [
    {"id": 1, "title": "Some Show (2020)", "year": 2020, "alternateTitles": []},
    {"id": 2, "title": "Some Show (AU)", "year": 2004, "alternateTitles": []},
    {"id": 3, "title": "Some Show (2026)", "year": 2026, "alternateTitles": []},
    {"id": 4, "title": "Grün & Blau", "year": 2015, "alternateTitles": [{"title": "Green and Blue (US)"}]},
    {"id": 5, "title": "My Royal Some Show", "year": 2026, "alternateTitles": []},
    {"title": "no id"}, "garbage",
]
INDEX = sr.namesake_index(LIBRARY, CODES)
TARGET = sr.EpisodeInfo(3, ("Some Show (2026)",), 2026, datetime(2026, 9, 1, tzinfo=timezone.utc))
NAME = "Some.Show.S01E05.1080p.WEB-DL.x264-GRP"
NO_SERIES = sr.EpisodeParse(None, "Some Show")


def namesakes(parse=NO_SERIES, name=NAME, info=TARGET, settings=None):
    return sr.namesakes(info, name, parse, INDEX, settings or CheckedSearchSettings())


def test_base_keys_are_the_title_with_and_without_its_suffix():
    assert sr.base_keys("Some Show (2020)", CODES) == {"someshow2020", "someshow"}
    assert sr.base_keys("Some Show (AU)", CODES) == {"someshowau", "someshow"}
    assert sr.base_keys("Some Show", CODES) == {"someshow"}
    # umlaut and '&' spellings survive the cut
    assert {"gruenundblau", "grunundblau"} <= sr.base_keys("Grün & Blau (2015)", CODES)


def test_the_index_holds_titles_and_alternate_titles():
    assert INDEX["someshow"] == {(1, 2020), (2, 2004), (3, 2026)}
    assert INDEX["greenandblue"] == {(4, 2015)}
    assert INDEX["gruenundblau"] == {(4, 2015)}


def test_a_name_that_is_the_title_of_other_series_is_a_namesake():
    assert namesakes() == {1, 2}


def test_the_series_searched_for_does_not_count():
    lone = sr.namesake_index([LIBRARY[2]], CODES)
    assert sr.namesakes(TARGET, NAME, NO_SERIES, lone, CheckedSearchSettings()) == frozenset()


def test_each_of_the_first_three_conditions_is_needed():
    assert namesakes(sr.EpisodeParse(1, "Some Show")) == frozenset()            # 1: /parse names a series
    assert namesakes(sr.EpisodeParse(None, "Some Show", 2020)) == frozenset()   # 2: a year was parsed
    assert namesakes(sr.EpisodeParse(None, "Some Show AU")) == frozenset()      # 3: a suffix
    assert namesakes(sr.EpisodeParse(None, "")) == frozenset()


def test_umlaut_spellings_find_the_namesake():
    info = sr.EpisodeInfo(9, ("Gruen und Blau (2024)",), 2024)
    assert namesakes(sr.EpisodeParse(None, "Gruen und Blau"), "Gruen.und.Blau.S01E01-GRP", info) == {4}


def test_no_prefix_or_word_match():
    # 'My Royal Some Show' is no namesake of 'Some Show', nor the other way round
    assert namesakes(sr.EpisodeParse(None, "Royal Some Show")) == frozenset()
    info = sr.EpisodeInfo(5, ("My Royal Some Show",), 2026)
    assert namesakes(sr.EpisodeParse(None, "My Royal Some Show"), "My.Royal.Some.Show.S01E01-GRP", info) == frozenset()


def test_the_year_of_the_series_in_the_name_lifts_the_veto():
    assert namesakes(name="Some.Show.S01E05.2026.1080p.WEB-DL-GRP") == frozenset()
    assert namesakes(name="Some.Show.S01E05.(2026).1080p.WEB-DL-GRP") == frozenset()
    # only as a word of its own
    assert namesakes(name="Some.Show.S01E05.X2026.1080p.WEB-DL-GRP") == {1, 2}
    # not when a namesake has that year as well
    twin = sr.namesake_index(LIBRARY + [{"id": 8, "title": "Some Show (US)", "year": 2026}], CODES)
    assert sr.namesakes(TARGET, "Some.Show.S01E05.2026-GRP", NO_SERIES, twin, CheckedSearchSettings()) == {1, 2, 8}


def test_needs_the_index_only_for_names_that_could_fire():
    settings = CheckedSearchSettings()
    assert sr.needs_namesake_index(NO_SERIES, settings)
    assert not sr.needs_namesake_index(sr.EpisodeParse(1, "Some Show"), settings)
    assert not sr.needs_namesake_index(sr.EpisodeParse(None, "Some Show", 2020), settings)
    assert not sr.needs_namesake_index(sr.EpisodeParse(None, "Some Show 2020"), settings)
    assert not sr.needs_namesake_index(NO_SERIES, CheckedSearchSettings(veto_namesake=False))


def test_evaluate_rejects_with_the_index_and_only_then():
    settings = CheckedSearchSettings()
    context = sr.RuleContext(namesake_index=INDEX)
    assert sr.evaluate(TARGET, NAME, None, NO_SERIES, settings, context).reasons == (REASON_NAMESAKE,)
    # without a context (Imports page) S5 checks nothing
    assert sr.evaluate(TARGET, NAME, None, NO_SERIES, settings).ok
    assert sr.evaluate(TARGET, NAME, None, NO_SERIES, CheckedSearchSettings(veto_namesake=False), context).ok


def test_the_parsed_year_is_read():
    parse = {"parsedEpisodeInfo": {"seriesTitle": "Some Show", "seriesTitleInfo": {"year": 2020}}, "series": None}
    assert sr.parse_from_resource(parse) == sr.EpisodeParse(None, "Some Show", 2020)
    assert sr.parse_from_resource({"parsedEpisodeInfo": {"seriesTitle": "Some Show"}}).year == 0
```

**Neue Datei** `tests/test_i2_episode_titles.py`:

```python
"""0.12.0, Sonarr rule S6: the episode title in the release name against the
episode titles of the series (pure functions, invented series)."""
import pytest

from backend.checked_search import episode_titles as et

SERIES = "Paw Friends"
EPISODES = (
    (1, 1, "The Big Race"),
    (1, 2, "Lost in the Woods"),
    (1, 3, "Birthday Surprise"),
    (1, 4, "Rainy Day Blues"),
    (1, 5, "The Missing Bone"),
    (1, 6, "Treasure Hunt"),
    (1, 7, "Episode 7"),
    (1, 8, "Fun"),
    (1, 9, "Strange Dog Condition"),
    (1, 10, "Moonlight Picnic Party"),
    (0, 1, "Holiday Special"),
)
TARGET = (1, 3)
TITLE = "Birthday Surprise"


def judge(release, target=TARGET, title=TITLE):
    return et.judge(release, target, title, SERIES, EPISODES)


@pytest.mark.parametrize("release, part", [
    ("Paw.Friends.S01E03.Birthday.Surprise.1080p.WEB-DL.x264-GRP", "Birthday Surprise"),
    ("Paw.Friends.S01E03.Birthday.Surprise-GRP", "Birthday Surprise"),
    ("Paw.Friends.S01E03E04.Birthday.Surprise.720p.HDTV-GRP", "Birthday Surprise"),
    ("Paw.Friends.S01E03-04.Birthday.Surprise.GERMAN.720p-GRP", "Birthday Surprise"),
    ("Paw.Friends.S01E03.1080p.WEB-DL-GRP", ""),
    ("Paw.Friends.2019.Birthday.Surprise.1080p-GRP", None),
])
def test_title_part_is_what_stands_between_sxxeyy_and_the_quality(release, part):
    assert et.release_title_part(release) == part


def test_a_title_that_fits_the_episode_passes():
    assert judge("Paw.Friends.S01E03.Birthday.Surprise.1080p.WEB-DL-GRP") == et.FITS
    # a title split or joined differently counts as the same title
    assert judge("Paw.Friends.S01E03.Birth.Day.Surprise.1080p.WEB-DL-GRP") == et.FITS


def test_a_title_of_another_episode_is_rejected():
    assert judge("Paw.Friends.S01E03.Lost.in.the.Woods.1080p.WEB-DL-GRP") == et.OTHER_EPISODE


def test_two_titles_of_other_episodes_in_one_name_are_rejected():
    assert judge("Paw.Friends.S01E03.Treasure.Hunt.Rainy.Day.Blues.1080p.WEB-DL-GRP") == et.OTHER_EPISODE


def test_two_titles_joined_by_a_hyphen_are_checked_one_by_one():
    release = "Paw.Friends.S01E03.Moonlite.Picnic.Party.Extra.Words.Here-Strange.Dog.Condition.WEB-DL-GRP"
    part = et.release_title_part(release)
    # as a whole the release is only a weak match (too many words unexplained) ...
    assert et._judge_part(part, TARGET, TITLE, SERIES, EPISODES) == et.OTHER_WEAK
    # ... its second title alone names episode 9
    assert judge(release) == et.OTHER_EPISODE


def test_a_weak_match_is_neither_rejected_nor_noted():
    # "Fun" is the whole title of episode 8, but a short word is no proof
    assert judge("Paw.Friends.S01E03.Fun.Times.1080p.WEB-DL-GRP") == et.OTHER_WEAK


def test_a_title_that_fits_no_episode_is_only_a_note():
    assert judge("Paw.Friends.S01E03.Geburtstags.Ueberraschung.GERMAN.1080p-GRP") == et.NO_MATCH


@pytest.mark.parametrize("release, category", [
    ("Paw.Friends.S01E03.1080p.WEB-DL-GRP", et.NO_TITLE),
    ("Paw.Friends.S01E03.Episode.3.1080p.WEB-DL-GRP", et.GENERIC),
    ("Paw.Friends.S01E03.Folge.3.GERMAN.1080p-GRP", et.GENERIC),
    ("Paw.Friends.S01E03.Surprise.1080p.WEB-DL-GRP", et.TOO_SHORT),
    # words of the series title (four letters and more) do not count
    ("Paw.Friends.S01E03.Friends.Surprise.1080p.WEB-DL-GRP", et.TOO_SHORT),
    ("Paw.Friends.2019.1080p.WEB-DL-GRP", et.NO_SXXEYY),
])
def test_names_without_a_comparable_title_are_not_judged(release, category):
    assert judge(release) == category


def test_an_episode_with_a_placeholder_title_is_not_judged():
    assert judge("Paw.Friends.S01E07.Lost.in.the.Woods.1080p.WEB-DL-GRP", (1, 7), "Episode 7") == et.TARGET_GENERIC
    assert judge("Paw.Friends.S01E07.Lost.in.the.Woods.1080p.WEB-DL-GRP", (1, 7), "TBA") == et.TARGET_GENERIC


def test_specials_count_as_episodes_of_the_series():
    assert judge("Paw.Friends.S01E03.Holiday.Special.1080p.WEB-DL-GRP") == et.OTHER_EPISODE


def test_untitled_and_needs_list():
    assert et.is_untitled("Paw.Friends.S01E03.1080p.WEB-DL-GRP", SERIES)
    assert et.is_untitled("Paw.Friends.S01E03.Episode.3.1080p.WEB-DL-GRP", SERIES)
    assert et.is_untitled("Paw.Friends.S01E03.Friends.1080p.WEB-DL-GRP", SERIES)
    assert not et.is_untitled("Paw.Friends.S01E03.Surprise.1080p.WEB-DL-GRP", SERIES)
    assert not et.is_untitled("Paw.Friends.2019.1080p.WEB-DL-GRP", SERIES)
    assert et.needs_episode_list("Paw.Friends.S01E03.Lost.in.the.Woods.1080p-GRP", SERIES)
    assert not et.needs_episode_list("Paw.Friends.S01E03.Surprise.1080p-GRP", SERIES)
    assert not et.needs_episode_list("Paw.Friends.S01E03.1080p-GRP", SERIES)


def test_episode_list_takes_number_and_title():
    resources = [{"seasonNumber": 1, "episodeNumber": 2, "title": "Lost in the Woods"},
                 {"seasonNumber": 0, "episodeNumber": 1, "title": None},
                 {"seasonNumber": True, "episodeNumber": 3}, "garbage", {"episodeNumber": 4}]
    assert et.episode_list(resources) == ((1, 2, "Lost in the Woods"), (0, 1, ""))
```

Der bestehende Test liest jetzt auch das Jahr aus `/parse`:

**Ersetze in** `tests/test_g1_sonarr_rules.py`:

```python
                                                      "seriesTitleInfo": {"year": 2025}}, "series": {"id": 10}})
    assert p == sr.EpisodeParse(10, "The Guest CO 2025")
```

**durch:**

```python
                                                      "seriesTitleInfo": {"year": 2025}}, "series": {"id": 10}})
    assert p == sr.EpisodeParse(10, "The Guest CO 2025", 2025)
```

- [ ] **Step 2: Tests laufen lassen, sie schlagen fehl**

Run: `.venv/bin/python -m pytest tests/test_i1_namesake.py tests/test_i2_episode_titles.py tests/test_g1_sonarr_rules.py -q -p no:cacheprovider`
Expected: FAIL beim Sammeln (`ImportError: cannot import name 'REASON_NAMESAKE'` bzw. `cannot import name 'episode_titles'`).

- [ ] **Step 3: Gründe, Einstellungen und Tooltips**

**Ersetze in** `backend/checked_search/verdict.py`:

```python
REASON_TOO_EARLY = "published too early"
REASON_PARSE_ERROR = "parse error"
```

**durch:**

```python
REASON_TOO_EARLY = "published too early"
# Sonarr S5-S7 (0.12.0)
REASON_NAMESAKE = "other series by name"
REASON_OTHER_EPISODE = "other episode by title"
REASON_NUMBERING = "episode numbering in doubt"
NOTE_NO_EPISODE_FITS = "episode title fits no episode"
REASON_PARSE_ERROR = "parse error"
```

**Ersetze in** `backend/checked_search/settings.py`:

```python
    "AU", "US", "UK", "GB", "DE", "CO", "CA", "NZ", "FR", "ES", "IT",
    "NL", "SE", "DK", "NO", "JP", "KR", "MX", "BR", "AR", "IN",
)
```

**durch:**

```python
    "AU", "US", "UK", "GB", "DE", "CO", "CA", "NZ", "FR", "ES", "IT",
    "NL", "SE", "DK", "NO", "JP", "KR", "MX", "BR", "AR", "IN", "IL",
)
```

**Ersetze in** `backend/checked_search/settings.py`:

```python
SONARR_FIELDS: tuple[str, ...] = (
    "veto_other_series", "check_suffix", "country_codes", "suffix_year_tolerance",
    "reject_days_before_air", "note_days_before_air", "skip_existing_file",
)
```

**durch:**

```python
SONARR_FIELDS: tuple[str, ...] = (
    "veto_other_series", "veto_namesake", "check_suffix", "country_codes", "suffix_year_tolerance",
    "reject_days_before_air", "note_days_before_air", "check_episode_title", "untitled_after_other_episode",
    "skip_existing_file",
)
```

**Ersetze in** `backend/checked_search/settings.py`:

```python
    "veto_other_series": "Veto when /parse names another series",
    "check_suffix": "Check country/year suffix",
```

**durch:**

```python
    "veto_other_series": "Veto when /parse names another series",
    "veto_namesake": "Veto when the title also fits another series",
    "check_episode_title": "Reject when the episode title names another episode",
    "untitled_after_other_episode": "Reject untitled releases when another release shows a different numbering",
    "check_suffix": "Check country/year suffix",
```

**Ersetze in** `backend/checked_search/settings.py`:

```python
    skip_existing_file: bool = True
    # Sonarr (S1-S3)
    veto_other_series: bool = True
```

**durch:**

```python
    skip_existing_file: bool = True
    # Sonarr (S1-S3, S5-S7)
    veto_other_series: bool = True
```

**Ersetze in** `backend/checked_search/settings.py`:

```python
    veto_other_series: bool = True
    check_suffix: bool = True
```

**durch:**

```python
    veto_other_series: bool = True
    veto_namesake: bool = True
    check_suffix: bool = True
```

**Ersetze in** `backend/checked_search/settings.py`:

```python
    note_days_before_air: int = 14
```

**durch:**

```python
    note_days_before_air: int = 14
    check_episode_title: bool = True
    untitled_after_other_episode: bool = True
```

**Ersetze in** `backend/tooltips.py`:

```python
    "cs_veto_other_series": "Sonarr rule S1: reject a release that Sonarr's /parse assigns to another series of your library.",
    "cs_check_suffix": "Sonarr rule S2: a year or country code at the end of the parsed series title must fit the series — the year within the tolerance of the series year, the country code also at the end of the series title or an alternate title.",
```

**durch:**

```python
    "cs_veto_other_series": "Sonarr rule S1: reject a release that Sonarr's /parse assigns to another series of your library.",
    "cs_veto_namesake": "Sonarr rule S5: reject a release whose name has no year, that Sonarr's /parse assigns to no series (the search matched it only by ID) and whose title is also the title of another series of your library, with or without that series' year or country suffix (\"Some Show\" while \"Some Show (2026)\" is searched and \"Some Show (2019)\" is in the library). Allowed again when the year of the series searched for stands in the name. Reads the series list only when a release needs it and keeps it for a day.",
    "cs_check_suffix": "Sonarr rule S2: a year or country code at the end of the parsed series title must fit the series — the year within the tolerance of the series year, the country code also at the end of the series title or an alternate title.",
```

**Ersetze in** `backend/tooltips.py`:

```python
    "cs_note_days_before_air": "Sonarr rule S3: releases published at least this many days (but not more than the reject limit) before the episode aired are only noted in the log. 0 to 36500.",
    "search_again_after_profile_change": (
```

**durch:**

```python
    "cs_note_days_before_air": "Sonarr rule S3: releases published at least this many days (but not more than the reject limit) before the episode aired are only noted in the log. 0 to 36500.",
    "cs_check_episode_title": "Sonarr rule S6: when the release name carries an episode title after SxxEyy, compare it with the titles of the series' episodes. Reject the release when the title clearly names another episode and not the one searched for (a group numbering differently). A title that fits no episode (a translated title, for example) is only noted. Reads the series' episode list once per run when a release has a title.",
    "cs_untitled_after_other_episode": "Sonarr rule S7 (needs S6): when S6 rejects a release of the search results because its title names another episode, also reject the releases of the same results without a title, if all their languages are languages of that release. Their numbers may be counted the same different way. Releases in other languages (a German release next to an English one) stay free.",
    "search_again_after_profile_change": (
```

- [ ] **Step 4: Modul für S6**

**Neue Datei** `backend/checked_search/episode_titles.py`:

```python
"""Episode title in the release name against the episode titles of the
series (Sonarr rules S6 and S7). Pure functions.

A port of the prototype the rules were measured with on the dry runs of
02.-07.10.2026 and the first active night (898 picked releases: 12
rejections, all right, none wrong). The thresholds below are the measured
ones; changing them changes what is rejected.
"""

import difflib
import math
import re
import unicodedata
from functools import lru_cache

# Categories of judge().
FITS = "fits"                        # the title fits the episode searched for
OTHER_EPISODE = "other_episode"      # it names another episode of the series (S6 rejects)
OTHER_WEAK = "other_weak"            # another episode, but too weak a match to reject
NO_MATCH = "no_match"                # fits no episode (a note: German titles, extra texts)
NO_TITLE = "no_title"                # nothing between SxxEyy and the quality tokens
GENERIC = "generic"                  # only a placeholder ("Episode 20")
TOO_SHORT = "too_short"              # fewer than MIN_WORDS title words
TARGET_GENERIC = "target_generic"    # the episode searched for has a placeholder title
NO_SXXEYY = "no_sxxeyy"              # no SxxEyy in the name: nothing to check
UNTITLED = frozenset({NO_TITLE, GENERIC})

MIN_WORDS = 2          # title words a release needs before it is compared
FIT_OK = 0.5           # fit with the episode searched for that counts as "fits"
WHOLE = 0.99           # share of another episode's title the release must hold
SPECIFIC_MAX_TITLES = 2  # a "specific" word is in at most this many episode titles
EXPLAINED_MIN = 0.5    # share of the release's words other episodes must explain
SAME_RATIO = 0.85      # difflib ratio for two words of 5+ letters to count as one

_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"})

# SxxEyy with optional further episodes (E01E02, E01-02, E01-E02).
_SXXEYY = re.compile(
    r"(?i)(?:^|[ ._\-\[(])S(\d{1,4})[ ._-]?E(\d{1,4})((?:[ ._-]?-?E?\d{1,4}(?=[ ._\-\])]|$))*)(?=[ ._\-\])]|$)")
_RESOLUTION = re.compile(r"^\d{3,4}[pi]$")
_TOKEN_SPLIT = re.compile(r"[ ._\[\]()+]+")
_WORD_SPLIT = re.compile(r"[^a-z0-9]+")
# A hyphen between two letters: where a name may join two titles.
_JOIN = re.compile(r"(?<=[A-Za-z])-(?=[A-Za-z])")

# Quality, source, language and service tokens: the title part ends at the first.
_STOP_TOKENS = frozenset("""
4k uhd sd
web webdl web-dl webrip web-rip webhd hdtv pdtv sdtv dsr dsrip satrip dvb dvbrip bluray blu-ray bdrip brrip
bdremux remux dvdrip dvd dvd5 dvd9 dvdr hdrip tvrip vhsrip
german deutsch ger eng english multi multi3 dual dl dubbed subbed nordic italian ita french truefrench vostfr
vf vff vfq spanish esp spa castellano latino polish pl russian rus turkish tur dutch swedish danish norwegian
finnish japanese jap korean kor chinese chs cht hindi arabic portuguese por en-gr
proper repack rerip real internal readnfo limited uncut uncensored extended complete dc ws hdr hdr10 hdr10plus
dv dovi sdr 10bit 8bit hevc avc x264 x265 h264 h265 xvid divx aac aac2 ac3 dd dd2 dd5 ddp ddp2 ddp5 eac3 dts
atmos truehd flac opus mp3
amzn nf dsnp atvp hmax hulu pcok pmtp itv itvx iplayer rte stan crav roku tubi joyn rtlplus tvnow ard zdf
arte mdr ndr wdr swr mtod tving wavve viu wetv funi adn
""".split())

# Words that do not count as title words.
STOP_WORDS = frozenset("""
the a an of and or in on at to for is it its with from by as
der das den dem des ein eine einer eines und im zum zur von vom mit auf fuer ist
le la les el il du un une et
""".split())

# A title made of these words (and numbers) is a placeholder, not a title.
GENERIC_WORDS = frozenset("""
episode episodes ep folge teil part chapter kapitel episodio capitulo afl aflevering avsnitt jakso odcinek
bolum tba tbd unknown untitled final finale
""".split())


def _ascii_words(text: str) -> list[str]:
    text = (text or "").translate(_UMLAUTS)
    text = re.sub(r"['’`´]", "", text)            # Doesn't -> Doesnt
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    text = text.replace("&", " and ")
    return [w for w in _WORD_SPLIT.split(text) if w]


def _is_stop_token(token: str) -> bool:
    low = token.lower()
    if _RESOLUTION.match(low) or low in _STOP_TOKENS:
        return True
    head = low.split("-")[0]
    return bool(head) and (head in _STOP_TOKENS or bool(_RESOLUTION.match(head)))


def release_title_part(release: str) -> str | None:
    """Text between SxxEyy and the first quality, source or language token;
    None when the name has no SxxEyy, '' when nothing stands there."""
    match = _SXXEYY.search(release or "")
    if not match:
        return None
    tokens = [t for t in _TOKEN_SPLIT.split(release[match.end():]) if t]
    taken: list[str] = []
    hit_stop = False
    for token in tokens:
        if _is_stop_token(token):
            hit_stop = True
            break
        taken.append(token)
    if not hit_stop and taken and "-" in taken[-1]:
        # No quality token at all: the last token may carry the group (Title-GRP).
        taken[-1] = taken[-1].rsplit("-", 1)[0]
    return " ".join(taken).strip(" -")


def real_words(text: str) -> list[str]:
    """Words of letters only, at least two of them, without stop words and
    placeholder words."""
    return [w for w in _ascii_words(text)
            if w.isalpha() and len(w) >= 2 and w not in STOP_WORDS and w not in GENERIC_WORDS]


def is_generic(text: str) -> bool:
    """Only placeholder words and numbers ('Episode 20', 'Folge 3', 'TBA')."""
    words = [w for w in _ascii_words(text) if w.isalpha()]
    return not words or all(w in GENERIC_WORDS for w in words)


def _series_words(series_title: str) -> frozenset[str]:
    return frozenset(w for w in real_words(series_title) if len(w) >= 4)


def _drop_series(words: list[str], series_words: frozenset[str]) -> list[str]:
    """Without the words of the series title (also with one letter less at
    the end: 'Dogs' for 'Dog')."""
    return [w for w in words if w not in series_words and w[:-1] not in series_words]


def _title_words(part: str, series_title: str) -> list[str]:
    return _drop_series(real_words(part), _series_words(series_title))


def needs_episode_list(release: str, series_title: str) -> bool:
    """Does the name carry a title S6 compares (and so the episode list)?"""
    part = release_title_part(release)
    return bool(part) and not is_generic(part) and len(_title_words(part, series_title)) >= MIN_WORDS


def is_untitled(release: str, series_title: str) -> bool:
    """SxxEyy without any title, or with a placeholder only (S7)."""
    part = release_title_part(release)
    if part is None:
        return False
    return not part or is_generic(part) or not _title_words(part, series_title)


def episode_list(resources) -> tuple[tuple[int, int, str], ...]:
    """GET /api/v3/episode?seriesId=… -> ((season, number, title), …)."""
    episodes = []
    for item in resources or []:
        if not isinstance(item, dict):
            continue
        season, number = item.get("seasonNumber"), item.get("episodeNumber")
        if isinstance(season, int) and isinstance(number, int) and not isinstance(season, bool) \
                and not isinstance(number, bool):
            episodes.append((season, number, str(item.get("title") or "")))
    return tuple(episodes)


@lru_cache(maxsize=4096)
def _same_word(a: str, b: str) -> bool:
    if a == b:
        return True
    return min(len(a), len(b)) >= 5 and difflib.SequenceMatcher(None, a, b).ratio() >= SAME_RATIO


def _coverage(of: list[str], within, weight=None) -> float:
    """Share of the words 'of' (weighted) that have a counterpart in 'within'."""
    if not of:
        return 0.0
    weight = weight or (lambda w: 1.0)
    total = sum(weight(w) for w in of)
    hit = sum(weight(w) for w in of if any(_same_word(w, x) for x in within))
    return hit / total if total else 0.0


def _df(lists: tuple[tuple[str, ...], ...], word: str) -> int:
    return sum(1 for words in lists if any(_same_word(word, w) for w in words))


def _squeeze(text: str) -> str:
    return "".join(w for w in _ascii_words(text) if w not in GENERIC_WORDS)


@lru_cache(maxsize=64)
def _series_index(series_title: str, episodes: tuple[tuple[int, int, str], ...]):
    """Title words per episode (series words left out) and the word weight:
    rare in the series' titles counts more ('dog' in a series about dogs
    counts little). Once per series and episode list."""
    series_words = _series_words(series_title)
    lists = tuple(tuple(_drop_series(real_words(title), series_words)) for (_, _, title) in episodes)
    n = max(len(lists), 1)
    df: dict[str, int] = {}
    for words in lists:
        for w in set(words):
            df[w] = df.get(w, 0) + 1

    @lru_cache(maxsize=None)
    def weight(word: str) -> float:
        count = df[word] if word in df else sum(c for w, c in df.items() if _same_word(word, w))
        return math.log((n + 1) / (count + 0.5))
    return lists, weight


def _judge_part(part: str, target: tuple[int, int], target_title: str, series_title: str,
                episodes: tuple[tuple[int, int, str], ...]) -> str:
    series_words = _series_words(series_title)
    words = _drop_series(real_words(part), series_words)
    if not part:
        return NO_TITLE
    if is_generic(part) or not words:
        return GENERIC
    if len(words) < MIN_WORDS:
        return TOO_SHORT
    if is_generic(target_title):
        return TARGET_GENERIC
    target_words = _drop_series(real_words(target_title), series_words)
    lists, weight = _series_index(series_title, episodes)
    own = max(_coverage(target_words, words, weight), _coverage(words, target_words, weight)) if target_words else 0.0
    squeezed_part, squeezed_target = _squeeze(part), _squeeze(target_title)
    if len(squeezed_target) >= 8 and len(squeezed_part) >= 8 and (
            squeezed_target in squeezed_part or squeezed_part in squeezed_target):
        own = 1.0                                              # 'Howl-o-Ween Charm' / 'Howloween.Charm'
    if own >= FIT_OK:
        return FITS
    best = None
    union: set[str] = set()
    for (season, number, _), title_words in zip(episodes, lists):
        if (season, number) == target or not title_words:
            continue
        if _coverage(list(title_words), words, weight) < WHOLE:   # the whole other title in the release?
            continue
        matched = {w for w in title_words if any(_same_word(w, x) for x in words)}
        union |= matched
        specific = {w for w in matched if len(w) >= 4 and _df(lists, w) <= SPECIFIC_MAX_TITLES}
        key = (len(specific), len(matched))
        if best is None or key > best[0]:
            best = (key, specific)
    if best is None:
        return NO_MATCH
    (specific_count, matched_count), specific = best
    explained = _coverage(words, sorted(union))
    strong = specific_count >= 1 and (matched_count >= 2 or any(len(w) >= 6 for w in specific))
    return OTHER_EPISODE if strong and explained >= EXPLAINED_MIN else OTHER_WEAK


def _halves(part: str) -> list[tuple[str, str]]:
    """Every split of the title part at a hyphen between two letters into two
    parts of at least MIN_WORDS words each (two titles in one name)."""
    splits = []
    for match in _JOIN.finditer(part):
        left, right = part[:match.start()], part[match.end():]
        if len(real_words(left)) >= MIN_WORDS and len(real_words(right)) >= MIN_WORDS:
            splits.append((left, right))
    return splits


def judge(release: str, target: tuple[int, int], target_title: str, series_title: str,
          episodes: tuple[tuple[int, int, str], ...]) -> str:
    """Category of the release's episode title for the episode target
    (season, number) titled target_title. episodes: episode_list() of the
    series."""
    part = release_title_part(release)
    if part is None:
        return NO_SXXEYY
    whole = _judge_part(part, target, target_title, series_title, episodes)
    if whole in (FITS, OTHER_EPISODE) or whole in UNTITLED or whole in (TOO_SHORT, TARGET_GENERIC):
        return whole
    for left, right in _halves(part):
        halves = (_judge_part(left, target, target_title, series_title, episodes),
                  _judge_part(right, target, target_title, series_title, episodes))
        if FITS not in halves and OTHER_EPISODE in halves:
            return OTHER_EPISODE
    return whole
```

- [ ] **Step 5: S5 und die Anbindung von S6/S7 in `sonarr_rules.py`**

**Ersetze in** `backend/checked_search/sonarr_rules.py`:

```python
  S4 file    the release of the existing file is never grabbed again
```

**durch:**

```python
  S4 file    the release of the existing file is never grabbed again
  S5 name    a name without year that /parse maps to no series and that is
             also the title of another series of the library (0.12.0)
  S6 title   the episode title in the name names another episode (0.12.0)
  S7 number  untitled releases of a list in which S6 found another episode,
             in the same languages (0.12.0)
```

**Ersetze in** `backend/checked_search/sonarr_rules.py`:

```python

from backend.checked_search.normalize import parse_utc, same_release, strip_extension, tokens
from backend.checked_search.settings import CheckedSearchSettings
```

**durch:**

```python

from backend.checked_search import episode_titles
from backend.checked_search.normalize import parse_utc, same_release, strip_extension, tokens, variants
from backend.checked_search.settings import CheckedSearchSettings
```

**Ersetze in** `backend/checked_search/sonarr_rules.py`:

```python
from backend.checked_search.verdict import (
    REASON_COUNTRY_SUFFIX, REASON_EXISTING_FILE, REASON_OTHER_SERIES, REASON_TOO_EARLY, REASON_YEAR_SUFFIX, Verdict,
)
```

**durch:**

```python
from backend.checked_search.verdict import (
    NOTE_NO_EPISODE_FITS, REASON_COUNTRY_SUFFIX, REASON_EXISTING_FILE, REASON_NAMESAKE, REASON_NUMBERING,
    REASON_OTHER_EPISODE, REASON_OTHER_SERIES, REASON_TOO_EARLY, REASON_YEAR_SUFFIX, Verdict,
)
```

**Ersetze in** `backend/checked_search/sonarr_rules.py`:

```python
_YEAR = re.compile(r"^(18|19|20)\d\d$")
```

**durch:**

```python
_YEAR = re.compile(r"^(18|19|20)\d\d$")
# The last word of a title with what surrounds it ("Nemesis (2026)" -> " (2026)").
_LAST_WORD = re.compile(r"[\W_]*[^\W_]+[\W_]*$")
_NAME_SPLIT = re.compile(r"[ ._\-()\[\]]+")
```

**Ersetze in** `backend/checked_search/sonarr_rules.py`:

```python
    existing_releases: tuple[str, ...] = ()  # episodeFile.sceneName, file name without extension
```

**durch:**

```python
    existing_releases: tuple[str, ...] = ()  # episodeFile.sceneName, file name without extension
    season_number: int | None = None         # of the episode searched for (S6, S7)
    episode_number: int | None = None
    episode_title: str = ""
```

**Ersetze in** `backend/checked_search/sonarr_rules.py`:

```python
    series_title: str = ""                  # parsedEpisodeInfo.seriesTitle
```

**durch:**

```python
    series_title: str = ""                  # parsedEpisodeInfo.seriesTitle
    year: int = 0                           # parsedEpisodeInfo.seriesTitleInfo.year


@dataclass(frozen=True)
class RuleContext:
    """What S5-S7 need beyond the one release, filled by the runner. A rule
    whose data is missing (None, empty) checks nothing: without a context
    evaluate() decides as before 0.12.0 (the Imports page)."""
    namesake_index: dict | None = None                         # namesake_index() of the library (S5)
    episodes: tuple | None = None                              # episode_titles.episode_list() of the series (S6)
    release_languages: frozenset = frozenset()                 # of this release, "Unknown" left out (S7)
    doubt_languages: tuple = ()                                # of the releases S6 rejected in the list (S7)
```

**Ersetze in** `backend/checked_search/sonarr_rules.py`:

```python
        existing_releases=tuple(name for name in existing if name),
    )
```

**durch:**

```python
        existing_releases=tuple(name for name in existing if name),
        season_number=_number(episode.get("seasonNumber")),
        episode_number=_number(episode.get("episodeNumber")),
        episode_title=str(episode.get("title") or ""),
    )
```

**Ersetze in** `backend/checked_search/sonarr_rules.py`:

```python
    )
```

**durch:**

```python
    )


def _number(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
```

**Ersetze in** `backend/checked_search/sonarr_rules.py`:

```python
    series_id = series.get("id") if isinstance(series, dict) else None
    return EpisodeParse(series_id=int(series_id) if series_id else None, series_title=info.get("seriesTitle") or "")
```

**durch:**

```python
    series_id = series.get("id") if isinstance(series, dict) else None
    year = _number((info.get("seriesTitleInfo") or {}).get("year")) or 0
    return EpisodeParse(series_id=int(series_id) if series_id else None, series_title=info.get("seriesTitle") or "",
                        year=year)
```

**Ersetze in** `backend/checked_search/sonarr_rules.py`:

```python

def _country_fits(info: EpisodeInfo, country: str, codes) -> bool:
```

**durch:**

```python

def base_keys(title: str, codes) -> set[str]:
    """Compact spellings of a series title, with and without its year or
    country suffix ('Nemesis (2026)' -> nemesis2026, nemesis). The suffix is
    cut from the original text, so umlaut and '&' spellings stay."""
    year, country = title_suffix(title, codes)
    base = title
    for _ in range((1 if year else 0) + (1 if country else 0)):
        base = _LAST_WORD.sub("", base)
    return variants(title) | variants(base)


def namesake_index(series_list, codes) -> dict[str, frozenset[tuple[int, int]]]:
    """GET /api/v3/series -> compact title (with and without suffix) ->
    {(series id, year)}, from the title and every alternate title (S5)."""
    index: dict[str, set[tuple[int, int]]] = {}
    for series in series_list or []:
        if not isinstance(series, dict) or _number(series.get("id")) is None:
            continue
        year = _number(series.get("year")) or 0
        titles = [series.get("title")]
        titles += [alt.get("title") for alt in series.get("alternateTitles") or [] if isinstance(alt, dict)]
        for title in titles:
            if isinstance(title, str) and title:
                for key in base_keys(title, codes):
                    index.setdefault(key, set()).add((series["id"], year))
    return {key: frozenset(pairs) for key, pairs in index.items()}


def needs_namesake_index(parse: EpisodeParse, settings: CheckedSearchSettings) -> bool:
    """S5 can only fire on a name /parse maps to no series, without a year
    and without a suffix: only then is the library's series list needed."""
    return (settings.veto_namesake and parse.series_id is None and bool(parse.series_title) and not parse.year
            and not any(title_suffix(parse.series_title, settings.country_codes)))


def namesakes(info: EpisodeInfo, release_title: str, parse: EpisodeParse, index, settings) -> frozenset[int]:
    """Other series of the library whose title (with or without its suffix)
    the name has as well ('Nemesis' for 'Nemesis (2024)' and 'Nemesis (AU)'
    while 'Nemesis (2026)' is searched). None when the year of the series
    searched for stands in the name as a word of its own and no namesake
    has that year."""
    if index is None or not needs_namesake_index(parse, settings):
        return frozenset()
    found = {pair for key in variants(parse.series_title) for pair in index.get(key, ())}
    others = {pair for pair in found if pair[0] != info.series_id}
    if not others:
        return frozenset()
    words = {int(w) for w in _NAME_SPLIT.split(release_title or "") if _YEAR.match(w)}
    if info.series_year and info.series_year in words and all(year != info.series_year for _, year in others):
        return frozenset()
    return frozenset(series_id for series_id, _ in others)


def _country_fits(info: EpisodeInfo, country: str, codes) -> bool:
```

**Ersetze in** `backend/checked_search/sonarr_rules.py`:

```python
def evaluate(info: EpisodeInfo, release_title: str, publish_date: datetime | None,
             parse: EpisodeParse, settings: CheckedSearchSettings) -> Verdict:
    reasons: list[str] = []
```

**durch:**

```python
def evaluate(info: EpisodeInfo, release_title: str, publish_date: datetime | None,
             parse: EpisodeParse, settings: CheckedSearchSettings, context: RuleContext | None = None) -> Verdict:
    reasons: list[str] = []
```

**Ersetze in** `backend/checked_search/sonarr_rules.py`:

```python
    notes: list[str] = []
```

**durch:**

```python
    notes: list[str] = []
    context = context or RuleContext()
```

**Ersetze in** `backend/checked_search/sonarr_rules.py`:

```python
        reasons.append(REASON_OTHER_SERIES)
```

**durch:**

```python
        reasons.append(REASON_OTHER_SERIES)

    if settings.veto_namesake and namesakes(info, release_title, parse, context.namesake_index, settings):
        reasons.append(REASON_NAMESAKE)
```

**Ersetze in** `backend/checked_search/sonarr_rules.py`:

```python
        reasons.append(REASON_EXISTING_FILE)
    return Verdict(tuple(reasons), tuple(notes))
```

**durch:**

```python
        reasons.append(REASON_EXISTING_FILE)

    series_title = info.series_titles[0] if info.series_titles else ""
    if settings.check_episode_title and context.episodes is not None and info.episode_number is not None:
        category = episode_titles.judge(release_title, (info.season_number, info.episode_number),
                                        info.episode_title, series_title, context.episodes)
        if category == episode_titles.OTHER_EPISODE:
            reasons.append(REASON_OTHER_EPISODE)
        elif category == episode_titles.NO_MATCH:
            notes.append(NOTE_NO_EPISODE_FITS)

    if (settings.check_episode_title and settings.untitled_after_other_episode and context.release_languages
            and episode_titles.is_untitled(release_title, series_title)
            and any(doubt and context.release_languages <= doubt for doubt in context.doubt_languages)):
        reasons.append(REASON_NUMBERING)
    return Verdict(tuple(reasons), tuple(notes))
```

- [ ] **Step 6: Tests laufen lassen, sie bestehen**

Run: `.venv/bin/python -m pytest tests/test_i1_namesake.py tests/test_i2_episode_titles.py tests/test_g1_sonarr_rules.py tests/test_g1_settings.py tests/test_g1_fingerprint.py tests/test_g5_form.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 7: Ganze Suite**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider`
Expected: PASS (der Runner ruft `evaluate` noch ohne Kontext auf; S5–S7 greifen dort erst nach Task 2).

- [ ] **Step 8: Commit**

```bash
git add backend/checked_search/episode_titles.py backend/checked_search/verdict.py backend/checked_search/settings.py backend/tooltips.py backend/checked_search/sonarr_rules.py tests/test_i1_namesake.py tests/test_i2_episode_titles.py tests/test_g1_sonarr_rules.py
git commit -m "feat: sonarr rules for namesakes, episode titles and numbering in doubt

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01WUYs9mtP8aQCJCSLMEjKFc"
```

---

### Task 2: Runner liest Serien- und Folgenliste und prüft die ganze Liste vorab

**Files:**
- Modify: `backend/checked_search/runner.py`
- Test: `tests/test_i3_numbering.py`, `tests/test_i4_runner_context.py` (neu)

**Interfaces:**
- Consumes: alles aus Task 1 („Produces“).
- Produces: `runner.SERIES_PATH`, `runner.SERIES_TIMEOUT` (120), `runner.EPISODES_PATH`, `runner.NAMESAKE_TTL_SECONDS` (86400), `runner._NAMESAKE_CACHE`, `runner._namesake_clock`; `_TitleCheck.namesake_index() -> dict`, `_TitleCheck.episode_list(series_id: int) -> tuple`, `_TitleCheck.numbering_doubt(info, releases) -> tuple[frozenset, ...]`, `_TitleCheck.rule_context(info, release, parse, doubt) -> sonarr_rules.RuleContext`, `_TitleCheck.verdict(task, info, release, doubt=())`.

- [ ] **Step 1: Tests schreiben**

`tests/test_i3_numbering.py` bringt das Test-Doppel `EpisodeListArr` mit (beantwortet `GET /api/v3/episode?seriesId=`), `tests/test_i4_runner_context.py` holt es von dort.

**Neue Datei** `tests/test_i3_numbering.py`:

```python
"""0.12.0, Sonarr rules S6 and S7 in a run: a release whose episode title
names another episode is rejected, and with it every untitled release of
the list in the same languages (invented series)."""
import copy

import pytest

from backend import database, db
from backend.checked_search import sonarr_rules as sr
from backend.checked_search.settings import CheckedSearchSettings
from backend.checked_search.verdict import NOTE_NO_EPISODE_FITS, REASON_NUMBERING, REASON_OTHER_EPISODE
from backend.config import settings
from backend.skills.search_missing import SearchMissingSkill
from tests.test_g3_runner import FakeArr, episode_grab, log_rows, make_instance, release

EPISODES = "/api/v3/episode"
SERIES = {"id": 10, "title": "Paw Friends", "year": 2019, "alternateTitles": [], "qualityProfileId": 1}
TITLES = ["The Big Race", "Lost in the Woods", "Birthday Surprise", "Rainy Day Blues", "The Missing Bone"]
OTHER = "Paw.Friends.S01E03.Lost.in.the.Woods.1080p.WEB-DL.x264-GRP"
UNTITLED = "Paw.Friends.S01E03.1080p.WEB-DL.x264-GRP"
GERMAN = "Paw.Friends.S01E03.GERMAN.1080p.WEB-DL.x264-GRP"
ENGLISH = [{"id": 1, "name": "English"}]
UNKNOWN = [{"id": 0, "name": "Unknown"}]


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


def paw_episode(number, title=None):
    return {"id": number, "seriesId": 10, "seasonNumber": 1, "episodeNumber": number,
            "title": TITLES[number - 1] if title is None else title, "airDateUtc": "2019-10-11T12:00:00Z",
            "hasFile": False, "monitored": True, "series": {"title": "Paw Friends"}}


class EpisodeListArr(FakeArr):
    """FakeArr that also answers GET /episode?seriesId= with the episodes of
    the series (Sonarr returns the whole series in one list)."""

    def http_get(self, path, params=None, timeout=10):
        if path != EPISODES:
            return super().http_get(path, params, timeout)
        params = dict(params or {})
        self.gets.append((path, params))
        self.timeouts[path] = timeout
        for pattern, error in self.get_errors.items():
            if self._matches(path, pattern):
                raise error
        return [copy.deepcopy(e) for e in self.episodes.values() if e["seriesId"] == params["seriesId"]]


def paw_release(title, guid, languages=None):
    data = release(title, guid, series_id=10, episode_ids=(3,))
    if languages is not None:
        data["languages"] = copy.deepcopy(languages)
    return data


def paw_agent(inst, releases, **kwargs):
    episodes = [paw_episode(n) for n in range(1, 6)]
    parses = {r["title"]: {"parsedEpisodeInfo": {"seriesTitle": "Paw Friends"}, "series": {"id": 10}}
              for r in releases}
    defaults = dict(missing=[paw_episode(3)], episodes=episodes, series=[SERIES], parses=parses,
                    releases={3: releases})
    defaults.update(kwargs)
    return EpisodeListArr(db.instances.get_by_id(inst["id"]), **defaults)


def sonarr(**fields):
    return make_instance(**{"name": "Sonarr", "type": "sonarr", "checked_search": "active", **fields})


def reasons(row):
    return {c["title"]: c["reasons"] for c in row["candidates"]}


def test_an_untitled_release_of_the_same_language_is_rejected_before_the_pick(db_path):
    # The untitled release comes first: without the look at the whole list
    # it would be grabbed before the title of the second one is seen.
    agent = paw_agent(sonarr(), [paw_release(UNTITLED, "g1", ENGLISH), paw_release(OTHER, "g2", ENGLISH),
                                 paw_release(GERMAN, "g3")])
    SearchMissingSkill().execute(agent)
    [row] = log_rows()
    assert reasons(row) == {UNTITLED: [REASON_NUMBERING], OTHER: [REASON_OTHER_EPISODE], GERMAN: []}
    assert row["pick"] == GERMAN
    assert agent.posts == [episode_grab("g3")]


def test_other_languages_and_unknown_stay_free(db_path):
    dual = [{"id": 1, "name": "English"}, {"id": 4, "name": "German"}]
    agent = paw_agent(sonarr(), [paw_release(OTHER, "g1", ENGLISH), paw_release(UNTITLED, "g2", dual)])
    SearchMissingSkill().execute(agent)
    assert agent.posts[0]["guid"] == "g2"

    inst = sonarr(name="Sonarr 2")
    agent = paw_agent(inst, [paw_release(OTHER, "g1", ENGLISH), paw_release(UNTITLED, "g2", UNKNOWN)])
    SearchMissingSkill().execute(agent)
    assert agent.posts[0]["guid"] == "g2"


def test_a_rejected_release_without_known_language_blocks_nothing(db_path):
    agent = paw_agent(sonarr(), [paw_release(OTHER, "g1", UNKNOWN), paw_release(UNTITLED, "g2", ENGLISH)])
    SearchMissingSkill().execute(agent)
    assert agent.posts[0]["guid"] == "g2"


@pytest.mark.parametrize("off", [{"check_episode_title": False}, {"untitled_after_other_episode": False}])
def test_s7_needs_both_switches(db_path, off):
    agent = paw_agent(sonarr(checked_search_settings=off),
                      [paw_release(UNTITLED, "g1", ENGLISH), paw_release(OTHER, "g2", ENGLISH)])
    SearchMissingSkill().execute(agent)
    [row] = log_rows()
    assert reasons(row)[UNTITLED] == []
    assert agent.posts == [{**episode_grab("g1"), "languages": ENGLISH}]


def test_a_title_that_fits_no_episode_is_only_a_note(db_path):
    german_title = "Paw.Friends.S01E03.Geburtstags.Ueberraschung.GERMAN.1080p.WEB-DL-GRP"
    agent = paw_agent(sonarr(), [paw_release(german_title, "g1")])
    SearchMissingSkill().execute(agent)
    [row] = log_rows()
    assert row["candidates"][0]["notes"] == [NOTE_NO_EPISODE_FITS]
    assert agent.posts == [episode_grab("g1")]


def test_evaluate_without_context_ignores_s6_and_s7():
    info = sr.EpisodeInfo(10, ("Paw Friends",), 2019, season_number=1, episode_number=3,
                          episode_title="Birthday Surprise")
    parse = sr.EpisodeParse(10, "Paw Friends")
    assert sr.evaluate(info, OTHER, None, parse, CheckedSearchSettings()).ok
    context = sr.RuleContext(release_languages=frozenset({"English"}), doubt_languages=(frozenset({"English"}),))
    assert sr.evaluate(info, UNTITLED, None, parse, CheckedSearchSettings(), context).reasons == (REASON_NUMBERING,)
    assert sr.evaluate(info, UNTITLED, None, parse, CheckedSearchSettings()).ok
```

**Neue Datei** `tests/test_i4_runner_context.py`:

```python
"""0.12.0: what the runner reads for S5-S7. The series list only when a name
could be a namesake, kept for a day per instance; the episode list once per
series and run; a list that cannot be read leaves the release unchecked
(parse error). The Imports page judges as before (invented series)."""
import pytest

from backend import database, db
from backend.checked_search import runner
from backend.checked_search.settings import CheckedSearchSettings
from backend.checked_search.verdict import (
    REASON_NAMESAKE, REASON_NUMBERING, REASON_OTHER_EPISODE, REASON_PARSE_ERROR,
)
from backend.config import settings
from backend.skills.search_missing import SearchMissingSkill
from tests.test_g3_runner import episode_grab, http_error, log_rows, make_instance, release, sql
from tests.test_i3_numbering import (
    ENGLISH, EPISODES, GERMAN, OTHER, UNTITLED, EpisodeListArr, paw_agent, paw_release, reasons,
)

SERIES_LIST = "/api/v3/series"
TARGET = {"id": 10, "title": "Some Show (2026)", "year": 2026, "alternateTitles": [], "qualityProfileId": 1}
NAMESAKE = {"id": 11, "title": "Some Show (2020)", "year": 2020, "alternateTitles": [], "qualityProfileId": 1}
BARE = "Some.Show.S01E03.1080p.WEB-DL.x264-GRP"
BARE_2 = "Some.Show.S01E03.720p.WEB-DL.x264-GRP"
WITH_YEAR = "Some.Show.2026.S01E03.GERMAN.1080p.WEB-DL.x264-GRP"
NO_SERIES = {"parsedEpisodeInfo": {"seriesTitle": "Some Show"}}
MAPPED = {"parsedEpisodeInfo": {"seriesTitle": "Some Show 2026", "seriesTitleInfo": {"year": 2026}},
          "series": {"id": 10}}


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


def sonarr(**fields):
    return make_instance(**{"name": "Sonarr", "type": "sonarr", "checked_search": "active", **fields})


def show_episode():
    return {"id": 3, "seriesId": 10, "seasonNumber": 1, "episodeNumber": 3, "title": "Birthday Surprise",
            "airDateUtc": "2026-09-01T12:00:00Z", "hasFile": False, "monitored": True,
            "series": {"title": "Some Show (2026)"}}


def show_agent(inst, names, **kwargs):
    parses = {BARE: NO_SERIES, BARE_2: NO_SERIES, WITH_YEAR: MAPPED}
    defaults = dict(missing=[show_episode()], episodes=[show_episode()], series=[TARGET, NAMESAKE],
                    parses=parses, releases={3: [release(n, f"g{i}", series_id=10, episode_ids=(3,))
                                                 for i, n in enumerate(names, 1)]})
    defaults.update(kwargs)
    return EpisodeListArr(db.instances.get_by_id(inst["id"]), **defaults)


def reads(agent, path):
    return [params for p, params in agent.gets if p == path]


def test_a_namesake_is_rejected_and_the_series_list_read_once(db_path):
    agent = show_agent(sonarr(), [BARE, BARE_2, WITH_YEAR])
    SearchMissingSkill().execute(agent)
    [row] = log_rows()
    assert reasons(row) == {BARE: [REASON_NAMESAKE], BARE_2: [REASON_NAMESAKE], WITH_YEAR: []}
    assert agent.posts == [episode_grab("g3")]
    assert len(reads(agent, SERIES_LIST)) == 1
    assert agent.timeouts[SERIES_LIST] == runner.SERIES_TIMEOUT
    assert reads(agent, EPISODES) == []          # no name carries an episode title


def test_the_series_list_is_not_read_without_need(db_path):
    agent = show_agent(sonarr(), [WITH_YEAR])
    SearchMissingSkill().execute(agent)
    assert agent.posts == [episode_grab("g1")]
    assert reads(agent, SERIES_LIST) == []
    agent = show_agent(sonarr(name="Sonarr 2", checked_search_settings={"veto_namesake": False}), [BARE])
    SearchMissingSkill().execute(agent)
    assert agent.posts == [episode_grab("g1")]
    assert reads(agent, SERIES_LIST) == []


def test_the_index_is_kept_for_a_day_per_instance(db_path, monkeypatch):
    inst = sonarr()
    now = [1000.0]
    monkeypatch.setattr(runner, "_namesake_clock", lambda: now[0])

    def check():
        config = db.instances.get_by_id(inst["id"])
        agent = show_agent(inst, [])
        title_check = runner._TitleCheck("search_missing", agent, 1, runner.MODE_ACTIVE,
                                         CheckedSearchSettings(), config)
        index = title_check.namesake_index()
        return agent, index

    agent, index = check()
    assert len(reads(agent, SERIES_LIST)) == 1 and index["someshow"] == {(10, 2026), (11, 2020)}
    agent, _ = check()                                    # a later run within the day
    assert reads(agent, SERIES_LIST) == []
    now[0] += runner.NAMESAKE_TTL_SECONDS
    agent, _ = check()                                    # a day later
    assert len(reads(agent, SERIES_LIST)) == 1
    sql("UPDATE instances SET updated_at='2030-01-01 00:00:00'")
    agent, _ = check()                                    # the instance was saved
    assert len(reads(agent, SERIES_LIST)) == 1
    assert [key for key in runner._NAMESAKE_CACHE if key[:2] == (str(db_path), inst["id"])] == [
        (str(db_path), inst["id"], "2030-01-01 00:00:00", tuple(sorted(CheckedSearchSettings().country_codes)))]


def test_an_unreadable_series_list_leaves_the_name_unchecked(db_path):
    agent = show_agent(sonarr(), [BARE, BARE_2, WITH_YEAR], get_errors={SERIES_LIST + "$": http_error(500)})
    SearchMissingSkill().execute(agent)
    [row] = log_rows()
    assert reasons(row) == {BARE: [REASON_PARSE_ERROR], BARE_2: [REASON_PARSE_ERROR], WITH_YEAR: []}
    assert agent.posts == [episode_grab("g3")]
    assert len(reads(agent, SERIES_LIST)) == 1          # not read again for the second name


def test_the_episode_list_is_read_once_per_series_and_run(db_path):
    titled = "Paw.Friends.S01E03.Birthday.Surprise.1080p.WEB-DL.x264-GRP"
    agent = paw_agent(sonarr(), [paw_release(OTHER, "g1", ENGLISH), paw_release(UNTITLED, "g2", ENGLISH),
                                 paw_release(titled, "g3", ENGLISH)])
    SearchMissingSkill().execute(agent)
    [row] = log_rows()
    assert reasons(row) == {OTHER: [REASON_OTHER_EPISODE], UNTITLED: [REASON_NUMBERING], titled: []}
    assert reads(agent, EPISODES) == [{"seriesId": 10}]


def test_an_unreadable_episode_list_leaves_titled_names_unchecked(db_path):
    agent = paw_agent(sonarr(), [paw_release(OTHER, "g1", ENGLISH), paw_release(UNTITLED, "g2", ENGLISH),
                                 paw_release(GERMAN, "g3")],
                      get_errors={EPISODES + "$": http_error(500)})
    SearchMissingSkill().execute(agent)
    [row] = log_rows()
    # S7 needs the list as well: the untitled release is taken
    assert reasons(row) == {OTHER: [REASON_PARSE_ERROR], UNTITLED: [], GERMAN: []}
    assert agent.posts == [{**episode_grab("g2"), "languages": ENGLISH}]
    assert reads(agent, EPISODES) == [{"seriesId": 10}]


def test_radarr_reads_neither_list(db_path):
    from tests.test_g3_runner import the_thing_agent
    agent = the_thing_agent(make_instance(checked_search="active"))
    SearchMissingSkill().execute(agent)
    assert reads(agent, SERIES_LIST) == [] and reads(agent, EPISODES) == []


def test_the_imports_page_judges_as_before():
    from backend.checked_search import import_check as ic
    from tests.test_h2_import_check import judge_show, sonarr_parse
    v = judge_show("Some.Show.S01E01.Lost.in.the.Woods.German.1080p.WEB.h264-GRP", sonarr_parse("Some Show"))
    assert v.state == ic.VERDICT_FITS
    assert not {REASON_NAMESAKE, REASON_OTHER_EPISODE, REASON_NUMBERING} & set(v.reasons)
```

- [ ] **Step 2: Tests laufen lassen, sie schlagen fehl**

Run: `.venv/bin/python -m pytest tests/test_i3_numbering.py tests/test_i4_runner_context.py -q -p no:cacheprovider`
Expected: FAIL (`AttributeError: module 'backend.checked_search.runner' has no attribute 'SERIES_TIMEOUT'` bzw. Releases ohne die neuen Gründe).

- [ ] **Step 3: Runner**

**Ersetze in** `backend/checked_search/runner.py`:

```python
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
```

**durch:**

```python
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
```

**Ersetze in** `backend/checked_search/runner.py`:

```python
from backend import db
from backend.checked_search import radarr_rules, sonarr_rules
from backend.checked_search.normalize import parse_utc
```

**durch:**

```python
from backend import db
from backend.checked_search import episode_titles, radarr_rules, sonarr_rules
from backend.checked_search.normalize import parse_utc
```

**Ersetze in** `backend/checked_search/runner.py`:

```python
)
from backend.skills.base import SubmitOutcome, UnsavedCheckedGrab
```

**durch:**

```python
)
from backend.config import settings as app_settings
from backend.skills.base import SubmitOutcome, UnsavedCheckedGrab
```

**Ersetze in** `backend/checked_search/runner.py`:

```python
PARSE_PATH = "/api/v3/parse"
INDEXER_PATH = "/api/v3/indexer"
```

**durch:**

```python
PARSE_PATH = "/api/v3/parse"
# Sonarr S5: the library's series (about 39 MB and 13 s for 11,000 series),
# read only when a release needs it and kept per instance for a day. The key
# holds the database (instance ids belong to it), the instance's updated_at
# and its country codes: saving the instance starts afresh.
SERIES_PATH = "/api/v3/series"
SERIES_TIMEOUT = 120
NAMESAKE_TTL_SECONDS = 24 * 3600
_NAMESAKE_CACHE: dict[tuple, tuple[float, dict]] = {}
_namesake_clock = time.monotonic
# Sonarr S6/S7: the episodes of a series, once per series and run.
EPISODES_PATH = "/api/v3/episode"
INDEXER_PATH = "/api/v3/indexer"
```

**Ersetze in** `backend/checked_search/runner.py`:

```python

def _pick(release: _Release | None) -> dict | None:
```

**durch:**

```python

def _languages(release: _Release) -> frozenset:
    """Language names GET /release reported for the release, without
    "Unknown" (S7 compares only known languages)."""
    raw = release.languages_raw if isinstance(release.languages_raw, list) else []
    return frozenset(item.get("name") for item in raw
                     if isinstance(item, dict) and isinstance(item.get("name"), str)
                     and item.get("name") and item.get("name") != "Unknown")


def _pick(release: _Release | None) -> dict | None:
```

**Ersetze in** `backend/checked_search/runner.py`:

```python
        self.settings_fingerprint = settings.rules_fingerprint(self.arr_type)
```

**durch:**

```python
        self.settings_fingerprint = settings.rules_fingerprint(self.arr_type)
        self.namesake_key = (str(app_settings.database_url), self.instance_id, str(config.get("updated_at") or ""),
                             tuple(sorted(settings.country_codes)))
        self._namesake_error: Exception | None = None
        self._episodes: dict[int, tuple | Exception] = {}
```

**Ersetze in** `backend/checked_search/runner.py`:

```python

    def verdict(self, task: CheckedTask, info, release: _Release) -> Verdict:
        if not self.mapped_here(task, info, release):
```

**durch:**

```python

    def namesake_index(self) -> dict:
        """Index of the library's series titles for S5: from the day cache,
        else read once (GET /api/v3/series). A read error is kept for the rest
        of the run (no second long read per release) and raised."""
        if self._namesake_error is not None:
            raise self._namesake_error
        now = _namesake_clock()
        cached = _NAMESAKE_CACHE.get(self.namesake_key)
        if cached is not None and now - cached[0] < NAMESAKE_TTL_SECONDS:
            return cached[1]
        try:
            series = self.agent.http_get(SERIES_PATH, timeout=SERIES_TIMEOUT)
            if not isinstance(series, list):
                raise ValueError("the series list was no list")
        except Exception as exc:
            self._namesake_error = exc
            raise
        index = sonarr_rules.namesake_index(series, self.settings.country_codes)
        for key in [k for k in _NAMESAKE_CACHE if k[:2] == self.namesake_key[:2]]:
            del _NAMESAKE_CACHE[key]
        _NAMESAKE_CACHE[self.namesake_key] = (now, index)
        return index

    def episode_list(self, series_id: int) -> tuple:
        """(season, number, title) of every episode of the series for S6 and
        S7 (GET /api/v3/episode?seriesId=…), once per series and run. A read
        error is kept for the run and raised."""
        cached = self._episodes.get(series_id)
        if cached is None:
            try:
                answer = self.agent.http_get(EPISODES_PATH, params={"seriesId": series_id})
                if not isinstance(answer, list):
                    raise ValueError("the episode list was no list")
                cached = episode_titles.episode_list(answer)
            except Exception as exc:
                cached = exc
            self._episodes[series_id] = cached
        if isinstance(cached, Exception):
            raise cached
        return cached

    def numbering_doubt(self, info, releases: list[_Release]) -> tuple[frozenset, ...]:
        """S7: the languages of each release of the list whose episode title
        names another episode (S6). Empty for Radarr, with S6 or S7 off,
        without a release that carries a title, or when the episode list
        cannot be read (S6 then marks the titled releases unchecked)."""
        if self.arr_type == "radarr" or info.episode_number is None or not (
                self.settings.check_episode_title and self.settings.untitled_after_other_episode):
            return ()
        series_title = info.series_titles[0] if info.series_titles else ""
        titled = [r for r in releases if episode_titles.needs_episode_list(r.title, series_title)]
        if not titled:
            return ()
        try:
            episodes = self.episode_list(info.series_id)
        except Exception:
            return ()
        target = (info.season_number, info.episode_number)
        return tuple(_languages(r) for r in titled
                     if episode_titles.judge(r.title, target, info.episode_title, series_title, episodes)
                     == episode_titles.OTHER_EPISODE)

    def rule_context(self, info, release: _Release, parse, doubt) -> "sonarr_rules.RuleContext":
        """What S5-S7 need for this release. Raises _ParseFailed when a list
        it needs cannot be read: the release counts as unchecked."""
        context = sonarr_rules.RuleContext(release_languages=_languages(release), doubt_languages=doubt)
        if sonarr_rules.needs_namesake_index(parse, self.settings):
            try:
                context = replace(context, namesake_index=self.namesake_index())
            except Exception as exc:
                raise _ParseFailed(f"{release.title}: could not read the series list: {exc}") from exc
        series_title = info.series_titles[0] if info.series_titles else ""
        if self.settings.check_episode_title and info.episode_number is not None \
                and episode_titles.needs_episode_list(release.title, series_title):
            try:
                context = replace(context, episodes=self.episode_list(info.series_id))
            except Exception as exc:
                raise _ParseFailed(f"{release.title}: could not read the episode list: {exc}") from exc
        return context

    def verdict(self, task: CheckedTask, info, release: _Release, doubt: tuple = ()) -> Verdict:
        if not self.mapped_here(task, info, release):
```

**Ersetze in** `backend/checked_search/runner.py`:

```python
                                         self.settings)
        return sonarr_rules.evaluate(info, release.title, release.publish_date,
                                     sonarr_rules.parse_from_resource(parsed), self.settings)
```

**durch:**

```python
                                         self.settings)
        parse = sonarr_rules.parse_from_resource(parsed)
        return sonarr_rules.evaluate(info, release.title, release.publish_date, parse, self.settings,
                                     self.rule_context(info, release, parse, doubt))
```

**Ersetze in** `backend/checked_search/runner.py`:

```python
        limit = self.settings.dry_run_max_releases if self.mode == MODE_DRY_RUN else len(releases)
        candidates: list[dict] = []
```

**durch:**

```python
        limit = self.settings.dry_run_max_releases if self.mode == MODE_DRY_RUN else len(releases)
        # S7 looks at the whole list before the first clean release is picked.
        doubt = self.numbering_doubt(info, releases[:limit])
        candidates: list[dict] = []
```

**Ersetze in** `backend/checked_search/runner.py`:

```python
            try:
                verdict = self.verdict(task, info, release)
            except _ParseFailed as exc:
```

**durch:**

```python
            try:
                verdict = self.verdict(task, info, release, doubt)
            except _ParseFailed as exc:
```

- [ ] **Step 4: Tests laufen lassen, sie bestehen**

Run: `.venv/bin/python -m pytest tests/test_i3_numbering.py tests/test_i4_runner_context.py tests/test_g3_runner.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 5: Ganze Suite**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/checked_search/runner.py tests/test_i3_numbering.py tests/test_i4_runner_context.py
git commit -m "feat: checked search reads series and episode lists for the new sonarr rules

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01WUYs9mtP8aQCJCSLMEjKFc"
```

---

### Task 3: Version 0.12.0, README und CHANGELOG

**Files:**
- Modify: `VERSION`, `CHANGELOG.md`, `README.md`, `tests/test_p6_docs.py`, `tests/test_z_release.py`

**Interfaces:**
- Consumes: Gründe, Einstellungen und Lesewege aus Task 1 und 2 (nur als Text).
- Produces: nichts für andere Tasks.

- [ ] **Step 1: Tests anpassen**

**Ersetze in** `tests/test_p6_docs.py`:

```python

def test_version_is_0_11_1():
    assert (ROOT / "VERSION").read_text().strip() == "0.11.1"
```

**durch:**

```python

def test_version_is_0_12_0():
    assert (ROOT / "VERSION").read_text().strip() == "0.12.0"
```

**Hänge an** `tests/test_z_release.py`:

```python
def test_readme_and_changelog_document_0_12_0():
    changelog = (ROOT / "CHANGELOG.md").read_text()
    assert changelog.index("## [Unreleased]") < changelog.index("## [0.12.0]") < changelog.index("## [0.11.1]")
    section = changelog[changelog.index("## [0.12.0]"):changelog.index("## [0.11.1]")]
    for text in ("### Added", "### Changed", "other series by name", "other episode by title",
                 "episode numbering in doubt", "episode title fits no episode", "`IL`", "GET /api/v3/series",
                 "settings changed"):
        assert text in section, text
    readme = (ROOT / "README.md").read_text()
    assert readme.index("## Upgrading to 0.12.0") < readme.index("## Upgrading to 0.11.1")
    upgrade = readme[readme.index("## Upgrading to 0.12.0"):readme.index("## Upgrading to 0.11.1")]
    for text in ("No database change", "`IL`", "Rollback: 0.11.1"):
        assert text in upgrade, text
    rules = readme[readme.index("### Checked search"):readme.index("### Profile changes")]
    for text in ("another series of the library", "episode title", "different", "parse error"):
        assert text in rules, text
```

- [ ] **Step 2: Tests laufen lassen, sie schlagen fehl**

Run: `.venv/bin/python -m pytest tests/test_p6_docs.py tests/test_z_release.py -q -p no:cacheprovider`
Expected: FAIL (`VERSION` ist noch `0.11.1`, `## [0.12.0]` fehlt).

- [ ] **Step 3: VERSION**

**Neue Datei** `VERSION`:

```text
0.12.0
```

- [ ] **Step 4: CHANGELOG**

**Ersetze in** `CHANGELOG.md`:

````markdown
## [Unreleased]
````

**durch:**

````markdown
## [Unreleased]

## [0.12.0] - 2026-10-07

### Added

- Checked search, three new Sonarr rules. Each is a setting of the instance with an info icon, on by default:
  - **Veto when the title also fits another series** (S5, reason "other series by name"): a release whose name has no year and that `/parse` maps to no series is rejected when its series title is also the title of another series of the library, with or without that series' year or country suffix. Example: `Some.Show.S01E05…` while "Some Show (2026)" is searched and "Some Show (2020)" and "Some Show (AU)" are in the library. An indexer can stamp such a release with the ID of the searched series, and Sonarr then maps it by that ID. The release passes when the year of the searched series stands in its name as a word of its own.
  - **Reject when the episode title names another episode** (S6, reason "other episode by title"): the episode title in the name (between `SxxEyy` and the quality) is compared with the episode titles of the series, specials included. A title that clearly names another episode and not the one searched for rejects the release; this catches a release group that numbers the episodes differently. A title that fits no episode (a translated title, extra words) is only a note: "episode title fits no episode".
  - **Reject untitled releases when another release shows a different numbering** (S7, reason "episode numbering in doubt", needs S6): when S6 rejects a release of the search results, the releases of the same results without an episode title are rejected too, if all their languages are languages of the rejected release. Releases in other languages and releases of unknown language stay free. The whole list is looked at before the first clean release is picked.
- `IL` is a default country code (the suffix rule S2).

### Changed

- Sonarr's checked search reads the series list (`GET /api/v3/series`, tens of MB for a large library, timeout 120 s) when a release name could be a namesake. It keeps the list in memory for a day per instance; saving the instance or a restart reads it again. It reads the episode list of a series (`GET /api/v3/episode?seriesId=`) once per run when a release name carries an episode title. If a list cannot be read, the release that needs it counts as unchecked ("parse error"), like a failed `/parse`.
- The new settings change the Sonarr rules fingerprint: a dry run checks every Sonarr title again (rows from before show "settings changed"). Active mode is not affected.
````

- [ ] **Step 5: README**

**Ersetze in** `README.md`:

````markdown

Radarr rules: year within a tolerance of the movie's years (optionally including its release dates), veto when `/parse` names another movie of the library, title match (exact, prefix, word match), releases without a year need an exact title, and the release of the existing file is never grabbed again. Sonarr rules (single episodes only — the pack modes are locked while checked search is on): veto when `/parse` names another series, country/year suffix must fit the series, releases published long before the air date are rejected (shortly before: noted), and the release of the existing file is skipped. Every rule and limit is a setting of the instance; the info icons in the form explain each one.
````

**durch:**

````markdown

Radarr rules: year within a tolerance of the movie's years (optionally including its release dates), veto when `/parse` names another movie of the library, title match (exact, prefix, word match), releases without a year need an exact title, and the release of the existing file is never grabbed again. Sonarr rules (single episodes only — the pack modes are locked while checked search is on): veto when `/parse` names another series, a name without year that `/parse` maps to no series is rejected when it is also the title of another series of the library (with or without its year or country suffix), country/year suffix must fit the series, releases published long before the air date are rejected (shortly before: noted), the release of the existing file is skipped, an episode title in the name that names another episode of the series rejects the release (a title that fits no episode is only noted), and when that happens, the releases of the same search without an episode title and in the same languages are rejected too (a release group may number the episodes differently). The namesake rule reads the series list (`GET /api/v3/series`) when a name needs it and keeps it for a day; the episode-title rules read the episode list of the series (`GET /api/v3/episode?seriesId=`) once per run. A release whose list cannot be read counts as unchecked ("parse error"). Every rule and limit is a setting of the instance; the info icons in the form explain each one.
````

**Ersetze in** `README.md`:

````markdown

## Upgrading to 0.11.1
````

**durch:**

````markdown

## Upgrading to 0.12.0

- No database change. Three new Sonarr settings of the checked search, all on by default: *Veto when the title also fits another series*, *Reject when the episode title names another episode* and *Reject untitled releases when another release shows a different numbering* (see [Checked search](#checked-search)). They work at once in active mode; the Pre-filter page shows what they reject.
- `IL` joins the default country codes. An instance that was saved before keeps its stored list: add `IL` under *Country codes* yourself if you want it.
- Sonarr runs read the series list again when a release name needs it (once a day per instance, kept in memory) and the episode list of a series when a release name carries an episode title.
- In a dry run every Sonarr title is checked again (the rules fingerprint changed).
- Rollback: 0.11.1 works with the same database and ignores the new settings.

## Upgrading to 0.11.1
````

- [ ] **Step 6: Tests laufen lassen, sie bestehen; ganze Suite**

Run: `.venv/bin/python -m pytest tests/test_p6_docs.py tests/test_z_release.py -q -p no:cacheprovider && .venv/bin/python -m pytest -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add VERSION CHANGELOG.md README.md tests/test_p6_docs.py tests/test_z_release.py
git commit -m "chore: release 0.12.0

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01WUYs9mtP8aQCJCSLMEjKFc"
```

---

## Danach (nicht Teil dieses Plans)

- Push-Prüfung (`push_pruefung.py`), Tag, Image und Einspielen erst nach dem Go des Betreibers.
- Nach dem Einspielen: `IL` in die gespeicherten Ländercodes beider Sonarr-Instanzen aufnehmen; CatDog bleibt unüberwacht, bis die Seite Pre-filter zeigt, dass S6 und S7 seine Releases sperren.
