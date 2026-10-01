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
