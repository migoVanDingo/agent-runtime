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
    def pattern(self) -> re.Pattern:
        """Regex pattern to match against user messages."""
        ...

    @abstractmethod
    def generate_plan(self, match: re.Match, message: str) -> Plan:
        """Generate a Plan from the regex match."""
        ...

    def try_match(self, message: str) -> Plan | None:
        """Try to match the message. Returns a Plan or None."""
        m = self.pattern.search(message)
        if m:
            return self.generate_plan(m, message)
        return None
