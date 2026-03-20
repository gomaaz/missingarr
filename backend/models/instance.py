from typing import Literal, Optional
from pydantic import BaseModel, HttpUrl, field_validator, model_validator


SearchOrder = Literal["random", "smart", "newest_first", "oldest_first"]
MissingMode = Literal["smart", "season_packs", "show_batch", "episode"]
UpgradeSource = Literal["wanted_list_only", "monitored_items_only", "both"]
InstanceType = Literal["sonarr", "radarr"]
ConnectionStatus = Literal["unknown", "online", "offline", "error"]


class InstanceBase(BaseModel):
    name: str
    type: InstanceType
    url: str
    enabled: bool = True
    search_missing_enabled: bool = True
    search_upgrades_enabled: bool = False
    interval_minutes: int = 15
    retry_hours: int = 1
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
        v = v.rstrip("/")
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("URL muss mit http:// oder https:// beginnen")
        return v

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Name darf nicht leer sein")
        if len(v) > 100:
            raise ValueError("Name darf max. 100 Zeichen haben")
        return v.strip()

    @field_validator("interval_minutes")
    @classmethod
    def validate_interval(cls, v: int) -> int:
        if v < 1:
            raise ValueError("Interval muss mindestens 1 Minute sein")
        if v > 10080:
            raise ValueError("Interval darf max. 10080 Minuten (1 Woche) sein")
        return v

    @field_validator("rate_cap")
    @classmethod
    def validate_rate_cap(cls, v: int) -> int:
        if v < 1:
            raise ValueError("Rate Cap muss mindestens 1 sein")
        if v > 1000:
            raise ValueError("Rate Cap darf max. 1000 sein")
        return v

    @field_validator("quiet_start", "quiet_end")
    @classmethod
    def validate_time(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        parts = v.split(":")
        if len(parts) != 2:
            raise ValueError("Zeit muss im Format HH:MM sein")
        try:
            h, m = int(parts[0]), int(parts[1])
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError
        except ValueError:
            raise ValueError("Zeit muss im Format HH:MM sein (00:00–23:59)")
        return f"{h:02d}:{m:02d}"

    @field_validator("missing_per_run", "upgrades_per_run")
    @classmethod
    def validate_per_run(cls, v: int) -> int:
        if v < 1:
            raise ValueError("Muss mindestens 1 sein")
        if v > 100:
            raise ValueError("Darf max. 100 sein")
        return v

    @field_validator("seconds_between_actions")
    @classmethod
    def validate_seconds(cls, v: int) -> int:
        if v < 0:
            raise ValueError("Darf nicht negativ sein")
        if v > 3600:
            raise ValueError("Darf max. 3600 Sekunden sein")
        return v


class InstanceCreate(InstanceBase):
    api_key: str

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("API-Key darf nicht leer sein")
        if len(v) < 8:
            raise ValueError("API-Key muss mindestens 8 Zeichen haben")
        return v.strip()


class InstanceUpdate(InstanceBase):
    api_key: Optional[str] = None

    @field_validator("api_key")
    @classmethod
    def validate_api_key_optional(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        v = v.strip()
        if len(v) < 8:
            raise ValueError("API-Key muss mindestens 8 Zeichen haben")
        return v


class InstanceRead(InstanceBase):
    id: int
    connection_status: ConnectionStatus = "unknown"
    last_seen_at: Optional[str] = None
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}
