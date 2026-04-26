"""ValidatorStage — logs the confirmed plan and guards against a None plan.

Phase 8 hardening: the ABORT reason now includes context.failure_reason so
the session log shows exactly why the planner gave up rather than just
"no plan available".
"""
from __future__ import annotations
from runtime.pipeline_context import PipelineContext
from runtime.stage_base import Stage
from runtime.stage_result import StageResult, StageStatus
from runtime.utils import banner
from logger import get_logger

logger = get_logger(__name__)


class ValidatorStage(Stage):
    """Logs the plan steps and aborts if no plan is available.

    Reads:  context.plan, context.failure_reason
    Writes: nothing (read-only gate)

    Phase 8: ABORT reason includes the last validation failure message so
    the fallback path is traceable in session logs.
    """

    name = "ValidatorStage"

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

        return StageResult(status=StageStatus.OK, updated_context=context)
