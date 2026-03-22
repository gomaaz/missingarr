from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.agents.base import BaseAgent


class BaseSkill(ABC):
    name: str = ""

    @abstractmethod
    def execute(self, agent: "BaseAgent", force: bool = False) -> None:
        """Execute this skill using the provided agent context."""
        ...
