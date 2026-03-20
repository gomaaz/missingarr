from backend.agents.base import BaseAgent
from backend.skills.search_missing import SearchMissingSkill
from backend.skills.health_check import HealthCheckSkill


class SonarrAgent(BaseAgent):
    def build_skills(self):
        skills = []
        cfg = self.config
        if cfg.get("search_missing_enabled", True):
            skills.append(SearchMissingSkill())
        skills.append(HealthCheckSkill())
        return skills
