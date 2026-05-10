"""CouncilStage — adversarial plan critic review before execution.

Council scrutiny is based on plan structure and risk — not on how the plan
was produced. High-risk plans and structurally complex plans run the council.

Dynamic scaling: low=0 councillors (skip), moderate=1, high=N (full pool).
On CHALLENGED with non-justify suggestions: sends challenges to planner for
revision. If revision fails or is invalid: strips challenged steps.
If all steps are stripped: ABORT (broken plan should not execute).
"""
from __future__ import annotations
from planning.planner import Planner
from planning.schema import Plan, ActionType
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

_DESTRUCTIVE_ACTION_TYPES = {ActionType.SHELL, ActionType.FILE_IO}


def _plan_complexity(plan: Plan) -> int:
    """Heuristic structural complexity score for council-bypass decision."""
    score = len(plan.steps)
    for s in plan.steps:
        if s.action_type in _DESTRUCTIVE_ACTION_TYPES:
            score += 2
        if s.tool == "bash_exec":
            score += 1
    return score


def _should_run_council(plan: Plan, risk: str, threshold: int) -> tuple[bool, str]:
    """Return (run, reason). Council scrutiny depends on the plan, not provenance."""
    if risk == "high":
        return True, "high risk"
    score = _plan_complexity(plan)
    if score >= threshold:
        return True, f"complexity score {score} >= threshold {threshold}"
    if risk == "moderate" and any(s.action_type in _DESTRUCTIVE_ACTION_TYPES for s in plan.steps):
        return True, "moderate risk + destructive action types present"
    return False, (
        f"skip: risk={risk}, complexity={score} < {threshold}, "
        f"no destructive-types-on-moderate match"
    )


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

    Reads:  context.plan, context.classification.risk
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
        skill_expansion_stage=None,
    ) -> None:
        self._critic = critic
        self._planner = planner
        self._validator = validator
        self._spinner = spinner
        self._skill_expansion = skill_expansion_stage

    def run(self, context: PipelineContext) -> StageResult:
        # No-op for direct mode.
        if context.classification is None or context.classification.mode != "plan":
            return StageResult(status=StageStatus.OK, updated_context=context)

        if context.plan is None:
            return StageResult(status=StageStatus.OK, updated_context=context)

        risk = context.classification.risk
        threshold = config.runtime.plan_critic.complexity_threshold
        should_run, reason = _should_run_council(context.plan, risk, threshold)
        logger.info(banner("Plan critic"))
        if not should_run:
            logger.info(f"  critic: skipped — {reason}")
            return StageResult(status=StageStatus.OK, updated_context=context)
        logger.info(f"  critic: running — {reason}")

        scaling = config.runtime.council.dynamic_scaling
        n_councillors = scaling.get(risk, 1)
        pool = config.runtime.council.councillors
        active = pool[:n_councillors]

        if not active:
            logger.info(f"  critic: skipped ({risk} risk → 0 councillors)")
            return StageResult(status=StageStatus.OK, updated_context=context)

        labels = [c.label for c in active]
        logger.info(f"  critic: {risk} risk → {len(active)} councillor(s): {labels}")
        self._spinner.update("Reviewing plan...")

        plan = context.plan
        critic_result = self._critic.review(
            plan, active_councillors=active, identity=context.identity
        )

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
            # Expand any skill: steps the revision introduced before validating.
            if self._skill_expansion is not None:
                context.plan = revised
                self._skill_expansion.run(context)
                revised = context.plan

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

        # Coherence check: if every remaining step is CONVERSATION type,
        # there is nothing to synthesize — plan is structurally incoherent.
        data_steps = [s for s in plan.steps if s.action_type != ActionType.CONVERSATION]
        if not data_steps:
            logger.info(
                "  council: plan stripped to CONVERSATION-only steps with no "
                "data-gathering — aborting to fallback"
            )
            return StageResult(
                status=StageStatus.ABORT,
                updated_context=context,
                reason="Plan stripped to conversation-only: no data-gathering steps remain",
            )

        logger.info(banner(f"Plan ready ({len(plan.steps)} steps)"))

        # Emit plan.revised if the council changed the plan
        if context.identity is not None and plan is not context.plan:
            from runtime.events import RuntimeEvent, get_event_bus
            n_challenges = len(critic_result.challenges) if critic_result.challenges else 0
            get_event_bus().emit(RuntimeEvent(
                "plan.revised",
                context.identity,
                payload={"n_challenges": n_challenges, "surviving_steps": len(plan.steps)},
                stage="CouncilStage",
            ))

        context.plan = plan
        return StageResult(status=StageStatus.OK, updated_context=context)
