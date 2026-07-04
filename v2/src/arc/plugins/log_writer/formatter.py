"""Pure functions that turn `RuntimeEvent` instances into log records.

Each function returns a `(logger_name, level, message)` triple. The plugin
turns those into actual `logging.LogRecord`s — keeping the format pure
makes them easy to unit-test without any logging side effects.

Logger names follow a per-concern namespace:
  arc.runtime    — session/turn lifecycle, cycle detection, pause/resume
  arc.llm        — LLM call boundaries
  arc.tool       — tool invocations, denials, failures
  arc.plugin     — plugin lifecycle and errors
"""
from __future__ import annotations

import logging

from arc.runtime.events import EventType, RuntimeEvent

# ── Visual marks ────────────────────────────────────────────────────────────
# Single-char glyphs that scan well in a log without breaking grep.

ARROW_IN = "→"
ARROW_OUT = "←"
FAILED = "✖"
DENIED = "⊘"
WARN_GLYPH = "⚠"

# Banner width — matches v1's visual style for long log scans
_BANNER_WIDTH = 56


def banner(text: str) -> str:
    """Format `── text ──────────…` to a stable width for visual scanning."""
    prefix = f"── {text} "
    return prefix + "─" * max(0, _BANNER_WIDTH - len(prefix))


def truncate(s: str, n: int) -> str:
    """Cap a string at n chars with a clear ellipsis marker."""
    if s is None:
        return ""
    s = str(s)
    if len(s) <= n:
        return s
    return s[:n] + f"… [+{len(s) - n} chars]"


# ── Per-event formatters ────────────────────────────────────────────────────


def format_event(event: RuntimeEvent, *, preview_chars: int = 200) -> list[tuple[str, int, str]]:
    """Map one event to one or more (logger_name, level, message) tuples.

    Multi-line output (banners) returns multiple tuples — the plugin emits
    each as its own log record so timestamps stay accurate.

    Unknown event types fall through to `_fmt_generic` which renders a
    compact line "<type>: k1=v1, k2=v2 …" so out-of-tree plugin events
    show up in session.log without requiring a custom formatter per type.
    """
    t = event.type
    fn = _DISPATCH.get(t)
    if fn is None:
        return _fmt_generic(event, preview_chars)
    try:
        return fn(event, preview_chars)
    except Exception as e:
        # Formatting must never crash the agent
        return [("arc.plugin", logging.ERROR,
                 f"log_writer formatter raised on {t}: {e!r}")]


def _fmt_generic(e: RuntimeEvent, preview_chars: int) -> list[tuple[str, int, str]]:
    """Fallback for unknown event types (typically plugin-emitted).

    Renders payload as "k=v" pairs, truncated. Routes by stage:
      - stage="tool"   → arc.tool logger
      - stage="plugin" → arc.plugin logger
      - else           → arc.runtime
    `severity` maps to log level.
    """
    if e.stage == "tool":
        logger = "arc.tool"
    elif e.stage == "plugin":
        logger = "arc.plugin"
    else:
        logger = "arc.runtime"

    sev = (e.severity or "info").lower()
    level = {
        "debug": logging.DEBUG,
        "info":  logging.INFO,
        "warn":  logging.WARNING,
        "warning": logging.WARNING,
        "error": logging.ERROR,
    }.get(sev, logging.INFO)

    pairs: list[str] = []
    for k, v in (e.payload or {}).items():
        text = repr(v) if not isinstance(v, (str, int, float, bool)) else str(v)
        pairs.append(f"{k}={truncate(text, 60)}")

    body = ", ".join(pairs) if pairs else "(no payload)"
    body = truncate(body, preview_chars)
    return [(logger, level, f"{e.type}  {body}")]


# ── Session lifecycle ──────────────────────────────────────────────────────


def _fmt_session_started(e: RuntimeEvent, n: int) -> list[tuple[str, int, str]]:
    p = e.payload
    return [
        ("arc.runtime", logging.INFO, "=" * _BANNER_WIDTH),
        ("arc.runtime", logging.INFO, f"  Session started"),
        ("arc.runtime", logging.INFO, f"  session_id: {e.session_id}"),
        ("arc.runtime", logging.INFO, f"  provider:   {p.get('provider')} / {p.get('model')}"),
        ("arc.runtime", logging.INFO, f"  workspace:  {p.get('workspace')}"),
        ("arc.runtime", logging.INFO, f"  tools:      {', '.join(p.get('tools', [])) or '(none)'}"),
        ("arc.runtime", logging.INFO, "=" * _BANNER_WIDTH),
    ]


def _fmt_session_ended(e: RuntimeEvent, n: int) -> list[tuple[str, int, str]]:
    p = e.payload
    return [
        ("arc.runtime", logging.INFO, "=" * _BANNER_WIDTH),
        ("arc.runtime", logging.INFO,
         f"  Session ended ({p.get('n_messages', 0)} messages)"),
        ("arc.runtime", logging.INFO, "=" * _BANNER_WIDTH),
    ]


# ── Turn lifecycle ─────────────────────────────────────────────────────────


def _fmt_turn_started(e: RuntimeEvent, n: int) -> list[tuple[str, int, str]]:
    user_input = e.content.get("user_input", "")
    return [
        ("arc.runtime", logging.INFO, banner(f"Turn ({e.turn_id})")),
        ("arc.runtime", logging.INFO, f"  user: {truncate(user_input, n)}"),
    ]


def _fmt_turn_ended(e: RuntimeEvent, n: int) -> list[tuple[str, int, str]]:
    p = e.payload
    final = e.content.get("final_response", "")
    lines = []
    if final:
        lines.append(("arc.runtime", logging.INFO,
                      f"  assistant: {truncate(final, n)}"))
    success = p.get("success", True)
    error = p.get("error")
    if success:
        lines.append(("arc.runtime", logging.INFO,
                      f"  turn complete  "
                      f"({p.get('n_llm_calls', 0)} llm, "
                      f"{p.get('n_tool_calls', 0)} tool)"))
    else:
        lines.append(("arc.runtime", logging.WARNING,
                      f"  turn ended with error: {error or 'unknown'}  "
                      f"({p.get('n_llm_calls', 0)} llm, "
                      f"{p.get('n_tool_calls', 0)} tool)"))
    return lines


# ── LLM call boundaries ────────────────────────────────────────────────────


def _fmt_llm_started(e: RuntimeEvent, n: int) -> list[tuple[str, int, str]]:
    p = e.payload
    return [
        ("arc.llm", logging.INFO,
         f"  {ARROW_IN} llm.call  "
         f"({p.get('model')}, {p.get('message_count', '?')} msgs, "
         f"{p.get('tool_count', '?')} tools)"),
    ]


def _fmt_llm_completed(e: RuntimeEvent, n: int) -> list[tuple[str, int, str]]:
    p = e.payload
    tokens_in = p.get('input_tokens', 0)
    tokens_out = p.get('output_tokens', 0)
    stop = p.get('stop_reason', '?')
    blocks = e.content.get("response_content", [])
    text_parts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
    text_preview = "".join(text_parts).strip()

    out: list[tuple[str, int, str]] = [
        ("arc.llm", logging.INFO,
         f"  {ARROW_OUT} llm.call  "
         f"(stop={stop}, tokens={tokens_in}/{tokens_out})"),
    ]
    if text_preview:
        out.append(("arc.llm", logging.INFO,
                    f"    text: {truncate(text_preview, n)}"))
    return out


def _fmt_llm_failed(e: RuntimeEvent, n: int) -> list[tuple[str, int, str]]:
    p = e.payload
    return [
        ("arc.llm", logging.ERROR,
         f"  {FAILED} llm.call failed: "
         f"{p.get('exception_type', '?')}: "
         f"{truncate(p.get('exception_message', ''), n)}"),
    ]


# ── Tool calls ─────────────────────────────────────────────────────────────


def _short_input(d: dict, n: int) -> str:
    if not d:
        return ""
    parts = [f"{k}={v!r}" for k, v in d.items()]
    joined = ", ".join(parts)
    return truncate(joined, n)


def _fmt_tool_started(e: RuntimeEvent, n: int) -> list[tuple[str, int, str]]:
    name = e.payload.get("tool_name", "?")
    inp = e.content.get("input", {})
    return [
        ("arc.tool", logging.INFO,
         f"  {ARROW_IN} {name}({_short_input(inp, n)})"),
    ]


def _fmt_tool_completed(e: RuntimeEvent, n: int) -> list[tuple[str, int, str]]:
    name = e.payload.get("tool_name", "?")
    output = e.content.get("output", "")
    out_lines = output.splitlines()
    if len(out_lines) > 1:
        summary = f"({len(out_lines)} lines, {len(output)} chars)"
    else:
        summary = f"({len(output)} chars)"
    lines: list[tuple[str, int, str]] = [
        ("arc.tool", logging.INFO,
         f"  {ARROW_OUT} {name} {summary}"),
    ]
    if output:
        lines.append(("arc.tool", logging.INFO,
                      f"    {truncate(output, n)}"))
    return lines


def _fmt_tool_failed(e: RuntimeEvent, n: int) -> list[tuple[str, int, str]]:
    name = e.payload.get("tool_name", "?")
    msg = e.payload.get("error_message", "(no message)")
    code = e.payload.get("error_code", "")
    return [
        ("arc.tool", logging.ERROR,
         f"  {FAILED} {name} [{code}]: {truncate(msg, n)}"),
    ]


def _fmt_tool_denied(e: RuntimeEvent, n: int) -> list[tuple[str, int, str]]:
    name = e.payload.get("tool_name", "?")
    reason = e.payload.get("reason", "")
    return [
        ("arc.tool", logging.WARNING,
         f"  {DENIED} {name} denied: {truncate(reason, n)}"),
    ]


# ── Plugin / runtime ───────────────────────────────────────────────────────


def _fmt_plugin_failed(e: RuntimeEvent, n: int) -> list[tuple[str, int, str]]:
    p = e.payload
    return [
        ("arc.plugin", logging.WARNING,
         f"  plugin {p.get('plugin')} failed in {p.get('hook')}: "
         f"{p.get('exception_type')}: {truncate(p.get('exception_message', ''), n)}"),
    ]


def _fmt_plugin_disabled(e: RuntimeEvent, n: int) -> list[tuple[str, int, str]]:
    p = e.payload
    return [
        ("arc.plugin", logging.WARNING,
         f"  plugin {p.get('plugin')} disabled: {p.get('reason')}"),
    ]


def _fmt_cycle_detected(e: RuntimeEvent, n: int) -> list[tuple[str, int, str]]:
    p = e.payload
    sig = p.get("signature", ("?", "?"))
    return [
        ("arc.runtime", logging.WARNING,
         f"  {WARN_GLYPH} cycle detected after {p.get('threshold', '?')} identical calls: "
         f"{sig[0] if isinstance(sig, (list, tuple)) else sig} — forcing wrap-up"),
    ]


def _fmt_context_packed(e: RuntimeEvent, n: int) -> list[tuple[str, int, str]]:
    p = e.payload
    n_before = p.get("n_messages_before", 0)
    n_after = p.get("n_messages_after", 0)
    bytes_dropped = p.get("bytes_dropped", 0)
    budget = p.get("budget_max_tokens")
    budget_note = f", budget={budget}" if budget else ""
    return [
        ("arc.runtime", logging.INFO,
         f"  context packed: {n_before} → {n_after} messages, "
         f"{bytes_dropped} bytes dropped{budget_note}"),
    ]


def _fmt_pause_requested(e: RuntimeEvent, n: int) -> list[tuple[str, int, str]]:
    return [("arc.runtime", logging.INFO, "  pause requested")]


def _fmt_conversation_cleared(e: RuntimeEvent, n: int) -> list[tuple[str, int, str]]:
    p = e.payload
    return [
        ("arc.runtime", logging.INFO,
         f"  /clear — conversation reset "
         f"({p.get('n_messages_cleared', 0)} messages; session continues)"),
    ]


def _fmt_safety_requested(e: RuntimeEvent, n: int) -> list[tuple[str, int, str]]:
    p = e.payload
    return [
        ("arc.safety", logging.INFO,
         f"  {WARN_GLYPH} safety: {p.get('pattern_name', '?')} → asking user "
         f"({truncate(p.get('command', ''), n)})"),
    ]


def _fmt_safety_allowed(e: RuntimeEvent, n: int) -> list[tuple[str, int, str]]:
    p = e.payload
    return [
        ("arc.safety", logging.INFO,
         f"  ✓ safety: {p.get('pattern_name', '?')} → allowed "
         f"(scope={p.get('scope', '?')})"),
    ]


def _fmt_safety_denied(e: RuntimeEvent, n: int) -> list[tuple[str, int, str]]:
    p = e.payload
    return [
        ("arc.safety", logging.WARNING,
         f"  {DENIED} safety: {p.get('pattern_name', '?')} → denied by user "
         f"({truncate(p.get('command', ''), n)})"),
    ]


def _fmt_session_aborted(e: RuntimeEvent, n: int) -> list[tuple[str, int, str]]:
    p = e.payload
    reason = p.get("reason", "?")
    if reason == "cost_cap":
        msg = (
            f"  {WARN_GLYPH} session aborted: cost ${p.get('running_usd', 0):.4f} "
            f"exceeded cap ${p.get('cap_usd', 0):.2f} "
            f"({p.get('provider', '?')}/{p.get('model', '?')})"
        )
    else:
        msg = f"  {WARN_GLYPH} session aborted: {reason}"
    return [("arc.runtime", logging.WARNING, msg)]


def _fmt_replay_target_completed(e: RuntimeEvent, n: int) -> list[tuple[str, int, str]]:
    p = e.payload
    return [
        ("arc.replay", logging.INFO,
         f"  ✓ replay target {p.get('provider', '?')}/{p.get('model', '?')} done — "
         f"{p.get('wallclock_seconds', 0):.1f}s, "
         f"cost ${p.get('cost_usd', 0):.4f}, "
         f"session {p.get('target_session_id', '?')[:14]}…"),
    ]


# ── Sub-agent dispatch (0020) ──────────────────────────────────────────────


def _fmt_subagent_dispatched(e: RuntimeEvent, n: int) -> list[tuple[str, int, str]]:
    p = e.payload
    retry = p.get("retry_attempt", 0) or 0
    retry_tag = f" (retry #{retry})" if retry else ""
    return [
        ("arc.subagent", logging.INFO,
         f"  {ARROW_IN} subagent {p.get('spec_name', '?')} dispatching → "
         f"{p.get('provider', '?')}/{p.get('model', '?')} "
         f"[child {str(p.get('child_session_id', '?'))[:14]}…]{retry_tag}"),
    ]


def _fmt_subagent_returned(e: RuntimeEvent, n: int) -> list[tuple[str, int, str]]:
    p = e.payload
    return [
        ("arc.subagent", logging.INFO,
         f"  {ARROW_OUT} subagent {p.get('spec_name', '?')} ok — "
         f"{p.get('turns', 0)} turns, "
         f"{p.get('tool_calls', 0)} tool calls, "
         f"${p.get('cost_usd', 0):.4f}, "
         f"{p.get('wallclock_s', 0):.1f}s, "
         f"output {p.get('output_chars', 0)} chars"),
    ]


def _fmt_subagent_aborted(e: RuntimeEvent, n: int) -> list[tuple[str, int, str]]:
    p = e.payload
    reason = p.get("reason", "?")
    return [
        ("arc.subagent", logging.WARNING,
         f"  {FAILED} subagent {p.get('spec_name', '?')} aborted: {reason} "
         f"({p.get('turns', 0)} turns, {p.get('wallclock_s', 0):.1f}s)"),
    ]


def _fmt_subagent_quota_exceeded(e: RuntimeEvent, n: int) -> list[tuple[str, int, str]]:
    p = e.payload
    return [
        ("arc.subagent", logging.WARNING,
         f"  {DENIED} subagent {p.get('spec_name', '?')} quota exceeded "
         f"(cap {p.get('cap', '?')}; denied task of {p.get('denied_task_chars', 0)} chars)"),
    ]


def _fmt_subagent_circuit_tripped(e: RuntimeEvent, n: int) -> list[tuple[str, int, str]]:
    p = e.payload
    return [
        ("arc.subagent", logging.WARNING,
         f"  {WARN_GLYPH} subagent {p.get('spec_name', '?')} circuit tripped "
         f"({p.get('consecutive_failures', '?')} consecutive failures); "
         f"locked for this session"),
    ]


def _fmt_subagent_retry_attempted(e: RuntimeEvent, n: int) -> list[tuple[str, int, str]]:
    p = e.payload
    return [
        ("arc.subagent", logging.WARNING,
         f"  {WARN_GLYPH} subagent {p.get('spec_name', '?')} transient retry #"
         f"{p.get('attempt', '?')} ({p.get('error_class', '?')}; "
         f"backoff {p.get('backoff_s', 0):.1f}s)"),
    ]


def _fmt_subagent_progress(e: RuntimeEvent, n: int) -> list[tuple[str, int, str]]:
    p = e.payload
    return [
        ("arc.subagent", logging.DEBUG,
         f"    [{p.get('spec_name', '?')}] {p.get('message', '?')}"),
    ]


# ── MCP client (0025) ──────────────────────────────────────────────────────


def _fmt_mcp_servers_configured(e: RuntimeEvent, n: int) -> list[tuple[str, int, str]]:
    p = e.payload
    return [("arc.mcp", logging.INFO,
             f"  mcp: {p.get('enabled_count', '?')} of {p.get('total', '?')} servers enabled")]


def _fmt_mcp_server_connected(e: RuntimeEvent, n: int) -> list[tuple[str, int, str]]:
    p = e.payload
    return [("arc.mcp", logging.INFO,
             f"  {ARROW_IN} mcp server {p.get('server', '?')} connected "
             f"({p.get('transport', '?')}, {p.get('tool_count', 0)} tools)")]


def _fmt_mcp_server_disconnected(e: RuntimeEvent, n: int) -> list[tuple[str, int, str]]:
    return [("arc.mcp", logging.INFO,
             f"  {ARROW_OUT} mcp server {e.payload.get('server', '?')} disconnected")]


def _fmt_mcp_tools_discovered(e: RuntimeEvent, n: int) -> list[tuple[str, int, str]]:
    p = e.payload
    return [("arc.mcp", logging.DEBUG,
             f"    mcp {p.get('server', '?')} tools: {truncate(', '.join(p.get('tools', [])), 200)}")]


def _fmt_mcp_tool_called(e: RuntimeEvent, n: int) -> list[tuple[str, int, str]]:
    p = e.payload
    return [("arc.mcp", logging.DEBUG,
             f"    {ARROW_IN} mcp call {p.get('tool', '?')} @ {p.get('server', '?')}")]


def _fmt_mcp_tool_result(e: RuntimeEvent, n: int) -> list[tuple[str, int, str]]:
    p = e.payload
    mark = FAILED if p.get("is_error") else ARROW_OUT
    return [("arc.mcp", logging.DEBUG,
             f"    {mark} mcp result {p.get('tool', '?')} ({p.get('bytes', 0)} bytes)")]


def _fmt_mcp_server_error(e: RuntimeEvent, n: int) -> list[tuple[str, int, str]]:
    p = e.payload
    return [("arc.mcp", logging.WARNING,
             f"  {WARN_GLYPH} mcp server {p.get('server', '?')} error: "
             f"{truncate(p.get('error', ''), 200)}")]


def _fmt_mcp_server_quarantined(e: RuntimeEvent, n: int) -> list[tuple[str, int, str]]:
    p = e.payload
    return [("arc.mcp", logging.WARNING,
             f"  {DENIED} mcp server {p.get('server', '?')} quarantined after "
             f"{p.get('strikes', '?')} strikes")]


# ── Dispatch table ─────────────────────────────────────────────────────────


_DISPATCH = {
    EventType.SESSION_STARTED: _fmt_session_started,
    EventType.SESSION_ENDED: _fmt_session_ended,
    EventType.TURN_STARTED: _fmt_turn_started,
    EventType.TURN_ENDED: _fmt_turn_ended,
    EventType.LLM_CALL_STARTED: _fmt_llm_started,
    EventType.LLM_CALL_COMPLETED: _fmt_llm_completed,
    EventType.LLM_CALL_FAILED: _fmt_llm_failed,
    EventType.TOOL_CALL_STARTED: _fmt_tool_started,
    EventType.TOOL_CALL_COMPLETED: _fmt_tool_completed,
    EventType.TOOL_CALL_FAILED: _fmt_tool_failed,
    EventType.TOOL_CALL_DENIED: _fmt_tool_denied,
    EventType.PLUGIN_HOOK_FAILED: _fmt_plugin_failed,
    EventType.PLUGIN_DISABLED: _fmt_plugin_disabled,
    EventType.RUNTIME_CYCLE_DETECTED: _fmt_cycle_detected,
    EventType.RUNTIME_CONTEXT_PACKED: _fmt_context_packed,
    EventType.PAUSE_REQUESTED: _fmt_pause_requested,
    EventType.CONVERSATION_CLEARED: _fmt_conversation_cleared,
    EventType.SAFETY_CONFIRMATION_REQUESTED: _fmt_safety_requested,
    EventType.SAFETY_CONFIRMATION_ALLOWED: _fmt_safety_allowed,
    EventType.SAFETY_CONFIRMATION_DENIED: _fmt_safety_denied,
    EventType.SESSION_ABORTED: _fmt_session_aborted,
    EventType.REPLAY_TARGET_COMPLETED: _fmt_replay_target_completed,
    EventType.SUBAGENT_DISPATCHED: _fmt_subagent_dispatched,
    EventType.SUBAGENT_RETURNED: _fmt_subagent_returned,
    EventType.SUBAGENT_ABORTED: _fmt_subagent_aborted,
    EventType.SUBAGENT_QUOTA_EXCEEDED: _fmt_subagent_quota_exceeded,
    EventType.SUBAGENT_CIRCUIT_TRIPPED: _fmt_subagent_circuit_tripped,
    EventType.SUBAGENT_RETRY_ATTEMPTED: _fmt_subagent_retry_attempted,
    EventType.SUBAGENT_PROGRESS: _fmt_subagent_progress,
    EventType.MCP_SERVERS_CONFIGURED: _fmt_mcp_servers_configured,
    EventType.MCP_SERVER_CONNECTED: _fmt_mcp_server_connected,
    EventType.MCP_SERVER_DISCONNECTED: _fmt_mcp_server_disconnected,
    EventType.MCP_TOOLS_DISCOVERED: _fmt_mcp_tools_discovered,
    EventType.MCP_TOOL_CALLED: _fmt_mcp_tool_called,
    EventType.MCP_TOOL_RESULT: _fmt_mcp_tool_result,
    EventType.MCP_SERVER_ERROR: _fmt_mcp_server_error,
    EventType.MCP_SERVER_QUARANTINED: _fmt_mcp_server_quarantined,
}
