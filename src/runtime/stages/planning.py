"""PlanningStage — runs the full LLM planner when no workflow matched.

Runs only in plan mode when context.plan is still None after WorkflowMatchStage.

Validation is folded in here because the retry-on-invalid loop requires
re-running the planner. The pipeline RETRY mechanism re-runs this stage, so
returning RETRY here will cause the planner to be called again with the
validation feedback appended to user_message.

On failure (planner returns None, or plan stays invalid after retries) returns
ABORT so the pipeline falls back to DirectExecutionStage.
"""
from __future__ import annotations
from planning.planner import Planner
from runtime.pipeline_context import PipelineContext
from runtime.schema import ValidationStatus
from runtime.stage_base import Stage
from runtime.stage_result import StageResult, StageStatus
from runtime.utils import banner
from runtime.validator import PlanValidator
from logger import get_logger

logger = get_logger(__name__)


class PlanningStage(Stage):
    """Runs the full LLM planner and validates the resulting plan.

    Reads:  context.user_message, context.packed_messages, context.failure_reason
    Writes: context.plan (always set on OK; never None on OK)

    Returns RETRY if the plan is structurally invalid — the pipeline runner
    will re-run this stage with context.failure_reason injected so the planner
    can self-correct. Returns ABORT if the planner returns None or if the plan
    is still invalid after max retries.
    """

    name = "PlanningStage"

    def __init__(self, planner: Planner, validator: PlanValidator, spinner) -> None:
        self._planner = planner
        self._validator = validator
        self._spinner = spinner

    def run(self, context: PipelineContext) -> StageResult:
        # No-op for direct mode.
        if context.classification is None or context.classification.mode != "plan":
            return StageResult(status=StageStatus.OK, updated_context=context)

        # No-op if a workflow already produced a plan.
        if context.plan is not None:
            return StageResult(status=StageStatus.OK, updated_context=context)

        logger.info(banner("Planning"))
        self._spinner.update("Planning...")

        user_message = context.user_message
        # On retry, append the validation feedback so the planner can self-correct.
        if context.failure_reason:
            user_message = user_message + "\n\nPrevious plan was invalid:\n" + context.failure_reason

        plan = self._planner.plan(user_message, messages=context.packed_messages)

        if plan is None:
            logger.info("  planner returned None — aborting to fallback")
            return StageResult(
                status=StageStatus.ABORT,
                updated_context=context,
                reason="Planner returned None",
            )

        # Validate before committing the plan to context.
        retry_label = " (retry)" if context.failure_reason else ""
        logger.info(banner(f"Plan validation{retry_label}"))
        validation = self._validator.validate(plan)

        if validation.status == ValidationStatus.INVALID:
            logger.info(f"  validation failed: {validation.feedback}")
            return StageResult(
                status=StageStatus.RETRY,
                updated_context=context,
                reason=validation.feedback or "Plan validation failed",
            )

        plan.risk = context.classification.risk
        logger.info(f"  planner produced {len(plan.steps)}-step plan (valid)")

        context.plan = plan
        return StageResult(status=StageStatus.OK, updated_context=context)
