"""Plugin factory.

Builds plugin instances from `config.plugins.enabled`. Each plugin has a
builder function that receives the runtime context it needs (session_id,
home dir, plugin-specific config dict).

Adding a new plugin = add a builder in `_BUILDERS` below. Unknown plugin
names raise at startup.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from arc.config import PluginsConfig
from arc.plugins.guard import GuardPlugin
from arc.plugins.jsonl_recorder import JSONLRecorder
from arc.plugins.log_writer import LogWriterPlugin
from arc.plugins.pause_resume import PauseResumePlugin
from arc.plugins.safety_gate import Pattern as SafetyPattern
from arc.plugins.safety_gate import SafetyGatePlugin
from arc.plugins.sliding_window_context import SlidingWindowContextPlugin
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


_BUILDERS = {
    "jsonl-recorder": _build_jsonl_recorder,
    "guard": _build_guard,
    "safety-gate": _build_safety_gate,
    "pause-resume": _build_pause_resume,
    "log-writer": _build_log_writer,
    "sliding-window-context": _build_sliding_window_context,
}


def build(cfg: PluginsConfig, build_ctx: PluginBuildContext) -> list[BuiltPlugin]:
    """Construct active plugins. Skips entries with enabled=False.

    Returns BuiltPlugin objects with their hooks_order so the runtime can
    register them in the right order against the hook registry.
    """
    out: list[BuiltPlugin] = []
    for entry in cfg.active():
        if entry.name not in _BUILDERS:
            raise ValueError(
                f"unknown plugin {entry.name!r} in plugins.enabled\n"
                f"  known: {sorted(_BUILDERS.keys())}\n"
                f"  (add a builder in arc/plugins/__init__.py to support more)"
            )
        instance = _BUILDERS[entry.name](entry.config, build_ctx)
        out.append(BuiltPlugin(
            name=entry.name,
            instance=instance,
            hooks_order=dict(entry.hooks_order),
        ))
    return out
