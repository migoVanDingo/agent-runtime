"""Shared ReAct tool-call loop.

Both ExecutionStage (plan mode) and DirectExecutionStage (direct/fallback mode)
use this loop. Each stage provides a ToolLoopConfig and an optional
ToolLoopHooks implementation that adapts the shared loop to its context.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Protocol

from providers.base import BaseProvider, TextBlock, ToolUseBlock
from runtime.context_manager import ContextManager
from runtime.injection_gate import handle_injection_warning
from runtime.tool_executor import ToolCallExecutor
from runtime.utils import banner, fmt_input, fmt_result, has_error_indicator
from logger import get_logger

logger = get_logger(__name__)


# ── Config ────────────────────────────────────────────────────────────────────

@dataclass
class ToolLoopConfig:
    max_iterations: int = 20
    max_tool_calls: int = 15
    max_consecutive_errors: int = 3
    tool_result_truncate_chars: int = 50_000
    # When non-empty, tool calls outside this set are rejected.
    authorized_tool_names: frozenset[str] = field(default_factory=frozenset)
    label: str = "ToolLoop"


# ── Result ────────────────────────────────────────────────────────────────────

@dataclass
class ToolLoopResult:
    response_text: str
    tool_errors: list[str]
    hit_max_tokens: bool = False
    hit_iteration_cap: bool = False
    hit_tool_call_cap: bool = False
    # Raw output of the last successful tool call (not the model's prose summary).
    # Criteria evaluators use this to inspect structured tool results.
    last_tool_output: str = ""


# ── Hooks protocol ────────────────────────────────────────────────────────────

class ToolLoopHooks(Protocol):
    """Callbacks the loop invokes at well-defined points.

    All methods are optional (default to no-ops); implementors override
    only what they need.
    """

    def on_tool_complete(self, tool_name: str, result: str) -> None: ...
    def on_max_tokens(self) -> None: ...
    def on_error_cleared(self, n_cleared: int) -> None: ...


class _NoopHooks:
    def on_tool_complete(self, tool_name: str, result: str) -> None: pass
    def on_max_tokens(self) -> None: pass
    def on_error_cleared(self, n_cleared: int) -> None: pass


# ── Loop ─────────────────────────────────────────────────────────────────────

class ToolLoop:
    """Execute a provider ReAct loop until the model stops or a cap is hit.

    Args:
        provider: LLM provider.
        messenger: conversation history holder.
        context_mgr: budget-constrained context packer.
        tool_executor: pre-wired guard+execute helper.
        spinner: UI spinner (may be a no-op).
        user_gate: user approval gate for escalations.
        config: loop caps and authorization policy.
        parent_identity: RuntimeIdentity to attach to tool-call events.
    """

    def __init__(
        self,
        provider: BaseProvider,
        messenger,
        context_mgr: ContextManager,
        tool_executor: ToolCallExecutor,
        spinner,
        user_gate,
        config: ToolLoopConfig,
        parent_identity=None,
    ) -> None:
        self._provider = provider
        self._messenger = messenger
        self._context_mgr = context_mgr
        self._tool_executor = tool_executor
        self._spinner = spinner
        self._user_gate = user_gate
        self._config = config
        self._identity = parent_identity

    def run(
        self,
        *,
        system: str,
        tools: list[dict],
        query: str,
        plan_start_index: int | None = None,
        hooks: ToolLoopHooks | None = None,
        resume_message: str = "Thinking...",
    ) -> ToolLoopResult:
        """Run the loop until the model ends naturally or a cap is reached."""
        hooks = hooks or _NoopHooks()
        cfg = self._config

        iteration = 0
        total_tool_calls = 0
        consecutive_errors = 0
        force_end = False
        last_had_errors = False
        error_correction_sent = False
        _recent_sigs: deque[tuple] = deque(maxlen=8)  # sliding window for cycle detection
        tool_errors: list[str] = []
        hit_max_tokens = False
        hit_iteration_cap = False
        hit_tool_call_cap = False
        last_tool_output: str = ""

        while True:
            iteration += 1

            if iteration > cfg.max_iterations:
                logger.info(f"  runtime: iteration cap ({cfg.max_iterations}) reached — forcing wrap-up")
                hit_iteration_cap = True
                self._messenger.add_user_message(
                    "You have exceeded the maximum number of iterations. "
                    "Stop all tool calls immediately and give the user a final response "
                    "summarizing what you were able to accomplish and what failed."
                )
                self._spinner.update("Wrapping up...")
                packed = self._context_mgr.pack(
                    self._messenger.get_messages(), query, plan_start_index=plan_start_index
                )
                response = self._provider.chat(
                    messages=packed, tools=[], system=system, label=cfg.label
                )
                self._messenger.add_assistant_message(response.content)
                return ToolLoopResult(
                    response_text=next((b.text for b in response.content if isinstance(b, TextBlock)), ""),
                    tool_errors=tool_errors,
                    hit_iteration_cap=True,
                    last_tool_output=last_tool_output,
                )

            packed = self._context_mgr.pack(
                self._messenger.get_messages(), query, plan_start_index=plan_start_index
            )
            response = self._provider.chat(
                messages=packed,
                tools=[] if force_end else tools,
                system=system,
                label=cfg.label,
            )

            # ── Terminal: end_turn or max_tokens ──────────────────────────────
            if response.stop_reason in ("end_turn", "max_tokens"):
                if response.stop_reason == "end_turn" and last_had_errors and not error_correction_sent:
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

                # Spinner ownership belongs to the calling stage — don't stop it here.
                # The caller stops it after processing the result.
                self._spinner.update(resume_message)
                self._messenger.add_assistant_message(response.content)

                if response.stop_reason == "max_tokens":
                    logger.info("  [max_tokens] — stopping")
                    hit_max_tokens = True
                    hooks.on_max_tokens()
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

                return ToolLoopResult(
                    response_text=next(
                        (b.text for b in response.content if isinstance(b, TextBlock)), ""
                    ),
                    tool_errors=tool_errors,
                    hit_max_tokens=hit_max_tokens,
                    hit_iteration_cap=hit_iteration_cap,
                    hit_tool_call_cap=hit_tool_call_cap,
                    last_tool_output=last_tool_output,
                )

            # ── Tool use ─────────────────────────────────────────────────────
            if response.stop_reason == "tool_use":
                self._messenger.add_assistant_message(response.content)
                tool_results = []

                for block in response.content:
                    if not isinstance(block, ToolUseBlock):
                        continue

                    logger.info(f"  → {block.name}  {fmt_input(block.name, block.input)}")

                    # Authorization gate
                    if cfg.authorized_tool_names and block.name not in cfg.authorized_tool_names:
                        logger.info(
                            f"  ✖ UNAUTHORIZED: '{block.name}' not in tool list "
                            f"{sorted(cfg.authorized_tool_names)}"
                        )
                        reject_msg = (
                            f"Tool call rejected: '{block.name}' is not authorized for this step. "
                            "Use only the tools provided."
                        )
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": reject_msg,
                        })
                        tool_errors.append(f"{block.name}: unauthorized")
                        continue

                    # Execute via tool_executor (guard + run)
                    outcome = self._tool_executor.execute(
                        block.name,
                        block.input,
                        resume_spinner_message=resume_message,
                        parent_identity=self._identity,
                    )
                    if outcome.guard_decision.value == "block":
                        logger.info(f"  ✖ BLOCKED: {outcome.guard_reason}")
                    elif outcome.guard_decision.value == "escalate":
                        logger.info(f"  ⚠ ESCALATE: {outcome.guard_reason}")
                    result = outcome.result.to_llm_content()

                    # Truncate oversized results
                    if len(result) > cfg.tool_result_truncate_chars:
                        orig_len = len(result)
                        result = (
                            result[:cfg.tool_result_truncate_chars]
                            + f"\n[truncated — output was {orig_len} chars, "
                            f"showing first {cfg.tool_result_truncate_chars}]"
                        )
                        logger.info(f"  [truncated tool result from {orig_len} chars]")

                    # Injection gate
                    injection = handle_injection_warning(
                        result,
                        user_gate=self._user_gate,
                        spinner=self._spinner,
                        resume_spinner_message=resume_message,
                    )
                    result = injection.content
                    if injection.cancelled:
                        logger.info("  ⚠ INJECTION WARNING: user cancelled fetched content")
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
                        force_end = True
                        continue

                    logger.info(f"  ← {fmt_result(result)}")
                    total_tool_calls += 1
                    hooks.on_tool_complete(block.name, result)

                    # Track last successful tool output for criteria evaluation.
                    if not has_error_indicator(result):
                        last_tool_output = result

                    # Error tracking
                    if has_error_indicator(result):
                        tool_errors.append(f"{block.name}: {result[:100]}")
                        consecutive_errors += 1
                        last_had_errors = True
                        error_correction_sent = False
                    else:
                        if tool_errors:
                            hooks.on_error_cleared(len(tool_errors))
                        consecutive_errors = 0
                        last_had_errors = False

                    # Cycle detection — catches both identical consecutive calls and
                    # alternating A→B→A→B patterns that fool single-sig detection.
                    # Mutation tools (write, bash, etc.) clear the window on success
                    # because state genuinely changed and prior sigs are stale.
                    _MUTATION_TOOLS = {"write_file", "bash_exec", "make_directory",
                                       "delete_file", "move_file", "copy_file"}
                    cur_sig = (block.name, str(sorted(block.input.items())))

                    if block.name in _MUTATION_TOOLS and not has_error_indicator(result):
                        _recent_sigs.clear()  # state changed — reset window
                    else:
                        _recent_sigs.append(cur_sig)

                    # Period-1: identical back-to-back
                    sigs = list(_recent_sigs)
                    cycle_detected = False
                    if len(sigs) >= 2 and sigs[-1] == sigs[-2]:
                        cycle_detected = True
                    # Period-2: A→B→A→B
                    elif len(sigs) >= 4 and sigs[-1] == sigs[-3] and sigs[-2] == sigs[-4]:
                        cycle_detected = True
                    # Period-3: A→B→C→A→B→C
                    elif (len(sigs) >= 6
                          and sigs[-1] == sigs[-4]
                          and sigs[-2] == sigs[-5]
                          and sigs[-3] == sigs[-6]):
                        cycle_detected = True

                    if cycle_detected:
                        period = (1 if len(sigs) >= 2 and sigs[-1] == sigs[-2]
                                  else 2 if len(sigs) >= 4 and sigs[-1] == sigs[-3]
                                  else 3)
                        logger.info(
                            f"  runtime: tool call cycle detected "
                            f"(period={period}, tool={block.name}) — forcing wrap-up"
                        )
                        force_end = True

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

                self._spinner.update(resume_message)
                self._messenger.add_tool_results(tool_results)

                # Consecutive error injection
                if consecutive_errors >= cfg.max_consecutive_errors:
                    logger.info(
                        f"  runtime: {consecutive_errors} consecutive errors — injecting stop"
                    )
                    self._messenger.add_user_message(
                        "Multiple consecutive tool calls have failed. "
                        "Stop retrying and report the issue to the user."
                    )
                    consecutive_errors = 0

                # Tool call cap
                if total_tool_calls >= cfg.max_tool_calls:
                    logger.info(
                        f"  runtime: tool call cap ({cfg.max_tool_calls}) reached — forcing wrap-up"
                    )
                    hit_tool_call_cap = True
                    self._messenger.add_user_message(
                        "You have reached the tool call limit. "
                        "Stop all tool calls and give the user a final response."
                    )
                    force_end = True
