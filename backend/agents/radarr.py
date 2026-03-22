from backend.agents.base import BaseAgent
from backend.skills.search_missing import SearchMissingSkill
from backend.skills.search_upgrades import SearchUpgradesSkill
from backend.skills.health_check import HealthCheckSkill


class RadarrAgent(BaseAgent):
    def build_skills(self):
        # Always register all skills so force triggers work regardless of
        # which scheduled jobs are enabled. Scheduler jobs are separately
        # gated on the _enabled flags inside _run().
        return [SearchMissingSkill(), SearchUpgradesSkill(), HealthCheckSkill()]
