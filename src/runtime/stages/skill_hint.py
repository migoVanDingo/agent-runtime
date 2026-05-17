"""SkillHintStage — advisory skill suggester.

Runs a cheap LLM (or regex) pass to suggest which skill, if any, the
planner might want to invoke. The output is HINT ONLY: it is not
load-bearing. The planner is free to ignore it.
"""
from __future__ import annotations
from runtime.classifier import WorkflowSelector
from runtime.pipeline_context import PipelineContext
from runtime.scope import RUNTIME, scoped
from runtime.stage_base import Stage
from runtime.stage_result import StageResult, StageStatus
from runtime.utils import banner
from skills.registry import SkillRegistry
from logger import get_logger

logger = get_logger(__name__)


class SkillHintStage(Stage):
    """Suggests a skill name to the planner. Never produces a plan."""

    name = "SkillHintStage"

    def __init__(
        self,
        skill_registry: SkillRegistry,
        skill_selector: WorkflowSelector,
    ) -> None:
        self._registry = skill_registry
        self._selector = skill_selector

    def run(self, context: PipelineContext) -> StageResult:
        if context.classification is None or context.classification.mode != "plan":
            return StageResult(status=StageStatus.OK, updated_context=context)

        logger.info(banner("Skill hint"))
        descriptions = self._registry.descriptions()
        # WorkflowSelector hits the runtime provider — enter runtime scope so
        # its packing picks the smaller budget and logs/events get tagged.
        with scoped(RUNTIME):
            chosen = self._selector.select(context.user_message, descriptions)
        valid = bool(chosen and self._registry.get(chosen) is not None)
        if valid:
            logger.info(f"  hint: '{chosen}' (advisory; planner may override)")
            context.skill_hint = chosen
        else:
            logger.info("  hint: none")
            context.skill_hint = None

        try:
            from runtime.events import RuntimeEvent, get_event_bus, get_runtime_identity
            get_event_bus().emit(RuntimeEvent(
                "skill.match.evaluated",
                get_runtime_identity(),
                payload={
                    "chosen": chosen if valid else None,
                    "n_candidates": len(descriptions),
                    "candidate_names": list(descriptions.keys()) if isinstance(descriptions, dict) else [],
                },
                stage="SkillHintStage",
            ))
        except Exception:
            pass

        return StageResult(status=StageStatus.OK, updated_context=context)
