"""`arc` CLI entry point.

Subcommands (per design §9.1):

  arc                  start interactive TUI session (added in phase 1 task #60)
  arc bootstrap        create ARC_HOME + default config
  arc bootstrap --force  overwrite existing config
  arc run "<prompt>"   one-shot, non-interactive turn; prints final reply
  arc replay <id>      (phase 2.0.5)
  arc sessions         list known sessions
  arc show <id>        pretty-print a recorded session
  arc config show      print resolved config
  arc config path      print config file path
  arc --home <path>    override ARC_HOME resolution (works with all subcommands)
  arc --version
  arc --help

Phase 1 implements all of these EXCEPT `replay` and the interactive TUI
(which lands in task #60). `arc` with no subcommand currently runs a one-shot
turn from stdin if stdin is not a TTY, or prints a message directing users to
`arc run` until the TUI is wired in.
"""
from __future__ import annotations

import sys

# Every `_cmd_*` handler is imported here as a module-level name (rather than
# called only via its owning submodule) so `unittest.mock.patch("arc.cli._cmd_X")`
# replaces the exact name `main()` dispatches to — and sibling command modules
# that call back into `arc.cli._cmd_interactive` resolve the patched version
# at call time.
from arc.cli.commands.bootstrap import _cmd_bootstrap as _cmd_bootstrap
from arc.cli.commands.compare import _cmd_compare as _cmd_compare
from arc.cli.commands.compare import t_short as t_short
from arc.cli.commands.config import (
    _cmd_config_path as _cmd_config_path,
)
from arc.cli.commands.config import (
    _cmd_config_show as _cmd_config_show,
)
from arc.cli.commands.interactive import _cmd_interactive as _cmd_interactive
from arc.cli.commands.llm import _cmd_llm as _cmd_llm
from arc.cli.commands.log import _cmd_log as _cmd_log
from arc.cli.commands.mcp import _cmd_mcp as _cmd_mcp
from arc.cli.commands.mcp import _mcp_add as _mcp_add
from arc.cli.commands.plugins import _cmd_plugins as _cmd_plugins
from arc.cli.commands.replay import (
    _cmd_replay as _cmd_replay,
)
from arc.cli.commands.replay import (
    _cmd_replay_batch as _cmd_replay_batch,
)
from arc.cli.commands.replay import (
    _cmd_replay_menu as _cmd_replay_menu,
)
from arc.cli.commands.rerun import _cmd_rerun as _cmd_rerun
from arc.cli.commands.resume import _cmd_resume as _cmd_resume
from arc.cli.commands.run import _cmd_run as _cmd_run
from arc.cli.commands.sessions import _cmd_sessions as _cmd_sessions
from arc.cli.commands.timeline import _cmd_timeline as _cmd_timeline
from arc.cli.commands.setup import _cmd_setup as _cmd_setup
from arc.cli.commands.show import _cmd_show as _cmd_show
from arc.cli.commands.subagents import _cmd_subagents as _cmd_subagents
from arc.cli.commands.wipe import _cmd_wipe as _cmd_wipe
from arc.cli.parser import _build_parser as _build_parser

# `as X` re-exports below are deliberate: these names aren't referenced
# inside this file, but are part of arc.cli's public surface (tests and
# other modules import them directly from `arc.cli`), so a plain import
# would trip ruff's F401 and risk being "cleaned up" by mistake.
from arc.cli.wiring import (
    BuiltSession as BuiltSession,
)
from arc.cli.wiring import (
    _apply_first_run_enablement as _apply_first_run_enablement,
)
from arc.cli.wiring import (
    _emit_discovery_report as _emit_discovery_report,
)
from arc.cli.wiring import (
    _emit_enablement_outcomes as _emit_enablement_outcomes,
)
from arc.cli.wiring import (
    _load_dotenv_into_environ as _load_dotenv_into_environ,
)
from arc.cli.wiring import (
    _make_subagent_registry as _make_subagent_registry,
)
from arc.cli.wiring import (
    _source_label as _source_label,
)
from arc.cli.wiring import (
    build_session as build_session,
)

# ── Top-level entry point ──────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # --home applies to every subcommand
    home_override: str | None = getattr(args, "home", None)

    # Load .env BEFORE any subcommand runs, so things like ARC_HOME in
    # the user's .env are picked up by resolve_home() consistently.
    # Individual subcommands used to call this themselves; centralizing
    # here means even read-only commands (config, sessions, show) get
    # consistent env resolution.
    _load_dotenv_into_environ(home_override)

    # Resolve the active TUI theme once, here, so every subcommand path
    # (interactive dialogs, hub, live TUI) sees the same theme without
    # threading it through every call site. Falls back to `default` if
    # config isn't readable yet (e.g. pre-bootstrap).
    from arc.tui.themes import resolve_from_home
    resolve_from_home(home_override)

    # Dispatch
    if args.command == "bootstrap":
        return _cmd_bootstrap(home_override, force=args.force)
    if args.command == "setup":
        return _cmd_setup(
            home_override,
            provider=args.provider,
            model=args.model,
            print_only=args.print_only,
            no_launch=args.no_launch,
            hub=args.hub,
            section=args.section,
        )
    if args.command == "llm":
        return _cmd_llm(home_override, args)
    if args.command == "wipe":
        return _cmd_wipe(home_override, args)
    if args.command == "run":
        return _cmd_run(home_override, prompt=args.prompt)
    if args.command == "sessions":
        return _cmd_sessions(home_override)
    if args.command == "timeline":
        return _cmd_timeline(
            home_override,
            open_browser=args.open_browser,
            rebuild=args.rebuild,
        )
    if args.command == "show":
        return _cmd_show(home_override, session_id=args.session_id)
    if args.command == "log":
        return _cmd_log(
            home_override,
            session_id=args.session_id,
            tail=args.tail,
        )
    if args.command == "replay":
        return _cmd_replay(
            home_override,
            session_id=args.session_id,
            live_llm=args.live_llm,
            do_diff=not args.no_diff,
            override_provider=args.override_provider,
            override_model=args.override_model,
            max_cost_usd=args.max_cost_usd,
            against_spec=args.against,
        )
    if args.command == "compare":
        return _cmd_compare(
            home_override,
            session_ids=args.session_ids,
            full=args.full,
        )
    if args.command == "resume":
        return _cmd_resume(
            home_override,
            session_id=args.session_id,
            prompt=args.prompt,
            no_tui=args.no_tui,
            at_turn=args.at_turn,
        )
    if args.command == "rerun":
        return _cmd_rerun(
            home_override,
            session_id=args.session_id,
            stop_on_error=args.stop_on_error,
        )
    if args.command == "config":
        if args.config_action == "show":
            return _cmd_config_show(home_override)
        if args.config_action == "path":
            return _cmd_config_path(home_override)
        parser.error(f"unknown config action: {args.config_action}")
    if args.command == "plugins":
        return _cmd_plugins(home_override, action=getattr(args, "plugins_action", None))
    if args.command == "mcp":
        return _cmd_mcp(home_override, args)
    if args.command == "subagents":
        return _cmd_subagents(
            home_override,
            action=getattr(args, "subagents_action", None),
            spec_name=getattr(args, "spec_name", None),
        )
    if args.command is None:
        return _cmd_interactive(home_override)

    parser.error(f"unknown command: {args.command}")
    return 2  # unreachable; parser.error exits


if __name__ == "__main__":
    sys.exit(main())
