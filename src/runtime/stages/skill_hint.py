"""SkillHintStage — advisory skill suggester.

Runs a cheap LLM (or regex) pass to suggest which skill, if any, the
planner might want to invoke. The output is HINT ONLY: it is not
load-bearing. The planner is free to ignore it.
"""
from __future__ import annotations
from runtime.classifier import WorkflowSelector
from runtime.pipeline_context import PipelineContext
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
        spinner,
    ) -> None:
        self._registry = skill_registry
        self._selector = skill_selector
        self._spinner = spinner

    def run(self, context: PipelineContext) -> StageResult:
        if context.classification is None or context.classification.mode != "plan":
            return StageResult(status=StageStatus.OK, updated_context=context)

        logger.info(banner("Skill hint"))
        descriptions = self._registry.descriptions()
        self._spinner.update("Routing...")
        chosen = self._selector.select(context.user_message, descriptions)
        if chosen and self._registry.get(chosen) is not None:
            logger.info(f"  hint: '{chosen}' (advisory; planner may override)")
            context.skill_hint = chosen
        else:
            logger.info("  hint: none")
            context.skill_hint = None

        return StageResult(status=StageStatus.OK, updated_context=context)
