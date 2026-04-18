"""Workflow matcher — tries to match user messages against workflow templates."""

from planning.schema import Plan
from workflows.templates import ALL_WORKFLOWS
from logger import get_logger

logger = get_logger(__name__)


class WorkflowMatcher:

    def match(self, message: str) -> Plan | None:
        """Try each workflow template in priority order. Returns first match or None."""
        for workflow in ALL_WORKFLOWS:
            plan = workflow.try_match(message)
            if plan is not None:
                logger.info(f"  workflow match: {workflow.name} ({len(plan.steps)} steps)")
                return plan
        return None
