"""DirectExecutionStage — free-form tool loop for direct mode requests.

Also serves as the ABORT fallback for any pipeline stage failure. Accepts
any context state — it will always produce a response even if plan, routing,
and everything else failed.

Delegates the actual loop to runtime.tool_loop.ToolLoop.
"""
from __future__ import annotations

from providers.base import BaseProvider
from routing.static_router import StaticRouter
from runtime.context_manager import ContextManager
from runtime.escalation import Escalation
from runtime.guard import ActionGuard
from runtime.pipeline_context import PipelineContext
from runtime.stage_base import Stage
from runtime.stage_result import StageResult, StageStatus
from runtime.tool_executor import ToolCallExecutor
from runtime.tool_loop import ToolLoop, ToolLoopConfig
from runtime.utils import banner
from app_config import config
from logger import get_logger
from session_paths import build_analysis_manifest

logger = get_logger(__name__)


class DirectExecutionStage(Stage):
    """Free-form tool loop. Used for direct mode and as the pipeline ABORT fallback.

    Reads:  context.user_message (and the messenger's conversation history)
    Writes: context.response

    Always returns OK — loop safety limits ensure termination.
    """

    name = "DirectExecutionStage"

    def __init__(
        self,
        provider: BaseProvider,
        registry,
        router: StaticRouter,
        context_mgr: ContextManager,
        messenger,
        guard: ActionGuard,
        user_gate,
        agent_system: str,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._router = router
        self._context_mgr = context_mgr
        self._messenger = messenger
        self._guard = guard
        self._user_gate = user_gate
        self._agent_system = agent_system
        self._tool_executor = ToolCallExecutor(registry, guard, user_gate)
        self._identity = None

    def run(self, context: PipelineContext) -> StageResult:
        if context.classification is not None and context.classification.mode != "plan":
            logger.info(banner("Direct execution"))

        self._identity = context.identity
        self._rag_context = context.rag_context
        self._checkpoint = context._pause_check  # stored for _run_loop
        response = self._run_loop(context.user_message)
        context.response = response
        return StageResult(status=StageStatus.OK, updated_context=context)

    def _run_loop(self, user_message: str) -> str:
        selected = self._router.select(user_message, self._messenger.get_messages())
        tools = self._registry.get_toolset_schema(selected)
        logger.info(f"  direct: toolsets selected: {selected}")

        loop_cfg = ToolLoopConfig(
            max_iterations=config.runtime.execution_monitor.max_step_retries * 10,
            max_tool_calls=15,
            max_consecutive_errors=3,
            tool_result_truncate_chars=50_000,
            label="DirectExecutionStage",
        )

        class _DirectHooks:
            def __init__(self, stage: DirectExecutionStage, msg: str):
                self._stage = stage
                self._msg = msg

            def on_tool_complete(self, tool_name: str, result: str) -> None:
                # Re-route toolsets after each tool call in case context shifted.
                new_selected = self._stage._router.select(
                    self._msg, self._stage._messenger.get_messages()
                )
                # We can't change tools mid-loop easily so this is informational only.
                logger.info(f"  direct: [iteration] toolsets: {new_selected}")

            def on_max_tokens(self) -> None: pass
            def on_error_cleared(self, n: int) -> None: pass

        loop = ToolLoop(
            provider=self._provider,
            messenger=self._messenger,
            context_mgr=self._context_mgr,
            tool_executor=self._tool_executor,
            user_gate=self._user_gate,
            config=loop_cfg,
            parent_identity=self._identity,
            checkpoint=getattr(self, "_checkpoint", None),
        )

        result = loop.run(
            system=self._agent_system + getattr(self, "_rag_context", "") + build_analysis_manifest(),
            tools=tools,
            query=user_message,
            resume_message="Thinking...",
        )
        return result.response_text
