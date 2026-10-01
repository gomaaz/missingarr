import pytest
from pydantic import ValidationError

from backend.checked_search.settings import CheckedSearchSettings
from backend.models.instance import CHECKED_SEARCH_MODE_MESSAGE, InstanceCreate, InstanceUpdate

BASE = {"name": "Sonarr", "type": "sonarr", "url": "http://sonarr:8989", "api_key": "k" * 32}


def test_create_defaults_to_off_with_default_settings():
    model = InstanceCreate(**BASE)
    assert model.checked_search == "off"
    assert model.checked_search_settings == CheckedSearchSettings()
    assert model.search_again_after_profile_change is True


def test_update_leaves_the_new_fields_unset():
    model = InstanceUpdate(**{k: v for k, v in BASE.items() if k != "api_key"})
    assert model.checked_search is None and model.checked_search_settings is None
    assert model.search_again_after_profile_change is None


@pytest.mark.parametrize("mode", ["smart", "season_packs", "show_batch"])
@pytest.mark.parametrize("checked", ["dry_run", "active"])
def test_sonarr_with_checked_search_allows_only_episodes(mode, checked):
    with pytest.raises(ValidationError) as info:
        InstanceCreate(**BASE, missing_mode=mode, checked_search=checked)
    assert CHECKED_SEARCH_MODE_MESSAGE in info.value.errors()[0]["msg"]


def test_episode_mode_and_radarr_are_fine():
    InstanceCreate(**BASE, missing_mode="episode", checked_search="active")
    InstanceCreate(**{**BASE, "type": "radarr"}, missing_mode="show_batch", checked_search="active")
    InstanceCreate(**BASE, missing_mode="show_batch", checked_search="off")


def test_settings_bounds_reach_the_user():
    with pytest.raises(ValidationError) as info:
        InstanceCreate(**BASE, checked_search_settings={"release_timeout_seconds": 5})
    assert "release_timeout_seconds must be between 10 and 600" in str(info.value)


def test_unknown_mode_is_refused():
    with pytest.raises(ValidationError):
        InstanceCreate(**BASE, checked_search="sometimes")
