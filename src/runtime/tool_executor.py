"""Shared tool-call execution helper.

This is the first extraction from the planned/direct ReAct loops. It keeps the
existing guard and spinner behavior in one place while preserving the loop
controllers around it.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

from runtime.escalation import Escalation
from runtime.events import RuntimeEvent, get_event_bus, get_runtime_identity
from runtime.guard import ActionGuard, GuardDecision
from runtime.tool_result import ToolResult
from tools.base import ToolWeight

_PAGE_THRESHOLD_CHARS = 8_000


_NO_PAGE_TOOLS = frozenset({
    # These tools exist specifically to bring content into context — never page them.
    # Paging their output would swallow the content the agent is trying to read.
    "read_file", "read_file_lines", "read_url",
    # LLDB tools — their output IS the analysis data; paging defeats the purpose.
    # lldb_trace is always small (~200 chars/hit). lldb_step can be larger but the
    # agent must see register snapshots to synthesize code from them.
    "lldb_trace", "lldb_step",
})


# Detect error responses so they're never persisted as fake artifacts.
# A short tool response that starts with "Error:" or "error" (case-insensitive
# at line start) is almost certainly a failure, not analysis data.
_ERROR_PATTERN = re.compile(r"^\s*(?:error|exception|traceback|failed)\b", re.IGNORECASE)


def _looks_like_error(raw: str) -> bool:
    """Return True if the tool output is a short error message that should not be paged.

    Heuristics: starts with "Error:" / "Exception" / "Traceback" / "Failed" AND
    is short enough to fit comfortably in context. Long stderr dumps (e.g. a
    big traceback from a heavy tool) are still paged so the runtime can decide
    what to do without context blowup, but a 95-byte "Error: GHIDRA_HOME not set"
    must never become a permanent artifact.
    """
    if len(raw) > 4_000:
        return False  # too long for inline; let it page even if it starts with "Error"
    return bool(_ERROR_PATTERN.match(raw))


def _maybe_page(tool, raw: str, tool_input: dict) -> str:
    """Write large tool output to an artifact file; return a short summary instead.

    Prevents heavy reversing tool results (Ghidra decompile, radare2 full disasm,
    etc.) from saturating the LLM context and triggering TPM rate-limit 429s.
    The full output is preserved on disk; the agent reads it on demand.

    read_file / read_file_lines / read_url are never paged — their entire purpose
    is to bring content into context; paging them would swallow the content twice.

    Error responses are NEVER paged — persisting an error as a "decompile artifact"
    causes the agent to later read the error string and treat it as analysis data.
    Errors must surface up to the monitor for the runtime to decide on retry/replan.
    """
    if getattr(tool, "name", "") in _NO_PAGE_TOOLS:
        return raw
    # Never persist error responses as artifacts. A short error string from a
    # HEAVY tool would otherwise be written to disk as e.g. ghidra_decompile.txt
    # and the agent would re-read it expecting real decompile output.
    if _looks_like_error(raw):
        return raw
    if getattr(tool, "weight", ToolWeight.MODERATE) != ToolWeight.HEAVY and len(raw) <= _PAGE_THRESHOLD_CHARS:
        return raw

    from session_paths import analysis_dir

    binary_path = tool_input.get("path", "unknown")
    fn_suffix = tool_input.get("function", "")
    slug = f"{tool.name}_{fn_suffix}" if fn_suffix else tool.name
    slug = re.sub(r"[^\w\-]", "_", slug)

    artifact = analysis_dir(binary_path) / f"{slug}.txt"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(raw, encoding="utf-8")

    # Index chunks into the per-session RAG store for semantic retrieval.
    try:
        from rag import get_rag_service
        from rag.chunker import chunk_text
        if rag := get_rag_service():
            session_id = get_runtime_identity().session_id
            rag.index_chunks(session_id, chunk_text(raw, source_file=str(artifact)))
    except Exception:
        pass  # RAG indexing is best-effort; never block the main path

    n_chars = len(raw)
    n_tok = n_chars // 4
    return (
        f"[artifact saved → {artifact}  ({n_chars:,} chars / ~{n_tok:,} tokens)]\n"
        f"Full output written to disk. Use a file-read tool to access it when needed. "
        f"Do not re-run this tool."
    )


def _index_analysis_write(path: str, content: str) -> None:
    """Index agent-written analysis files into the per-session RAG chunk store.

    Called by ToolCallExecutor after a successful write_file call targeting
    _analysis/. The tool stays passive; the runtime decides what to index.
    """
    try:
        from rag import get_rag_service
        from rag.chunker import chunk_text
        if rag := get_rag_service():
            session_id = get_runtime_identity().session_id
            rag.index_chunks(session_id, chunk_text(content, source_file=path))
    except Exception:
        pass  # best-effort; never block the main path


@dataclass(frozen=True)
class ToolExecutionOutcome:
    result: ToolResult
    guard_decision: GuardDecision
    guard_reason: str = ""


class ToolCallExecutor:
    def __init__(self, registry, guard: ActionGuard, user_gate) -> None:
        self._registry = registry
        self._guard = guard
        self._user_gate = user_gate

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
        started = RuntimeEvent(
            "tool.call.started",
            identity,
            payload={
                "tool_name": tool_name,
                "tool_call_id": identity.tool_call_id,
                "input_preview": str(tool_input)[:500],
            },
            content={"input": tool_input},
            stage="ToolCallExecutor",
        )
        get_event_bus().emit(started)
        t0 = time.monotonic()
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
            self._emit_completed(identity, tool_name, result, started.event_id, t0)
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
            if self._user_gate.prompt(escalation):
                self._guard.record_approval(tool_name, tool_input)
                result = self._safe_execute(tool_name, tool_input)
            else:
                result = ToolResult.error(
                    f"Tool call denied by user: {guard_reason}",
                    error_code="policy_denied",
                )
            self._emit_completed(identity, tool_name, result, started.event_id, t0)
            return ToolExecutionOutcome(
                result=result,
                guard_decision=guard_decision,
                guard_reason=guard_reason,
            )

        result = self._safe_execute(tool_name, tool_input)
        self._emit_completed(identity, tool_name, result, started.event_id, t0)
        return ToolExecutionOutcome(
            result=result,
            guard_decision=guard_decision,
            guard_reason=guard_reason,
        )

    def _safe_execute(self, tool_name: str, tool_input: dict) -> ToolResult:
        try:
            tool = self._registry.get(tool_name)
            raw = tool.safe_execute(tool_input)
            paged = _maybe_page(tool, raw, tool_input)
            # Runtime indexes agent-written analysis files — tool stays passive.
            if tool_name == "write_file" and "_analysis" in str(tool_input.get("path", "")):
                _index_analysis_write(
                    tool_input.get("path", ""),
                    tool_input.get("content", ""),
                )
            return ToolResult.success(paged)
        except KeyError:
            return ToolResult.error(
                f"Error: tool '{tool_name}' does not exist.",
                error_code="tool_not_found",
            )

    def _emit_completed(
        self,
        identity,
        tool_name: str,
        result: ToolResult,
        parent_event_id: str,
        t0: float,
    ) -> None:
        duration_ms = int((time.monotonic() - t0) * 1000)
        get_event_bus().emit(
            RuntimeEvent(
                "tool.call.completed",
                identity,
                payload={
                    "tool_name": tool_name,
                    "tool_call_id": identity.tool_call_id,
                    "ok": result.ok,
                    "error_code": result.error_code,
                    "result_preview": result.content[:500],
                    "result_bytes": len(result.content.encode(errors="replace")),
                },
                content={
                    "output": result.content,
                    "output_bytes": len(result.content.encode(errors="replace")),
                },
                stage="ToolCallExecutor",
                parent_event_id=parent_event_id,
                duration_ms=duration_ms,
                severity="info" if result.ok else "error",
            )
        )
