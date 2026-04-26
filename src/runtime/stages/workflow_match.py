"""WorkflowMatchStage — routes requests to pre-built workflow plans.

Tries three paths in order:
  1. Classifier hint  — semantic match from RoutingStage's classification
  2. Regex match      — pattern-based matching against all registered workflows
  3. Targeted fallback — dedicated LLM call when both above miss

A None plan after all three paths is not a failure — it means
PlanningStage should run the full planner.
"""
from __future__ import annotations
from runtime.classifier import WorkflowSelector
from runtime.pipeline_context import PipelineContext
from runtime.stage_base import Stage
from runtime.stage_result import StageResult, StageStatus
from runtime.utils import banner
from workflows.matcher import WorkflowMatcher
from logger import get_logger

logger = get_logger(__name__)


class WorkflowMatchStage(Stage):
    """Attempts to match the request to a pre-built workflow plan.

    Writes to context:
      - plan         (Plan | None — None means planner should run)
      - routing_path (str | None)
    """

    name = "WorkflowMatchStage"

    def __init__(
        self,
        workflow_matcher: WorkflowMatcher,
        workflow_selector: WorkflowSelector,
        spinner,
    ) -> None:
        self._workflow_matcher = workflow_matcher
        self._workflow_selector = workflow_selector
        self._spinner = spinner

    def run(self, context: PipelineContext) -> StageResult:
        # No-op for direct mode — all plan-path stages gate on mode == "plan".
        if context.classification is None or context.classification.mode != "plan":
            return StageResult(status=StageStatus.OK, updated_context=context)

        # If a plan was already set (shouldn't happen at this stage, but guard).
        if context.plan is not None:
            return StageResult(status=StageStatus.OK, updated_context=context)

        logger.info(banner("Workflow match"))

        user_message = context.user_message
        wf_descriptions = self._workflow_matcher.get_descriptions()
        plan = None
        routing_path = None
        workflow_name = None

        # ── 1. Classifier hint ───────────────────────────────────────
        hint = context.classification.workflow_hint
        if hint:
            workflow = self._workflow_matcher.get_by_name(hint)
            if workflow is not None:
                plan = workflow.try_match(user_message)
                if plan is not None:
                    routing_path = "classifier_hint"
                    workflow_name = hint
                    logger.info(
                        f"  workflow: classifier hint '{hint}' confirmed by pattern "
                        f"({len(plan.steps)} steps)"
                    )
                else:
                    try:
                        plan = workflow.generate_plan(None, user_message)
                        routing_path = "classifier_hint_direct"
                        workflow_name = hint
                        logger.info(
                            f"  workflow: classifier hint '{hint}' used directly "
                            f"(pattern miss) ({len(plan.steps)} steps)"
                        )
                    except Exception:
                        logger.info(
                            f"  workflow: classifier hint '{hint}' could not generate "
                            f"without regex match — falling through"
                        )

        # ── 2. Regex match ───────────────────────────────────────────
        if plan is None:
            matched = self._workflow_matcher.match_with_name(user_message)
            if matched is not None:
                workflow_name, plan = matched
                routing_path = "regex"

        # ── 3. Targeted fallback ─────────────────────────────────────
        if plan is None:
            logger.info(banner("Workflow fallback"))
            self._spinner.update("Routing...")
            fallback_name = self._workflow_selector.select(user_message, wf_descriptions)
            if fallback_name:
                workflow = self._workflow_matcher.get_by_name(fallback_name)
                if workflow is not None:
                    try:
                        plan = workflow.generate_plan(None, user_message)
                        routing_path = "fallback"
                        workflow_name = fallback_name
                        logger.info(
                            f"  workflow fallback matched '{fallback_name}' "
                            f"({len(plan.steps)} steps)"
                        )
                    except Exception:
                        logger.info(
                            f"  workflow fallback '{fallback_name}' could not generate "
                            f"without regex match — falling through"
                        )

        if plan is not None:
            logger.info(f"  workflow routing: {routing_path}")
        # plan=None is OK — PlanningStage will run the full planner

        context.plan = plan
        context.routing_path = routing_path
        context.workflow_name = workflow_name
        return StageResult(status=StageStatus.OK, updated_context=context)
