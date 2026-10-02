import pytest
from pydantic import ValidationError

from backend.models.instance import FIELD_BOUNDS, InstanceCreate, InstanceUpdate

BASE = {"name": "Sonarr", "type": "sonarr", "url": "http://sonarr:8989", "api_key": "k" * 32}

BIG_SONARR = dict(interval_minutes=60, missing_mode="episode", missing_per_run=4,
                   rate_cap=300, rate_window_minutes=60, search_upgrades_enabled=False,
                   retry_hours=0)
BIG_RADARR = dict(type="radarr", interval_minutes=30, missing_per_run=600,
                   rate_cap=999_999_999, search_upgrades_enabled=False, retry_hours=0,
                   upgrades_per_run=0)


def make(model=InstanceCreate, **fields):
    return model(**{**BASE, **fields})


@pytest.mark.parametrize("model", [InstanceCreate, InstanceUpdate])
def test_big_setups_stay_valid(model):
    make(model, **BIG_SONARR)
    make(model, **BIG_RADARR)


@pytest.mark.parametrize("field,value", [
    ("interval_minutes", 0), ("interval_minutes", -5), ("interval_minutes", 10081),
    ("interval_minutes", 10_000_000_000), ("rate_cap", 0), ("rate_cap", 1_000_000_001),
    ("rate_window_minutes", 0), ("retry_hours", -1), ("seconds_between_actions", -1),
    ("hours_after_release", -1), ("missing_per_run", -3), ("upgrades_per_run", -1),
])
@pytest.mark.parametrize("model", [InstanceCreate, InstanceUpdate])
def test_out_of_range_values_are_rejected(model, field, value):
    with pytest.raises(ValidationError, match=field):
        make(model, **{field: value})


def test_per_run_zero_is_only_allowed_while_the_skill_is_off():
    make(search_upgrades_enabled=False, upgrades_per_run=0)
    make(search_missing_enabled=False, missing_per_run=0)
    with pytest.raises(ValidationError, match="upgrades_per_run"):
        make(search_upgrades_enabled=True, upgrades_per_run=0)
    with pytest.raises(ValidationError, match="missing_per_run"):
        make(search_missing_enabled=True, missing_per_run=0)


def test_every_bound_names_a_real_field():
    for field in FIELD_BOUNDS:
        assert field in InstanceCreate.model_fields


@pytest.mark.parametrize("url", [
    "http://sonarr:8989", "http://10.0.0.5:8989/sonarr", "https://127.0.0.1:7878/",
    "http://[::1]:8989", "http://192.168.1.10:7878",
])
def test_private_and_local_urls_stay_allowed(url):
    assert make(url=url).url == url.rstrip("/")


@pytest.mark.parametrize("url", [
    "http://sonarr:8989/?x=", "http://sonarr:8989/api?", "http://sonarr:8989#frag",
    "http://user:pw@sonarr:8989", "http://user@sonarr:8989", "ftp://sonarr",
    "http://", "http://sonarr:99999",
])
def test_urls_with_query_fragment_userinfo_or_bad_port_are_rejected(url):
    with pytest.raises(ValidationError):
        make(url=url)


@pytest.mark.parametrize("name", ["bad\x00name", "tab\tname", "x" * 101])
def test_names_with_control_characters_or_too_long_are_rejected(name):
    with pytest.raises(ValidationError):
        make(name=name)


def test_names_with_quotes_and_brackets_stay_allowed():
    assert make(name="Alice's \"Sonarr\" <4K>").name == "Alice's \"Sonarr\" <4K>"
