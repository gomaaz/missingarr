from backend.agents.base import BaseAgent
from backend.skills.search_missing import SearchMissingSkill
from backend.skills.search_upgrades import SearchUpgradesSkill
from backend.skills.health_check import HealthCheckSkill
from backend.skills.verify_commands import VerifyCommandsSkill


class SonarrAgent(BaseAgent):
    def build_skills(self):
        # Always register all skills: force triggers need them, and the
        # scheduler registers a job for every skill and gates it on the
        # enable flags in _run_skill.
        return [SearchMissingSkill(), SearchUpgradesSkill(), HealthCheckSkill(), VerifyCommandsSkill()]
