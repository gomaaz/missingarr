import copy

import pytest

from backend.checked_search.fingerprint import changes, fingerprint, fingerprints, short

# The shape of the API resources (Radarr 6.4 / Sonarr 4.0), display fields included.
PROFILE = {
    "id": 1, "name": "HD", "upgradeAllowed": True, "cutoff": 7, "minFormatScore": 0, "cutoffFormatScore": 100,
    "minUpgradeFormatScore": 1, "language": {"id": 1, "name": "English"},
    "items": [
        {"id": 0, "name": None, "quality": {"id": 3, "name": "WEBDL-1080p", "source": "webdl", "resolution": 1080},
         "items": [], "allowed": True},
        {"id": 1001, "name": "Bluray", "quality": None, "allowed": True, "items": [
            {"id": 0, "quality": {"id": 7, "name": "Bluray-1080p"}, "items": [], "allowed": True}]},
    ],
    "formatItems": [{"format": 3, "name": "German", "score": 100}, {"format": 4, "name": "Remux", "score": -10}],
}
FORMATS = [
    {"id": 3, "name": "German", "includeCustomFormatWhenRenaming": False, "specifications": [
        {"name": "German", "implementation": "LanguageSpecification", "implementationName": "Language",
         "infoLink": "https://wiki.example/custom-formats", "negate": False, "required": True,
         "fields": [{"order": 0, "name": "value", "label": "Language", "helpText": "", "value": 4,
                     "type": "select", "advanced": False,
                     "selectOptions": [{"value": 1, "name": "English"}, {"value": 4, "name": "German"}]}]},
        {"name": "Not English", "implementation": "LanguageSpecification", "implementationName": "Language",
         "negate": True, "required": False,
         "fields": [{"order": 0, "name": "value", "label": "Language", "value": 1, "type": "select"}]}]},
    {"id": 4, "name": "Remux", "specifications": [
        {"name": "Remux", "implementation": "QualityModifierSpecification", "implementationName": "Quality Modifier",
         "negate": False, "required": True, "fields": [{"name": "value", "label": "Quality Modifier", "value": 5}]}]},
]
RELEASE_PROFILES = [{"id": 1, "name": "German", "enabled": True, "required": ["German", "DL"], "ignored": [],
                     "indexerId": 0, "tags": [1, 2]}]


def base():
    return fingerprint(PROFILE, FORMATS, RELEASE_PROFILES)


def changed(mutate):
    """The fingerprint after mutate(profile, formats, release_profiles) on copies."""
    profile, formats, release_profiles = copy.deepcopy((PROFILE, FORMATS, RELEASE_PROFILES))
    mutate(profile, formats, release_profiles)
    return fingerprint(profile, formats, release_profiles)


def reorder(value):
    """The same content with every dict's keys in reverse order."""
    if isinstance(value, dict):
        return {key: reorder(value[key]) for key in reversed(list(value))}
    if isinstance(value, list):
        return [reorder(item) for item in value]
    return value


def test_fingerprint_is_16_hex_characters():
    value = base()
    assert len(value) == 16 and int(value, 16) >= 0


def test_same_content_in_another_key_order_gives_the_same_fingerprint():
    assert fingerprint(reorder(PROFILE), reorder(FORMATS), reorder(RELEASE_PROFILES)) == base()


def test_a_changed_score_changes_the_fingerprint():
    assert changed(lambda p, f, r: p["formatItems"][0].update(score=50)) != base()


@pytest.mark.parametrize("mutate", [
    lambda p, f, r: p.update(cutoffFormatScore=200),
    lambda p, f, r: p.update(minUpgradeFormatScore=5),
    lambda p, f, r: p.update(upgradeAllowed=False),
    lambda p, f, r: p.update(language={"id": 4, "name": "German"}),
    lambda p, f, r: p["items"][1]["items"][0].update(allowed=False),
    lambda p, f, r: f[0]["specifications"][1].update(negate=False),
    lambda p, f, r: f[1]["specifications"][0].update(required=False),
    lambda p, f, r: f[1]["specifications"][0]["fields"][0].update(value=6),
], ids=["cutoff score", "min upgrade score", "upgrades", "language", "allowed", "negate", "required", "field value"])
def test_every_rule_field_counts(mutate):
    assert changed(mutate) != base()


def test_the_quality_ranking_keeps_its_order():
    assert changed(lambda p, f, r: p["items"].reverse()) != base()


def test_display_texts_and_names_do_not_count():
    def relabel(p, f, r):
        p["name"] = "Renamed"
        p["formatItems"][0]["name"] = "Deutsch"
        p["items"][1]["name"] = "Disc"
        p["items"][0]["quality"]["name"] = "WEB 1080p"
        f[0]["name"] = "Deutsch"
        spec = f[0]["specifications"][0]
        spec.update(name="Deutsch", implementationName="Sprache", infoLink="https://wiki.example/other")
        spec["fields"][0].update(label="Sprache", helpText="Die Sprache", order=3,
                                 selectOptions=[{"value": 4, "name": "Deutsch"}, {"value": 99, "name": "Klingon"}])
        r[0]["name"] = "Deutsch"
    assert changed(relabel) == base()


def test_unordered_collections_do_not_count():
    def shuffle(p, f, r):
        p["formatItems"].reverse()
        f.reverse()
        f[1]["specifications"].reverse()        # the German format after f.reverse()
        r[0]["tags"].reverse()
        r[0]["required"].reverse()
    assert changed(shuffle) == base()
    other = {"id": 2, "enabled": True, "required": [], "ignored": ["CAM"], "indexerId": 0, "tags": []}
    assert fingerprint(PROFILE, FORMATS, [RELEASE_PROFILES[0], other]) == \
        fingerprint(PROFILE, FORMATS, [other, RELEASE_PROFILES[0]])


def test_terms_as_text_count_like_a_list():
    assert changed(lambda p, f, r: r[0].update(required="German, DL")) == base()


def test_a_recreated_release_profile_with_a_new_id_does_not_count():
    assert changed(lambda p, f, r: r[0].update(id=7)) == base()


def test_a_changed_custom_format_or_release_profile_changes_every_profile():
    other = {**PROFILE, "id": 2, "name": "UHD"}
    before = fingerprints([PROFILE, other], FORMATS, RELEASE_PROFILES)
    formats = copy.deepcopy(FORMATS)
    formats[0]["specifications"][0]["fields"][0]["value"] = 2
    after_format = fingerprints([PROFILE, other], formats, RELEASE_PROFILES)
    after_release = fingerprints([PROFILE, other], FORMATS, [{**RELEASE_PROFILES[0], "ignored": ["CAM"]}])
    for after in (after_format, after_release):
        assert after["1"] != before["1"] and after["2"] != before["2"]


def test_fingerprints_are_keyed_by_profile_id():
    other = {**PROFILE, "id": 2, "name": "UHD", "cutoff": 19}
    result = fingerprints([PROFILE, other, {"name": "no id"}, {"id": True}], FORMATS, [])
    assert set(result) == {"1", "2"}
    assert result["1"] != result["2"]
    # the id itself is no rule: two profiles with the same rules share a fingerprint
    assert fingerprints([PROFILE, {**PROFILE, "id": 3, "name": "Copy"}], FORMATS, [])["3"] == result["1"]


@pytest.mark.parametrize("answers", [({"id": 1}, [], []), ([PROFILE], None, []), ([PROFILE], [], "x")])
def test_unexpected_answers_are_refused(answers):
    with pytest.raises(ValueError):
        fingerprints(*answers)


def test_changes_names_only_profiles_that_existed_before():
    assert changes({"1": "aaa", "2": "bbb", "3": "ccc"}, {"1": "aaa", "2": "xxx", "4": "ddd"}) == [("2", "bbb", "xxx")]
    assert changes({}, {"1": "aaa"}) == []


def test_short():
    assert short("0123456789abcdef") == "01234567"
    assert short(None) == "—"
