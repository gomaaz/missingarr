"""Per-instance settings of the checked search.

Stored as one JSON column (instances.checked_search_settings). Keys that are
missing get their default, so a later rule can add a setting without a
migration. Bounds apply when a user saves; a stored value is never rejected
when it is read (from_stored falls back to the default for that key).
"""

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

CheckedSearchMode = Literal["off", "dry_run", "active"]
CHECKED_SEARCH_MODES: tuple[str, ...] = ("off", "dry_run", "active")

DEFAULT_COUNTRY_CODES: tuple[str, ...] = (
    "AU", "US", "UK", "GB", "DE", "CO", "CA", "NZ", "FR", "ES", "IT",
    "NL", "SE", "DK", "NO", "JP", "KR", "MX", "BR", "AR", "IN",
)

SETTING_BOUNDS: dict[str, tuple[int, int]] = {
    "release_timeout_seconds": (10, 600),
    "time_budget_minutes": (1, 1440),
    "dry_run_max_releases": (1, 1000),
    "search_again_after_days": (1, 365),
    "year_tolerance": (0, 10),
    "prefix_min_length": (1, 50),
    "word_min_core_words": (1, 10),
    "suffix_year_tolerance": (0, 10),
    "reject_days_before_air": (0, 36500),
    "note_days_before_air": (0, 36500),
}

GENERAL_FIELDS: tuple[str, ...] = (
    "release_timeout_seconds", "time_budget_minutes", "dry_run_max_releases", "search_again_after_days",
)
RADARR_FIELDS: tuple[str, ...] = (
    "year_tolerance", "count_release_dates", "veto_other_movie", "prefix_match", "prefix_min_length",
    "word_match", "word_min_core_words", "no_year_needs_exact", "skip_existing_file",
)
SONARR_FIELDS: tuple[str, ...] = (
    "veto_other_series", "check_suffix", "country_codes", "suffix_year_tolerance",
    "reject_days_before_air", "note_days_before_air", "skip_existing_file",
)

# Form labels (English like the rest of the UI). Tooltips: backend/tooltips.py, key "cs_<field>".
FIELD_LABELS: dict[str, str] = {
    "release_timeout_seconds": "Release search timeout (s)",
    "time_budget_minutes": "Time budget per run (min)",
    "dry_run_max_releases": "Dry run: max releases checked",
    "search_again_after_days": "Search again if still missing after (days)",
    "year_tolerance": "Year tolerance",
    "count_release_dates": "Count release dates as years",
    "veto_other_movie": "Veto when /parse names another movie",
    "prefix_match": "Prefix match",
    "prefix_min_length": "Prefix minimum length",
    "word_match": "Word match",
    "word_min_core_words": "Word match minimum core words",
    "no_year_needs_exact": "Releases without year need an exact title",
    "skip_existing_file": "Skip the release of the existing file",
    "veto_other_series": "Veto when /parse names another series",
    "check_suffix": "Check country/year suffix",
    "country_codes": "Country codes",
    "suffix_year_tolerance": "Suffix year tolerance",
    "reject_days_before_air": "Reject releases published this many days before air date",
    "note_days_before_air": "Note releases published this many days before air date",
}

_CODE = re.compile(r"^[A-Z]{2,3}$")


class CheckedSearchSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # General
    release_timeout_seconds: int = 120
    time_budget_minutes: int = 25
    dry_run_max_releases: int = 100
    # A title the checked search grabbed that is still in the wanted list after
    # this many days is searched again (takes the place of *arr's "Redownload
    # Failed from Interactive Search", which is switched off).
    search_again_after_days: int = 7
    # Radarr (V6)
    year_tolerance: int = 1
    count_release_dates: bool = True
    veto_other_movie: bool = True
    prefix_match: bool = True
    prefix_min_length: int = 6
    word_match: bool = True
    word_min_core_words: int = 2
    no_year_needs_exact: bool = True
    # Radarr rule 1a and Sonarr S4
    skip_existing_file: bool = True
    # Sonarr (S1-S3)
    veto_other_series: bool = True
    check_suffix: bool = True
    country_codes: list[str] = Field(default_factory=lambda: list(DEFAULT_COUNTRY_CODES))
    suffix_year_tolerance: int = 1
    reject_days_before_air: int = 365
    note_days_before_air: int = 14

    @field_validator("country_codes", mode="before")
    @classmethod
    def _codes(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            value = re.split(r"[\s,;]+", value)
        if not isinstance(value, (list, tuple)):
            raise ValueError("country_codes must be a list of two- or three-letter codes")
        codes: list[str] = []
        for item in value:
            code = str(item).strip().upper()
            if not code:
                continue
            if not _CODE.match(code):
                raise ValueError(f"country code '{code[:10]}' must be two or three letters")
            if code not in codes:
                codes.append(code)
        return codes

    @model_validator(mode="after")
    def _bounds(self):
        errors = [
            f"{name} must be between {low} and {high}"
            for name, (low, high) in SETTING_BOUNDS.items()
            if not low <= getattr(self, name) <= high
        ]
        if errors:
            raise ValueError("; ".join(errors))
        return self

    @classmethod
    def from_stored(cls, value) -> "CheckedSearchSettings":
        """Tolerant read of what the database holds: a dict, JSON text, None
        or garbage. Unknown keys are ignored; a key whose value no longer
        validates falls back to its default instead of failing the run."""
        if isinstance(value, str):
            try:
                value = json.loads(value) if value.strip() else {}
            except ValueError:
                value = {}
        if not isinstance(value, dict):
            value = {}
        accepted: dict = {}
        for key in cls.model_fields:
            if key not in value:
                continue
            try:
                cls.model_validate({**accepted, key: value[key]})
            except ValidationError:
                continue
            accepted[key] = value[key]
        return cls.model_validate(accepted)

    def rules_fingerprint(self, arr_type: str) -> str:
        """Fingerprint of everything that decides a dry-run verdict of this
        app: its rules and the dry-run limit (16 hex characters). A dry-run
        row counts for the round only under the fingerprint in force, so a
        changed rule has the titles checked again. Timeouts, the time budget
        and the days before a new search change no verdict."""
        names = ("dry_run_max_releases",) + (RADARR_FIELDS if arr_type == "radarr" else SONARR_FIELDS)
        payload = {}
        for name in names:
            value = getattr(self, name)
            payload[name] = sorted(value) if isinstance(value, list) else value
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
