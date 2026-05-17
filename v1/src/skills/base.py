"""Skill base class.

A skill is a named, passive building block. It knows how to expand a
sub-goal into concrete steps; it does NOT make runtime decisions.

Forbidden in skill code:
  - platform.system() and any host-specific branching
  - reads of settings for capability detection (ghidra_home, ContainerSession.available())
  - iteration counts or "repeat up to N times" in step descriptions
  - flag prescriptions on steps (retry/escalate/defer)

Skills declare WHAT they do. Infrastructure decides HOW.
"""
from __future__ import annotations
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from planning.schema import Step

if TYPE_CHECKING:
    from skills.criteria import CompletionCriteria


@dataclass
class SkillContext:
    """Inputs available when expanding a skill into concrete steps."""
    original_query: str
    skill_args: dict
    starting_step_number: int


class Skill(ABC):
    """Base class for skills."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable identifier; used as tool='skill:<name>'."""
        ...

    @property
    @abstractmethod
    def intent(self) -> str:
        """One-paragraph description for the planner system prompt and SkillHintStage."""
        ...

    @property
    def pattern(self) -> re.Pattern | None:
        """Optional regex pattern for cheap hint matching. Not load-bearing."""
        return None

    @abstractmethod
    def expand(self, ctx: SkillContext) -> list[Step]:
        """Return concrete steps for this skill. Steps are numbered from ctx.starting_step_number."""
        ...

    @property
    def completion_criteria(self) -> "CompletionCriteria | None":
        """Optional completion criteria. None → ContinuationStage uses LLM judgment."""
        return None

    def continuation_steps(
        self,
        ctx: SkillContext,
        prior_results: list[Step],
    ) -> list[Step] | None:
        """Optional skill-replay for ContinuationStage LOOP path. Default: not loopable."""
        return None
