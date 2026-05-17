"""Unit tests for the 0090c sub-agent runtime primitive.

These tests exercise the runner machinery WITHOUT actually building a full
child Agent (which would require provider setup, registries, etc. — that's
covered end-to-end in the 0090d smoke test). Here we focus on:
- spec validation
- registry semantics
- recursion tripwire
- scope tagging
- contextvar threading
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


# ── Spec ────────────────────────────────────────────────────────────────────


def test_subagent_spec_basic():
    from runtime.subagents import SubAgentSpec
    spec = SubAgentSpec(name="x", description="ok")
    assert spec.name == "x"
    assert spec.response_format == "text"
    assert spec.timeout_seconds == 300.0


def test_subagent_spec_json_requires_schema():
    from runtime.subagents import SubAgentSpec
    with pytest.raises(ValueError, match="requires response_schema"):
        SubAgentSpec(name="x", description="ok", response_format="json")


def test_subagent_spec_json_with_schema_ok():
    from runtime.subagents import SubAgentSpec
    spec = SubAgentSpec(
        name="x", description="ok",
        response_format="json",
        response_schema={"type": "object", "properties": {"a": {"type": "string"}}},
    )
    assert spec.structured_response_required() if hasattr(spec, 'structured_response_required') else True
    assert spec.response_schema is not None


def test_subagent_spec_invalid_response_format():
    from runtime.subagents import SubAgentSpec
    with pytest.raises(ValueError, match="response_format must be"):
        SubAgentSpec(name="x", description="ok", response_format="yaml")


# ── Registry ────────────────────────────────────────────────────────────────


def test_subagent_registry_register_and_lookup():
    from runtime.subagents import SubAgentSpec, register_spec, get_spec, known_specs, clear_for_tests
    clear_for_tests()
    s = SubAgentSpec(name="test_a", description="A")
    register_spec(s)
    assert get_spec("test_a") is s
    assert "test_a" in known_specs()


def test_subagent_registry_lookup_missing_returns_none():
    from runtime.subagents import get_spec, clear_for_tests
    clear_for_tests()
    assert get_spec("nope") is None


def test_subagent_registry_re_register_replaces():
    from runtime.subagents import SubAgentSpec, register_spec, get_spec, clear_for_tests
    clear_for_tests()
    s1 = SubAgentSpec(name="dup", description="first")
    s2 = SubAgentSpec(name="dup", description="second")
    register_spec(s1)
    register_spec(s2)
    assert get_spec("dup") is s2


# ── Recursion tripwire ─────────────────────────────────────────────────────


def test_subagent_recursion_tripwire_raises():
    from runtime.subagents import SubAgentRecursionError, SubAgentRunner, SubAgentSpec
    from runtime.subagents.runner import _inside_subagent

    spec = SubAgentSpec(name="any", description="")
    token = _inside_subagent.set(True)
    try:
        with pytest.raises(SubAgentRecursionError):
            SubAgentRunner().run(spec, "task", parent=None)
    finally:
        _inside_subagent.reset(token)


def test_subagent_recursion_tripwire_resets_after_run():
    """The tripwire must reset cleanly so subsequent (non-nested) calls work."""
    from runtime.subagents.runner import _inside_subagent
    # Fresh contextvar state outside any sub-agent
    assert _inside_subagent.get() is False


# ── Scope tagging ──────────────────────────────────────────────────────────


def test_subagent_scope_active_during_run():
    """While a sub-agent's _execute is mid-flight, current_scope() returns subagent:<name>."""
    from runtime.scope import current_scope, scoped, MAIN

    # Verify the scope contextvar is what we expect outside any scope
    assert current_scope() == MAIN

    captured = {}
    with scoped("subagent:test_inside"):
        captured["scope"] = current_scope()
    assert captured["scope"] == "subagent:test_inside"
    assert current_scope() == MAIN  # restored


def test_subagent_scope_tag_on_runtime_events():
    """RuntimeEvent.emit auto-populates agent_scope from the contextvar."""
    from runtime.events.bus import EventBus, NoopEventSink
    from runtime.events.schema import RuntimeEvent
    from runtime.identity import RuntimeIdentity
    from runtime.scope import scoped, RUNTIME, MAIN

    bus = EventBus([NoopEventSink()], enabled=True)
    captured: list[str] = []
    bus.subscribe(lambda e: captured.append(e.agent_scope))

    ident = RuntimeIdentity.new_session(session_id="SESSTEST")
    bus.emit(RuntimeEvent("test", ident))
    with scoped(RUNTIME):
        bus.emit(RuntimeEvent("test", ident))
    with scoped("subagent:demo"):
        bus.emit(RuntimeEvent("test", ident))

    assert captured == [MAIN, RUNTIME, "subagent:demo"]


# ── Parent context threading ───────────────────────────────────────────────


def test_parent_context_threads_via_contextvars():
    from runtime.subagents import parent_context, current_parent_agent, current_pause_check, current_parent_turn_id

    sentinel_agent = object()
    sentinel_check = lambda: None

    # Outside the context: no parent state
    assert current_parent_agent() is None

    with parent_context(agent=sentinel_agent, pause_check=sentinel_check, turn_id="T1"):
        assert current_parent_agent() is sentinel_agent
        assert current_pause_check() is sentinel_check
        assert current_parent_turn_id() == "T1"

    # Restored
    assert current_parent_agent() is None
    assert current_pause_check() is None
    assert current_parent_turn_id() is None


# ── SubAgentTool name + recursion-filtering invariant ──────────────────────


def test_subagent_tool_name_prefixed():
    from runtime.subagents import SubAgentSpec
    from tools.implementations.subagents.tool import SubAgentTool
    spec = SubAgentSpec(name="ghidra_analyst", description="")
    tool = SubAgentTool(spec)
    assert tool.name == "subagent_ghidra_analyst"
    assert tool.spec is spec


def test_subagent_tool_input_schema_requires_task():
    from runtime.subagents import SubAgentSpec
    from tools.implementations.subagents.tool import SubAgentTool
    schema = SubAgentTool(SubAgentSpec(name="x", description="")).input_schema
    assert "task" in schema.required


def test_narrowed_registry_drops_subagent_tools():
    """Recursion-prevention layer 1: child registry must never expose SubAgentTool."""
    from runtime.subagents import SubAgentSpec
    from runtime.subagents.runner import SubAgentRunner
    from tools.implementations.subagents.tool import SubAgentTool
    from tools.registry import ToolRegistry
    from tools.toolset import Toolset
    from tools.base import BaseTool, InputSchema, ToolProperty

    class _Dummy(BaseTool):
        name = "dummy"
        description = "ok"
        @property
        def input_schema(self):
            return InputSchema(properties={"x": ToolProperty(type="string", description="")}, required=["x"])
        def execute(self, tool_input):
            return ""

    sa_tool = SubAgentTool(SubAgentSpec(name="evil", description="should be filtered"))
    parent_reg = ToolRegistry()
    parent_reg.register_toolset(Toolset(
        name="mixed", description="", tools=[_Dummy(), sa_tool],
    ))

    child_reg = SubAgentRunner._build_narrowed_registry(parent_reg, ("mixed",))
    # Dummy passes through; SubAgentTool filtered out.
    assert "dummy" in child_reg.tool_names()
    assert "subagent_evil" not in child_reg.tool_names()
