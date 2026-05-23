"""Tests for the log_writer plugin + formatter."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from arc.plugins.log_writer import LogWriterPlugin
from arc.plugins.log_writer.formatter import (
    banner,
    format_event,
    truncate,
)
from arc.runtime.events import EventType, RuntimeEvent
from arc.runtime.hooks import SessionContext


# ── Pure helpers ───────────────────────────────────────────────────────────


def test_banner_pads_to_fixed_width():
    b = banner("Turn 1")
    assert b.startswith("── Turn 1 ──")
    assert len(b) >= 56


def test_truncate_short_string_unchanged():
    assert truncate("hello", 100) == "hello"


def test_truncate_long_string_adds_marker():
    s = "x" * 500
    out = truncate(s, 100)
    assert len(out) < 150
    assert "+400 chars" in out


def test_truncate_none_returns_empty():
    assert truncate(None, 10) == ""


# ── format_event dispatch table ───────────────────────────────────────────


def _evt(type_, payload=None, content=None, session_id="SES_x", turn_id="TRN_y"):
    return RuntimeEvent(
        type=type_,
        payload=payload or {},
        content=content or {},
        session_id=session_id,
        turn_id=turn_id,
    )


def test_format_unknown_event_falls_through_to_generic_formatter():
    """Unknown event types render via the generic fallback so plugin-emitted
    events show up in session.log without per-event-type formatters.
    """
    records = format_event(_evt("totally.made.up", payload={"foo": "bar", "n": 3}))
    assert len(records) == 1
    logger, _level, msg = records[0]
    assert logger == "arc.runtime"
    assert "totally.made.up" in msg
    assert "foo=bar" in msg
    assert "n=3" in msg


def test_generic_formatter_routes_by_stage():
    """Plugin/tool stage events route to arc.plugin / arc.tool loggers."""
    plugin_evt = RuntimeEvent(type="briefbot.ready", stage="plugin",
                              payload={"items": 12000}, session_id="S", turn_id="T")
    tool_evt = RuntimeEvent(type="example_shout.invoked", stage="tool",
                            payload={"name": "Alice"}, session_id="S", turn_id="T")
    plugin_records = format_event(plugin_evt)
    tool_records = format_event(tool_evt)
    assert plugin_records[0][0] == "arc.plugin"
    assert tool_records[0][0] == "arc.tool"


def test_format_session_started_includes_banner_and_provider():
    e = _evt(EventType.SESSION_STARTED, payload={
        "provider": "gemini", "model": "gemini-3.1-flash-lite-preview",
        "workspace": "/tmp", "tools": ["ls", "bash_exec"],
    })
    records = format_event(e)
    msgs = [r[2] for r in records]
    assert any("Session started" in m for m in msgs)
    assert any("gemini" in m for m in msgs)
    assert any("ls" in m and "bash_exec" in m for m in msgs)


def test_format_session_ended_has_message_count():
    e = _evt(EventType.SESSION_ENDED, payload={"n_messages": 7})
    records = format_event(e)
    msgs = [r[2] for r in records]
    assert any("Session ended" in m and "7" in m for m in msgs)


def test_format_turn_started_has_banner_and_user_input():
    e = _evt(EventType.TURN_STARTED, content={"user_input": "hello buddy"})
    records = format_event(e)
    msgs = [r[2] for r in records]
    assert any("── Turn" in m for m in msgs)
    assert any("user: hello buddy" in m for m in msgs)


def test_format_turn_ended_success():
    e = _evt(EventType.TURN_ENDED,
             payload={"success": True, "n_llm_calls": 2, "n_tool_calls": 1, "error": None},
             content={"final_response": "all done"})
    records = format_event(e)
    msgs = [r[2] for r in records]
    levels = [r[1] for r in records]
    assert any("assistant: all done" in m for m in msgs)
    assert any("turn complete" in m for m in msgs)
    assert logging.WARNING not in levels


def test_format_turn_ended_failure_uses_warn_level():
    e = _evt(EventType.TURN_ENDED,
             payload={"success": False, "error": "paused",
                      "n_llm_calls": 1, "n_tool_calls": 0},
             content={"final_response": ""})
    records = format_event(e)
    levels = [r[1] for r in records]
    msgs = [r[2] for r in records]
    assert logging.WARNING in levels
    assert any("paused" in m for m in msgs)


def test_format_llm_started_has_compact_summary():
    e = _evt(EventType.LLM_CALL_STARTED, payload={
        "model": "gemini-3.1-flash-lite-preview",
        "message_count": 3, "tool_count": 2,
    })
    records = format_event(e)
    msg = records[0][2]
    assert "→" in msg
    assert "llm.call" in msg
    assert "3" in msg and "2" in msg


def test_format_llm_completed_with_text_preview():
    e = _evt(EventType.LLM_CALL_COMPLETED,
             payload={"stop_reason": "end_turn", "input_tokens": 100, "output_tokens": 30},
             content={"response_content": [{"type": "text", "text": "hello world"}]})
    records = format_event(e)
    msgs = [r[2] for r in records]
    assert any("100/30" in m for m in msgs)
    assert any("hello world" in m for m in msgs)


def test_format_llm_completed_with_tool_use_no_text_preview():
    e = _evt(EventType.LLM_CALL_COMPLETED,
             payload={"stop_reason": "tool_use", "input_tokens": 50, "output_tokens": 10},
             content={"response_content": [
                 {"type": "tool_use", "tool_use_id": "x", "tool_name": "ls",
                  "tool_input": {"path": "."}},
             ]})
    records = format_event(e)
    msgs = [r[2] for r in records]
    # No "text:" line — there's no text content
    assert not any("text:" in m for m in msgs)


def test_format_llm_failed_is_error_level():
    e = _evt(EventType.LLM_CALL_FAILED, payload={
        "exception_type": "RuntimeError", "exception_message": "exploded",
    })
    records = format_event(e)
    assert records[0][1] == logging.ERROR
    assert "exploded" in records[0][2]


def test_format_tool_started_renders_input_compactly():
    e = _evt(EventType.TOOL_CALL_STARTED,
             payload={"tool_name": "ls"},
             content={"input": {"path": "/tmp", "depth": 2}})
    records = format_event(e)
    msg = records[0][2]
    assert "→" in msg
    assert "ls(" in msg
    assert "path=" in msg


def test_format_tool_completed_shows_line_count():
    e = _evt(EventType.TOOL_CALL_COMPLETED,
             payload={"tool_name": "ls", "ok": True, "output_bytes": 47},
             content={"output": "a.txt\nb.txt\nc.txt\nd.txt"})
    records = format_event(e)
    msgs = [r[2] for r in records]
    assert any("4 lines" in m for m in msgs)


def test_format_tool_failed_is_error_level():
    e = _evt(EventType.TOOL_CALL_FAILED, payload={
        "tool_name": "ls", "error_code": "tool_error",
        "error_message": "path is not a directory",
    })
    records = format_event(e)
    assert records[0][1] == logging.ERROR
    assert "ls" in records[0][2]
    assert "tool_error" in records[0][2]


def test_format_tool_denied_is_warn_level():
    e = _evt(EventType.TOOL_CALL_DENIED, payload={
        "tool_name": "bash_exec", "reason": "blocked pattern matched",
    })
    records = format_event(e)
    assert records[0][1] == logging.WARNING
    assert "denied" in records[0][2]


def test_format_cycle_detected_is_warn_level():
    e = _evt(EventType.RUNTIME_CYCLE_DETECTED, payload={
        "threshold": 3, "signature": ["ls", '{"path":"."}'],
    })
    records = format_event(e)
    assert records[0][1] == logging.WARNING
    assert "cycle" in records[0][2]
    assert "ls" in records[0][2]


def test_format_plugin_failed():
    e = _evt(EventType.PLUGIN_HOOK_FAILED, payload={
        "plugin": "guard", "hook": "before_tool_call",
        "exception_type": "ValueError", "exception_message": "bad input",
    })
    records = format_event(e)
    assert records[0][1] == logging.WARNING
    assert "guard" in records[0][2]


def test_formatter_never_raises_on_malformed_event():
    """If a formatter throws, we catch it and log the failure as an error."""
    e = _evt(EventType.LLM_CALL_COMPLETED,
             payload="not a dict — should crash",  # type: ignore
             content={})
    records = format_event(e)
    # Should NOT raise; should emit a single error record
    assert len(records) == 1
    assert records[0][1] == logging.ERROR


# ── Plugin end-to-end ──────────────────────────────────────────────────────


def _session_ctx() -> SessionContext:
    return SessionContext(
        session_id="SES_logwriter",
        workspace="/tmp",
        provider_name="fake",
        provider_model="fake-1",
        started_at="2026-05-18T00:00:00",
    )


def test_plugin_creates_log_file_on_session_start(tmp_path):
    sd = tmp_path / "sessions"
    sd.mkdir()
    p = LogWriterPlugin(sessions_dir=sd, session_id="SES_x")
    p.on_session_start(_session_ctx())
    assert (sd / "SES_x" / "session.log").is_file()
    p.on_session_end(_session_ctx(), outcome=None)


def test_plugin_writes_session_started_line(tmp_path):
    sd = tmp_path / "sessions"
    sd.mkdir()
    p = LogWriterPlugin(sessions_dir=sd, session_id="SES_x")
    p.on_session_start(_session_ctx())
    p.on_event(_session_ctx(), _evt(EventType.SESSION_STARTED, payload={
        "provider": "fake", "model": "fake-1",
        "workspace": "/tmp", "tools": ["echo"],
    }))
    p.on_session_end(_session_ctx(), outcome=None)

    text = (sd / "SES_x" / "session.log").read_text()
    assert "Session started" in text
    assert "fake / fake-1" in text
    assert "echo" in text


def test_plugin_writes_formatted_lines_in_order(tmp_path):
    sd = tmp_path / "sessions"
    sd.mkdir()
    p = LogWriterPlugin(sessions_dir=sd, session_id="SES_y")
    ctx = _session_ctx()
    p.on_session_start(ctx)

    p.on_event(ctx, _evt(EventType.TURN_STARTED,
                          content={"user_input": "hi"}))
    p.on_event(ctx, _evt(EventType.TOOL_CALL_STARTED,
                          payload={"tool_name": "ls"},
                          content={"input": {"path": "."}}))
    p.on_event(ctx, _evt(EventType.TOOL_CALL_COMPLETED,
                          payload={"tool_name": "ls", "ok": True, "output_bytes": 5},
                          content={"output": "a\nb"}))
    p.on_session_end(ctx, outcome=None)

    text = (sd / "SES_y" / "session.log").read_text()
    lines = text.splitlines()
    # Each line is prefixed with timestamp + level + display name
    assert any("[INFO] arc.runtime:" in line for line in lines)
    assert any("[INFO] arc.tool:" in line for line in lines)
    assert any("user: hi" in line for line in lines)
    assert any("→ ls(" in line for line in lines)
    assert any("← ls" in line for line in lines)


def test_plugin_respects_exclude_filter(tmp_path):
    sd = tmp_path / "sessions"
    sd.mkdir()
    p = LogWriterPlugin(
        sessions_dir=sd, session_id="SES_z",
        exclude_events=[EventType.TURN_STARTED],
    )
    ctx = _session_ctx()
    p.on_session_start(ctx)
    p.on_event(ctx, _evt(EventType.TURN_STARTED, content={"user_input": "hi"}))
    p.on_event(ctx, _evt(EventType.TURN_ENDED,
                          payload={"success": True, "n_llm_calls": 0, "n_tool_calls": 0},
                          content={"final_response": "done"}))
    p.on_session_end(ctx, outcome=None)

    text = (sd / "SES_z" / "session.log").read_text()
    assert "hi" not in text
    assert "turn complete" in text


def test_plugin_respects_include_filter(tmp_path):
    sd = tmp_path / "sessions"
    sd.mkdir()
    p = LogWriterPlugin(
        sessions_dir=sd, session_id="SES_inc",
        include_events=[EventType.TURN_STARTED],
    )
    ctx = _session_ctx()
    p.on_session_start(ctx)
    p.on_event(ctx, _evt(EventType.TURN_STARTED, content={"user_input": "x"}))
    p.on_event(ctx, _evt(EventType.LLM_CALL_STARTED, payload={
        "model": "y", "message_count": 1, "tool_count": 0,
    }))
    p.on_session_end(ctx, outcome=None)

    text = (sd / "SES_inc" / "session.log").read_text()
    assert "user: x" in text
    assert "llm.call" not in text


def test_plugin_respects_level_filter(tmp_path):
    """level=warning skips INFO-level records."""
    sd = tmp_path / "sessions"
    sd.mkdir()
    p = LogWriterPlugin(sessions_dir=sd, session_id="SES_lvl", level="warning")
    ctx = _session_ctx()
    p.on_session_start(ctx)
    p.on_event(ctx, _evt(EventType.LLM_CALL_STARTED, payload={
        "model": "y", "message_count": 1, "tool_count": 0,
    }))  # INFO
    p.on_event(ctx, _evt(EventType.TOOL_CALL_DENIED, payload={
        "tool_name": "bash_exec", "reason": "blocked",
    }))  # WARNING
    p.on_session_end(ctx, outcome=None)

    text = (sd / "SES_lvl" / "session.log").read_text()
    assert "llm.call" not in text  # INFO suppressed
    assert "denied" in text  # WARNING kept


def test_plugin_does_not_pollute_root_logger(tmp_path):
    """Records from the plugin must NOT show up in the root logger."""
    sd = tmp_path / "sessions"
    sd.mkdir()
    root_records: list = []

    class _Catcher(logging.Handler):
        def emit(self, record):
            root_records.append(record)

    root = logging.getLogger()
    catcher = _Catcher()
    root.addHandler(catcher)
    try:
        p = LogWriterPlugin(sessions_dir=sd, session_id="SES_iso")
        ctx = _session_ctx()
        p.on_session_start(ctx)
        p.on_event(ctx, _evt(EventType.LLM_CALL_STARTED, payload={
            "model": "y", "message_count": 1, "tool_count": 0,
        }))
        p.on_session_end(ctx, outcome=None)
    finally:
        root.removeHandler(catcher)

    # propagate=False means the catcher attached to root sees nothing
    plugin_records = [r for r in root_records if "arc._sess" in r.name]
    assert plugin_records == []


def test_plugin_truncates_long_outputs(tmp_path):
    sd = tmp_path / "sessions"
    sd.mkdir()
    p = LogWriterPlugin(sessions_dir=sd, session_id="SES_trunc", preview_chars=50)
    ctx = _session_ctx()
    p.on_session_start(ctx)

    long_output = "x" * 1000
    p.on_event(ctx, _evt(EventType.TOOL_CALL_COMPLETED,
                          payload={"tool_name": "echo", "ok": True, "output_bytes": 1000},
                          content={"output": long_output}))
    p.on_session_end(ctx, outcome=None)

    text = (sd / "SES_trunc" / "session.log").read_text()
    # The output should be truncated, with "+950 chars" marker
    assert "+950 chars" in text
    # Full 1000-char output should NOT appear
    assert long_output not in text


def test_plugin_cleans_up_handler_on_session_end(tmp_path):
    sd = tmp_path / "sessions"
    sd.mkdir()
    p = LogWriterPlugin(sessions_dir=sd, session_id="SES_clean")
    p.on_session_start(_session_ctx())
    assert p._handler is not None
    p.on_session_end(_session_ctx(), outcome=None)
    assert p._handler is None
