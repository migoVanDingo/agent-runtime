"""Argument parser construction for the `arc` CLI.

Pure `argparse` wiring — no dependency on other `arc.cli` submodules, so it
sits at the bottom of the package's internal import DAG.
"""
from __future__ import annotations

import argparse

from arc import __version__


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

    timeline = sub.add_parser(
        "timeline", help="generate/open the visual session timeline (0027)")
    timeline.add_argument("--open", dest="open_browser", action="store_true",
                          help="open timeline.html in a browser")
    timeline.add_argument("--rebuild", action="store_true",
                          help="force full regeneration (all per-session pages too)")

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
