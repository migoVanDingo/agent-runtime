"""`arc` with no subcommand — the inline interactive TUI session."""
from __future__ import annotations

from arc.cli.wiring import (
    _apply_first_run_enablement,
    _emit_discovery_report,
    _emit_enablement_outcomes,
    _load_dotenv_into_environ,
    _make_subagent_registry,
    build_session,
)


def _cmd_interactive(home_override: str | None) -> int:
    """Interactive session — the inline TUI."""
    _load_dotenv_into_environ(home_override)

    from arc.bootstrap import bootstrap, paths_for, resolve_home
    from arc.config import load
    from arc.providers import build as build_provider
    from arc.tools import build as build_tools
    from arc.tui.app import TUIApp

    home = resolve_home(home_override)
    bootstrap(home)
    paths = paths_for(home)
    cfg = load(paths.config_file)

    # First-run plugin enablement: interactive prompt for any plugin that
    # was pip-installed since the last session and isn't in config.yml.
    # MUST happen before build_plugins() so newly-enabled plugins are loaded.
    cfg, enablement_outcomes = _apply_first_run_enablement(
        paths, cfg, interactive=True,
    )

    # Interactive mode: TUIGate prompts the user via prompt_toolkit when
    # a tool trips an escalation pattern. Construct the gate with a shared
    # console so escalation prompts use the same render pipeline as the rest.
    from rich.console import Console

    from arc.user_gate import TUIGate
    console = Console()

    built_session = build_session(
        cfg, paths,
        provider=build_provider(cfg.provider),
        tools=build_tools(cfg.tools),
        subagent_registry=_make_subagent_registry(cfg, home),
        gate=TUIGate(console=console),
    )
    sess, bus = built_session.session, built_session.bus

    # Emit discovery + enablement events onto the session bus so they
    # land in events.jsonl alongside session.started. Done before app.run()
    # so the bus is wired but after AgentSession is built.
    _emit_discovery_report(bus)
    _emit_enablement_outcomes(bus, enablement_outcomes)

    app = TUIApp(cfg, sess, home_display=str(home), console=console)
    return app.run()
