"""CouncilStage — adversarial plan critic review before execution.

Workflow-generated plans bypass the council entirely — they are pre-designed
and validated by intent, not hallucinated by the planner.

Dynamic scaling: low=0 councillors (skip), moderate=1, high=N (full pool).
On CHALLENGED with non-justify suggestions: sends challenges to planner for
revision. If revision fails or is invalid: strips challenged steps.
If all steps are stripped: ABORT (broken plan should not execute).
"""
from __future__ import annotations
from planning.planner import Planner
from planning.schema import Plan
from runtime.critic import PlanCritic
from runtime.pipeline_context import PipelineContext
from runtime.schema import CriticVerdict, ValidationStatus
from runtime.stage_base import Stage
from runtime.stage_result import StageResult, StageStatus
from runtime.utils import banner
from runtime.validator import PlanValidator
from app_config import config
from logger import get_logger

logger = get_logger(__name__)

# Routing paths that indicate a workflow-generated plan (bypass council).
_WORKFLOW_PATHS = {"classifier_hint", "classifier_hint_direct", "regex", "fallback"}


def _strip_challenged_steps(plan: Plan, critic_result) -> Plan | None:
    """Remove steps the critic suggested dropping or replacing.

    'justify' challenges are kept (benefit of the doubt).
    Returns the stripped plan, or None if no steps remain.
    """
    if not critic_result.challenges:
        return plan

    drop_steps = set()
    for c in critic_result.challenges:
        if c.suggestion in ("drop", "replace"):
            drop_steps.add(c.step)
        # "justify" — keep (benefit of the doubt)

    if not drop_steps:
        return plan

    kept = [s for s in plan.steps if s.step not in drop_steps]
    if not kept:
        logger.info("  all steps stripped by critic — plan is empty")
        return None

    # Re-number steps sequentially
    for i, s in enumerate(kept, 1):
        s.step = i

    logger.info(f"  stripped {len(drop_steps)} challenged step(s), {len(kept)} remaining")
    plan.steps = kept
    return plan


class CouncilStage(Stage):
    """Adversarial critic review of the plan.

    Reads:  context.plan, context.routing_path, context.classification.risk
    Writes: context.plan (may be revised or stripped)

    Returns ABORT if all steps are stripped (broken plan must not execute).
    """

    name = "CouncilStage"

    def __init__(
        self,
        critic: PlanCritic,
        planner: Planner,
        validator: PlanValidator,
        spinner,
    ) -> None:
        self._critic = critic
        self._planner = planner
        self._validator = validator
        self._spinner = spinner

    def run(self, context: PipelineContext) -> StageResult:
        # No-op for direct mode.
        if context.classification is None or context.classification.mode != "plan":
            return StageResult(status=StageStatus.OK, updated_context=context)

        if context.plan is None:
            return StageResult(status=StageStatus.OK, updated_context=context)

        # Workflow-generated plans bypass the critic.
        if context.routing_path in _WORKFLOW_PATHS:
            logger.info(banner("Plan critic"))
            logger.info(f"  critic: skipped (workflow-generated plan via '{context.routing_path}')")
            return StageResult(status=StageStatus.OK, updated_context=context)

        risk = context.classification.risk
        scaling = config.runtime.council.dynamic_scaling
        n_councillors = scaling.get(risk, 1)
        pool = config.runtime.council.councillors
        active = pool[:n_councillors]

        logger.info(banner("Plan critic"))

        if not active:
            logger.info(f"  critic: skipped ({risk} risk → 0 councillors)")
            return StageResult(status=StageStatus.OK, updated_context=context)

        labels = [c.label for c in active]
        logger.info(f"  critic: {risk} risk → {len(active)} councillor(s): {labels}")
        self._spinner.update("Reviewing plan...")

        plan = context.plan
        critic_result = self._critic.review(plan, active_councillors=active)

        if critic_result is None or critic_result.verdict != CriticVerdict.CHALLENGED:
            return StageResult(status=StageStatus.OK, updated_context=context)

        # All challenges are JUSTIFY — structurally sound, skip expensive revision.
        all_justify = all(
            (c.suggestion or "justify") == "justify"
            for c in (critic_result.challenges or [])
        )
        if all_justify:
            logger.info("  all challenges are JUSTIFY — skipping revision (plan unchanged)")
            return StageResult(status=StageStatus.OK, updated_context=context)

        # Send challenges to planner for revision.
        logger.info("  sending challenges to planner for revision")
        challenges_text = self._critic.format_challenges(critic_result)
        revised = self._planner.revise(plan, challenges_text)

        if revised is not None:
            for s in revised.steps:
                logger.info(f"  Step {s.step} [{s.action_type.value}] tool={s.tool}: {s.description}")
            logger.info(banner("Plan validation (post-critic)"))
            validation = self._validator.validate(revised)
            if validation.status == ValidationStatus.VALID:
                plan = revised
            else:
                logger.info("  revised plan failed validation — stripping challenged steps")
                plan = _strip_challenged_steps(plan, critic_result)
        else:
            logger.info("  planner revision returned None — stripping challenged steps")
            plan = _strip_challenged_steps(plan, critic_result)

        if plan is None:
            return StageResult(
                status=StageStatus.ABORT,
                updated_context=context,
                reason="All plan steps stripped by council critic",
            )

        # Phase 8 coherence check: if requires_synthesis but every remaining
        # step is CONVERSATION type (no data-gathering steps), synthesis has
        # nothing to work with — the plan is structurally incoherent.
        if plan.requires_synthesis:
            from planning.schema import ActionType
            data_steps = [
                s for s in plan.steps
                if s.action_type != ActionType.CONVERSATION
            ]
            if not data_steps:
                logger.info(
                    "  council: plan requires synthesis but has no data-gathering steps "
                    "after stripping — aborting to fallback"
                )
                return StageResult(
                    status=StageStatus.ABORT,
                    updated_context=context,
                    reason="Plan stripped to synthesis-only: no data-gathering steps remain",
                )

        logger.info(banner(f"Plan ready ({len(plan.steps)} steps)"))
        context.plan = plan
        return StageResult(status=StageStatus.OK, updated_context=context)
