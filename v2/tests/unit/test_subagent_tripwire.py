"""Recursion-prohibition tripwire — both layers."""
from __future__ import annotations

import pytest

from arc.runtime.subagents.tripwire import inside_subagent, subagent_scope


def test_default_outside():
    assert inside_subagent() is False


def test_scope_sets_and_restores():
    with subagent_scope():
        assert inside_subagent() is True
    assert inside_subagent() is False


def test_scope_nests_and_restores():
    """Nested scopes still restore properly (defense against weird code)."""
    with subagent_scope():
        with subagent_scope():
            assert inside_subagent() is True
        assert inside_subagent() is True
    assert inside_subagent() is False


def test_runner_dispatch_inside_scope_raises():
    """Layer 2 — even if a runner were callable from inside a child,
    dispatch() refuses immediately."""
    from arc.runtime.subagents.errors import SubAgentRecursionError
    from arc.runtime.subagents.registry import SubAgentRegistry
    from arc.runtime.subagents.runner import SubAgentRunner
    from arc.runtime.bus import EventBus, HookRegistry
    from arc.tools.base import ToolRegistry

    # Minimal registry with one spec.
    from arc.subagent_api import SubAgentSpec
    reg = SubAgentRegistry(
        builtins={
            "x": SubAgentSpec(name="x", description="d", provider="anthropic",
                              model="m", system_prompt="p"),
        },
        entry_point_loader=lambda: [],
    )
    reg.discover({})
    bus_registry = HookRegistry(failure_threshold=3, exception_message_max_chars=500)
    bus = EventBus(bus_registry)
    runner = SubAgentRunner(
        registry=reg, parent_bus=bus, parent_tools=ToolRegistry(),
        parent_config=None,  # not reached — tripwire fires first
        arc_home=None, sessions_dir=None,  # type: ignore[arg-type]
    )

    with subagent_scope():
        with pytest.raises(SubAgentRecursionError):
            runner.dispatch("x", "task", parent_session_id="parent")
