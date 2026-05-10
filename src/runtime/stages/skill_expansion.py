"""SkillExpansionStage — expands skill:<name> steps into concrete steps.

Runs after PlanningStage, before EntityCriticStage. Idempotent on plans
without skill calls. Re-numbers steps after expansion.
"""
from __future__ import annotations
from planning.schema import Plan, Step
from runtime.pipeline_context import PipelineContext
from runtime.stage_base import Stage
from runtime.stage_result import StageResult, StageStatus
from runtime.utils import banner
from skills.base import SkillContext
from skills.registry import SkillRegistry
from logger import get_logger

logger = get_logger(__name__)

_SKILL_PREFIX = "skill:"


class SkillExpansionStage(Stage):
    """Inlines skill calls into concrete plan steps.

    A step with tool='skill:foo' is replaced by the steps that foo.expand()
    returns, with step numbers continuous within the parent plan.

    Sets context.active_skill_name when exactly one skill step was expanded
    (for ContinuationStage criteria lookup).
    """

    name = "SkillExpansionStage"

    def __init__(self, registry: SkillRegistry) -> None:
        self._registry = registry

    def expand_steps(self, steps: list[Step], original_query: str) -> list[Step]:
        """Expand any skill:xxx references in a list of steps.

        Called by ExecutionStage after replanning so that skill references in
        replanned steps receive the same expansion treatment as steps produced
        at pipeline start. The runtime owns this decision — skill expansion is
        infrastructure, not planner responsibility.
        """
        has_skill = any((s.tool or "").startswith(_SKILL_PREFIX) for s in steps)
        if not has_skill:
            return steps

        new_steps: list[Step] = []
        for s in steps:
            tool = s.tool or ""
            if not tool.startswith(_SKILL_PREFIX):
                new_steps.append(s)
                continue
            skill_name = tool[len(_SKILL_PREFIX):]
            skill = self._registry.get(skill_name)
            if skill is None:
                logger.info(f"  replan skill expansion: unknown skill '{skill_name}' — keeping as literal")
                new_steps.append(s)
                continue
            ctx = SkillContext(
                original_query=original_query,
                skill_args={"description": s.description},
                starting_step_number=len(new_steps) + 1,
            )
            try:
                expanded = skill.expand(ctx)
                logger.info(f"  replan skill expansion: skill:{skill_name} → {len(expanded)} step(s)")
                new_steps.extend(expanded)
            except Exception as e:
                logger.info(f"  replan skill expansion: '{skill_name}' failed ({e!r}) — keeping as literal")
                new_steps.append(s)

        # Re-number sequentially from 1
        for i, st in enumerate(new_steps, 1):
            st.step = i
        return new_steps

    def run(self, context: PipelineContext) -> StageResult:
        plan = context.plan
        if plan is None:
            return StageResult(status=StageStatus.OK, updated_context=context)

        skill_steps = [s for s in plan.steps if (s.tool or "").startswith(_SKILL_PREFIX)]
        if not skill_steps:
            context.active_skill_name = None
            return StageResult(status=StageStatus.OK, updated_context=context)

        logger.info(banner("Skill expansion"))

        new_steps: list[Step] = []
        expanded_skill_names: list[str] = []

        for s in plan.steps:
            tool = s.tool or ""
            if not tool.startswith(_SKILL_PREFIX):
                new_steps.append(s)
                continue

            skill_name = tool[len(_SKILL_PREFIX):]
            skill = self._registry.get(skill_name)
            if skill is None:
                logger.info(f"  unknown skill '{skill_name}' — keeping step as literal")
                new_steps.append(s)
                continue

            ctx = SkillContext(
                original_query=plan.original_query,
                skill_args={"description": s.description},
                starting_step_number=len(new_steps) + 1,
            )
            try:
                expanded = skill.expand(ctx)
            except Exception as e:
                logger.info(f"  skill '{skill_name}' expand failed: {e!r}")
                new_steps.append(s)
                continue

            logger.info(f"  step {s.step}: skill:{skill_name} → {len(expanded)} concrete step(s)")
            new_steps.extend(expanded)
            expanded_skill_names.append(skill_name)

        # Re-number sequentially
        for i, st in enumerate(new_steps, 1):
            st.step = i

        plan.steps = new_steps
        context.plan = plan
        logger.info(f"  expanded plan: {len(new_steps)} step(s)")

        # Set active_skill_name to the LAST expanded skill for criteria lookup.
        # For compound plans (multiple skills), the last skill is the terminal one
        # whose CompletionCriteria defines "done" — e.g. test-reconstruction's
        # all_match=true is the right terminal check even when deep-disassembly ran first.
        context.active_skill_name = expanded_skill_names[-1] if expanded_skill_names else None
        if len(expanded_skill_names) > 1:
            logger.info(
                f"  active skill (terminal): '{expanded_skill_names[-1]}' "
                f"(compound: {expanded_skill_names})"
            )

        return StageResult(status=StageStatus.OK, updated_context=context)
