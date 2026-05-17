"""ValidatorStage — re-validates the expanded plan, logs it, guards against None.

PlanningStage runs the structural validator inline against the planner's raw
output (pre-expansion). Skills like ``skill:deep-disassembly`` are opaque at
that point, so the pre-expansion pass skips checks that depend on concrete
tools (e.g., the "must include a write_file step" rule when the query asks
for written output).

After SkillExpansionStage replaces each ``skill:*`` step with its concrete
expansion, this stage re-runs the validator with ``post_expansion=True`` so
those checks fire against the real expanded plan. If a skill genuinely
forgot to produce required steps, we abort to the fallback path with a
useful reason in the session log.

Phase 8 hardening: ABORT reason includes context.failure_reason so the
session log shows exactly why the planner gave up rather than just
"no plan available".
"""
from __future__ import annotations
from runtime.pipeline_context import PipelineContext
from runtime.stage_base import Stage
from runtime.stage_result import StageResult, StageStatus
from runtime.schema import ValidationStatus
from runtime.utils import banner
from runtime.validator import PlanValidator
from logger import get_logger

logger = get_logger(__name__)


class ValidatorStage(Stage):
    """Re-validate the plan post-expansion, log steps, abort on missing plan.

    Reads:  context.plan, context.failure_reason
    Writes: context.failure_reason (on validation failure)
    """

    name = "ValidatorStage"

    def __init__(self, validator: PlanValidator | None = None) -> None:
        # The validator is optional so existing pipelines that didn't wire it
        # in keep working (logging-only behaviour). When provided, we use it
        # to do the post-expansion structural check.
        self._validator = validator

    def run(self, context: PipelineContext) -> StageResult:
        # No-op for direct mode.
        if context.classification is None or context.classification.mode != "plan":
            return StageResult(status=StageStatus.OK, updated_context=context)

        if context.plan is None:
            reason = "No plan available after planning stage"
            if context.failure_reason:
                reason = f"{reason}: {context.failure_reason}"
            logger.info(f"  validator: {reason} — aborting to fallback")
            return StageResult(
                status=StageStatus.ABORT,
                updated_context=context,
                reason=reason,
            )

        plan = context.plan
        logger.info(banner(f"Plan ({len(plan.steps)} steps)"))
        for s in plan.steps:
            logger.info(f"  Step {s.step} [{s.action_type.value}] tool={s.tool}: {s.description}")

        # Post-expansion structural validation. ONLY re-checks rules that the
        # pre-expansion pass had to defer (e.g., write_file presence when the
        # plan included a skill step). Rules like max_steps are deliberately
        # NOT re-run here — skills naturally expand into many steps and the
        # pre-expansion cap is meant to guard planner sprawl, not post-skill
        # composition sprawl.
        if self._validator is not None:
            result = self._validator.validate_post_expansion(plan)
            if result.status == ValidationStatus.INVALID:
                reason = f"post-expansion validation failed: {result.feedback}"
                logger.info(f"  validator: {reason} — aborting to fallback")
                context.failure_reason = result.feedback
                return StageResult(
                    status=StageStatus.ABORT,
                    updated_context=context,
                    reason=reason,
                )

        return StageResult(status=StageStatus.OK, updated_context=context)
