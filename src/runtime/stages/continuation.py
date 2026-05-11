"""ContinuationStage — owns task-level completion decisions.

This stage replaces Plan.requires_synthesis as the authority over what
happens after ExecutionStage finishes. It can also loop back to
ExecutionStage with a continuation plan.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from planning.planner import Planner
from planning.schema import Plan
from providers.base import BaseProvider, TextBlock
from runtime.pipeline_context import PipelineContext
from runtime.schema import ContinuationDecision, ContinuationState
from runtime.stage_base import Stage
from runtime.stage_result import StageResult, StageStatus
from runtime.utils import banner
from app_config import config
from logger import get_logger

if TYPE_CHECKING:
    from runtime.stages.execution import ExecutionStage
    from runtime.stages.skill_expansion import SkillExpansionStage
    from skills.registry import SkillRegistry

logger = get_logger(__name__)


class ContinuationStage(Stage):
    """Owns the question 'are we done with the task?'.

    Reads:  context.plan, context.user_message, context.continuation_state,
            context.active_skill_name
    Writes: context.continuation_state, possibly context.plan (on LOOP)

    Returns:
      OK    when synthesis should run next
      DONE  when the answer is already in context.response and synthesis isn't needed
    """

    name = "ContinuationStage"

    def __init__(
        self,
        provider: BaseProvider,
        planner: Planner,
        execution_stage: "ExecutionStage",
        skill_registry: "SkillRegistry | None" = None,
        skill_expansion_stage: "SkillExpansionStage | None" = None,
    ) -> None:
        self._provider = provider
        self._planner = planner
        self._execution = execution_stage
        self._skill_registry = skill_registry
        self._skill_expansion = skill_expansion_stage

    def run(self, context: PipelineContext) -> StageResult:
        cfg = config.runtime.continuation

        if not cfg.enabled:
            return self._fall_through_legacy(context)
        if context.plan is None:
            return StageResult(status=StageStatus.OK, updated_context=context)

        if context.continuation_state is None:
            context.continuation_state = ContinuationState()

        logger.info(banner("Continuation"))

        while True:
            decision = self._decide(context, cfg)
            context.continuation_state.last_decision = decision.value
            context.continuation_state.history.append({
                "iteration": context.continuation_state.iteration_count,
                "plan_steps": len(context.plan.steps) if context.plan else 0,
                "decision": decision.value,
            })

            if decision == ContinuationDecision.DONE:
                logger.info("  continuation: DONE — no synthesis needed")
                return StageResult(status=StageStatus.DONE, updated_context=context)

            if decision == ContinuationDecision.SYNTHESIZE:
                logger.info("  continuation: SYNTHESIZE — pass to synthesizer")
                return StageResult(status=StageStatus.OK, updated_context=context)

            # LOOP
            new_plan = self._build_continuation_plan(context)
            if new_plan is None:
                logger.info("  continuation: LOOP requested but no continuation plan — synthesizing instead")
                return StageResult(status=StageStatus.OK, updated_context=context)

            context.continuation_state.iteration_count += 1
            if context.continuation_state.iteration_count > cfg.max_iterations:
                logger.info(
                    f"  continuation: iteration cap ({cfg.max_iterations}) reached — synthesizing"
                )
                return StageResult(status=StageStatus.OK, updated_context=context)

            context.plan = new_plan
            logger.info(
                f"  continuation: LOOP iteration {context.continuation_state.iteration_count} — "
                f"{len(new_plan.steps)} new step(s)"
            )

            # Expand any skill:<name> steps before executing.
            if self._skill_expansion is not None:
                self._skill_expansion.run(context)

            self._execution.run(context)
            # Loop top — re-decide based on the new state.

    # ── Decision logic ──────────────────────────────────────────────

    def _decide(self, context: PipelineContext, cfg) -> ContinuationDecision:
        plan = context.plan
        if plan is None or not plan.steps:
            return ContinuationDecision.DONE

        # 1. Active criteria from the plan's source skill, if any.
        criteria = self._active_criteria(context)
        if criteria is not None:
            outcome = self._evaluate_criteria(criteria, context)
            from skills.criteria import CriteriaOutcome
            if outcome == CriteriaOutcome.MET:
                logger.info(f"  continuation: criteria MET ({criteria.__class__.__name__}) → {criteria.on_met.value}")
                return criteria.on_met
            if outcome == CriteriaOutcome.NOT_MET:
                logger.info("  continuation: criteria NOT_MET → LOOP")
                return ContinuationDecision.LOOP
            logger.info("  continuation: criteria INCONCLUSIVE → LLM judge")

        # 2. LLM judge
        if cfg.use_llm_judge:
            return self._llm_judge(context, cfg)

        return ContinuationDecision.SYNTHESIZE

    def _active_criteria(self, context: PipelineContext):
        name = context.active_skill_name
        if name is None or self._skill_registry is None:
            return None
        skill = self._skill_registry.get(name)
        return skill.completion_criteria if skill else None

    def _evaluate_criteria(self, criteria, context: PipelineContext):
        from skills.criteria import (
            StructuralCriteria, LLMJudgedCriteria, CriteriaContext, CriteriaOutcome,
        )
        cctx = CriteriaContext(plan=context.plan, user_message=context.user_message)
        if isinstance(criteria, StructuralCriteria):
            return criteria.evaluate(cctx)
        if isinstance(criteria, LLMJudgedCriteria):
            return self._evaluate_llm_criteria(criteria, context)
        return CriteriaOutcome.INCONCLUSIVE

    def _evaluate_llm_criteria(self, criteria, context: PipelineContext):
        from messenger import Messenger
        from runtime.json_extract import extract_json
        from skills.criteria import CriteriaOutcome

        system = (
            "You evaluate whether a specific completion criterion is satisfied "
            "by an autonomous agent's executed plan. Return strict JSON: "
            '{"satisfied": true|false, "reason": "..."}.'
        )
        user = (
            f"Original request: {context.user_message}\n\n"
            f"Executed plan ({len(context.plan.steps)} steps):\n{context.plan.summary()}\n\n"
            f"Criterion to evaluate:\n{criteria.prompt}"
        )
        messenger = Messenger()
        messenger.add_user_message(user)
        try:
            response = self._provider.chat(
                messages=messenger.get_messages(), tools=[], system=system,
                label="ContinuationCriteria",
            )
        except Exception as e:
            logger.info(f"  continuation: LLM criteria call failed ({e!r})")
            return CriteriaOutcome.INCONCLUSIVE

        raw = next((b.text for b in response.content if isinstance(b, TextBlock)), "")
        data = extract_json(raw)
        if not isinstance(data, dict) or "satisfied" not in data:
            return CriteriaOutcome.INCONCLUSIVE
        return CriteriaOutcome.MET if bool(data["satisfied"]) else CriteriaOutcome.NOT_MET

    def _llm_judge(self, context: PipelineContext, cfg) -> ContinuationDecision:
        """Single focused LLM call. Defaults to SYNTHESIZE on parse failure."""
        from messenger import Messenger
        from runtime.json_extract import extract_json

        plan = context.plan
        prior_lines = ""
        hist = context.continuation_state.history
        if hist:
            prior_lines = "\nPrior iterations:\n" + "\n".join(
                f"  iter {h['iteration']}: {h['decision']} ({h['plan_steps']} steps)"
                for h in hist[-3:]
            )

        system = (
            "You evaluate whether an autonomous agent has finished the user's task.\n"
            "Respond with strict JSON:\n"
            '{"judgment": "done"|"need_more"|"trivial", '
            '"reason": "...", "missing": "..."}\n\n'
            "done    — the executed plan addresses the request; synthesis recommended\n"
            "need_more — clear unmet requirement; describe in 'missing'\n"
            "trivial — single-tool answer that needs no synthesis\n"
        )
        user = (
            f"Original request: {context.user_message}\n\n"
            f"Executed plan ({len(plan.steps)} steps):\n{plan.summary()}\n\n"
            f"Iteration {context.continuation_state.iteration_count} "
            f"of max {cfg.max_iterations}.{prior_lines}"
        )

        messenger = Messenger()
        messenger.add_user_message(user)
        try:
            response = self._provider.chat(
                messages=messenger.get_messages(),
                tools=[],
                system=system,
                label=cfg.llm_judge_label,
            )
        except Exception as e:
            logger.info(f"  continuation: LLM judge call failed ({e!r}) — defaulting to SYNTHESIZE")
            return ContinuationDecision.SYNTHESIZE

        raw = next((b.text for b in response.content if isinstance(b, TextBlock)), "")
        data = extract_json(raw)
        if not isinstance(data, dict):
            logger.info("  continuation: LLM judge returned unparseable JSON — defaulting to SYNTHESIZE")
            return ContinuationDecision.SYNTHESIZE

        judgment = (data.get("judgment") or "").lower()
        reason = data.get("reason", "")
        missing = data.get("missing", "")
        logger.info(f"  continuation: judge={judgment} reason={reason!r} missing={missing!r}")

        if judgment == "trivial":
            return ContinuationDecision.DONE
        if judgment == "need_more":
            return ContinuationDecision.LOOP
        return ContinuationDecision.SYNTHESIZE

    # ── Continuation plan generation ───────────────────────────────

    def _build_continuation_plan(self, context: PipelineContext) -> Plan | None:
        plan = context.plan
        if plan is None or not plan.steps:
            return None

        # Tier 1: skill replay
        name = context.active_skill_name
        if name is not None and self._skill_registry is not None:
            skill = self._skill_registry.get(name)
            if skill is not None:
                from skills.base import SkillContext
                sctx = SkillContext(
                    original_query=plan.original_query,
                    skill_args={},
                    starting_step_number=1,
                )
                replay_steps = skill.continuation_steps(sctx, plan.steps)
                if replay_steps:
                    return Plan(
                        original_query=plan.original_query,
                        steps=replay_steps,
                        risk=getattr(plan, "risk", "low"),
                    )

        # Tier 2: planner replan
        last_step = plan.steps[-1]
        new_steps = self._planner.replan(
            plan, last_step, "continuation requested by ContinuationStage"
        )
        if not new_steps:
            return None
        return Plan(
            original_query=plan.original_query,
            steps=new_steps,
            risk=getattr(plan, "risk", "low"),
        )

    # ── Legacy fall-through (only when stage is disabled) ──────────

    def _fall_through_legacy(self, context: PipelineContext) -> StageResult:
        plan = context.plan
        if plan is None:
            return StageResult(status=StageStatus.OK, updated_context=context)
        return StageResult(status=StageStatus.OK, updated_context=context)
