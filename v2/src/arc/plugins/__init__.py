"""Plugin factory.

Builds plugin instances from `config.plugins.enabled`. Each plugin has a
builder function that receives the runtime context it needs (session_id,
home dir, plugin-specific config dict).

Adding a built-in plugin = add a builder in `_BUILDERS` below. External
plugins are discovered automatically via the `arc.plugins` entry-point
group (see `discovery.py`); they're merged into the builder table at import
time, with built-ins always winning on name conflict.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from arc.config import PluginsConfig
from arc.plugins.guard import GuardPlugin
from arc.plugins.jsonl_recorder import JSONLRecorder
from arc.plugins.log_writer import LogWriterPlugin
from arc.plugins.max_cost import MaxCostPlugin
from arc.plugins.pause_resume import PauseResumePlugin
from arc.plugins.safety_gate import Pattern as SafetyPattern
from arc.plugins.safety_gate import SafetyGatePlugin
from arc.plugins.sliding_window_context import SlidingWindowContextPlugin
from arc.tui.pricing import PricingTable
from arc.user_gate import NoOpGate, UserGate


@dataclass(frozen=True)
class BuiltPlugin:
    """A constructed plugin + its hook ordering. Ready for registry.register()."""
    name: str
    instance: Any
    hooks_order: dict[str, int]


@dataclass(frozen=True)
class PluginBuildContext:
    """Things the runtime knows that plugins may need at construction.

    `user_gate` is consumed by plugins that need to prompt the human
    (notably the guard's escalation flow). Callers wire the right gate:
      arc (interactive) → TUIGate
      arc run           → NoOpGate (auto-denies escalations)
      tests             → fakes

    `bus` is consumed by plugins that EMIT events back into the stream
    (e.g., the context manager emits runtime.context_packed when it
    filters). Typed as Any to avoid importing EventBus here (would
    create a possible circular if EventBus ever needs plugin-side types).
    """
    sessions_dir: Path
    session_id: str
    config_snapshot_yaml: str | None = None
    user_gate: UserGate | None = None
    bus: Any = None


def _build_jsonl_recorder(cfg: dict, build_ctx: PluginBuildContext) -> Any:
    # The JSONL recorder takes no config knobs of its own (yet) — everything
    # it needs comes from build_ctx (paths).
    return JSONLRecorder(
        sessions_dir=build_ctx.sessions_dir,
        session_id=build_ctx.session_id,
        config_snapshot_yaml=build_ctx.config_snapshot_yaml,
    )


def _build_guard(cfg: dict, build_ctx: PluginBuildContext) -> Any:
    gate = build_ctx.user_gate or NoOpGate()
    return GuardPlugin(
        allowlist_tools=list(cfg.get("allowlist_tools", [])),
        blocklist_patterns=list(cfg.get("blocklist_patterns", [])),
        escalation_required_patterns=list(cfg.get("escalation_required_patterns", [])),
        delegate_only_tools=dict(cfg.get("delegate_only_tools", {})),
        user_gate=gate,
    )


def _build_pause_resume(cfg: dict, build_ctx: PluginBuildContext) -> Any:
    return PauseResumePlugin(
        sessions_dir=build_ctx.sessions_dir,
        session_id=build_ctx.session_id,
    )


def _build_log_writer(cfg: dict, build_ctx: PluginBuildContext) -> Any:
    return LogWriterPlugin(
        sessions_dir=build_ctx.sessions_dir,
        session_id=build_ctx.session_id,
        level=str(cfg.get("level", "info")),
        preview_chars=int(cfg.get("preview_chars", 200)),
        include_events=list(cfg.get("include_events", []) or []),
        exclude_events=list(cfg.get("exclude_events", []) or []),
    )


def _build_safety_gate(cfg: dict, build_ctx: PluginBuildContext) -> Any:
    gate = build_ctx.user_gate or NoOpGate()
    customs = [
        SafetyPattern(
            name=str(p["name"]),
            description=str(p.get("description", "")),
            regex=str(p["regex"]),
        )
        for p in cfg.get("custom_patterns", []) or []
    ]
    p = SafetyGatePlugin(
        enabled=bool(cfg.get("enabled", True)),
        bypass_mode=bool(cfg.get("bypass_mode", False)),
        enabled_pattern_names=list(cfg.get("enabled_patterns", [])),
        custom_patterns=customs,
        user_gate=gate,
    )
    if build_ctx.bus is not None:
        p.bind_bus(build_ctx.bus)
    return p


def _build_sliding_window_context(cfg: dict, build_ctx: PluginBuildContext) -> Any:
    max_tokens = cfg.get("max_tokens")
    p = SlidingWindowContextPlugin(
        keep_first_turns=int(cfg.get("keep_first_turns", 2)),
        keep_last_turns=int(cfg.get("keep_last_turns", 20)),
        max_tokens=int(max_tokens) if max_tokens is not None else None,
        token_estimate_chars_per=int(cfg.get("token_estimate_chars_per", 4)),
    )
    if build_ctx.bus is not None:
        p.bind_bus(build_ctx.bus)
    return p


def _build_max_cost(cfg: dict, build_ctx: PluginBuildContext) -> Any:
    from arc.bootstrap import paths_for, resolve_home
    cap = float(cfg.get("max_cost_usd", 0.0))
    if cap <= 0:
        raise ValueError("max_cost plugin requires positive max_cost_usd")
    cache_path = paths_for(resolve_home()).home / "pricing_cache.json"
    table = PricingTable(cache_path=cache_path)
    p = MaxCostPlugin(max_cost_usd=cap, pricing_table=table)
    if build_ctx.bus is not None:
        p.bind_bus(build_ctx.bus)
    return p


def _build_mcp(cfg: dict, build_ctx: PluginBuildContext) -> Any:
    # MCP client bridge (0025). Parses its own config block; a config syntax
    # error fails fast (like max-cost) — connection failures are per-server
    # isolated at runtime by the manager, not here.
    from arc.mcp.bridge import McpBridge
    from arc.mcp.config import parse_mcp_config
    bridge = McpBridge(parse_mcp_config(cfg))
    if build_ctx.bus is not None:
        bridge.bind_bus(build_ctx.bus)
    return bridge


_BUILTIN_BUILDERS: dict[str, Any] = {
    "jsonl-recorder": _build_jsonl_recorder,
    "guard": _build_guard,
    "safety-gate": _build_safety_gate,
    "pause-resume": _build_pause_resume,
    "log-writer": _build_log_writer,
    "sliding-window-context": _build_sliding_window_context,
    "max-cost": _build_max_cost,
    "mcp": _build_mcp,
}

# Backwards-compatible alias — existing code (and tests) reference `_BUILDERS`.
# This is now populated by `_refresh_builders()` to include both built-ins and
# discovered external plugins.
_BUILDERS: dict[str, Any] = {}

# Captured at import time so the runtime can surface it for observability.
_LAST_DISCOVERY = None  # type: DiscoveryReport | None  # noqa: F821 — forward


def _refresh_builders() -> "DiscoveryReport":
    """Rebuild `_BUILDERS` from built-ins + freshly discovered entry points.

    Called once at module import and re-runnable from tests (after monkey-
    patching `entry_points`). External builders that collide with built-in
    names are dropped — built-ins always win — and the conflict is logged
    in the returned report so the user can see why.
    """
    from arc.plugins.discovery import discover

    report = discover(builtin_names=set(_BUILTIN_BUILDERS.keys()))
    new_builders: dict[str, Any] = dict(_BUILTIN_BUILDERS)
    for d in report.discovered:
        new_builders[d.name] = d.builder

    _BUILDERS.clear()
    _BUILDERS.update(new_builders)

    global _LAST_DISCOVERY
    _LAST_DISCOVERY = report
    return report


def last_discovery() -> "DiscoveryReport | None":
    """Return the most recent DiscoveryReport (for observability + the
    `arc plugins` menu). None only if discovery hasn't run yet, which would
    indicate a bug — this module's import calls `_refresh_builders()`.
    """
    return _LAST_DISCOVERY


def builtin_plugin_names() -> set[str]:
    """Names of plugins that ship with arc. Used by the menu to render the
    `built-in` tag and prevent users from disabling them via the toggle.
    """
    return set(_BUILTIN_BUILDERS.keys())


# Run discovery exactly once at import. Tests that need to re-discover after
# installing a fake entry point can call `_refresh_builders()` directly.
_refresh_builders()


# Default priority for hooks the plugin implements but the user didn't pin
# in config.yml. Higher number = later. Built-ins typically use 5–30 for
# leading observability/policy concerns; 50 puts external plugins after
# those defaults but ahead of any user-pinned tail-end work.
DEFAULT_PLUGIN_HOOK_PRIORITY = 50


def _resolve_hooks_order(instance: Any, configured: dict[str, int]) -> dict[str, int]:
    """Auto-fill hook priorities for plugins whose config.yml entry has an
    empty `hooks_order` — typical for external plugins persisted by the
    first-run enablement flow.

    Behavior:
      - Non-empty `configured` → return it unchanged. Built-ins (and any
        plugin whose author explicitly pinned hooks in config.yml) keep
        their wiring exactly as specified.
      - Empty `configured` → register every hook method the plugin defines
        at DEFAULT_PLUGIN_HOOK_PRIORITY. Without this, an external plugin
        loads but its `on_session_start` / `provides_tools()` never fires,
        which manifests as "the plugin's tools mysteriously don't appear."
    """
    if configured:
        return dict(configured)
    from arc.runtime.hooks import ALL_HOOK_NAMES
    resolved: dict[str, int] = {}
    for hook_name in ALL_HOOK_NAMES:
        method = getattr(instance, hook_name, None)
        if callable(method):
            resolved[hook_name] = DEFAULT_PLUGIN_HOOK_PRIORITY
    return resolved


def build(cfg: PluginsConfig, build_ctx: PluginBuildContext) -> list[BuiltPlugin]:
    """Construct active plugins. Skips entries with enabled=False.

    Unknown plugin names (in config.yml but not in `_BUILDERS`) emit a soft
    warning and are skipped rather than raising — common scenario is "user
    uninstalled the plugin package but left the entry in config.yml". The
    `arc plugins` menu surfaces these as dangling so the user can clean up.

    Returns BuiltPlugin objects with their hooks_order so the runtime can
    register them in the right order against the hook registry. Missing
    entries in hooks_order are auto-populated from the plugin's method set
    so external plugins "just work" without forcing the user to wire each
    hook priority by hand.
    """
    out: list[BuiltPlugin] = []
    for entry in cfg.active():
        if entry.name not in _BUILDERS:
            # Don't crash the session — the plugin is just missing. The menu
            # will show it as dangling so the user can remove it. We still
            # write to stderr so a CI run doesn't silently lose a plugin.
            import sys
            sys.stderr.write(
                f"[arc] plugin {entry.name!r} is enabled in config.yml but "
                f"not installed; skipping. Run `arc plugins` to clean up.\n"
            )
            continue
        instance = _BUILDERS[entry.name](entry.config, build_ctx)
        hooks_order = _resolve_hooks_order(instance, dict(entry.hooks_order))
        out.append(BuiltPlugin(
            name=entry.name,
            instance=instance,
            hooks_order=hooks_order,
        ))
    return out


# ── Plugin-contributed tools ──────────────────────────────────────────────


def merge_plugin_tools(plugins: list[BuiltPlugin], tool_registry) -> list[str]:
    """Call `provides_tools()` on each plugin and merge results into the
    tool registry. Plugin-contributed tools are NOT listed in config's
    `tools.enabled` — enabling the plugin is implicit consent.

    Returns the list of newly-registered tool names so callers can log/emit.

    Raises `ValueError` on name collision (built-in tool same-named, or two
    plugins offering the same tool). Silent override is the worst outcome;
    we want the user to rename their tool explicitly.
    """
    added: list[str] = []
    for built in plugins:
        provider = getattr(built.instance, "provides_tools", None)
        if not callable(provider):
            continue
        try:
            tools = list(provider() or [])
        except Exception as exc:  # noqa: BLE001 — surface, don't crash
            raise ValueError(
                f"plugin {built.name!r} raised from provides_tools(): {exc!r}"
            ) from exc
        for tool in tools:
            if tool.name in tool_registry:
                raise ValueError(
                    f"plugin {built.name!r} provides tool {tool.name!r} but a "
                    f"tool with that name is already registered"
                )
            tool_registry.register(tool)
            added.append(tool.name)
    return added


def bind_bus_to_tools(tool_registry, bus) -> list[str]:
    """Call `bind_bus(bus)` on every tool that defines it. Optional contract:
    tools that need to emit structured events implement `bind_bus`; tools
    that don't (the boring majority) don't need to.

    Returns the names of tools that received the bus, for logging.
    """
    bound: list[str] = []
    for tool in tool_registry.all():
        binder = getattr(tool, "bind_bus", None)
        if callable(binder):
            binder(bus)
            bound.append(tool.name)
    return bound
