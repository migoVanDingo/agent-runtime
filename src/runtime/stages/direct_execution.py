"""DirectExecutionStage — free-form tool loop for direct mode requests.

Also serves as the ABORT fallback for any pipeline stage failure. Accepts
any context state — it will always produce a response even if plan, routing,
and everything else failed.

Direct lift of Agent._run_loop() from agent.py.
"""
from __future__ import annotations
from providers.base import BaseProvider, TextBlock, ToolUseBlock
from routing.static_router import StaticRouter
from runtime.context_manager import ContextManager
from runtime.escalation import Escalation
from runtime.guard import ActionGuard, GuardDecision
from runtime.pipeline_context import PipelineContext
from runtime.stage_base import Stage
from runtime.stage_result import StageResult, StageStatus
from runtime.utils import banner, fmt_input, fmt_result, has_error_indicator
from tools.implementations.web.read_url import INJECTION_WARNING_PREFIX
from app_config import config
from logger import get_logger

logger = get_logger(__name__)

_DIRECT_MAX_TOOL_RESULT_CHARS = 50_000
_DIRECT_MAX_TOOL_CALLS = 15
_DIRECT_MAX_CONSECUTIVE_ERRORS = 3
_DIRECT_MAX_ITERATIONS = 20


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
        spinner,
        agent_system: str,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._router = router
        self._context_mgr = context_mgr
        self._messenger = messenger
        self._guard = guard
        self._user_gate = user_gate
        self._spinner = spinner
        self._agent_system = agent_system

    def run(self, context: PipelineContext) -> StageResult:
        # For direct mode that fell through DirectInlineStage, log the banner.
        if context.classification is not None and context.classification.mode != "plan":
            self._spinner.update("Thinking...")
            logger.info(banner("Direct execution"))

        response = self._run_loop(context.user_message)
        context.response = response
        return StageResult(status=StageStatus.OK, updated_context=context)

    # ── Internal loop (lifted from Agent._run_loop) ───────────────────

    def _run_loop(self, user_message: str) -> str:
        iteration = 0
        last_had_errors = False
        error_correction_sent = False
        total_tool_calls = 0
        consecutive_errors = 0
        force_end = False
        _last_tool_sig: tuple | None = None

        while True:
            iteration += 1
            if iteration > _DIRECT_MAX_ITERATIONS:
                logger.info(f"  runtime: iteration cap ({_DIRECT_MAX_ITERATIONS}) reached — forcing wrap-up")
                self._messenger.add_user_message(
                    "You have exceeded the maximum number of iterations. "
                    "Stop all tool calls immediately and give the user a final response "
                    "summarizing what you were able to accomplish and what failed."
                )
                self._spinner.stop()
                packed = self._context_mgr.pack(self._messenger.get_messages(), user_message)
                response = self._provider.chat(
                    messages=packed,
                    tools=[],
                    system=self._agent_system,
                    label="DirectExecutionStage",
                )
                self._messenger.add_assistant_message(response.content)
                logger.info(banner("Done"))
                return next(
                    (b.text for b in response.content if isinstance(b, TextBlock)), ""
                )

            selected = self._router.select(user_message, self._messenger.get_messages())
            tools = self._registry.get_toolset_schema(selected)
            logger.info(f"  [iteration {iteration}] toolsets: {selected}")

            packed = self._context_mgr.pack(self._messenger.get_messages(), user_message)
            response = self._provider.chat(
                messages=packed,
                tools=[] if force_end else tools,
                system=self._agent_system,
                label="DirectExecutionStage",
            )

            if response.stop_reason in ("end_turn", "max_tokens"):
                if last_had_errors and not error_correction_sent:
                    logger.info("  runtime: model ended turn after tool errors — injecting correction")
                    self._messenger.add_assistant_message(response.content)
                    self._messenger.add_user_message(
                        "One or more of your previous tool calls returned errors. "
                        "Do not claim success if the operation failed. "
                        "Review the errors and either retry with corrected parameters or "
                        "acknowledge the failure to the user."
                    )
                    error_correction_sent = True
                    last_had_errors = False
                    continue

                self._spinner.stop()
                self._messenger.add_assistant_message(response.content)
                if response.stop_reason == "max_tokens":
                    logger.info("  [max_tokens] — stopping")
                    dangling = [b for b in response.content if isinstance(b, ToolUseBlock)]
                    if dangling:
                        logger.info(f"  [max_tokens] patching {len(dangling)} dangling tool_use block(s)")
                        self._messenger.add_tool_results([
                            {
                                "type": "tool_result",
                                "tool_use_id": b.id,
                                "content": "[interrupted: response ended at max_tokens before tool could execute]",
                            }
                            for b in dangling
                        ])
                logger.info(banner("Done"))
                return next(
                    (b.text for b in response.content if isinstance(b, TextBlock)), ""
                )

            if response.stop_reason == "tool_use":
                self._messenger.add_assistant_message(response.content)
                tool_results = []
                for block in response.content:
                    if isinstance(block, ToolUseBlock):
                        logger.info(f"  → {block.name}  {fmt_input(block.name, block.input)}")

                        guard_decision, guard_reason = self._guard.check_tool_call(block.name, block.input)

                        if guard_decision == GuardDecision.BLOCK:
                            logger.info(f"  ✖ BLOCKED: {guard_reason}")
                            result = f"Tool call blocked by safety policy: {guard_reason}"
                        elif guard_decision == GuardDecision.ESCALATE:
                            logger.info(f"  ⚠ ESCALATE: {guard_reason}")
                            escalation = Escalation(
                                reason=guard_reason,
                                source="guard",
                                tool_name=block.name,
                                tool_input=block.input,
                            )
                            self._spinner.stop()
                            if self._user_gate.prompt(escalation):
                                self._guard.record_approval(block.name, block.input)
                                self._spinner.start(f"Running {block.name}...")
                                try:
                                    tool = self._registry.get(block.name)
                                    result = tool.safe_execute(block.input)
                                except KeyError:
                                    result = f"Error: tool '{block.name}' does not exist."
                            else:
                                result = f"Tool call denied by user: {guard_reason}"
                            self._spinner.start("Thinking...")
                        else:
                            self._spinner.update(f"Running {block.name}...")
                            try:
                                tool = self._registry.get(block.name)
                                result = tool.safe_execute(block.input)
                            except KeyError:
                                result = f"Error: tool '{block.name}' does not exist."

                        # ── Injection gate: read_url detected adversarial content ──
                        if result.startswith(INJECTION_WARNING_PREFIX):
                            logger.info("  ⚠ INJECTION WARNING: read_url detected adversarial content — halting loop")
                            self._spinner.stop()
                            print(f"\n{'─'*60}")
                            print("  SECURITY WARNING: Possible prompt injection detected")
                            print("  in fetched web content. The content has been quarantined")
                            print("  and has NOT entered context.")
                            print(f"{'─'*60}")
                            print(result.replace(INJECTION_WARNING_PREFIX + "\n", ""))
                            print(f"{'─'*60}")
                            user_choice = input("  Proceed with reading this content? [y/N]: ").strip().lower()
                            if user_choice == "y":
                                self._spinner.start("Thinking...")
                                result = result.replace(INJECTION_WARNING_PREFIX + "\n", "[SECURITY REVIEW PASSED BY USER]\n")
                            else:
                                # Expel the artifact (removes from store + disk)
                                import re as _re
                                key_match = _re.search(r"Artifact-key: (\S+)", result)
                                if key_match:
                                    artifact_key = key_match.group(1)
                                    del_choice = input(f"  Delete quarantined artifact '{artifact_key}'? [Y/n]: ").strip().lower()
                                    if del_choice != "n":
                                        try:
                                            from runtime.artifact_store import get_artifact_store
                                            get_artifact_store().expel(artifact_key)
                                            print(f"  Expelled artifact '{artifact_key}'")
                                        except Exception:
                                            pass
                                result = "Tool call cancelled by user: potential prompt injection in fetched content."
                                tool_results.append({
                                    "type": "tool_result",
                                    "tool_use_id": block.id,
                                    "content": result,
                                })
                                self._messenger.add_tool_results(tool_results)
                                self._messenger.add_user_message(
                                    "The user cancelled this operation due to a potential prompt injection "
                                    "detected in the fetched web content. Stop and inform the user."
                                )
                                self._spinner.start("Thinking...")
                                force_end = True
                                continue

                        if len(result) > _DIRECT_MAX_TOOL_RESULT_CHARS:
                            original_len = len(result)
                            result = (
                                result[:_DIRECT_MAX_TOOL_RESULT_CHARS]
                                + f"\n[truncated — output was {original_len} chars, "
                                f"showing first {_DIRECT_MAX_TOOL_RESULT_CHARS}]"
                            )
                            logger.info(f"  [truncated tool result from {original_len} to {_DIRECT_MAX_TOOL_RESULT_CHARS} chars]")

                        logger.info(f"  ← {fmt_result(result)}")
                        total_tool_calls += 1

                        if has_error_indicator(result):
                            consecutive_errors += 1
                        else:
                            consecutive_errors = 0

                        cur_sig = (block.name, str(sorted(block.input.items())))
                        if cur_sig == _last_tool_sig and not has_error_indicator(result):
                            logger.info(f"  runtime: repeated identical tool call ({block.name}) — forcing wrap-up")
                            force_end = True
                        _last_tool_sig = cur_sig

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })

                last_had_errors = any(
                    has_error_indicator(r["content"]) for r in tool_results
                )
                if last_had_errors:
                    logger.info("  ⚠ tool error(s) detected in results")
                    error_correction_sent = False

                self._messenger.add_tool_results(tool_results)

                if consecutive_errors >= _DIRECT_MAX_CONSECUTIVE_ERRORS:
                    logger.info(f"  runtime: {consecutive_errors} consecutive errors — injecting stop")
                    self._messenger.add_user_message(
                        "Multiple consecutive tool calls have failed. "
                        "Stop retrying and report the issue to the user."
                    )
                    consecutive_errors = 0

                if total_tool_calls >= _DIRECT_MAX_TOOL_CALLS:
                    logger.info(f"  runtime: tool call cap ({_DIRECT_MAX_TOOL_CALLS}) reached — forcing wrap-up")
                    self._messenger.add_user_message(
                        "You have reached the tool call limit. "
                        "Stop all tool calls and give the user a final response."
                    )
                    force_end = True

                self._spinner.update("Thinking...")
