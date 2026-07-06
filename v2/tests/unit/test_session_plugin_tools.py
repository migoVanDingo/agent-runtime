"""Integration: provides_tools() + bind_bus(bus) plumbed through AgentSession.

Verifies the runtime ordering:
  on_session_start → provides_tools → bind_bus → session.started
plus the observability events (PLUGIN_TOOLS_REGISTERED) that follow.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from arc.config import load
from arc.defaults import DEFAULT_CONFIG_YAML
from arc.plugin_api import (
    LLMResponse,
    RuntimeEvent,
    Tool,
    ToolInputSchema,
)
from arc.runtime.bus import EventBus, HookRegistry
from arc.runtime.events import EventType
from arc.runtime.loop import AgentSession
from arc.tools.base import ToolRegistry


# ── Minimal Config helper ─────────────────────────────────────────────────


@pytest.fixture
def cfg(tmp_path):
    """Load the default config from the shipped DEFAULT_CONFIG_YAML so we
    don't have to hand-maintain field lists as Config evolves."""
    p = tmp_path / "config.yml"
    p.write_text(DEFAULT_CONFIG_YAML)
    return load(p)


# ── Stub Tool + Plugin ────────────────────────────────────────────────────


class _Tool:
    def __init__(self, name: str, *, accepts_bus: bool):
        self.name = name
        self.description = "stub"
        self.bus_seen = None
        self._accepts_bus = accepts_bus

    @property
    def input_schema(self) -> ToolInputSchema:
        return ToolInputSchema(properties={}, required=[])

    def execute(self, input):
        return "ok"

    def bind_bus(self, bus):  # only defined if accepts_bus
        self.bus_seen = bus


class _QuietTool:
    name = "quiet"
    description = "no bus"

    @property
    def input_schema(self):
        return ToolInputSchema(properties={}, required=[])

    def execute(self, input):
        return "ok"


class _PluginWithTools:
    """A plugin that contributes tools via provides_tools().
    Mirrors the briefbot/template shape."""

    name = "fake_plugin"

    def __init__(self):
        self._tools = [_Tool("alpha", accepts_bus=True),
                       _Tool("beta",  accepts_bus=True),
                       _QuietTool()]
        self.session_started_at = None

    def on_session_start(self, ctx):
        self.session_started_at = ctx.session_id

    def provides_tools(self):
        return list(self._tools)


# ── Fake provider so AgentSession doesn't need a real LLM ────────────────


class _FakeProvider:
    name = "fake"

    def call(self, req):
        return LLMResponse(
            content=[], stop_reason="end_turn",
            input_tokens=0, output_tokens=0, raw={},
        )


# ── Tests ─────────────────────────────────────────────────────────────────


def test_merge_disabled_keeps_preregistered_tools(cfg):
    # Replay's shape: the registry is pre-seeded with recorded-tool stubs
    # whose names collide with what live plugins contribute. With
    # merge_contributed_tools=False, start() must not raise and the
    # pre-registered tool must win.
    tools = ToolRegistry()
    stub = _QuietTool()
    stub.name = "alpha"  # collides with _PluginWithTools's first tool
    tools.register(stub)

    registry = HookRegistry(failure_threshold=3, exception_message_max_chars=500)
    bus = EventBus(registry)
    plugin = _PluginWithTools()
    registry.register(plugin, hooks_order={"on_session_start": 10})

    sess = AgentSession(
        config=cfg, provider=_FakeProvider(), tools=tools,
        registry=registry, bus=bus, session_id="SES_test",
        merge_contributed_tools=False,
    )
    sess.start()  # would raise "already registered" with merges on

    assert tools.get("alpha") is stub          # stub won
    assert "beta" not in tools                 # nothing else merged either
    assert plugin.session_started_at == "SES_test"  # hooks still ran


def test_provides_tools_merged_into_registry_at_session_start(cfg):
    tools = ToolRegistry()  # starts empty
    registry = HookRegistry(failure_threshold=3, exception_message_max_chars=500)
    bus = EventBus(registry)

    plugin = _PluginWithTools()
    registry.register(plugin, hooks_order={"on_session_start": 10})

    sess = AgentSession(
        config=cfg, provider=_FakeProvider(), tools=tools,
        registry=registry, bus=bus, session_id="SES_test",
    )
    sess.start()

    # All 3 tools from the plugin should now be in the registry
    assert "alpha" in tools
    assert "beta" in tools
    assert "quiet" in tools
    assert plugin.session_started_at == "SES_test"


def test_bind_bus_called_only_on_tools_that_define_it(cfg):
    tools = ToolRegistry()
    registry = HookRegistry(failure_threshold=3, exception_message_max_chars=500)
    bus = EventBus(registry)

    plugin = _PluginWithTools()
    registry.register(plugin, hooks_order={"on_session_start": 10})

    sess = AgentSession(
        config=cfg, provider=_FakeProvider(), tools=tools,
        registry=registry, bus=bus, session_id="SES_test",
    )
    sess.start()

    alpha = tools.get("alpha")
    beta = tools.get("beta")
    quiet = tools.get("quiet")
    assert alpha.bus_seen is bus
    assert beta.bus_seen is bus
    # QuietTool has no bus_seen attribute because it has no bind_bus
    assert not hasattr(quiet, "bus_seen")


def test_session_started_event_includes_plugin_tools(cfg):
    """The session.started event's `tools` payload should reflect the FINAL
    tool list — built-in + plugin-contributed. Tests the ordering: merge
    must happen before the event is emitted."""
    tools = ToolRegistry()
    registry = HookRegistry(failure_threshold=3, exception_message_max_chars=500)
    bus = EventBus(registry)

    captured: list[RuntimeEvent] = []

    class _Recorder:
        name = "rec"
        def on_event(self, ctx, event):
            captured.append(event)

    registry.register(_Recorder(), hooks_order={"on_event": 1})
    plugin = _PluginWithTools()
    registry.register(plugin, hooks_order={"on_session_start": 10})

    sess = AgentSession(
        config=cfg, provider=_FakeProvider(), tools=tools,
        registry=registry, bus=bus, session_id="SES_x",
    )
    sess.start()

    started = [e for e in captured if e.type == EventType.SESSION_STARTED]
    assert len(started) == 1
    tools_in_event = set(started[0].payload["tools"])
    assert {"alpha", "beta", "quiet"} <= tools_in_event


def test_plugin_tools_registered_event_emitted(cfg):
    tools = ToolRegistry()
    registry = HookRegistry(failure_threshold=3, exception_message_max_chars=500)
    bus = EventBus(registry)

    captured: list[RuntimeEvent] = []

    class _Recorder:
        name = "rec"
        def on_event(self, ctx, event):
            captured.append(event)

    registry.register(_Recorder(), hooks_order={"on_event": 1})
    registry.register(_PluginWithTools(), hooks_order={"on_session_start": 10})

    sess = AgentSession(
        config=cfg, provider=_FakeProvider(), tools=tools,
        registry=registry, bus=bus, session_id="SES_x",
    )
    sess.start()

    registered = [e for e in captured if e.type == EventType.PLUGIN_TOOLS_REGISTERED]
    assert len(registered) == 1
    assert set(registered[0].payload["tools"]) == {"alpha", "beta", "quiet"}


def test_tool_name_collision_raises_at_start(cfg):
    tools = ToolRegistry()
    tools.register(_Tool("alpha", accepts_bus=False))  # pre-existing
    registry = HookRegistry(failure_threshold=3, exception_message_max_chars=500)
    bus = EventBus(registry)
    registry.register(_PluginWithTools(), hooks_order={"on_session_start": 10})

    sess = AgentSession(
        config=cfg, provider=_FakeProvider(), tools=tools,
        registry=registry, bus=bus, session_id="SES_x",
    )
    with pytest.raises(ValueError, match="alpha"):
        sess.start()


def test_session_without_plugins_works(cfg):
    """Regression: the new merge step must be a no-op when no plugin
    declares provides_tools. Sanity check we didn't break vanilla sessions.
    """
    tools = ToolRegistry()
    registry = HookRegistry(failure_threshold=3, exception_message_max_chars=500)
    bus = EventBus(registry)

    sess = AgentSession(
        config=cfg, provider=_FakeProvider(), tools=tools,
        registry=registry, bus=bus, session_id="SES_x",
    )
    sess.start()  # should not raise
    sess.end()
