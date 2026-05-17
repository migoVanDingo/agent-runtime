"""Skill registry — replaces workflows.matcher.WorkflowMatcher."""
from __future__ import annotations
from skills.base import Skill
from logger import get_logger

logger = get_logger(__name__)


class SkillRegistry:

    def __init__(self, skills: list[Skill] | None = None) -> None:
        from skills.implementations import ALL_SKILLS
        _skills = skills if skills is not None else ALL_SKILLS
        self._by_name = {s.name: s for s in _skills}

    def register(self, skill: Skill) -> None:
        """Register a skill (used by the plugin loader)."""
        self._by_name[skill.name] = skill
        logger.info(f"Registered skill: {skill.name}")

    def get(self, name: str) -> Skill | None:
        return self._by_name.get(name)

    def names(self) -> list[str]:
        return list(self._by_name.keys())

    def descriptions(self) -> list[tuple[str, str]]:
        return [(s.name, s.intent) for s in self._by_name.values()]

    def get_descriptions(self) -> list[tuple[str, str]]:
        """Alias kept for compatibility with RoutingStage call sites."""
        return self.descriptions()
