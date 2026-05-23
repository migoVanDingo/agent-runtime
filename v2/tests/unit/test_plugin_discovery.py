"""Tests for entry-point plugin discovery + the public plugin_api shim.

Discovery integrates with `importlib.metadata.entry_points`. We avoid the
flakiness of installing real packages by monkeypatching `entry_points()` to
return synthetic entry points.
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import EntryPoint

import pytest

from arc import plugin_api
from arc.plugins import (
    _BUILDERS,
    _refresh_builders,
    bind_bus_to_tools,
    builtin_plugin_names,
    last_discovery,
    merge_plugin_tools,
)
from arc.plugins.discovery import (
    ENTRY_POINT_GROUP,
    DiscoveryReport,
    discover,
)


# ── arc.plugin_api shim ────────────────────────────────────────────────────


def test_plugin_api_exports_stable_surface():
    """Plugins import from this exact set. Renames here are a breaking change."""
    required = {
        "Tool", "ToolError", "ToolInputSchema",
        "RuntimeEvent", "EventType",
        "PluginBuildContext", "SessionContext", "TurnOutcome", "UserInput",
        "Message", "ToolCall", "ToolResult", "ToolDenial",
        "LLMRequest", "LLMResponse", "ContentBlock",
        "PASS_THROUGH", "Cancelled", "PauseRequested",
    }
    missing = required - set(plugin_api.__all__)
    assert not missing, f"plugin_api missing public exports: {missing}"


def test_plugin_api_version_is_tuple():
    assert isinstance(plugin_api.__api_version__, tuple)
    assert plugin_api.__api_version__ >= (0, 1)


# ── Entry-point discovery ─────────────────────────────────────────────────


def _fake_entry_points(monkeypatch, *triples):
    """Install fake entry points for discovery to find.

    `triples` is an iterable of (name, value, package_version) or
    (name, value, package_version, loader_callable_or_exception).
    """
    eps = []
    builders: dict[str, object] = {}

    for t in triples:
        name, value, version = t[0], t[1], t[2]
        loader = t[3] if len(t) >= 4 else (lambda _cfg, _ctx: object())
        ep = EntryPoint(name=name, value=value, group=ENTRY_POINT_GROUP)
        eps.append(ep)
        builders[value] = loader

    class FakeEntryPoints(list):
        def select(self, *, group):
            return [e for e in self if group == ENTRY_POINT_GROUP]

    def _load(self):
        target = builders.get(self.value)
        if isinstance(target, Exception):
            raise target
        return target

    monkeypatch.setattr(EntryPoint, "load", _load)
    monkeypatch.setattr(
        "arc.plugins.discovery.entry_points",
        lambda: FakeEntryPoints(eps),
    )

    @dataclass
    class FakeDist:
        metadata: dict
        version: str
        entry_points: list

    fake_dist = FakeDist(
        metadata={"Name": "arc-plugin-fake"},
        version="0.0.1",
        entry_points=eps,
    )
    monkeypatch.setattr("arc.plugins.discovery.distributions", lambda: [fake_dist])


def test_discover_returns_empty_when_no_entry_points(monkeypatch):
    monkeypatch.setattr(
        "arc.plugins.discovery.entry_points",
        lambda: (lambda: type("E", (), {"select": lambda self, *, group: []})())(),
    )
    monkeypatch.setattr("arc.plugins.discovery.distributions", lambda: [])
    report = discover(builtin_names=set())
    assert report.discovered == []
    assert report.conflicts == []
    assert report.failures == []


def test_discover_picks_up_new_plugin(monkeypatch):
    builder = lambda _cfg, _ctx: object()  # noqa: E731
    _fake_entry_points(monkeypatch, ("alpha", "fake.mod:build", "1.0", builder))
    report = discover(builtin_names=set())
    assert len(report.discovered) == 1
    d = report.discovered[0]
    assert d.name == "alpha"
    assert d.builder is builder


def test_discover_skips_builtin_collision(monkeypatch):
    _fake_entry_points(monkeypatch, ("guard", "fake.mod:build", "1.0"))
    report = discover(builtin_names={"guard"})
    assert report.discovered == []
    assert len(report.conflicts) == 1
    assert report.conflicts[0].name == "guard"
    assert report.conflicts[0].kind == "builtin"


def test_discover_catches_load_failures(monkeypatch):
    broken = ImportError("fake module missing")
    _fake_entry_points(monkeypatch,
                        ("works", "ok.mod:build", "1.0"),
                        ("broken", "broken.mod:build", "0.1", broken))
    report = discover(builtin_names=set())
    names = {d.name for d in report.discovered}
    assert names == {"works"}
    assert len(report.failures) == 1
    assert report.failures[0].name == "broken"
    assert "ImportError" in report.failures[0].error


def test_discover_flags_non_callable_entry_point(monkeypatch):
    _fake_entry_points(monkeypatch,
                        ("notcallable", "broken.mod:thing", "1.0", "this-is-a-string"))
    report = discover(builtin_names=set())
    assert report.discovered == []
    assert len(report.failures) == 1
    assert "non-callable" in report.failures[0].error


def test_refresh_builders_merges_discovered(monkeypatch):
    builder = lambda _cfg, _ctx: object()  # noqa: E731
    _fake_entry_points(monkeypatch, ("external_thing", "fake:build", "0.1", builder))
    report = _refresh_builders()
    assert "external_thing" in _BUILDERS
    assert _BUILDERS["external_thing"] is builder
    assert any(d.name == "external_thing" for d in report.discovered)
    # Re-refresh without the fake entry point should drop it
    monkeypatch.setattr(
        "arc.plugins.discovery.entry_points",
        lambda: type("E", (), {"select": lambda self, *, group: []})(),
    )
    monkeypatch.setattr("arc.plugins.discovery.distributions", lambda: [])
    _refresh_builders()
    assert "external_thing" not in _BUILDERS


def test_last_discovery_is_populated_at_import():
    """Discovery runs at module import, so even with no entry points the
    report is non-None (it's just empty)."""
    report = last_discovery()
    assert report is not None
    assert isinstance(report, DiscoveryReport)


def test_builtin_plugin_names_matches_BUILDERS_subset():
    # Each builtin name must exist as a key in _BUILDERS
    builtins = builtin_plugin_names()
    for name in builtins:
        assert name in _BUILDERS


# ── provides_tools + bind_bus helpers ─────────────────────────────────────


class _StubTool:
    """Minimal Tool implementing the protocol structurally."""
    def __init__(self, name, *, accepts_bus=False):
        self.name = name
        self.description = "stub"
        self._bus_seen = None
        self._accepts_bus = accepts_bus

    @property
    def input_schema(self):
        from arc.plugin_api import ToolInputSchema
        return ToolInputSchema(properties={}, required=[])

    def execute(self, input):
        return "ok"

    def bind_bus(self, bus):
        if not self._accepts_bus:
            raise AssertionError("bind_bus called on tool that doesn't accept it")
        self._bus_seen = bus


class _StubPluginWithTools:
    name = "stub"
    def __init__(self, tools):
        self._tools = tools
    def provides_tools(self):
        return list(self._tools)


def test_merge_plugin_tools_registers_new_tools():
    from arc.plugin_api import ToolRegistry
    from arc.plugins import BuiltPlugin

    reg = ToolRegistry()
    plugin = _StubPluginWithTools([_StubTool("alpha"), _StubTool("beta")])
    built = BuiltPlugin(name="stub", instance=plugin, hooks_order={})

    added = merge_plugin_tools([built], reg)
    assert sorted(added) == ["alpha", "beta"]
    assert "alpha" in reg
    assert "beta" in reg


def test_merge_plugin_tools_raises_on_collision():
    from arc.plugin_api import ToolRegistry
    from arc.plugins import BuiltPlugin

    reg = ToolRegistry()
    reg.register(_StubTool("existing"))

    plugin = _StubPluginWithTools([_StubTool("existing")])
    built = BuiltPlugin(name="stub", instance=plugin, hooks_order={})

    with pytest.raises(ValueError, match="already registered"):
        merge_plugin_tools([built], reg)


def test_merge_plugin_tools_skips_plugins_without_provides_tools():
    from arc.plugin_api import ToolRegistry
    from arc.plugins import BuiltPlugin

    class _NoProvider:
        name = "noprov"

    reg = ToolRegistry()
    built = BuiltPlugin(name="noprov", instance=_NoProvider(), hooks_order={})
    added = merge_plugin_tools([built], reg)
    assert added == []


class _QuietTool:
    """Tool that does NOT implement bind_bus — should be skipped."""
    name = "quiet"
    description = "no bus"

    @property
    def input_schema(self):
        from arc.plugin_api import ToolInputSchema
        return ToolInputSchema(properties={}, required=[])

    def execute(self, input):
        return "ok"


def test_bind_bus_to_tools_calls_bind_bus_only_when_defined():
    from arc.plugin_api import ToolRegistry

    reg = ToolRegistry()
    chatty = _StubTool("chatty", accepts_bus=True)
    quiet = _QuietTool()
    reg.register(chatty)
    reg.register(quiet)

    bus = object()
    bound = bind_bus_to_tools(reg, bus)
    assert bound == ["chatty"]
    assert chatty._bus_seen is bus


# ── hooks_order auto-fill ─────────────────────────────────────────────────


class _PluginWithHooks:
    """Plugin that implements three hooks but the user's config didn't pin
    any priorities (the first-run-enablement shape)."""
    name = "fake_pl"

    def on_session_start(self, ctx): pass
    def on_session_end(self, ctx, outcome): pass
    def provides_tools(self): return []


def test_resolve_hooks_order_autofills_when_config_empty():
    from arc.plugins import DEFAULT_PLUGIN_HOOK_PRIORITY, _resolve_hooks_order

    resolved = _resolve_hooks_order(_PluginWithHooks(), configured={})
    # Both lifecycle hooks the plugin defines should get default priorities
    assert resolved["on_session_start"] == DEFAULT_PLUGIN_HOOK_PRIORITY
    assert resolved["on_session_end"] == DEFAULT_PLUGIN_HOOK_PRIORITY
    # provides_tools is not in ALL_HOOK_NAMES — it's a tool-contribution
    # contract, not a hook — so it should not appear
    assert "provides_tools" not in resolved
    # Hooks the plugin doesn't implement are absent (we don't blindly fill
    # every hook in the catalog)
    assert "before_llm_call" not in resolved


def test_resolve_hooks_order_preserves_explicit_config():
    """Built-in plugins pin specific priorities in defaults.py — auto-fill
    must NOT silently widen those to all implemented hooks. The test asserts
    we keep the configured shape unchanged when it's non-empty."""
    from arc.plugins import _resolve_hooks_order

    configured = {"on_event": 100}  # mimics the recorder's stub config in tests
    resolved = _resolve_hooks_order(_PluginWithHooks(), configured=configured)
    assert resolved == configured
