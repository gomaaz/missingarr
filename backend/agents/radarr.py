from backend.agents.base import BaseAgent
from backend.skills.search_missing import SearchMissingSkill
from backend.skills.search_upgrades import SearchUpgradesSkill
from backend.skills.health_check import HealthCheckSkill


class RadarrAgent(BaseAgent):
    def build_skills(self):
        skills = []
        cfg = self.config
        if cfg.get("search_missing_enabled", True):
            skills.append(SearchMissingSkill())
        if cfg.get("search_upgrades_enabled", False):
            skills.append(SearchUpgradesSkill())
        skills.append(HealthCheckSkill())
        return skills
