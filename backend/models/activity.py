from typing import Literal, Optional
from pydantic import BaseModel


LogLevel = Literal["info", "warn", "error", "debug"]
SkillName = Literal["search_missing", "search_upgrades", "health_check", "system"]
HistoryStatus = Literal["running", "success", "error"]


class ActivityEntry(BaseModel):
    id: int
    instance_id: Optional[int]
    instance_name: str
    level: LogLevel
    skill: Optional[str]
    message: str
    created_at: str

    model_config = {"from_attributes": True}


class SearchHistoryEntry(BaseModel):
    id: int
    instance_id: Optional[int]
    instance_name: str
    skill: Literal["search_missing", "search_upgrades"]
    wanted_count: int
    triggered_count: int
    started_at: str
    finished_at: Optional[str]
    status: HistoryStatus
    error_message: Optional[str]

    model_config = {"from_attributes": True}
