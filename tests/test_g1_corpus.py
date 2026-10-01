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
