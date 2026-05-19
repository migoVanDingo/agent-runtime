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
from arc.plugins.pause_resume import PauseResumePlugin
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
    """
    sessions_dir: Path
    session_id: str
    config_snapshot_yaml: str | None = None
    user_gate: UserGate | None = None


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


_BUILDERS = {
    "jsonl-recorder": _build_jsonl_recorder,
    "guard": _build_guard,
    "pause-resume": _build_pause_resume,
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
