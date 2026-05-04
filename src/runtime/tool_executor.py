"""Shared tool-call execution helper.

This is the first extraction from the planned/direct ReAct loops. It keeps the
existing guard and spinner behavior in one place while preserving the loop
controllers around it.
"""

from __future__ import annotations

from dataclasses import dataclass

from runtime.escalation import Escalation
from runtime.events import RuntimeEvent, get_event_bus, get_runtime_identity
from runtime.guard import ActionGuard, GuardDecision
from runtime.tool_result import ToolResult


@dataclass(frozen=True)
class ToolExecutionOutcome:
    result: ToolResult
    guard_decision: GuardDecision
    guard_reason: str = ""


class ToolCallExecutor:
    def __init__(self, registry, guard: ActionGuard, user_gate, spinner) -> None:
        self._registry = registry
        self._guard = guard
        self._user_gate = user_gate
        self._spinner = spinner

    def execute(
        self,
        tool_name: str,
        tool_input: dict,
        *,
        resume_spinner_message: str,
        parent_identity=None,
    ) -> ToolExecutionOutcome:
        # Prefer the caller-supplied identity (carries pipeline/plan/step IDs);
        # fall back to process-level identity for calls outside the pipeline.
        base = parent_identity if parent_identity is not None else get_runtime_identity()
        identity = base.for_tool_call()
        get_event_bus().emit(
            RuntimeEvent(
                "tool.call.started",
                identity,
                payload={
                    "tool_name": tool_name,
                    "input_preview": str(tool_input)[:500],
                },
                stage="ToolCallExecutor",
            )
        )
        guard_decision, guard_reason = self._guard.check_tool_call(tool_name, tool_input)
        get_event_bus().emit(
            RuntimeEvent(
                "policy.decision",
                identity,
                payload={
                    "tool_name": tool_name,
                    "decision": guard_decision.value,
                    "reason": guard_reason,
                },
                stage="ToolCallExecutor",
            )
        )

        if guard_decision == GuardDecision.BLOCK:
            result = ToolResult.error(
                f"Tool call blocked by safety policy: {guard_reason}",
                error_code="policy_blocked",
            )
            self._emit_completed(identity, tool_name, result)
            return ToolExecutionOutcome(
                result=result,
                guard_decision=guard_decision,
                guard_reason=guard_reason,
            )

        if guard_decision == GuardDecision.ESCALATE:
            escalation = Escalation(
                reason=guard_reason,
                source="guard",
                tool_name=tool_name,
                tool_input=tool_input,
            )
            self._spinner.stop()
            if self._user_gate.prompt(escalation):
                self._guard.record_approval(tool_name, tool_input)
                self._spinner.start(f"Running {tool_name}...")
                result = self._safe_execute(tool_name, tool_input)
            else:
                result = ToolResult.error(
                    f"Tool call denied by user: {guard_reason}",
                    error_code="policy_denied",
                )
            self._spinner.start(resume_spinner_message)
            self._emit_completed(identity, tool_name, result)
            return ToolExecutionOutcome(
                result=result,
                guard_decision=guard_decision,
                guard_reason=guard_reason,
            )

        self._spinner.update(f"Running {tool_name}...")
        result = self._safe_execute(tool_name, tool_input)
        self._emit_completed(identity, tool_name, result)
        return ToolExecutionOutcome(
            result=result,
            guard_decision=guard_decision,
            guard_reason=guard_reason,
        )

    def _safe_execute(self, tool_name: str, tool_input: dict) -> ToolResult:
        try:
            tool = self._registry.get(tool_name)
            return ToolResult.success(tool.safe_execute(tool_input))
        except KeyError:
            return ToolResult.error(
                f"Error: tool '{tool_name}' does not exist.",
                error_code="tool_not_found",
            )

    def _emit_completed(self, identity, tool_name: str, result: ToolResult) -> None:
        get_event_bus().emit(
            RuntimeEvent(
                "tool.call.completed",
                identity,
                payload={
                    "tool_name": tool_name,
                    "ok": result.ok,
                    "error_code": result.error_code,
                    "result_preview": result.content[:500],
                    "result_bytes": len(result.content.encode(errors="replace")),
                },
                stage="ToolCallExecutor",
            )
        )
