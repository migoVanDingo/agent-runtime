"""Base class for workflow templates.

A workflow is a pre-defined plan pattern that can be matched against
user messages. When matched, it generates a Plan directly — bypassing
the LLM planner entirely.
"""

from __future__ import annotations
import re
from abc import ABC, abstractmethod
from planning.schema import Plan


class Workflow(ABC):
    """Base class for workflow templates."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name for logging."""
        ...

    @property
    @abstractmethod
    def intent(self) -> str:
        """1-2 sentence description of what user requests this workflow handles.
        Written for an LLM audience — used in classifier and fallback prompts."""
        ...

    @property
    @abstractmethod
    def pattern(self) -> re.Pattern:
        """Regex pattern to match against user messages."""
        ...

    @abstractmethod
    def generate_plan(self, match: re.Match | None, message: str) -> Plan:
        """Generate a Plan from the regex match.
        match may be None when invoked via classifier hint without a regex match.
        Workflows that require regex groups to extract parameters should raise
        ValueError in that case so the caller can fall through gracefully.
        """
        ...

    def try_match(self, message: str) -> Plan | None:
        """Try to match the message via regex. Returns a Plan or None."""
        m = self.pattern.search(message)
        if m:
            return self.generate_plan(m, message)
        return None
