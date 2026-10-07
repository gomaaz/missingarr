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

# Of those, the tokens that never start a title: resolution aside, sources
# and codecs. The others ('Real', 'Spanish', 'Web', 'Complete') also begin
# titles ('Spanish.Fry', 'Web.of.Lies'); S7 looks past them.
_HARD_TOKENS = frozenset("""
4k uhd webdl web-dl webrip web-rip webhd hdtv pdtv sdtv dsrip satrip dvbrip bluray blu-ray bdrip brrip bdremux
remux dvdrip dvd5 dvd9 dvdr hdrip tvrip vhsrip hevc avc x264 x265 h264 h265 xvid divx
""".split())

# Release tags that are no stop tokens of the title part (S6 was measured
# without them) but never a title either: S7 looks past them as well
# ('GERMAN.DOKU.1080p', 'German.DL.ANiME.1080p' stay untitled).
_TAG_WORDS = frozenset("doku docu anime ml fs dtsma disneyhd netflixhd amazonhd hdtvrip".split())

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


def _is_hard_token(token: str) -> bool:
    low = token.lower()
    head = low.split("-")[0]
    return any(_RESOLUTION.match(t) or t in _HARD_TOKENS for t in (low, head) if t)


def _words_before_quality(release: str) -> str:
    """Words between SxxEyy and the first source or codec token, without the
    other quality and language tokens: what could still be a title when the
    title part ends early ('Spanish.Fry.1080p' -> 'Fry')."""
    match = _SXXEYY.search(release or "")
    tokens = [t for t in _TOKEN_SPLIT.split(release[match.end():]) if t] if match else []
    taken: list[str] = []
    hit_hard = False
    for token in tokens:
        if _is_hard_token(token):
            hit_hard = True
            break
        if not _is_stop_token(token) and token.lower().split("-")[0] not in _TAG_WORDS:
            taken.append(token)
    if not hit_hard and taken and "-" in taken[-1]:
        taken[-1] = taken[-1].rsplit("-", 1)[0]
    return " ".join(taken)


def is_untitled(release: str, series_title: str) -> bool:
    """SxxEyy without any title, or with a placeholder only (S7). A title
    that begins with a quality or language word ('Spanish.Fry') is a title:
    S6 does not compare it, and S7 leaves it alone."""
    part = release_title_part(release)
    if part is None:
        return False
    if part and not is_generic(part) and _title_words(part, series_title):
        return False
    rest = _words_before_quality(release)
    return is_generic(rest) or not _title_words(rest, series_title)


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
