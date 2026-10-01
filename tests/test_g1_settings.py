import json

import pytest
from pydantic import ValidationError

from backend.checked_search.settings import (
    CHECKED_SEARCH_MODES, DEFAULT_COUNTRY_CODES, FIELD_LABELS, GENERAL_FIELDS, RADARR_FIELDS,
    SETTING_BOUNDS, SONARR_FIELDS, CheckedSearchSettings,
)


def test_defaults_match_the_spec():
    s = CheckedSearchSettings()
    assert (s.release_timeout_seconds, s.time_budget_minutes, s.dry_run_max_releases) == (120, 25, 100)
    assert s.search_again_after_days == 7
    assert (s.year_tolerance, s.prefix_min_length, s.word_min_core_words) == (1, 6, 2)
    assert s.count_release_dates and s.veto_other_movie and s.prefix_match and s.word_match
    assert s.no_year_needs_exact and s.skip_existing_file
    assert s.veto_other_series and s.check_suffix
    assert s.country_codes == list(DEFAULT_COUNTRY_CODES)
    assert (s.suffix_year_tolerance, s.reject_days_before_air, s.note_days_before_air) == (1, 365, 14)
    assert CHECKED_SEARCH_MODES == ("off", "dry_run", "active")


def test_every_field_belongs_to_a_group_and_has_a_label():
    grouped = set(GENERAL_FIELDS) | set(RADARR_FIELDS) | set(SONARR_FIELDS)
    assert grouped == set(CheckedSearchSettings.model_fields)
    assert set(FIELD_LABELS) == grouped
    assert set(SETTING_BOUNDS) <= grouped


def test_bounds_are_enforced_on_save():
    with pytest.raises(ValidationError) as info:
        CheckedSearchSettings(release_timeout_seconds=5, time_budget_minutes=2000, search_again_after_days=0)
    message = info.value.errors()[0]["msg"]
    assert "release_timeout_seconds must be between 10 and 600" in message
    assert "time_budget_minutes must be between 1 and 1440" in message
    assert "search_again_after_days must be between 1 and 365" in message


def test_rules_fingerprint_follows_only_what_decides_a_verdict():
    base = CheckedSearchSettings()
    radarr, sonarr = base.rules_fingerprint("radarr"), base.rules_fingerprint("sonarr")
    assert len(radarr) == 16 and radarr != sonarr
    # a rule of the app or the dry-run limit: another fingerprint
    assert CheckedSearchSettings(year_tolerance=2).rules_fingerprint("radarr") != radarr
    assert CheckedSearchSettings(skip_existing_file=False).rules_fingerprint("sonarr") != sonarr
    assert CheckedSearchSettings(dry_run_max_releases=5).rules_fingerprint("sonarr") != sonarr
    # a rule of the other app, timeouts, the budget, the days: the same
    assert CheckedSearchSettings(check_suffix=False).rules_fingerprint("radarr") == radarr
    assert CheckedSearchSettings(release_timeout_seconds=300, time_budget_minutes=5,
                                 search_again_after_days=30).rules_fingerprint("radarr") == radarr
    # the country codes are a set
    assert CheckedSearchSettings(country_codes=["US", "DE"]).rules_fingerprint("sonarr") == \
        CheckedSearchSettings(country_codes=["DE", "US"]).rules_fingerprint("sonarr")


def test_country_codes_accept_text_and_reject_garbage():
    assert CheckedSearchSettings(country_codes="au, us;de  us").country_codes == ["AU", "US", "DE"]
    with pytest.raises(ValidationError):
        CheckedSearchSettings(country_codes=["U.S."])


def test_from_stored_fills_missing_keys_with_defaults():
    s = CheckedSearchSettings.from_stored({"year_tolerance": 2})
    assert s.year_tolerance == 2
    assert s.release_timeout_seconds == 120


def test_from_stored_reads_json_text_and_ignores_unknown_keys():
    s = CheckedSearchSettings.from_stored(json.dumps({"prefix_match": False, "rule_from_the_future": 1}))
    assert s.prefix_match is False


def test_from_stored_never_fails():
    assert CheckedSearchSettings.from_stored("not json") == CheckedSearchSettings()
    assert CheckedSearchSettings.from_stored(None) == CheckedSearchSettings()
    assert CheckedSearchSettings.from_stored([1, 2]) == CheckedSearchSettings()
    # an out-of-bounds value falls back to its default, the rest is kept
    s = CheckedSearchSettings.from_stored({"release_timeout_seconds": 1, "year_tolerance": 3})
    assert (s.release_timeout_seconds, s.year_tolerance) == (120, 3)
