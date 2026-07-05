"""Shared session-wiring helpers used across multiple `arc` subcommands.

Lives below `arc.cli.commands.*` in the package's internal import DAG —
command modules import from here, this module never imports from them.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class BuiltSession:
    session: Any
    bus: Any
    registry: Any
    plugins: list
    session_id: str


def build_session(cfg, paths, *, provider, tools, subagent_registry,
                  gate=None, session_id=None, extra_plugins=(),
                  initial_messages=None) -> BuiltSession:
    """Wire a fresh AgentSession from its parts — the shared core the run,
    interactive, replay, resume and rerun commands all need.

    Callers supply provider / tools / subagent_registry (which differ per
    command) plus an optional gate and command-specific `extra_plugins`
    (a list of (plugin, hooks_order) pairs, e.g. max_cost). Returns the session
    plus its bus / registry / built plugins so the caller can start it and, if
    needed, post-tweak a plugin (e.g. swap the gate for interactive resume).
    """
    from arc.plugins import PluginBuildContext
    from arc.plugins import build as build_plugins
    from arc.runtime.bus import EventBus, HookRegistry
    from arc.runtime.ids import new_session_id
    from arc.runtime.loop import AgentSession

    registry = HookRegistry(
        failure_threshold=cfg.plugins.failure_threshold,
        exception_message_max_chars=cfg.plugins.exception_message_max_chars,
    )
    bus = EventBus(registry)
    sid = session_id or new_session_id()
    plugins = build_plugins(cfg.plugins, PluginBuildContext(
        sessions_dir=paths.sessions_dir,
        session_id=sid,
        config_snapshot_yaml=paths.config_file.read_text(),
        user_gate=gate,
        bus=bus,
    ))
    for built in plugins:
        registry.register(built.instance, hooks_order=built.hooks_order)
    for plugin, hooks_order in extra_plugins:
        registry.register(plugin, hooks_order=hooks_order)
    kwargs = {}
    if initial_messages is not None:
        kwargs["initial_messages"] = initial_messages
    session = AgentSession(
        config=cfg, provider=provider, tools=tools,
        registry=registry, bus=bus, session_id=sid,
        subagent_registry=subagent_registry, **kwargs,
    )
    return BuiltSession(session=session, bus=bus, registry=registry,
                        plugins=plugins, session_id=sid)


# ── Sub-agent registry helper ──────────────────────────────────────────────


def _make_subagent_registry(cfg, home):
    """Discover sub-agents (built-ins + entry points + config), return the
    registry ready for AgentSession to consume.

    None on construction failure — session continues without sub-agents.
    Failures get written to stderr (same shape as plugin dangling errors)
    so the user sees them.
    """
    try:
        from arc.runtime.subagents.registry import SubAgentRegistry
        reg = SubAgentRegistry(arc_home=home)
        reg.discover(cfg.subagents.as_overrides())
        return reg
    except Exception as exc:  # noqa: BLE001 — sub-agents are optional, don't crash session
        sys.stderr.write(
            f"[arc] sub-agent registry construction failed; "
            f"sub-agents unavailable this session: "
            f"{type(exc).__name__}: {exc}\n"
        )
        return None


def _source_label(spec) -> str:
    """One-word source tag for the `arc subagents` table."""
    if spec.source == "plugin":
        return f"plugin:{spec.source_package or 'unknown'}"
    return spec.source


# ── First-run plugin enablement helper ─────────────────────────────────────


def _apply_first_run_enablement(
    paths, cfg, *, interactive: bool
):
    """Run the first-run enablement flow for newly-discovered plugins.

    Returns (cfg_possibly_reloaded, outcomes). If any plugin was persisted
    to config.yml, the config is reloaded so the freshly-enabled plugin is
    visible to the rest of startup.

    `interactive` controls whether the user is actually prompted. Headless
    callers (`arc run`, batch replay) pass False — discovered plugins stay
    dormant until the user runs `arc` (interactive) or `arc plugins`.

    Outcomes are returned (not yet emitted) so the caller can emit them
    onto the AgentSession's bus once it exists, ensuring the events land
    in the session's recorded event log.
    """
    from arc.config import load
    from arc.plugins import last_discovery
    from arc.plugins.enablement import find_new_plugins, run_first_run_flow

    report = last_discovery()
    if report is None or not report.discovered:
        return cfg, []

    new_plugins = find_new_plugins(report, cfg.plugins)
    if not new_plugins:
        return cfg, []

    outcomes = run_first_run_flow(
        paths.config_file,
        new_plugins=new_plugins,
        interactive=interactive,
    )
    persisted = [o for o in outcomes if o.persisted]
    if persisted:
        cfg = load(paths.config_file)
    return cfg, outcomes


def _emit_enablement_outcomes(bus, outcomes) -> None:
    """Emit RuntimeEvents for the outcomes onto the session bus. Called
    AFTER session.started so the events land in the recorded event log.
    """
    if not outcomes:
        return
    from arc.runtime.events import EventType, RuntimeEvent

    for o in outcomes:
        if o.skipped_reason is not None:
            bus.emit(RuntimeEvent(
                type=EventType.PLUGIN_FIRST_RUN_PROMPTED,
                stage="cli",
                payload={
                    "name": o.name,
                    "package": o.package,
                    "package_version": o.package_version,
                    "skipped_reason": o.skipped_reason,
                },
            ))
            continue
        bus.emit(RuntimeEvent(
            type=EventType.PLUGIN_FIRST_RUN_ENABLED if o.enabled
                 else EventType.PLUGIN_FIRST_RUN_DECLINED,
            stage="cli",
            payload={
                "name": o.name,
                "package": o.package,
                "package_version": o.package_version,
            },
        ))
        if o.persisted:
            bus.emit(RuntimeEvent(
                type=EventType.PLUGIN_CONFIG_PERSISTED,
                stage="cli",
                payload={"name": o.name, "enabled": o.enabled},
            ))


def _emit_discovery_report(bus) -> None:
    """Emit a one-shot summary of what entry-point discovery found at boot.
    Idempotent — safe to call multiple times; arc.plugins caches the last
    report.
    """
    from arc.plugins import last_discovery
    from arc.runtime.events import EventType, RuntimeEvent, Severity

    report = last_discovery()
    if report is None:
        return

    bus.emit(RuntimeEvent(
        type=EventType.PLUGINS_DISCOVERED,
        stage="cli",
        payload={
            "discovered": [
                {"name": d.name, "package": d.package, "version": d.package_version}
                for d in report.discovered
            ],
            "conflicts": [
                {"name": c.name, "from": c.discovered_from, "conflicts_with": c.conflicts_with}
                for c in report.conflicts
            ],
        },
    ))
    for failure in report.failures:
        bus.emit(RuntimeEvent(
            type=EventType.PLUGIN_LOAD_FAILED,
            stage="cli",
            severity=Severity.ERROR,
            payload={
                "name": failure.name,
                "package": failure.package,
                "entry_point": failure.entry_point_value,
                "error": failure.error,
            },
        ))


# ── .env loading ───────────────────────────────────────────────────────────


def _load_dotenv_into_environ(home_override: str | None) -> None:
    """Load .env if one exists.

    Looks first at $CWD/.env, then at ARC_HOME/.env. Existing env vars win
    (we don't clobber). Minimal impl to avoid hard dep on python-dotenv at
    startup time — that lib is in pyproject for plugin convenience but not
    required here.
    """
    candidates = [Path.cwd() / ".env"]
    try:
        from arc.bootstrap import resolve_home
        candidates.append(resolve_home(home_override) / ".env")
    except Exception:
        pass

    for path in candidates:
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())
