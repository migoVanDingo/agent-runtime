"""`arc mcp` — manage MCP servers consumed by the built-in `mcp` plugin (0025)."""
from __future__ import annotations

import arc.cli as _cli


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
        return _cli._cmd_interactive(home_override)
    return result.rc
