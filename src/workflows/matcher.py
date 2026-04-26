"""Workflow matcher — tries to match user messages against workflow templates."""

from planning.schema import Plan
from workflows.base import Workflow
from workflows.templates import ALL_WORKFLOWS
from logger import get_logger

logger = get_logger(__name__)


class WorkflowMatcher:

    def match(self, message: str) -> Plan | None:
        """Try each workflow template in priority order. Returns first match or None."""
        result = self.match_with_name(message)
        return result[1] if result else None

    def match_with_name(self, message: str) -> tuple[str, Plan] | None:
        """Try each workflow template in priority order. Returns (name, plan) or None."""
        for workflow in ALL_WORKFLOWS:
            plan = workflow.try_match(message)
            if plan is not None:
                logger.info(f"  workflow match: {workflow.name} ({len(plan.steps)} steps)")
                return workflow.name, plan
        return None

    def get_by_name(self, name: str) -> Workflow | None:
        """Look up a workflow by its name. Returns None if not found."""
        for workflow in ALL_WORKFLOWS:
            if workflow.name == name:
                return workflow
        return None

    def get_descriptions(self) -> list[tuple[str, str]]:
        """Return (name, intent) pairs for all registered workflows.
        Used to inject workflow descriptions into classifier and fallback prompts."""
        return [(w.name, w.intent) for w in ALL_WORKFLOWS]
