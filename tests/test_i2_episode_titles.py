"""0.12.0, Sonarr rule S6: the episode title in the release name against the
episode titles of the series (pure functions, invented series)."""
import pytest

from backend.checked_search import episode_titles as et
from backend.checked_search import sonarr_rules as sr
from backend.checked_search.settings import CheckedSearchSettings

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


def test_a_title_joined_differently_fits_by_its_squeezed_letters():
    # word by word only 'Magic' matches; squeezed, the whole target title stands in the name
    episodes = EPISODES[:2] + ((1, 3, "Snow-o-Rama Magic"),) + EPISODES[3:]
    assert et.judge("Paw.Friends.S01E03.Snoworama.Magic.Time.1080p.WEB-DL-GRP", TARGET, "Snow-o-Rama Magic",
                    SERIES, episodes) == et.FITS


def test_two_titles_are_not_rejected_when_one_of_them_fits_the_episode():
    episodes = EPISODES[:2] + ((1, 3, "The Long Winding Mountain Road Trip Home"),) + EPISODES[3:]
    release = "Paw.Friends.S01E03.Mountain.Road-Lost.Woods.Treasure.WEB-DL-GRP"
    assert et.judge(release, TARGET, "The Long Winding Mountain Road Trip Home", SERIES, episodes) == et.OTHER_WEAK


@pytest.mark.parametrize("release", [
    "Paw.Friends.S01E03.Spanish.Fry.1080p.WEB-DL-GRP",
    "Paw.Friends.S01E03.Real.Cats.Wear.Plaid.1080p.WEB-DL-GRP",
    "Paw.Friends.S01E03.Web.of.Lies.1080p.WEB-DL-GRP",
    "Paw.Friends.S01E03.The.Real.Deal.GERMAN.1080p.WEB-DL-GRP",
])
def test_a_title_that_starts_with_a_quality_word_is_no_untitled_release(release):
    # S6 does not compare it (the title part ends at the word), but S7 must not take it for untitled
    assert not et.is_untitled(release, SERIES)
    assert not et.needs_episode_list(release, SERIES)


@pytest.mark.parametrize("release", [
    "Paw.Friends.S01E03.GERMAN.DL.1080p.WEB-DL.x264-GRP",
    "Paw.Friends.S01E03.WEB.H264-GRP",
    "Paw.Friends.S01E03.AMZN.WEB-DL.DDP2.0.H.264-GRP",
    "Paw.Friends.S01E03.REPACK.GERMAN-GRP",
    "Paw.Friends.S01E03.Folge.3.GERMAN.1080p-GRP",
    # release tags behind the language: no title either
    "Paw.Friends.S01E03.GERMAN.DOKU.1080p.WEB.H264-GRP",
    "Paw.Friends.S01E03.German.DL.ANiME.1080p.WEB.H264-GRP",
    "Paw.Friends.S01E03.German.ML.EAC3.1080p.NF.WEB.H264-GRP",
    "Paw.Friends.S01E03.German.DL.NetflixHD.x264-GRP",
    "Paw.Friends.S01E03.Folge.3.GERMAN.DL.DTSMA.1080p.BDRiP.x264-GRP",
])
def test_quality_words_alone_leave_a_release_untitled(release):
    assert et.is_untitled(release, SERIES)


@pytest.mark.parametrize("release, title", [
    ("Paw.Friends.S01E03.Fun.Times.1080p.WEB-DL-GRP", TITLE),                       # other_weak
    ("Paw.Friends.S01E07.Lost.in.the.Woods.1080p.WEB-DL-GRP", "Episode 7"),         # target_generic
    ("Paw.Friends.S01E03.Surprise.1080p.WEB-DL-GRP", TITLE),                        # too_short
])
def test_evaluate_neither_rejects_nor_notes_the_other_categories(release, title):
    number = 7 if title == "Episode 7" else 3
    info = sr.EpisodeInfo(10, (SERIES,), 2019, season_number=1, episode_number=number, episode_title=title)
    verdict = sr.evaluate(info, release, None, sr.EpisodeParse(10, SERIES), CheckedSearchSettings(),
                          sr.RuleContext(episodes=EPISODES))
    assert (verdict.reasons, verdict.notes) == ((), ())
