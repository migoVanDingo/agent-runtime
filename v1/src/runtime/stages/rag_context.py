"""RagContextStage — populate context.rag_context before execution stages see it.

Runs first in the pipeline so both plan execution and direct execution have
the historical context block available in their system prompts.

Owns the decision of whether to call the RAG service and what to inject.
The tool layer and skill layer are not involved.
"""
from __future__ import annotations

from logger import get_logger
from runtime.pipeline_context import PipelineContext
from runtime.stage_base import Stage
from runtime.stage_result import StageResult, StageStatus

logger = get_logger(__name__)


class RagContextStage(Stage):
    name = "RagContextStage"

    def run(self, context: PipelineContext) -> StageResult:
        from rag import get_rag_service
        from runtime.events import get_runtime_identity
        from app_config import config

        if rag := get_rag_service():
            try:
                session_id = get_runtime_identity().session_id
                context.rag_context = rag.build_context_block(
                    context.user_message,
                    session_id,
                    config.rag.injection_budget_chars,
                )
                if context.rag_context:
                    logger.info("  rag: context block injected")
            except Exception as e:
                logger.warning(f"  rag: context build skipped — {e}")
                context.rag_context = ""

        return StageResult(status=StageStatus.OK, updated_context=context)
