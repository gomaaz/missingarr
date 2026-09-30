from typing import Literal, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, field_validator, model_validator


SearchOrder = Literal["random", "smart", "newest_first", "oldest_first"]
MissingMode = Literal["smart", "season_packs", "show_batch", "episode"]
UpgradeSource = Literal["wanted_list_only", "monitored_items_only", "both"]
InstanceType = Literal["sonarr", "radarr"]
ConnectionStatus = Literal["unknown", "online", "offline", "error"]

# Generous on purpose: Radarr runs live with rate_cap=999999999 and
# missing_per_run=600, and d8aadd8 once removed tighter limits that blocked
# such values. The bounds only stop values that break the scheduler (A1).
FIELD_BOUNDS: dict[str, tuple[int, int]] = {
    "interval_minutes": (1, 10080),
    "retry_hours": (0, 87600),
    "rate_window_minutes": (1, 525600),
    "rate_cap": (1, 1_000_000_000),
    "missing_per_run": (0, 100_000),
    "upgrades_per_run": (0, 100_000),
    "seconds_between_actions": (0, 3600),
    "hours_after_release": (0, 87600),
}
NAME_MAX_LENGTH = 100


class InstanceBase(BaseModel):
    name: str
    type: InstanceType
    url: str
    enabled: bool = True
    search_missing_enabled: bool = True
    search_upgrades_enabled: bool = False
    interval_minutes: int = 15
    retry_hours: int = 0
    rate_window_minutes: int = 60
    rate_cap: int = 25
    search_order: SearchOrder = "random"
    missing_mode: MissingMode = "episode"
    missing_per_run: int = 5
    upgrades_per_run: int = 1
    seconds_between_actions: int = 2
    hours_after_release: int = 9
    upgrade_source: UpgradeSource = "monitored_items_only"
    quiet_start: Optional[str] = None
    quiet_end: Optional[str] = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        # The path is appended to this base, so a '?' or '#' would let the
        # stored value retarget any path on the host (C4).
        if "?" in v or "#" in v:
            raise ValueError("URL must not contain '?' or '#'")
        parts = urlsplit(v)
        if parts.scheme not in ("http", "https"):
            raise ValueError("URL must start with http:// or https://")
        if not parts.hostname:
            raise ValueError("URL must contain a host name")
        if "@" in parts.netloc:
            raise ValueError("URL must not contain a user name or password")
        try:
            parts.port
        except ValueError:
            raise ValueError("URL contains an invalid port")
        return v

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Name must not be empty")
        if len(v) > NAME_MAX_LENGTH:
            raise ValueError(f"Name must not exceed {NAME_MAX_LENGTH} characters")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in v):
            raise ValueError("Name must not contain control characters")
        return v

    @field_validator("quiet_start", "quiet_end")
    @classmethod
    def validate_time(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        parts = v.split(":")
        if len(parts) != 2:
            raise ValueError("Time must be in HH:MM format")
        try:
            h, m = int(parts[0]), int(parts[1])
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError
        except ValueError:
            raise ValueError("Time must be in HH:MM format (00:00–23:59)")
        return f"{h:02d}:{m:02d}"


class _InstanceWrite(InstanceBase):
    """Bounds for values a user saves. Deliberately not on InstanceBase:
    rows already in the database are never rejected when they are read."""

    @model_validator(mode="after")
    def _check_bounds(self):
        errors = []
        for field, (low, high) in FIELD_BOUNDS.items():
            value = getattr(self, field)
            if not low <= value <= high:
                errors.append(f"{field} must be between {low} and {high}")
        if self.search_missing_enabled and self.missing_per_run < 1:
            errors.append("missing_per_run must be at least 1 while missing search is enabled")
        if self.search_upgrades_enabled and self.upgrades_per_run < 1:
            errors.append("upgrades_per_run must be at least 1 while upgrade search is enabled")
        if errors:
            raise ValueError("; ".join(errors))
        return self


class InstanceCreate(_InstanceWrite):
    api_key: str

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("API key must not be empty")
        return v.strip()


class InstanceUpdate(_InstanceWrite):
    api_key: Optional[str] = None

    @field_validator("api_key")
    @classmethod
    def validate_api_key_optional(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        return v.strip()


class InstanceRead(InstanceBase):
    id: int
    connection_status: ConnectionStatus = "unknown"
    last_seen_at: Optional[str] = None
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}
