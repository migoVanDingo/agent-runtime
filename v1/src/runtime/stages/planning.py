"""PlanningStage — runs the full LLM planner.

Runs in plan mode. The planner is the sole plan author;
SkillHintStage provides an advisory hint the planner may use.

Parse-failure retry policy lives here (not in Planner) per 0079 / 0086b:
the runtime decides whether and how many times to re-attempt.  The planner
returns Plan | PlanParseFailure; this stage caps retries at
config.planning.max_parse_retries (default 1 — same as the old behavior).

Validation is folded in here because the retry-on-invalid loop requires
re-running the planner. The pipeline RETRY mechanism re-runs this stage, so
returning RETRY here will cause the planner to be called again with the
validation feedback appended to user_message.

On failure (exhausted parse retries, or plan stays invalid after validation
retries) returns ABORT so the pipeline falls back to DirectExecutionStage.
"""
from __future__ import annotations
from planning.planner import Planner, PlanParseFailure
from runtime.pipeline_context import PipelineContext
from runtime.schema import ValidationStatus
from runtime.stage_base import Stage
from runtime.stage_result import StageResult, StageStatus
from runtime.utils import banner
from runtime.validator import PlanValidator
from app_config import config
from logger import get_logger

logger = get_logger(__name__)


class PlanningStage(Stage):
    """Runs the full LLM planner and validates the resulting plan.

    Reads:  context.user_message, context.packed_messages, context.failure_reason
    Writes: context.plan (always set on OK; never None on OK)

    Returns RETRY if the plan is structurally invalid — the pipeline runner
    will re-run this stage with context.failure_reason injected so the planner
    can self-correct. Returns ABORT if the planner exhausts parse retries or if
    the plan is still invalid after max validation retries.
    """

    name = "PlanningStage"

    def __init__(self, planner: Planner, validator: PlanValidator) -> None:
        self._planner = planner
        self._validator = validator

    def run(self, context: PipelineContext) -> StageResult:
        # No-op for direct mode.
        if context.classification is None or context.classification.mode != "plan":
            return StageResult(status=StageStatus.OK, updated_context=context)

        logger.info(banner("Planning"))

        user_message = context.user_message
        # On pipeline-level retry, append the validation feedback so the planner
        # can self-correct on the next stage invocation.
        if context.failure_reason:
            user_message = user_message + "\n\nPrevious plan was invalid:\n" + context.failure_reason

        # ── Parse-failure retry loop (stage owns this policy per 0086b) ──────
        # The planner is called once per iteration; on PlanParseFailure the
        # previous error is fed back as a schema_correction_hint so the model
        # knows what it produced wrong.  max_parse_retries=1 reproduces the old
        # "retry once on invalid JSON" behavior exactly.
        max_parse_retries = config.planning.max_parse_retries
        hint: str | None = None
        outcome: Plan | PlanParseFailure | None = None
        for attempt in range(max_parse_retries + 1):
            outcome = self._planner.plan(
                user_message,
                messages=context.packed_messages,
                skill_hint=context.skill_hint,
                schema_correction_hint=hint,
            )
            if not isinstance(outcome, PlanParseFailure):
                break
            logger.info(
                f"  PlanningStage: parse failure on attempt {attempt + 1}: {outcome.error}"
            )
            hint = outcome.error
        else:
            # Exhausted retries — hand control back to the pipeline
            reason = f"plan parse failure after {max_parse_retries + 1} attempt(s): {outcome.error}"
            logger.info(f"  PlanningStage: {reason} — aborting to fallback")
            return StageResult(
                status=StageStatus.ABORT,
                updated_context=context,
                reason=reason,
            )

        plan = outcome  # type: ignore[assignment]  # guaranteed Plan here

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
        # Mint a plan_id so execution events correlate back to this plan.
        if context.identity is not None:
            context.identity = context.identity.for_plan()
        return StageResult(status=StageStatus.OK, updated_context=context)
