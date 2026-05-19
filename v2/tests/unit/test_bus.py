"""Tests for hook registry + event bus."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import pytest

from arc.runtime.bus import EventBus, HookRegistry
from arc.runtime.events import EventType, RuntimeEvent
from arc.runtime.hooks import (
    Cancelled,
    LLMRequest,
    PauseRequested,
    SessionContext,
    ToolCall,
    ToolResult,
    UserInput,
)


# ── Test helpers ───────────────────────────────────────────────────────────


def _empty_session_ctx() -> SessionContext:
    return SessionContext(
        session_id="ses_test",
        workspace="/tmp",
        provider_name="gemini",
        provider_model="gemini-3.1-flash-live-preview",
        started_at="2026-05-17T00:00:00Z",
    )


def _empty_req() -> LLMRequest:
    return LLMRequest(messages=[], system="", tools=[], model="gemini", params={})


# ── Registration ────────────────────────────────────────────────────────────


def test_unknown_hook_name_raises_on_register():
    class P:
        name = "p1"
        def not_a_real_hook(self, ctx, req): pass

    reg = HookRegistry(failure_threshold=3, exception_message_max_chars=500)
    with pytest.raises(ValueError, match="unknown hook"):
        reg.register(P(), hooks_order={"not_a_real_hook": 10})


def test_missing_method_raises_on_register():
    class P:
        name = "p1"
        # declares the hook in order, but no method by that name

    reg = HookRegistry(failure_threshold=3, exception_message_max_chars=500)
    with pytest.raises(ValueError, match="no callable method"):
        reg.register(P(), hooks_order={"before_llm_call": 10})


def test_register_succeeds_when_method_exists():
    class P:
        name = "p1"
        def before_llm_call(self, ctx, req):
            return None

    reg = HookRegistry(failure_threshold=3, exception_message_max_chars=500)
    reg.register(P(), hooks_order={"before_llm_call": 10})


# ── fire() — value-threading hooks ─────────────────────────────────────────


def test_fire_with_no_plugins_returns_value_unchanged():
    reg = HookRegistry(failure_threshold=3, exception_message_max_chars=500)
    req = _empty_req()
    result = reg.fire("before_llm_call", req, ctx=None)
    assert result is req


def test_fire_threads_value_through_chain():
    class Mutator:
        name = "mutator"
        def __init__(self, suffix):
            self.suffix = suffix
        def before_llm_call(self, ctx, req):
            return replace(req, system=req.system + self.suffix)

    reg = HookRegistry(failure_threshold=3, exception_message_max_chars=500)
    reg.register(Mutator("A"), hooks_order={"before_llm_call": 10})
    reg.register(Mutator("B"), hooks_order={"before_llm_call": 20})

    req = _empty_req()
    result = reg.fire("before_llm_call", req, ctx=None)
    assert result.system == "AB"


def test_fire_lower_priority_runs_first():
    class Mutator:
        name = "x"
        def __init__(self, tag):
            self.tag = tag
        def before_llm_call(self, ctx, req):
            return replace(req, system=req.system + self.tag)

    reg = HookRegistry(failure_threshold=3, exception_message_max_chars=500)
    # Register in reverse priority order to prove priority matters, not registration order
    reg.register(Mutator("LAST"), hooks_order={"before_llm_call": 99})
    reg.register(Mutator("FIRST"), hooks_order={"before_llm_call": 1})

    result = reg.fire("before_llm_call", _empty_req(), ctx=None)
    assert result.system == "FIRSTLAST"


def test_returning_none_means_passthrough():
    class NoOp:
        name = "noop"
        def before_llm_call(self, ctx, req):
            return None  # = PASS_THROUGH

    reg = HookRegistry(failure_threshold=3, exception_message_max_chars=500)
    reg.register(NoOp(), hooks_order={"before_llm_call": 10})
    req = _empty_req()
    result = reg.fire("before_llm_call", req, ctx=None)
    assert result is req


# ── Failure isolation ─────────────────────────────────────────────────────


def test_plugin_exception_does_not_break_chain():
    class Broken:
        name = "broken"
        def before_llm_call(self, ctx, req):
            raise RuntimeError("boom")

    class Good:
        name = "good"
        def before_llm_call(self, ctx, req):
            return replace(req, system="GOOD")

    reg = HookRegistry(failure_threshold=3, exception_message_max_chars=500)
    bus = EventBus(reg)
    reg.register(Broken(), hooks_order={"before_llm_call": 10})
    reg.register(Good(), hooks_order={"before_llm_call": 20})

    result = reg.fire("before_llm_call", _empty_req(), ctx=None)
    assert result.system == "GOOD"


def test_on_event_failure_does_not_recurse_infinitely():
    """If an on_event plugin raises, the bus must NOT emit a plugin.hook.failed
    event back into the on_event chain — that's infinite recursion.
    Counts still increment so auto-disable still works.
    """
    n_calls = 0
    class BrokenRecorder:
        name = "broken-rec"
        def on_event(self, ctx, event):
            nonlocal n_calls
            n_calls += 1
            raise RuntimeError("disk full")

    reg = HookRegistry(failure_threshold=10, exception_message_max_chars=500)
    bus = EventBus(reg)
    reg.register(BrokenRecorder(), hooks_order={"on_event": 100})

    # Emit one event. The broken recorder raises. The registry tries to record
    # the failure but skips the emit because hook_name == "on_event".
    bus.emit(RuntimeEvent(type=EventType.TURN_STARTED))
    assert n_calls == 1  # NOT recursing into a second call


def test_failed_plugin_emits_event_on_bus():
    emitted = []
    class Recorder:
        name = "recorder"
        def on_event(self, ctx, event):
            emitted.append(event)

    class Broken:
        name = "broken"
        def before_llm_call(self, ctx, req):
            raise ValueError("nope")

    reg = HookRegistry(failure_threshold=3, exception_message_max_chars=500)
    bus = EventBus(reg)
    reg.register(Recorder(), hooks_order={"on_event": 100})
    reg.register(Broken(), hooks_order={"before_llm_call": 10})

    reg.fire("before_llm_call", _empty_req(), ctx=None)
    failures = [e for e in emitted if e.type == EventType.PLUGIN_HOOK_FAILED]
    assert len(failures) == 1
    assert failures[0].payload["plugin"] == "broken"
    assert failures[0].payload["hook"] == "before_llm_call"
    assert "nope" in failures[0].payload["exception_message"]


def test_plugin_auto_disabled_after_threshold():
    class Broken:
        name = "broken"
        def before_llm_call(self, ctx, req):
            raise RuntimeError("boom")

    emitted = []
    class Recorder:
        name = "recorder"
        def on_event(self, ctx, event):
            emitted.append(event)

    reg = HookRegistry(failure_threshold=2, exception_message_max_chars=500)
    bus = EventBus(reg)
    reg.register(Recorder(), hooks_order={"on_event": 100})
    reg.register(Broken(), hooks_order={"before_llm_call": 10})

    # Fire enough times to trip the threshold
    reg.fire("before_llm_call", _empty_req(), ctx=None)
    reg.fire("before_llm_call", _empty_req(), ctx=None)

    disabled = [e for e in emitted if e.type == EventType.PLUGIN_DISABLED]
    assert len(disabled) == 1
    assert disabled[0].payload["plugin"] == "broken"

    # Subsequent fires should NOT count the disabled plugin
    n_failures_before = sum(1 for e in emitted if e.type == EventType.PLUGIN_HOOK_FAILED)
    reg.fire("before_llm_call", _empty_req(), ctx=None)
    n_failures_after = sum(1 for e in emitted if e.type == EventType.PLUGIN_HOOK_FAILED)
    assert n_failures_after == n_failures_before


def test_pause_requested_propagates_not_caught():
    class Pauser:
        name = "pauser"
        def pause_check(self, ctx):
            raise PauseRequested()

    reg = HookRegistry(failure_threshold=3, exception_message_max_chars=500)
    bus = EventBus(reg)
    reg.register(Pauser(), hooks_order={"pause_check": 50})

    with pytest.raises(PauseRequested):
        reg.fire_observer("pause_check", ctx=None)


def test_cancelled_propagates_not_caught():
    class Canceller:
        name = "canceller"
        def pause_check(self, ctx):
            raise Cancelled()

    reg = HookRegistry(failure_threshold=3, exception_message_max_chars=500)
    bus = EventBus(reg)
    reg.register(Canceller(), hooks_order={"pause_check": 50})

    with pytest.raises(Cancelled):
        reg.fire_observer("pause_check", ctx=None)


# ── EventBus ───────────────────────────────────────────────────────────────


def test_bus_fanout_to_on_event_plugins():
    received = []
    class A:
        name = "a"
        def on_event(self, ctx, event):
            received.append(("a", event.type))
    class B:
        name = "b"
        def on_event(self, ctx, event):
            received.append(("b", event.type))

    reg = HookRegistry(failure_threshold=3, exception_message_max_chars=500)
    bus = EventBus(reg)
    reg.register(A(), hooks_order={"on_event": 10})
    reg.register(B(), hooks_order={"on_event": 20})

    bus.emit(RuntimeEvent(type=EventType.TURN_STARTED))
    bus.emit(RuntimeEvent(type=EventType.TURN_ENDED))

    assert received == [
        ("a", "turn.started"), ("b", "turn.started"),
        ("a", "turn.ended"), ("b", "turn.ended"),
    ]


def test_bus_emit_with_no_subscribers_is_silent():
    reg = HookRegistry(failure_threshold=3, exception_message_max_chars=500)
    bus = EventBus(reg)
    # Should not raise
    bus.emit(RuntimeEvent(type=EventType.TURN_STARTED))


def test_bus_passes_session_context_to_plugins():
    received_ctx = []
    class P:
        name = "p"
        def on_event(self, ctx, event):
            received_ctx.append(ctx)

    reg = HookRegistry(failure_threshold=3, exception_message_max_chars=500)
    bus = EventBus(reg)
    reg.register(P(), hooks_order={"on_event": 10})

    sctx = _empty_session_ctx()
    bus.set_session_context(sctx)
    bus.emit(RuntimeEvent(type=EventType.TURN_STARTED))

    assert received_ctx == [sctx]


# ── before_tool_call denial path ───────────────────────────────────────────


def test_before_tool_call_can_return_denial():
    from arc.runtime.hooks import ToolDenial

    class Denier:
        name = "denier"
        def before_tool_call(self, ctx, call):
            return ToolDenial(tool_call_id=call.tool_call_id, name=call.name, reason="nope")

    reg = HookRegistry(failure_threshold=3, exception_message_max_chars=500)
    bus = EventBus(reg)
    reg.register(Denier(), hooks_order={"before_tool_call": 10})

    call = ToolCall(tool_call_id="tcl_x", name="bash_exec", input={"command": "rm -rf /"})
    result = reg.fire("before_tool_call", call, ctx=None)
    assert isinstance(result, ToolDenial)
    assert result.reason == "nope"
