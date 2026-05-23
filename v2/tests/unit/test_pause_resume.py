"""Tests for PauseResumePlugin + message reconstruction."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from arc.plugins.pause_resume import PauseResumePlugin
from arc.resume import messages_from_events, messages_from_session
from arc.runtime.events import EventType
from arc.runtime.hooks import ContentBlock, Message, PauseRequested


# ── Plugin: pause_check ────────────────────────────────────────────────────


def _plugin(tmp_path: Path) -> PauseResumePlugin:
    sessions = tmp_path / "sessions"
    (sessions / "SES_test").mkdir(parents=True)
    return PauseResumePlugin(sessions_dir=sessions, session_id="SES_test")


def test_pause_check_silent_when_no_trigger(tmp_path):
    p = _plugin(tmp_path)
    # Should NOT raise
    p.pause_check(ctx=None)


def test_request_pause_sets_flag_then_pause_check_raises(tmp_path):
    p = _plugin(tmp_path)
    p.request_pause()
    with pytest.raises(PauseRequested):
        p.pause_check(ctx=None)


def test_pause_check_clears_flag_after_raise(tmp_path):
    """Second pause_check after a pause should be silent until re-triggered."""
    p = _plugin(tmp_path)
    p.request_pause()
    with pytest.raises(PauseRequested):
        p.pause_check(ctx=None)
    # Now silent
    p.pause_check(ctx=None)


def test_signal_file_triggers_pause(tmp_path):
    p = _plugin(tmp_path)
    p.pause_signal_path.touch()
    with pytest.raises(PauseRequested):
        p.pause_check(ctx=None)


def test_signal_file_is_removed_after_pause(tmp_path):
    p = _plugin(tmp_path)
    p.pause_signal_path.touch()
    assert p.pause_signal_path.exists()
    with pytest.raises(PauseRequested):
        p.pause_check(ctx=None)
    assert not p.pause_signal_path.exists()


def test_pause_check_with_both_triggers_raises_once(tmp_path):
    p = _plugin(tmp_path)
    p.request_pause()
    p.pause_signal_path.touch()
    with pytest.raises(PauseRequested):
        p.pause_check(ctx=None)
    # Both cleared
    p.pause_check(ctx=None)


# ── Plugin: signal path location ──────────────────────────────────────────


def test_signal_path_is_under_session_dir(tmp_path):
    p = _plugin(tmp_path)
    assert p.pause_signal_path == tmp_path / "sessions" / "SES_test" / "pause"


# ── Reconstruct messages from events ──────────────────────────────────────


def _evt(t, payload=None, content=None):
    return {
        "event_id": f"EVT_{t}",
        "session_id": "SES_x",
        "turn_id": "TRN_x",
        "scope": "main",
        "parent_event_id": None,
        "ts": "2026-05-18T00:00:00",
        "ts_monotonic_ns": 0,
        "type": t,
        "stage": "test",
        "severity": "info",
        "duration_ms": None,
        "payload": payload or {},
        "content": content or {},
        "schema_version": 1,
    }


def test_reconstruct_empty_events_yields_empty():
    assert messages_from_events([]) == []


def test_reconstruct_simple_user_assistant_pair():
    events = [
        _evt(EventType.SESSION_STARTED),
        _evt(EventType.TURN_STARTED, content={"user_input": "hello"}),
        _evt(EventType.LLM_CALL_STARTED),
        _evt(EventType.LLM_CALL_COMPLETED,
             content={"response_content": [{"type": "text", "text": "hi back"}]}),
        _evt(EventType.TURN_ENDED),
        _evt(EventType.SESSION_ENDED),
    ]
    msgs = messages_from_events(events)
    assert len(msgs) == 2
    assert msgs[0].role == "user"
    assert msgs[0].content == "hello"
    assert msgs[1].role == "assistant"
    assert len(msgs[1].content) == 1
    assert msgs[1].content[0].type == "text"
    assert msgs[1].content[0].text == "hi back"


def test_reconstruct_with_tool_call():
    events = [
        _evt(EventType.TURN_STARTED, content={"user_input": "list files"}),
        _evt(EventType.LLM_CALL_COMPLETED, content={"response_content": [
            {"type": "tool_use", "tool_use_id": "tcl_1",
             "tool_name": "ls", "tool_input": {"path": "/tmp"}},
        ]}),
        _evt(EventType.TOOL_CALL_COMPLETED,
             payload={"tool_name": "ls", "tool_call_id": "tcl_1", "ok": True},
             content={"output": "a.txt\nb.txt"}),
        _evt(EventType.LLM_CALL_COMPLETED, content={"response_content": [
            {"type": "text", "text": "two files: a.txt, b.txt"},
        ]}),
    ]
    msgs = messages_from_events(events)
    assert len(msgs) == 4
    assert [m.role for m in msgs] == ["user", "assistant", "tool", "assistant"]
    # Tool message has function_response with the recorded output
    tool_msg = msgs[2]
    assert tool_msg.name == "ls"
    assert tool_msg.content[0]["function_response"]["response"]["result"] == "a.txt\nb.txt"


def test_reconstruct_with_tool_denial():
    events = [
        _evt(EventType.TURN_STARTED, content={"user_input": "rm something"}),
        _evt(EventType.LLM_CALL_COMPLETED, content={"response_content": [
            {"type": "tool_use", "tool_use_id": "tcl_1",
             "tool_name": "bash_exec", "tool_input": {"command": "rm -rf /"}},
        ]}),
        _evt(EventType.TOOL_CALL_DENIED,
             payload={"tool_name": "bash_exec", "tool_call_id": "tcl_1",
                      "reason": "blocked pattern matched"}),
    ]
    msgs = messages_from_events(events)
    assert len(msgs) == 3
    tool_msg = msgs[2]
    assert tool_msg.role == "tool"
    assert "denied" in tool_msg.content[0]["function_response"]["response"]["result"]


def test_reconstruct_paused_mid_turn():
    """Pause leaves a turn.started with no llm.call.completed for that iteration.
    The reconstructed list should still include the user input."""
    events = [
        _evt(EventType.TURN_STARTED, content={"user_input": "do something"}),
        # No llm.call.completed (pause hit between iterations)
        _evt(EventType.TURN_ENDED, payload={"error": "paused", "success": False}),
    ]
    msgs = messages_from_events(events)
    assert len(msgs) == 1
    assert msgs[0].role == "user"
    assert msgs[0].content == "do something"


def test_reconstruct_preserves_thought_signature_bytes():
    """Critical for Gemini 3+: thought_signature must survive round-trip."""
    import base64
    sig_bytes = b"\x00\x01\x02secret"
    sig_b64 = base64.b64encode(sig_bytes).decode("ascii")

    events = [
        _evt(EventType.LLM_CALL_COMPLETED, content={"response_content": [
            {
                "type": "tool_use", "tool_use_id": "tcl_x",
                "tool_name": "echo", "tool_input": {"text": "x"},
                "metadata": {"thought_signature": {"__bytes_b64__": sig_b64}},
            },
        ]}),
    ]
    msgs = messages_from_events(events)
    block = msgs[0].content[0]
    assert block.metadata is not None
    assert block.metadata["thought_signature"] == sig_bytes


def test_messages_from_session_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        messages_from_session(tmp_path / "missing")


def test_messages_from_session_reads_real_file(tmp_path):
    session_dir = tmp_path / "SES_x"
    session_dir.mkdir()
    events = [
        _evt(EventType.TURN_STARTED, content={"user_input": "hi"}),
        _evt(EventType.LLM_CALL_COMPLETED,
             content={"response_content": [{"type": "text", "text": "ok"}]}),
    ]
    (session_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n"
    )
    msgs = messages_from_session(session_dir)
    assert len(msgs) == 2


# ── AgentSession.initial_messages ─────────────────────────────────────────


def test_agent_session_starts_with_initial_messages():
    from arc.runtime.bus import EventBus, HookRegistry
    from arc.runtime.loop import AgentSession
    from arc.tools.base import ToolRegistry
    from arc.runtime.hooks import LLMRequest, LLMResponse
    from arc.config import (
        BootstrapConfig, Config, PluginsConfig, ProviderConfig,
        RetryConfig, RuntimeConfig, ToolsConfig, TUIConfig,
    )

    class FakeProv:
        name = "fake"
        def chat(self, req: LLMRequest) -> LLMResponse:
            # Capture the messages sent to verify initial ones are included
            self.last_req = req
            return LLMResponse(
                content=[ContentBlock(type="text", text="done")],
                stop_reason="end_turn",
                input_tokens=1, output_tokens=1, raw={},
            )

    cfg = Config(
        runtime=RuntimeConfig(
            workspace=".", max_iterations=5, max_tool_calls_per_turn=5,
            show_thinking=True, log_level="info",
            system_prompt="be concise",
            iteration_cap_message="wrap", tool_call_cap_message="wrap",
            cycle_detection_threshold=3, cycle_detected_message="cycle stop",
        ),
        provider=ProviderConfig(
            name="fake", model="fake-1", api_key_env="X", base_url=None,
            timeout_seconds=10.0,
            retry=RetryConfig(max_attempts=1, backoff_base_seconds=0.01, backoff_max_seconds=0.05),
            params={},
        ),
        tools=ToolsConfig(enabled=[], config={}),
        plugins=PluginsConfig(failure_threshold=3, exception_message_max_chars=500, enabled=[]),
        tui=TUIConfig(enabled=False, theme="default", inline_mode=True,
                      spinner_style="dots", prompt_prefix="❯ ",
                      show_token_counts=True, show_event_count=False,
                      show_thinking=True, tool_output_max_lines=30,
                      toolbar_enabled=True, input_history_enabled=True),
        bootstrap=BootstrapConfig(create_workspace_dir=False, write_example_session=False),
        source_path=None,  # type: ignore[arg-type]
    )

    initial = [
        Message(role="user", content="prior turn"),
        Message(role="assistant", content=[ContentBlock(type="text", text="prior reply")]),
    ]

    registry = HookRegistry(failure_threshold=3, exception_message_max_chars=500)
    bus = EventBus(registry)
    provider = FakeProv()
    sess = AgentSession(
        config=cfg, provider=provider, tools=ToolRegistry(),
        registry=registry, bus=bus, session_id="SES_resume",
        initial_messages=initial,
    )
    sess.run_turn("continue please")

    # The provider should have seen prior + new user message
    msgs = provider.last_req.messages
    # 3 messages: prior user, prior assistant, new user (added by run_turn)
    assert len(msgs) == 3
    assert msgs[0].content == "prior turn"
    assert msgs[2].content == "continue please"
