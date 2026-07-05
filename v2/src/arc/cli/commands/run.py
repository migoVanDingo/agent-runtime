"""`arc run` — one-shot, non-interactive turn."""
from __future__ import annotations

from arc.cli.wiring import (
    _apply_first_run_enablement,
    _emit_discovery_report,
    _emit_enablement_outcomes,
    _load_dotenv_into_environ,
    _make_subagent_registry,
    build_session,
)


def _cmd_run(home_override: str | None, *, prompt: str) -> int:
    """One-shot turn. Bootstraps if needed, loads config, runs, prints reply."""
    _load_dotenv_into_environ(home_override)

    from arc.bootstrap import bootstrap, paths_for, resolve_home
    from arc.config import load
    from arc.providers import build as build_provider
    from arc.tools import build as build_tools
    from arc.user_gate import NoOpGate

    home = resolve_home(home_override)
    bootstrap(home)  # idempotent — creates layout on first run
    paths = paths_for(home)

    cfg = load(paths.config_file)

    # First-run enablement: headless mode never prompts (interactive=False).
    # Discovered-but-not-in-config plugins stay dormant. Outcomes are still
    # emitted so observers see "we noticed but skipped".
    cfg, enablement_outcomes = _apply_first_run_enablement(
        paths, cfg, interactive=False,
    )

    # Headless mode: any guard escalation auto-denies (NoOpGate logs to stderr).
    built = build_session(
        cfg, paths,
        provider=build_provider(cfg.provider),
        tools=build_tools(cfg.tools),
        subagent_registry=_make_subagent_registry(cfg, home),
        gate=NoOpGate(),
    )
    sess, bus = built.session, built.bus

    try:
        sess.start()
        _emit_discovery_report(bus)
        _emit_enablement_outcomes(bus, enablement_outcomes)
        outcome = sess.run_turn(prompt)
        print(outcome.final_response)
        return 0 if outcome.success else 1
    finally:
        sess.end()
