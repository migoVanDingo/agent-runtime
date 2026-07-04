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

import argparse
import json
import os
import sys
from pathlib import Path

from arc import __version__


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


# ── Parser ─────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="arc", description="arc — agent runtime")
    p.add_argument("--version", action="version", version=f"arc {__version__}")
    p.add_argument(
        "--home",
        metavar="PATH",
        help="override ARC_HOME resolution with an explicit path",
    )

    sub = p.add_subparsers(dest="command")

    boot = sub.add_parser("bootstrap", help="create ARC_HOME + default config")
    boot.add_argument(
        "--force", action="store_true",
        help="overwrite an existing config.yml (sessions are untouched)",
    )

    wipe = sub.add_parser(
        "wipe",
        help="delete state under ARC_HOME (sessions, logs, etc.).  Default: sessions only.",
    )
    wipe.add_argument(
        "--all", dest="wipe_all", action="store_true",
        help="un-bootstrap: remove the entire ARC_HOME tree",
    )
    wipe.add_argument(
        "--sessions", action="store_true",
        help="remove sessions/ (default if no targets given)",
    )
    wipe.add_argument(
        "--llm", action="store_true",
        help="remove llm/ (server PID file + log)",
    )
    wipe.add_argument(
        "--history", action="store_true",
        help="remove the TUI input-history file",
    )
    wipe.add_argument(
        "--pricing-cache", dest="pricing_cache", action="store_true",
        help="remove pricing_cache.json (will refetch from LiteLLM on next run)",
    )
    wipe.add_argument(
        "--yes", "-y", dest="assume_yes", action="store_true",
        help="skip the confirmation prompt",
    )
    wipe.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="print what would be removed, don't actually delete",
    )

    llm = sub.add_parser(
        "llm",
        help="manage the local inference server (llama-server / llama-cpp-python)",
    )
    # llm_action is optional: no subcommand → opens the setup hub on LLM Server.
    llm_sub = llm.add_subparsers(dest="llm_action")
    llm_sub.add_parser("list", help="list registered models + which is running")
    llm_sub.add_parser("status", help="show details about the running server")
    llm_start = llm_sub.add_parser("start", help="start the server for a given model id")
    llm_start.add_argument("model_id", help="id from llm_servers.yml")
    llm_sub.add_parser("stop", help="stop the running server (SIGTERM → SIGKILL after 10s)")
    llm_restart = llm_sub.add_parser("restart", help="stop current + start the named model")
    llm_restart.add_argument("model_id", help="id from llm_servers.yml")
    llm_logs = llm_sub.add_parser("logs", help="print recent lines from the server log")
    llm_logs.add_argument("--tail", type=int, default=50, help="show only the last N lines")

    setup = sub.add_parser(
        "setup",
        help="interactive setup hub (provider, plugins, themes, sub-agents, …)",
    )
    setup.add_argument(
        "--provider", default=None,
        help="skip the provider menu; use this provider name (anthropic|gemini|ollama|llama_cpp)",
    )
    setup.add_argument(
        "--model", default=None,
        help="skip the model menu; use this model id (requires --provider)",
    )
    setup.add_argument(
        "--print", dest="print_only", action="store_true",
        help="run the picker but dump the resulting YAML to stdout instead of writing",
    )
    setup.add_argument(
        "--no-launch", dest="no_launch", action="store_true",
        help="don't drop into a TUI session after writing config (default is to launch)",
    )
    setup.add_argument(
        "--picker", dest="hub", action="store_false", default=True,
        help="run the classic provider/model picker only — skip the setup hub",
    )
    setup.add_argument(
        "--section", default=None, metavar="NAME",
        help="open the hub focused on a specific section "
             "(provider|plugins|subagents|replay|llm|themes|status|wipe|config)",
    )

    run = sub.add_parser("run", help="one-shot, non-interactive turn")
    run.add_argument("prompt", help="the user message to send (in quotes)")

    sub.add_parser("sessions", help="list known sessions")

    show = sub.add_parser("show", help="pretty-print a recorded session")
    show.add_argument("session_id", help="session id (e.g., SES01HXYZ...)")

    log = sub.add_parser(
        "log", help="print the human-readable session.log for a session",
    )
    log.add_argument("session_id", help="session id whose log to print")
    log.add_argument(
        "--tail", type=int, default=None, metavar="N",
        help="show only the last N lines",
    )

    replay = sub.add_parser("replay", help="replay a recorded session")
    replay.add_argument(
        "session_id", nargs="?", default=None,
        help="session id to replay (omit to launch the interactive replay menu)",
    )
    replay.add_argument(
        "--live-llm", action="store_true",
        help="mode 3: call the LLM live, stub only the tools "
             "(use to test prompt/model changes against a recorded scenario)",
    )
    replay.add_argument(
        "--no-diff", action="store_true",
        help="don't compare against the original; just run the replay",
    )
    replay.add_argument(
        "--override-provider", default=None, metavar="NAME",
        help="cross-provider replay (0019): use a different provider than the original",
    )
    replay.add_argument(
        "--override-model", default=None, metavar="ID",
        help="cross-provider replay (0019): use this model id with the override provider",
    )
    replay.add_argument(
        "--max-cost-usd", type=float, default=None, metavar="N",
        help="abort the replay if cost exceeds N USD (0019)",
    )
    replay.add_argument(
        "--against", default=None, metavar="P:M,P:M,…",
        help="batch replay against multiple targets (e.g. 'ollama:llama3.1:8b,anthropic:claude-haiku-4-5')",
    )

    compare = sub.add_parser(
        "compare", help="side-by-side comparison of two or more recorded sessions (0019)",
    )
    compare.add_argument("session_ids", nargs="+", help="2+ session ids to compare")
    compare.add_argument(
        "--full", action="store_true",
        help="dump events.jsonl files side-by-side (verbose; for debugging)",
    )

    resume = sub.add_parser(
        "resume",
        help="continue a recorded (paused or completed) session in a new session",
    )
    resume.add_argument("session_id", help="session id to resume from")
    resume.add_argument(
        "--prompt", default=None,
        help="next user turn to run immediately (headless). "
             "Omit to drop into interactive mode.",
    )
    resume.add_argument(
        "--no-tui", action="store_true",
        help="if --prompt is omitted, exit after restore instead of starting TUI",
    )
    resume.add_argument(
        "--at-turn", type=int, default=None, metavar="N",
        help="branch: restore only the first N turns instead of all of them "
             "(mode 4 from the replay catalog)",
    )

    rerun = sub.add_parser(
        "rerun",
        help="re-run a recorded session's user inputs against a fresh agent "
             "(live LLM + live tools — mode 5)",
    )
    rerun.add_argument("session_id", help="session id whose user inputs to replay")
    rerun.add_argument(
        "--stop-on-error", action="store_true",
        help="bail on the first turn that fails (default: continue through all)",
    )

    cfg = sub.add_parser("config", help="inspect resolved configuration")
    cfg_sub = cfg.add_subparsers(dest="config_action", required=True)
    cfg_sub.add_parser("show", help="print resolved config")
    cfg_sub.add_parser("path", help="print resolved config file path")

    plugins = sub.add_parser(
        "plugins",
        help="manage installed plugins (enable/disable/clean up)",
        description=(
            "Manage built-in and out-of-tree (pip-installed) plugins.\n"
            "\n"
            "With no subcommand, opens an interactive checkbox menu showing\n"
            "every plugin arc knows about — built-ins, discovered external\n"
            "packages, and any dangling config entries from uninstalled\n"
            "packages. Space toggles, Enter saves to ~/.arc/config.yml.\n"
            "\n"
            "External plugins are pip-installable packages that register\n"
            "via the `arc.plugins` entry-point group. arc prompts once on\n"
            "first discovery; this command is the always-available toggle."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    plugins_sub = plugins.add_subparsers(
        dest="plugins_action",
        metavar="{list}",
        title="subcommands",
    )
    plugins_sub.add_parser(
        "list",
        help="print plugin status as a plain-text table (non-interactive)",
    )
    # No subcommand → interactive menu

    mcp = sub.add_parser(
        "mcp",
        help="manage MCP servers (enable/disable per server)",
        description=(
            "Manage external MCP servers consumed by the built-in `mcp` plugin.\n"
            "\n"
            "With no subcommand, opens the setup hub on the MCP Servers section —\n"
            "a checkbox toggle over each configured server. `list` prints the\n"
            "config-level status; `status` probes live connections. Servers live\n"
            "under plugins.enabled[mcp].config.servers in config.yml.\n"
            "See _design/0025-mcp-client-integration.md."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mcp_sub = mcp.add_subparsers(
        dest="mcp_action", metavar="{list,status,add,remove}", title="subcommands")
    mcp_sub.add_parser("list", help="print configured MCP servers (non-interactive)")
    mcp_sub.add_parser("status", help="connect and report live server status + tools")

    m_add = mcp_sub.add_parser("add", help="add or update an MCP server in config.yml")
    m_add.add_argument("name", help="server name (also the default tool prefix)")
    m_add.add_argument("--transport", choices=["http", "stdio"], required=True)
    m_add.add_argument("--url", help="http: server URL, e.g. http://127.0.0.1:8770/mcp")
    # dest must NOT be `command` — that's the top-level subcommand dest.
    m_add.add_argument("--command", dest="mcp_command",
                       help="stdio: command line, e.g. 'uvx proxmox-mcp'")
    m_add.add_argument("--env", action="append", default=[], metavar="K=V",
                       help="stdio: env var (repeatable)")
    m_add.add_argument("--cwd", help="stdio: working directory")
    m_add.add_argument("--tool-prefix", dest="tool_prefix",
                       help="tool name prefix (default: server name)")
    m_add.add_argument("--tools-allow", dest="tools_allow", help="comma-separated allowlist")
    m_add.add_argument("--tools-deny", dest="tools_deny", help="comma-separated denylist")
    m_add.add_argument("--disabled", action="store_true", help="add but leave disabled")

    m_rm = mcp_sub.add_parser("remove", help="remove an MCP server from config.yml")
    m_rm.add_argument("name", help="server name to remove")

    subagents = sub.add_parser(
        "subagents",
        help="manage sub-agent specs (list/show/enable/disable)",
        description=(
            "Manage built-in, plugin-shipped, and config-defined sub-agent specs.\n"
            "\n"
            "Sub-agents are scoped child agents the parent can dispatch as a tool.\n"
            "Each spec pins its own provider/model, system prompt, tool allowlist,\n"
            "and dispatch guards. See _design/0020-subagent-dispatch.md.\n"
            "\n"
            "The interactive TUI menu is not yet implemented; use `list` / `show` /\n"
            "`enable` / `disable` to inspect and toggle specs."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sa_sub = subagents.add_subparsers(
        dest="subagents_action",
        metavar="{list,show,enable,disable}",
        title="subcommands",
    )
    sa_sub.add_parser("list", help="print discovered sub-agent specs as a table")
    sa_show = sa_sub.add_parser("show", help="pretty-print one spec's merged fields")
    sa_show.add_argument("spec_name", metavar="NAME", help="spec name to show")
    sa_enable = sa_sub.add_parser("enable", help="enable a spec (writes config.yml)")
    sa_enable.add_argument("spec_name", metavar="NAME", help="spec name to enable")
    sa_disable = sa_sub.add_parser("disable", help="disable a spec (writes config.yml)")
    sa_disable.add_argument("spec_name", metavar="NAME", help="spec name to disable")

    return p


# ── Subcommand impls ───────────────────────────────────────────────────────


def _cmd_bootstrap(home_override: str | None, *, force: bool) -> int:
    from arc.bootstrap import bootstrap, format_bootstrap_summary, resolve_home
    home = resolve_home(home_override)
    result = bootstrap(home, force_config=force)
    print(format_bootstrap_summary(result))
    return 0


def _cmd_wipe(home_override: str | None, args) -> int:
    """`arc wipe` — delete state under ARC_HOME.  See `arc/wipe.py`."""
    from arc.bootstrap import resolve_home
    from arc.wipe import WipeTargets, build_plan, execute_plan, format_plan

    home = resolve_home(home_override)
    targets = WipeTargets(
        all_=args.wipe_all,
        sessions=args.sessions,
        llm=args.llm,
        history=args.history,
        pricing_cache=args.pricing_cache,
    ).with_default_if_empty()

    plan = build_plan(home, targets)
    if plan.is_noop:
        print(f"nothing to wipe under {home} (no matching files exist)")
        return 0

    print(format_plan(plan))

    if args.dry_run:
        print("(dry-run: no changes made)")
        return 0

    if not args.assume_yes:
        # No TTY → refuse silently rather than accidentally wiping in CI
        if not sys.stdin.isatty():
            print("aborted: not a TTY; pass --yes to confirm in non-interactive runs",
                  file=sys.stderr)
            return 1
        try:
            answer = input("proceed? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("aborted")
            return 1
        if answer not in ("y", "yes"):
            print("aborted")
            return 1

    removed = execute_plan(plan)
    print(f"wiped {len(removed)} path(s).")
    return 0


def _cmd_llm(home_override: str | None, args) -> int:
    """`arc llm` dispatcher.  See 0018.

    No subcommand → opens the setup hub on the LLM Server section.
    """
    from arc.bootstrap import bootstrap, paths_for, resolve_home
    from arc import llm as _llm
    from arc.llm.registry import RegistryError
    from arc.setup.hub import run_hub

    home = resolve_home(home_override)
    bootstrap(home)
    paths = paths_for(home)

    action = args.llm_action
    if action is None:
        result = run_hub(home, initial_section="llm")
        if result.launch_session:
            return _cmd_interactive(home_override)
        return result.rc
    try:
        if action == "list":
            return _llm.list_models(paths)
        if action == "status":
            return _llm.show_status(paths)
        if action == "start":
            return _llm.start_server(paths, args.model_id)
        if action == "stop":
            return _llm.stop_server(paths)
        if action == "restart":
            return _llm.restart_server(paths, args.model_id)
        if action == "logs":
            return _llm.show_logs(paths, tail=args.tail)
    except RegistryError as e:
        print(str(e), file=sys.stderr)
        return 2

    print(f"unknown llm action: {action}", file=sys.stderr)
    return 2


def _cmd_setup(
    home_override: str | None,
    *,
    provider: str | None,
    model: str | None,
    print_only: bool,
    no_launch: bool,
    hub: bool = True,
    section: str | None = None,
) -> int:
    """`arc setup` — opens the interactive setup hub by default.

    Behavior matrix:
      arc setup                 → hub (sidebar + content; navigates to every section)
      arc setup --picker        → classic provider/model picker (0017), then launch TUI
      arc setup --provider X    → non-interactive write (preserves prior contract)
      arc setup --section NAME  → hub focused on NAME

    See _design/0023-setup-hub-and-themes.md for the hub, 0017 for the picker.
    """
    from arc.bootstrap import bootstrap, resolve_home
    from arc.setup import run_setup
    from arc.setup.hub import run_hub

    if model is not None and provider is None:
        print("--model requires --provider", file=sys.stderr)
        return 2

    # No flags + hub enabled → open the hub (the default path).
    if hub and provider is None and model is None and not print_only:
        home = resolve_home(home_override)
        # Hub assumes ARC_HOME exists; bootstrap if missing (idempotent).
        bootstrap(home)
        result = run_hub(home, initial_section=section)
        if result.launch_session:
            return _cmd_interactive(home_override)
        return result.rc

    try:
        result = run_setup(
            home=resolve_home(home_override),
            provider_override=provider,
            model_override=model,
            print_only=print_only,
        )
    except SystemExit as exc:
        # run_setup raises SystemExit on abort/error with a clear message
        print(str(exc.code) if exc.code and not isinstance(exc.code, int) else "aborted",
              file=sys.stderr)
        return 1 if exc.code else 0

    if print_only:
        return 0

    print(f"arc setup → {result.provider}/{result.model}")
    print(f"  config: {result.config_path}")
    print(result.diff_text)
    if result.api_key_warning:
        print(f"  warning: {result.api_key_warning}", file=sys.stderr)

    # Auto-launch the TUI if the user just walked the interactive picker.
    # Skip for scripted mode (flags-only), --no-launch, or missing api key
    # — the last one would just fail at provider construction.
    interactive_path = provider is None and model is None
    if not interactive_path:
        return 0
    if no_launch:
        return 0
    if result.api_key_warning:
        print("  (skipping launch — fix the env var above, then run `arc`)",
              file=sys.stderr)
        return 0

    print()
    print(f"starting session against {result.provider}/{result.model}…")
    return _cmd_interactive(home_override)


def _cmd_config_path(home_override: str | None) -> int:
    from arc.bootstrap import paths_for, resolve_home
    p = paths_for(resolve_home(home_override))
    print(p.config_file)
    return 0 if p.config_file.exists() else 1


def _cmd_config_show(home_override: str | None) -> int:
    """Print the resolved config (as YAML) for debugging."""
    from arc.bootstrap import paths_for, resolve_home
    from arc.config import load
    p = paths_for(resolve_home(home_override))
    if not p.config_file.exists():
        print(f"no config at {p.config_file}", file=sys.stderr)
        print(f"run `arc bootstrap` to create one", file=sys.stderr)
        return 1
    # Just print the raw file contents — it's the source of truth
    print(p.config_file.read_text(), end="")
    return 0


def _cmd_sessions(home_override: str | None) -> int:
    """List recorded sessions from sessions/index.jsonl."""
    from arc.bootstrap import paths_for, resolve_home
    p = paths_for(resolve_home(home_override))
    if not p.sessions_index.exists():
        print("no sessions recorded yet", file=sys.stderr)
        return 0

    rows = []
    for line in p.sessions_index.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if not rows:
        print("no sessions recorded yet", file=sys.stderr)
        return 0

    # Simple aligned columns
    print(f"{'session_id':32}  {'started_at':28}  {'provider':10}  {'model':40}")
    for r in rows:
        print(f"{r.get('session_id', '?'):32}  "
              f"{r.get('started_at', '?')[:26]:28}  "
              f"{r.get('provider', '?'):10}  "
              f"{r.get('model', '?'):40}")
    return 0


def _cmd_log(home_override: str | None, *, session_id: str, tail: int | None) -> int:
    """Print the v1-style session.log written by the log-writer plugin."""
    from arc.bootstrap import paths_for, resolve_home
    p = paths_for(resolve_home(home_override))
    log_path = p.sessions_dir / session_id / "session.log"
    if not log_path.is_file():
        print(f"no session.log for session {session_id!r} at {log_path}",
              file=sys.stderr)
        return 1
    lines = log_path.read_text(encoding="utf-8").splitlines()
    if tail is not None:
        lines = lines[-tail:]
    for line in lines:
        print(line)
    return 0


def _cmd_show(home_override: str | None, *, session_id: str) -> int:
    """Render a recorded session as human-readable text (from canonical events)."""
    from arc.bootstrap import paths_for, resolve_home
    p = paths_for(resolve_home(home_override))
    session_dir = p.sessions_dir / session_id
    events_file = session_dir / "events.jsonl"
    if not events_file.exists():
        print(f"no events for session {session_id!r} at {events_file}", file=sys.stderr)
        return 1

    for line in events_file.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = e.get("ts", "")[11:23]  # HH:MM:SS.mmm
        typ = e.get("type", "?")
        stage = e.get("stage", "")
        scope = e.get("scope", "main")
        scope_tag = "" if scope == "main" else f" [{scope}]"
        print(f"{ts}  {typ:30}  {stage}{scope_tag}")
    return 0


def _cmd_run(home_override: str | None, *, prompt: str) -> int:
    """One-shot turn. Bootstraps if needed, loads config, runs, prints reply."""
    _load_dotenv_into_environ(home_override)

    from arc.bootstrap import bootstrap, paths_for, resolve_home
    from arc.config import load
    from arc.plugins import PluginBuildContext, build as build_plugins
    from arc.providers import build as build_provider
    from arc.runtime.bus import EventBus, HookRegistry
    from arc.runtime.ids import new_session_id
    from arc.runtime.loop import AgentSession
    from arc.tools import build as build_tools

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

    # Wire everything together
    provider = build_provider(cfg.provider)
    tools = build_tools(cfg.tools)
    registry = HookRegistry(
        failure_threshold=cfg.plugins.failure_threshold,
        exception_message_max_chars=cfg.plugins.exception_message_max_chars,
    )
    bus = EventBus(registry)

    session_id = new_session_id()
    config_snapshot_yaml = paths.config_file.read_text()

    # Headless mode: any guard escalation auto-denies. The NoOpGate logs
    # to stderr so the user can see why a tool was blocked.
    from arc.user_gate import NoOpGate
    gate = NoOpGate()

    plugins = build_plugins(cfg.plugins, PluginBuildContext(
        sessions_dir=paths.sessions_dir,
        session_id=session_id,
        config_snapshot_yaml=config_snapshot_yaml,
        user_gate=gate,
        bus=bus,
    ))
    for built in plugins:
        registry.register(built.instance, hooks_order=built.hooks_order)

    sess = AgentSession(
        config=cfg, provider=provider, tools=tools,
        registry=registry, bus=bus, session_id=session_id,
        subagent_registry=_make_subagent_registry(cfg, home),
    )

    try:
        sess.start()
        _emit_discovery_report(bus)
        _emit_enablement_outcomes(bus, enablement_outcomes)
        outcome = sess.run_turn(prompt)
        print(outcome.final_response)
        return 0 if outcome.success else 1
    finally:
        sess.end()


def _cmd_replay(
    home_override: str | None,
    *,
    session_id: str | None,
    live_llm: bool,
    do_diff: bool,
    override_provider: str | None = None,
    override_model: str | None = None,
    max_cost_usd: float | None = None,
    against_spec: str | None = None,
) -> int:
    """Replay a recorded session.

    Modes:
      - no session_id  → drop into the 0019 TUI replay menu
      - --against      → batch replay against multiple targets (0019)
      - override flags → cross-provider single replay (0019)
      - --live-llm     → mode 3 same-provider
      - default        → mode 2 deterministic
    """
    _load_dotenv_into_environ(home_override)

    # ── No session id → TUI replay menu ───────────────────────────────────
    if session_id is None:
        return _cmd_replay_menu(home_override)

    from arc.bootstrap import bootstrap, paths_for, resolve_home
    from arc.config import load
    from arc.plugins import PluginBuildContext, build as build_plugins
    from arc.providers import build as build_provider
    from arc.replay import (
        MissingRecordingError,
        ReplayDivergenceError,
        ReplayProvider,
        ReplayingToolRegistry,
        diff_event_logs,
        load as load_replay,
    )
    from arc.replay.override import OverrideError, apply_override
    from arc.runtime.bus import EventBus, HookRegistry
    from arc.runtime.ids import new_session_id
    from arc.runtime.loop import AgentSession

    home = resolve_home(home_override)
    paths = paths_for(home)
    source_dir = paths.sessions_dir / session_id

    # ── --against → batch fan-out via batch.py ─────────────────────────
    if against_spec is not None:
        return _cmd_replay_batch(
            home, paths.sessions_dir,
            source_id=session_id,
            against_spec=against_spec,
            max_cost_usd=max_cost_usd,
        )

    try:
        replay_data = load_replay(source_dir)
    except MissingRecordingError as e:
        print(f"replay: {e}", file=sys.stderr)
        return 1

    # Use the current config (so the user can edit it between recording
    # and replay to test changes). The snapshot is informational only.
    cfg = load(paths.config_file)

    # Apply provider/model override if requested (0019).  Override implies
    # --live-llm; otherwise the recorded ReplayProvider would just replay
    # the original LLM and the override would have no effect.
    if override_provider is not None:
        if not live_llm:
            live_llm = True  # silently upgrade — override is meaningless without --live-llm
        if not override_model:
            print("--override-provider requires --override-model", file=sys.stderr)
            return 2
        try:
            cfg = apply_override(cfg, provider=override_provider, model=override_model)
        except OverrideError as e:
            print(f"replay override: {e}", file=sys.stderr)
            return 2

    # Tool registry comes from the recording, not the config — the recording
    # tells us what tool names were called, with what inputs/outputs.
    mode = "by_call" if live_llm else "in_order"
    tools = ReplayingToolRegistry(replay_data, mode=mode)

    # Provider: stubbed (mode 2) or real (mode 3)
    if live_llm:
        provider = build_provider(cfg.provider)
    else:
        provider = ReplayProvider(replay_data.llm_responses)

    registry = HookRegistry(
        failure_threshold=cfg.plugins.failure_threshold,
        exception_message_max_chars=cfg.plugins.exception_message_max_chars,
    )
    bus = EventBus(registry)

    new_session_id_ = new_session_id()
    plugins = build_plugins(cfg.plugins, PluginBuildContext(
        sessions_dir=paths.sessions_dir,
        session_id=new_session_id_,
        config_snapshot_yaml=paths.config_file.read_text(),
        bus=bus,
    ))
    for built in plugins:
        registry.register(built.instance, hooks_order=built.hooks_order)

    # 0019: inject max_cost plugin if requested.  Lives outside cfg.plugins
    # because the cap is a per-invocation flag, not a config-file setting.
    max_cost_plugin = None
    if max_cost_usd is not None and max_cost_usd > 0:
        from arc.plugins.max_cost import MaxCostPlugin
        from arc.tui.pricing import PricingTable
        max_cost_plugin = MaxCostPlugin(
            max_cost_usd=float(max_cost_usd),
            pricing_table=PricingTable(cache_path=paths.home / "pricing_cache.json"),
        )
        max_cost_plugin.bind_bus(bus)
        registry.register(max_cost_plugin, hooks_order={})

    sess = AgentSession(
        config=cfg, provider=provider, tools=tools,
        registry=registry, bus=bus, session_id=new_session_id_,
        subagent_registry=_make_subagent_registry(cfg, home),
    )

    mode_label = "3 (live LLM)" if live_llm else "2 (strict)"
    if override_provider:
        mode_label += f" — override {override_provider}/{override_model}"
    if max_cost_usd is not None:
        mode_label += f" — max ${max_cost_usd:.2f}"
    print(f"replaying {session_id} → {new_session_id_}  (mode {mode_label})")

    diverged = False
    aborted = False
    try:
        sess.start()
        for user_input in replay_data.user_inputs:
            sess.run_turn(user_input)
    except ReplayDivergenceError as e:
        print(f"\nREPLAY DIVERGED:\n  {e}\n", file=sys.stderr)
        diverged = True
    except Exception as e:
        # max_cost or other plugin errors bubble here
        from arc.plugins.max_cost import MaxCostExceeded
        if isinstance(e, MaxCostExceeded):
            print(f"\nREPLAY ABORTED: {e}", file=sys.stderr)
            aborted = True
        else:
            raise
    finally:
        sess.end()

    # Mark the new session as a replay
    new_meta_path = paths.sessions_dir / new_session_id_ / "meta.json"
    if new_meta_path.exists():
        import json
        meta = json.loads(new_meta_path.read_text())
        meta["replay_of"] = session_id
        meta["replay_mode"] = "by_call" if live_llm else "in_order"
        new_meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False))

    if not do_diff or aborted:
        return 1 if (diverged or aborted) else 0

    # Run the diff
    result = diff_event_logs(
        original=source_dir / "events.jsonl",
        replayed=paths.sessions_dir / new_session_id_ / "events.jsonl",
    )

    if result.matched and not diverged:
        print(f"replay matches ({result.n_events_a} events)")
        return 0

    print(f"\nreplay DIVERGED ({result.n_events_a} original vs "
          f"{result.n_events_b} replayed events)", file=sys.stderr)
    if result.first_divergence_index is not None:
        print(f"first divergence at event index "
              f"{result.first_divergence_index}", file=sys.stderr)
    print(file=sys.stderr)
    print(result.unified_diff, file=sys.stderr)
    return 1


def _cmd_replay_batch(
    home: Path,
    sessions_dir: Path,
    *,
    source_id: str,
    against_spec: str,
    max_cost_usd: float | None,
) -> int:
    """Multi-target replay via arc.replay.batch.  Auto-launches `arc compare`
    at the end against the source + all completed targets (0019)."""
    from arc.replay.batch import BatchTarget, run_batch
    from arc.replay.compare import render_full_comparison
    from arc.replay.override import OverrideError, parse_target_list

    try:
        target_pairs = parse_target_list(against_spec)
    except OverrideError as e:
        print(f"replay --against: {e}", file=sys.stderr)
        return 2

    targets = [BatchTarget(provider=p, model=m) for p, m in target_pairs]
    print(f"replay batch: source={source_id}  targets={len(targets)}")
    for t in targets:
        print(f"  + {t.short()}")

    def _on_start(t):
        print(f"\n→ running {t.short()} …", file=sys.stderr)

    def _on_done(r):
        status = "ok" if r.succeeded else f"failed (rc={r.return_code})"
        sid = r.target_session_id or "—"
        print(f"  {t_short(r.target)}: {status}  session={sid}  {r.elapsed_seconds:.1f}s",
              file=sys.stderr)

    results = run_batch(
        source_session_id=source_id,
        targets=targets,
        arc_home=home,
        max_cost_usd=max_cost_usd,
        on_target_start=_on_start,
        on_target_done=_on_done,
    )

    successes = [r for r in results if r.succeeded]
    print(f"\nbatch complete: {len(successes)}/{len(results)} succeeded")

    if successes:
        # Auto-launch compare against source + successful targets
        dirs = [sessions_dir / source_id] + [
            sessions_dir / r.target_session_id for r in successes  # type: ignore[arg-type]
        ]
        from arc.tui.pricing import PricingTable
        table = PricingTable(cache_path=home / "pricing_cache.json")
        print()
        print(render_full_comparison(dirs, pricing_table=table))

    return 0 if all(r.succeeded for r in results) else 1


def t_short(target) -> str:  # tiny shim, batch.BatchTarget has .short()
    return target.short()


def _cmd_compare(
    home_override: str | None,
    *,
    session_ids: list[str],
    full: bool,
) -> int:
    """`arc compare` — side-by-side comparison of N sessions (0019)."""
    from arc.bootstrap import paths_for, resolve_home
    from arc.replay.compare import render_full_comparison
    from arc.tui.pricing import PricingTable

    if len(session_ids) < 2:
        print("arc compare: need at least 2 session ids", file=sys.stderr)
        return 2

    home = resolve_home(home_override)
    paths = paths_for(home)
    dirs = [paths.sessions_dir / sid for sid in session_ids]
    missing = [d for d in dirs if not (d / "events.jsonl").is_file()]
    if missing:
        for d in missing:
            print(f"compare: missing events.jsonl in {d}", file=sys.stderr)
        return 1

    if full:
        # Verbose mode: just dump the events files side-by-side
        for d in dirs:
            print(f"\n========== {d.name} ==========")
            print((d / "events.jsonl").read_text())
        return 0

    table = PricingTable(cache_path=home / "pricing_cache.json")
    print(render_full_comparison(dirs, pricing_table=table))
    return 0


def _cmd_replay_menu(home_override: str | None) -> int:
    """`arc replay` with no args → opens the setup hub on the Replay section."""
    from arc.bootstrap import bootstrap, paths_for, resolve_home
    from arc.setup.hub import run_hub

    home = resolve_home(home_override)
    bootstrap(home)
    result = run_hub(home, initial_section="replay")
    if result.launch_session:
        return _cmd_interactive(home_override)
    return result.rc


def _cmd_resume(
    home_override: str | None,
    *,
    session_id: str,
    prompt: str | None,
    no_tui: bool,
    at_turn: int | None = None,
) -> int:
    """Resume a session — load conversation, start a new session, continue.

    The new session is marked `resumed_from: <original>` in meta. Sessions
    can be resumed regardless of whether they were paused or completed —
    this doubles as "continue an old conversation."

    `at_turn` is the mode 4 (branch) knob: restore only the first N turns.
    None = restore everything (regular resume).
    """
    _load_dotenv_into_environ(home_override)

    from arc.bootstrap import bootstrap, paths_for, resolve_home
    from arc.config import load
    from arc.plugins import PluginBuildContext, build as build_plugins
    from arc.providers import build as build_provider
    from arc.resume import count_completed_turns, messages_from_session
    from arc.runtime.bus import EventBus, HookRegistry
    from arc.runtime.ids import new_session_id
    from arc.runtime.loop import AgentSession
    from arc.tools import build as build_tools

    home = resolve_home(home_override)
    paths = paths_for(home)
    source_dir = paths.sessions_dir / session_id

    if not source_dir.is_dir():
        print(f"resume: session not found: {session_id}", file=sys.stderr)
        print(f"  expected: {source_dir}", file=sys.stderr)
        return 1

    # Validate / clamp --at-turn
    effective_at_turn: int | None = at_turn
    if at_turn is not None:
        total = count_completed_turns(source_dir)
        if at_turn < 0:
            print(f"resume: --at-turn must be >= 0", file=sys.stderr)
            return 1
        if at_turn > total:
            print(
                f"resume: --at-turn {at_turn} > available turns ({total}); "
                f"clamping to {total}",
                file=sys.stderr,
            )
            effective_at_turn = total
        if at_turn == 0:
            print(
                "resume: --at-turn 0 → no messages restored (fresh session)",
                file=sys.stderr,
            )

    try:
        prior_messages = messages_from_session(
            source_dir, max_turns=effective_at_turn,
        )
    except FileNotFoundError as e:
        print(f"resume: {e}", file=sys.stderr)
        return 1

    cfg = load(paths.config_file)

    provider = build_provider(cfg.provider)
    tools = build_tools(cfg.tools)
    registry = HookRegistry(
        failure_threshold=cfg.plugins.failure_threshold,
        exception_message_max_chars=cfg.plugins.exception_message_max_chars,
    )
    bus = EventBus(registry)

    new_sid = new_session_id()
    from arc.user_gate import NoOpGate
    gate = NoOpGate()
    plugins = build_plugins(cfg.plugins, PluginBuildContext(
        sessions_dir=paths.sessions_dir,
        session_id=new_sid,
        config_snapshot_yaml=paths.config_file.read_text(),
        user_gate=gate,
        bus=bus,
    ))
    for built in plugins:
        registry.register(built.instance, hooks_order=built.hooks_order)

    sess = AgentSession(
        config=cfg, provider=provider, tools=tools,
        registry=registry, bus=bus, session_id=new_sid,
        subagent_registry=_make_subagent_registry(cfg, home),
        initial_messages=prior_messages,
    )

    branch_note = ""
    if at_turn is not None:
        branch_note = f"  (branch @ turn {effective_at_turn})"
    print(f"resuming {session_id} → {new_sid}{branch_note}  "
          f"({len(prior_messages)} messages restored)")

    def _mark_resume_meta():
        """Stamp resumed_from + restored_message_count on the new session's meta.

        Run AFTER sess.end() so the recorder's own on_session_end write doesn't
        clobber our additions. The recorder writes meta from its own dict and
        doesn't merge what's on disk.
        """
        meta_path = paths.sessions_dir / new_sid / "meta.json"
        if meta_path.exists():
            import json
            meta = json.loads(meta_path.read_text())
            meta["resumed_from"] = session_id
            meta["restored_message_count"] = len(prior_messages)
            if at_turn is not None:
                meta["branched_at_turn"] = effective_at_turn
            meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False))

    # Headless one-shot
    if prompt is not None:
        try:
            sess.start()
            outcome = sess.run_turn(prompt)
        finally:
            sess.end()
            _mark_resume_meta()
        print(outcome.final_response)
        return 0 if outcome.success else 1

    # No prompt, --no-tui: restore-only
    if no_tui:
        try:
            sess.start()
        finally:
            sess.end()
            _mark_resume_meta()
        print("session restored. exit (--no-tui set, no --prompt given).")
        return 0

    # Interactive continuation via TUI (TUIApp.run handles start/end itself)
    from rich.console import Console
    from arc.tui.app import TUIApp
    from arc.user_gate import TUIGate
    console = Console()
    # Upgrade the guard's gate from NoOp to TUI now that we know it's interactive
    for built in plugins:
        if built.name == "guard":
            built.instance._gate = TUIGate(console=console)
    app = TUIApp(cfg, sess, home_display=str(home), console=console)
    try:
        return app.run()
    finally:
        _mark_resume_meta()


def _cmd_rerun(
    home_override: str | None,
    *,
    session_id: str,
    stop_on_error: bool,
) -> int:
    """Rerun (mode 5): replay just the user inputs against a fresh agent.

    Live LLM + live tools — actually does the work again. New session is
    marked `rerun_of: <original>` in meta. Useful as a scenario regression
    test ("does this still pass with my current config?").
    """
    _load_dotenv_into_environ(home_override)

    from arc.bootstrap import bootstrap, paths_for, resolve_home
    from arc.config import load
    from arc.plugins import PluginBuildContext, build as build_plugins
    from arc.providers import build as build_provider
    from arc.rerun import user_inputs_from_session
    from arc.runtime.bus import EventBus, HookRegistry
    from arc.runtime.ids import new_session_id
    from arc.runtime.loop import AgentSession
    from arc.tools import build as build_tools
    from arc.user_gate import NoOpGate

    home = resolve_home(home_override)
    paths = paths_for(home)
    source_dir = paths.sessions_dir / session_id

    if not source_dir.is_dir():
        print(f"rerun: session not found: {session_id}", file=sys.stderr)
        return 1

    try:
        inputs = user_inputs_from_session(source_dir)
    except FileNotFoundError as e:
        print(f"rerun: {e}", file=sys.stderr)
        return 1

    if not inputs:
        print(f"rerun: source session has no user inputs", file=sys.stderr)
        return 1

    cfg = load(paths.config_file)
    provider = build_provider(cfg.provider)
    tools = build_tools(cfg.tools)
    registry = HookRegistry(
        failure_threshold=cfg.plugins.failure_threshold,
        exception_message_max_chars=cfg.plugins.exception_message_max_chars,
    )
    bus = EventBus(registry)

    new_sid = new_session_id()
    plugins = build_plugins(cfg.plugins, PluginBuildContext(
        sessions_dir=paths.sessions_dir,
        session_id=new_sid,
        config_snapshot_yaml=paths.config_file.read_text(),
        user_gate=NoOpGate(),
        bus=bus,
    ))
    for built in plugins:
        registry.register(built.instance, hooks_order=built.hooks_order)

    sess = AgentSession(
        config=cfg, provider=provider, tools=tools,
        registry=registry, bus=bus, session_id=new_sid,
        subagent_registry=_make_subagent_registry(cfg, home),
    )

    print(f"rerunning {session_id} → {new_sid}  "
          f"({len(inputs)} user input(s))")

    n_ok = 0
    n_failed = 0
    try:
        sess.start()
        for i, user_text in enumerate(inputs, start=1):
            print(f"\n[rerun turn {i}/{len(inputs)}]  {user_text[:80]}"
                  f"{'...' if len(user_text) > 80 else ''}")
            outcome = sess.run_turn(user_text)
            if outcome.success:
                n_ok += 1
                if outcome.final_response:
                    print(outcome.final_response)
            else:
                n_failed += 1
                print(f"[turn failed: {outcome.error}]", file=sys.stderr)
                if stop_on_error:
                    print(f"rerun: stopping after first failure (--stop-on-error)",
                          file=sys.stderr)
                    break
    finally:
        sess.end()
        # Mark rerun_of on meta AFTER end() (same race fix as resume)
        meta_path = paths.sessions_dir / new_sid / "meta.json"
        if meta_path.exists():
            import json
            meta = json.loads(meta_path.read_text())
            meta["rerun_of"] = session_id
            meta["rerun_turns_attempted"] = n_ok + n_failed
            meta["rerun_turns_succeeded"] = n_ok
            meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False))

    print(f"\nrerun complete: {n_ok} ok, {n_failed} failed, "
          f"{len(inputs) - n_ok - n_failed} skipped")
    return 0 if n_failed == 0 else 1


def _cmd_interactive(home_override: str | None) -> int:
    """Interactive session — the inline TUI."""
    _load_dotenv_into_environ(home_override)

    from arc.bootstrap import bootstrap, paths_for, resolve_home
    from arc.config import load
    from arc.plugins import PluginBuildContext, build as build_plugins
    from arc.providers import build as build_provider
    from arc.runtime.bus import EventBus, HookRegistry
    from arc.runtime.ids import new_session_id
    from arc.runtime.loop import AgentSession
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

    provider = build_provider(cfg.provider)
    tools = build_tools(cfg.tools)
    registry = HookRegistry(
        failure_threshold=cfg.plugins.failure_threshold,
        exception_message_max_chars=cfg.plugins.exception_message_max_chars,
    )
    bus = EventBus(registry)

    session_id = new_session_id()

    # Interactive mode: TUIGate prompts the user via prompt_toolkit when
    # a tool trips an escalation pattern. Construct the gate with a shared
    # console so escalation prompts use the same render pipeline as the rest.
    from rich.console import Console
    from arc.user_gate import TUIGate
    console = Console()
    gate = TUIGate(console=console)

    plugins = build_plugins(cfg.plugins, PluginBuildContext(
        sessions_dir=paths.sessions_dir,
        session_id=session_id,
        config_snapshot_yaml=paths.config_file.read_text(),
        user_gate=gate,
        bus=bus,
    ))
    for built in plugins:
        registry.register(built.instance, hooks_order=built.hooks_order)

    sess = AgentSession(
        config=cfg, provider=provider, tools=tools,
        registry=registry, bus=bus, session_id=session_id,
        subagent_registry=_make_subagent_registry(cfg, home),
    )
    # Emit discovery + enablement events onto the session bus so they
    # land in events.jsonl alongside session.started. Done before app.run()
    # so the bus is wired but after AgentSession is built.
    _emit_discovery_report(bus)
    _emit_enablement_outcomes(bus, enablement_outcomes)

    app = TUIApp(cfg, sess, home_display=str(home), console=console)
    return app.run()


# ── arc plugins ────────────────────────────────────────────────────────────


def _cmd_plugins(home_override: str | None, *, action: str | None) -> int:
    """`arc plugins` — manage installed plugins.

    No action  → opens the setup hub on the Plugins section.
    list       → non-interactive plain-text status table.
    """
    from arc.bootstrap import bootstrap, paths_for, resolve_home
    from arc.setup.hub import run_hub
    from arc.setup.plugin_menu import list_plugins

    home = resolve_home(home_override)
    bootstrap(home)
    paths = paths_for(home)

    if action == "list":
        return list_plugins(paths.config_file)
    result = run_hub(home, initial_section="plugins")
    if result.launch_session:
        return _cmd_interactive(home_override)
    return result.rc


def _mcp_add(config_path, args) -> int:
    """Parse `arc mcp add` args and register the server via the writer."""
    import shlex
    import sys

    from arc.mcp.config import McpConfigError
    from arc.setup.writer import render_changes, write_mcp_server_add

    command = shlex.split(args.mcp_command) if args.mcp_command else None
    env: dict[str, str] = {}
    for kv in args.env or []:
        if "=" not in kv:
            sys.stderr.write(f"error: --env expects K=V, got {kv!r}\n")
            return 1
        k, v = kv.split("=", 1)
        env[k] = v
    allow = [x.strip() for x in args.tools_allow.split(",")] if args.tools_allow else None
    deny = [x.strip() for x in args.tools_deny.split(",")] if args.tools_deny else None
    try:
        changes = write_mcp_server_add(
            config_path, name=args.name, transport=args.transport, url=args.url,
            command=command, env=env or None, cwd=args.cwd, tool_prefix=args.tool_prefix,
            tools_allow=allow, tools_deny=deny, enabled=not args.disabled,
        )
    except (McpConfigError, ValueError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 1
    sys.stdout.write(render_changes(changes) + "\n")
    sys.stdout.write("(takes effect next session)\n")
    return 0


def _cmd_mcp(home_override: str | None, args) -> int:
    """`arc mcp` — manage MCP servers.

    No action  → setup hub on the MCP Servers section.
    list       → config-level server table (non-interactive).
    status     → connect and report live state + tool counts.
    add        → add/update a server in config.yml (programmatic registration).
    remove     → remove a server from config.yml.
    """
    import sys

    from arc.bootstrap import bootstrap, paths_for, resolve_home
    from arc.setup.hub import run_hub
    from arc.setup.mcp_menu import list_mcp

    action = getattr(args, "mcp_action", None)
    home = resolve_home(home_override)
    bootstrap(home)
    paths = paths_for(home)

    if action == "add":
        return _mcp_add(paths.config_file, args)
    if action == "remove":
        from arc.setup.writer import render_changes, write_mcp_server_remove
        try:
            changes = write_mcp_server_remove(paths.config_file, name=args.name)
        except ValueError as exc:
            sys.stderr.write(f"error: {exc}\n")
            return 1
        sys.stdout.write(render_changes(changes) + "\n")
        return 0

    if action == "list":
        return list_mcp(paths.config_file)
    if action == "status":
        from arc.mcp.bridge import McpBridge
        from arc.mcp.config import parse_mcp_config
        from arc.setup.mcp_menu import _mcp_config_dict

        cfg = parse_mcp_config(_mcp_config_dict(paths.config_file))
        if not cfg.servers:
            sys.stdout.write("(no MCP servers configured)\n")
            return 0
        bridge = McpBridge(cfg)
        bridge.on_session_start(ctx=None)  # connects
        try:
            for row in bridge.status():
                mark = "●" if row["state"] == "connected" else "○"
                line = (f"  {mark} {row['name']:<20} {row['transport']:<6} "
                        f"{row['state']:<12} {row['tool_count']} tools")
                if row["error"]:
                    line += f"  ({row['error']})"
                sys.stdout.write(line + "\n")
        finally:
            bridge.on_session_end(ctx=None)
        return 0

    result = run_hub(home, initial_section="mcp")
    if result.launch_session:
        return _cmd_interactive(home_override)
    return result.rc


def _cmd_subagents(
    home_override: str | None,
    *,
    action: str | None,
    spec_name: str | None,
) -> int:
    """`arc subagents` — list/show/enable/disable sub-agent specs.

    No action  → opens the setup hub on the Sub-agents section.
    list       → tabular dump of every discovered spec with source + status.
    show NAME  → pretty-print the merged spec.
    enable / disable NAME → toggle the `subagents.<name>.enabled` flag in config.yml.
    """
    from arc.bootstrap import bootstrap, paths_for, resolve_home
    from arc.config import load
    from arc.runtime.subagents.registry import SubAgentRegistry
    from arc.setup.hub import run_hub
    from arc.setup.writer import write_subagent_enablement

    home = resolve_home(home_override)
    bootstrap(home)
    paths = paths_for(home)

    if action is None:
        result = run_hub(home, initial_section="subagents")
        if result.launch_session:
            return _cmd_interactive(home_override)
        return result.rc

    cfg = load(paths.config_file)
    registry = SubAgentRegistry(arc_home=home)
    report = registry.discover(cfg.subagents.as_overrides())

    if action == "list":
        specs = registry.all_specs()
        if not specs:
            print("(no sub-agents discovered)")
            return 0
        # Column widths
        name_w = max(len("NAME"), max(len(n) for n in specs))
        prov_w = max(len("PROVIDER/MODEL"), max(len(f"{s.provider}/{s.model}") for s in specs.values()))
        src_w = max(len("SOURCE"), max(len(_source_label(s)) for s in specs.values()))
        header = f"  {'STATUS':8}  {'NAME':{name_w}}  {'PROVIDER/MODEL':{prov_w}}  {'SOURCE':{src_w}}"
        print(header)
        print("  " + "─" * (len(header) - 2))
        for name in sorted(specs):
            spec = specs[name]
            status = "ENABLED " if registry.is_enabled(name) else "DISABLED"
            pm = f"{spec.provider}/{spec.model}"
            src = _source_label(spec)
            print(f"  {status:8}  {name:{name_w}}  {pm:{prov_w}}  {src:{src_w}}")
        if report.conflicts or report.failures:
            print()
            for c in report.conflicts:
                print(f"  ⚠ name collision: {c.name!r} from {c.discovered_from} "
                      f"conflicts with {c.conflicts_with}")
            for f in report.failures:
                print(f"  ✖ load failure: {f.name!r} from {f.package}: {f.error}")
        return 0

    if action == "show":
        if not spec_name:
            print("usage: arc subagents show NAME", file=sys.stderr)
            return 2
        try:
            spec = registry.get(spec_name)
        except KeyError:
            print(f"unknown sub-agent: {spec_name!r}", file=sys.stderr)
            print(f"  available: {', '.join(sorted(registry.all_specs())) or '(none)'}",
                  file=sys.stderr)
            return 2
        print(f"sub-agent: {spec.name}")
        print(f"  source:                     {_source_label(spec)}")
        print(f"  enabled:                    {registry.is_enabled(spec.name)}")
        print(f"  description:                {spec.description}")
        print(f"  provider/model:             {spec.provider}/{spec.model}")
        print(f"  tools:                      {', '.join(spec.tools) or '(none)'}")
        print(f"  timeout_s:                  {spec.timeout_s}")
        print(f"  max_turns:                  {spec.max_turns}")
        print(f"  max_dispatches_per_session: {spec.max_dispatches_per_session}")
        print(f"  max_consecutive_failures:   {spec.max_consecutive_failures}")
        print(f"  max_transient_retries:      {spec.max_transient_retries}")
        if spec.api_key_env:
            print(f"  api_key_env:                {spec.api_key_env}")
        if spec.base_url:
            print(f"  base_url:                   {spec.base_url}")
        if spec.expected_output:
            print(f"  expected_output:            {spec.expected_output}")
        print(f"  system_prompt:              [{len(spec.system_prompt)} chars]")
        return 0

    if action in ("enable", "disable"):
        if not spec_name:
            print(f"usage: arc subagents {action} NAME", file=sys.stderr)
            return 2
        if spec_name not in registry.all_specs():
            print(f"unknown sub-agent: {spec_name!r}", file=sys.stderr)
            return 2
        changes = write_subagent_enablement(
            paths.config_file,
            name=spec_name,
            enabled=(action == "enable"),
        )
        for ch in changes:
            print(f"  {ch.key}: {ch.old} → {ch.new}")
        return 0

    print(f"unknown subagents action: {action}", file=sys.stderr)
    return 2


def _source_label(spec) -> str:
    """One-word source tag for the `arc subagents` table."""
    if spec.source == "plugin":
        return f"plugin:{spec.source_package or 'unknown'}"
    return spec.source


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


if __name__ == "__main__":
    sys.exit(main())
