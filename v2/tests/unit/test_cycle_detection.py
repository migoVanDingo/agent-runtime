"""Tests for cycle detection in the ReAct loop.

The bug this prevents: model calls a tool, gets an error, calls the EXACT
same tool with the EXACT same input again, gets the same error, repeats
forever. Seen in v1 ad nauseum. v2 detects N consecutive identical
signatures and forces a wrap-up.
"""
from __future__ import annotations

from collections import deque

import pytest

from arc.config import (
    BootstrapConfig, Config, PluginsConfig, ProviderConfig,
    RetryConfig, RuntimeConfig, ToolsConfig, TUIConfig,
)
from arc.runtime.bus import EventBus, HookRegistry
from arc.runtime.events import EventType
from arc.runtime.hooks import ContentBlock, LLMRequest, LLMResponse, ToolCall
from arc.runtime.loop import (
    AgentSession,
    _call_signature,
    _is_period_1_cycle,
)
from arc.tools.base import ToolError, ToolInputSchema, ToolRegistry


# ── Pure helpers ───────────────────────────────────────────────────────────


def test_call_signature_canonicalizes_input_key_order():
    a = ToolCall(tool_call_id="x", name="t", input={"a": 1, "b": 2})
    b = ToolCall(tool_call_id="y", name="t", input={"b": 2, "a": 1})
    assert _call_signature(a) == _call_signature(b)


def test_call_signature_differentiates_by_name():
    a = ToolCall(tool_call_id="x", name="t1", input={"a": 1})
    b = ToolCall(tool_call_id="x", name="t2", input={"a": 1})
    assert _call_signature(a) != _call_signature(b)


def test_call_signature_differentiates_by_input():
    a = ToolCall(tool_call_id="x", name="t", input={"a": 1})
    b = ToolCall(tool_call_id="x", name="t", input={"a": 2})
    assert _call_signature(a) != _call_signature(b)


def test_period_1_cycle_detection_below_threshold():
    sigs = deque([("t", "x"), ("t", "x")], maxlen=8)
    assert _is_period_1_cycle(sigs, threshold=3) is False


def test_period_1_cycle_at_threshold():
    sigs = deque([("t", "x"), ("t", "x"), ("t", "x")], maxlen=8)
    assert _is_period_1_cycle(sigs, threshold=3) is True


def test_period_1_cycle_with_different_last_call_is_false():
    sigs = deque([("t", "x"), ("t", "x"), ("t", "y")], maxlen=8)
    assert _is_period_1_cycle(sigs, threshold=3) is False


def test_period_1_cycle_only_considers_last_N():
    """Earlier different calls don't matter — just the last `threshold`."""
    sigs = deque([("a", "1"), ("b", "2"), ("t", "x"), ("t", "x"), ("t", "x")],
                 maxlen=8)
    assert _is_period_1_cycle(sigs, threshold=3) is True


def test_period_1_cycle_threshold_1_or_0_disabled():
    """Threshold <= 1 makes no sense; the function should refuse to fire."""
    sigs = deque([("t", "x")], maxlen=8)
    assert _is_period_1_cycle(sigs, threshold=0) is False
    assert _is_period_1_cycle(sigs, threshold=1) is False


# ── End-to-end loop cycle detection ────────────────────────────────────────


def _cfg(cycle_threshold: int = 3) -> Config:
    return Config(
        runtime=RuntimeConfig(
            workspace=".", max_iterations=50, max_tool_calls_per_turn=50,
            show_thinking=True, log_level="info",
            system_prompt="be concise",
            iteration_cap_message="wrap (iter)",
            tool_call_cap_message="wrap (tools)",
            cycle_detection_threshold=cycle_threshold,
            cycle_detected_message="STOP — you are in a cycle",
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


class _CyclingProvider:
    """Always asks for the same tool call. Models that get stuck behave this way."""
    name = "fake"
    def __init__(self):
        self.calls = 0
    def chat(self, req):
        self.calls += 1
        # Always tool_use with identical input — would loop forever without detection
        return LLMResponse(
            content=[ContentBlock(
                type="tool_use", tool_use_id=f"tcl_{self.calls}",
                tool_name="ls", tool_input={"path": "/no-such-place"},
            )],
            stop_reason="tool_use",
            input_tokens=1, output_tokens=1, raw={},
        )


class _FailingLS:
    """Tool that always raises — like ls on a non-directory path."""
    name = "ls"
    description = "list dir"
    @property
    def input_schema(self):
        return ToolInputSchema(properties={"path": {"type": "string"}}, required=["path"])
    def execute(self, input):
        raise ToolError("path is not a directory")


class _EventRecorder:
    name = "rec"
    def __init__(self):
        self.events = []
    def on_event(self, ctx, event):
        self.events.append(event)


def _build_session(cycle_threshold: int = 3):
    cfg = _cfg(cycle_threshold)
    registry = HookRegistry(failure_threshold=3, exception_message_max_chars=500)
    bus = EventBus(registry)
    tools = ToolRegistry()
    tools.register(_FailingLS())
    rec = _EventRecorder()
    registry.register(rec, hooks_order={"on_event": 100})
    provider = _CyclingProvider()
    sess = AgentSession(
        config=cfg, provider=provider, tools=tools,
        registry=registry, bus=bus, session_id="SES_cycle",
    )
    # Patch _force_wrap_up to return a known string without calling provider again
    sess._force_wrap_up = lambda ctx: "wrapped up after cycle"
    return sess, rec, provider


def test_cycle_detected_after_threshold_identical_calls():
    sess, rec, provider = _build_session(cycle_threshold=3)
    outcome = sess.run_turn("trigger a cycle")

    assert not outcome.success
    assert outcome.error == "cycle"
    assert outcome.final_response == "wrapped up after cycle"
    # Should have stopped well short of max_iterations (50)
    # 3 identical calls trigger; the 4th iteration sees the cycle and breaks
    assert provider.calls <= 5


def test_cycle_detected_emits_event():
    sess, rec, _ = _build_session(cycle_threshold=3)
    sess.run_turn("trigger a cycle")
    cycle_evts = [e for e in rec.events
                  if e.type == EventType.RUNTIME_CYCLE_DETECTED]
    assert len(cycle_evts) == 1
    assert cycle_evts[0].payload["threshold"] == 3
    assert cycle_evts[0].payload["signature"][0] == "ls"


def test_higher_threshold_allows_more_repetition_before_firing():
    sess, _, provider = _build_session(cycle_threshold=5)
    sess.run_turn("trigger a cycle")
    # With threshold 5, need 5 identical calls before cycle fires
    assert provider.calls >= 5


def test_no_cycle_when_calls_vary():
    """If the model varies its inputs, cycle detection stays silent."""
    cfg = _cfg(cycle_threshold=2)
    registry = HookRegistry(failure_threshold=3, exception_message_max_chars=500)
    bus = EventBus(registry)
    tools = ToolRegistry()
    tools.register(_FailingLS())
    rec = _EventRecorder()
    registry.register(rec, hooks_order={"on_event": 100})

    class VaryingProvider:
        name = "fake"
        def __init__(self):
            self.n = 0
        def chat(self, req):
            self.n += 1
            if self.n > 3:
                # End the turn
                return LLMResponse(
                    content=[ContentBlock(type="text", text="done")],
                    stop_reason="end_turn",
                    input_tokens=1, output_tokens=1, raw={},
                )
            return LLMResponse(
                content=[ContentBlock(
                    type="tool_use", tool_use_id=f"x{self.n}",
                    tool_name="ls", tool_input={"path": f"/path-{self.n}"},
                )],
                stop_reason="tool_use",
                input_tokens=1, output_tokens=1, raw={},
            )

    sess = AgentSession(
        config=cfg, provider=VaryingProvider(), tools=tools,
        registry=registry, bus=bus, session_id="SES_varying",
    )
    outcome = sess.run_turn("vary inputs")
    assert outcome.success
    cycle_evts = [e for e in rec.events
                  if e.type == EventType.RUNTIME_CYCLE_DETECTED]
    assert cycle_evts == []
