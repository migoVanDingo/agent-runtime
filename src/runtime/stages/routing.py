"""Routing stages: RoutingStage and DirectInlineStage.

RoutingStage makes the single combined API call that both classifies
intent and (for direct mode) produces an inline answer in one shot.

DirectInlineStage immediately follows RoutingStage. If the model produced
a clean conversational answer, it returns DONE to short-circuit the rest
of the pipeline. Otherwise it returns OK and the pipeline continues.
"""
from __future__ import annotations
from providers.base import BaseProvider, TextBlock
from runtime.context_manager import ContextManager
from runtime.pipeline_context import PipelineContext
from runtime.stage_base import Stage
from runtime.stage_result import StageResult, StageStatus
from runtime.utils import (
    banner,
    build_routing_system,
    parse_routing_response,
    is_clean_inline_answer,
    extract_entity_context,
)
from skills.registry import SkillRegistry
from app_config import config
from logger import get_logger

logger = get_logger(__name__)


class RoutingStage(Stage):
    """Single combined API call: classifies intent and optionally answers inline.

    Writes to context:
      - packed_messages
      - classification
      - answer_text
      - entity_context
    """

    name = "RoutingStage"

    def __init__(
        self,
        provider: BaseProvider,
        context_mgr: ContextManager,
        skill_registry: SkillRegistry,
        messenger,
    ) -> None:
        self._provider = provider
        self._context_mgr = context_mgr
        self._skill_registry = skill_registry
        self._messenger = messenger

    def run(self, context: PipelineContext) -> StageResult:
        logger.info(banner("Intent routing"))

        skill_descriptions = self._skill_registry.descriptions()
        valid_skill_names = {name for name, _ in skill_descriptions}

        packed = self._context_mgr.pack(
            self._messenger.get_messages(),
            context.user_message,
        )

        routing_system = build_routing_system(config.agent.system_prompt, skill_descriptions)

        routing_response = self._provider.chat(
            messages=packed,
            tools=[],
            system=routing_system,
            label="RoutingStage",
        )

        full_text = next(
            (b.text for b in routing_response.content if isinstance(b, TextBlock)), ""
        )

        classification, answer_text = parse_routing_response(full_text, valid_skill_names)

        logger.info(
            f"  mode={classification.mode}  risk={classification.risk}"
            f"  hint={classification.skill_hint}"
        )

        context.packed_messages = packed
        context.classification = classification
        context.answer_text = answer_text
        context.entity_context = extract_entity_context(packed)

        return StageResult(status=StageStatus.OK, updated_context=context)


class DirectInlineStage(Stage):
    """Short-circuit for clean conversational inline answers.

    If the routing model produced a clean answer (no code fences, no
    action-promising phrases) for a direct-mode request, stores it as
    the response and returns DONE — skipping all downstream stages.

    Otherwise returns OK and the pipeline continues normally.
    """

    name = "DirectInlineStage"

    def run(self, context: PipelineContext) -> StageResult:
        if context.classification is None:
            # Should never happen after RoutingStage but guard anyway.
            return StageResult(status=StageStatus.OK, updated_context=context)

        if context.classification.mode != "direct":
            return StageResult(status=StageStatus.OK, updated_context=context)

        if is_clean_inline_answer(context.answer_text):
            logger.info(banner("Direct (inline)"))
            # Add the inline answer to conversation history.
            from providers.base import TextBlock as TB
            self._add_to_messenger(context.answer_text)
            context.response = context.answer_text.strip()
            return StageResult(status=StageStatus.DONE, updated_context=context)

        return StageResult(status=StageStatus.OK, updated_context=context)

    def __init__(self, messenger) -> None:
        self._messenger = messenger

    def _add_to_messenger(self, text: str) -> None:
        from providers.base import TextBlock
        self._messenger.add_assistant_message([TextBlock(text=text)])
