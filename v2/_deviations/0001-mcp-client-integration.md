# Deviations — 0025 MCP client integration

Running log of where the implementation departs from
`_design/0025-mcp-client-integration.md`, with rationale. For review.

**Build status:** all 6 phases implemented. 749 unit tests pass (was 743; +6 new
unit + 1 updated hub test), 6 MCP integration tests pass live (stdio + HTTP real
servers + full builder→bridge path). ruff-clean on all new files. angr-style
graceful-degrade if the `mcp` SDK is absent. Deviations D1–D3 below.

## D1 — MCP is a built-in **plugin**, config in its plugin block (not a top-level `mcp:` section)

**Plan:** a top-level `mcp:` config section parsed by `config.py::_parse_mcp`
into `Config.mcp`, consumed by a bridge registered in `_BUILTIN_BUILDERS`.

**Built:** MCP is a built-in plugin named `mcp` in `_BUILTIN_BUILDERS`. Its
servers live in the plugin's **own config block**
(`plugins.enabled[mcp].config.servers`), parsed inside `arc/mcp/config.py`.

**Why:**
- `build_plugins(cfg.plugins, PluginBuildContext(...))` is called at **6+ sites**
  in `cli.py`, and `PluginBuildContext` carries no `Config` handle. Threading a
  top-level `mcp:` block to the bridge would mean changing `PluginBuildContext`
  (a public-surface dataclass) and every construction site. A plugin builder
  receives `entry.config` for free — zero plumbing.
- It's *more* on-tenet: "MCP support IS a plugin." The bridge inherits discovery,
  quarantine, config, first-run enablement, bus binding, and the `arc plugins`
  menu with no new machinery.
- **`config.py` is untouched** — no `KNOWN_TOP_LEVEL` edit, no `_parse_mcp`, no
  `Config.mcp`. Config parsing for MCP lives in `arc/mcp/config.py` and runs on
  the plugin's config dict.

**Consequences:**
- The whole MCP subsystem can be toggled as one plugin in `arc plugins`
  (bonus: kill-all-MCP switch); per-server enable/disable is the dedicated
  setup section editing `config.servers[]` (still delivered — see Phase 5).
- Config shape becomes:
  ```yaml
  plugins:
    enabled:
      - name: mcp
        config:
          servers:
            - name: container
              transport: http
              url: http://127.0.0.1:8770/mcp
              enabled: true
              tool_prefix: container
        hooks_order: {}
  ```
- Everything else in 0025 (per-server isolation, both transports, adapter→Tool,
  events, `arc[mcp]` extra, setup parity) is unchanged.

## D2 — Setup section + `arc mcp list` show config-level status, not live connection state

**Plan:** the MCP setup section shows per-server **connection status + tool
counts** (`● container http connected 12 tools`).

**Built:** the setup hub section and `arc mcp list` show **config-level** rows
(name, transport, enabled, `→ prefix_*`). Live status + tool counts require
actually connecting, which is provided by a **separate `arc mcp status`** command
(it stands up the bridge, connects, prints live state), not by the hub render.

**Why:** the hub re-renders frequently and cheaply; connecting to every server
(spawning stdio subprocesses / HTTP handshakes) on each render would be slow and
have side effects. `arc mcp status` is the explicit, on-demand live probe.

**Consequence:** enable/disable parity with plugins is fully delivered (checkbox
modal + comment-preserving `write_mcp_server_enablement`); live status is one
command away rather than inline in the hub.

## D3 — Per-server first-run consent deferred

**Plan:** first-run consent per newly-discovered server (like plugin first-run
enablement).

**Built:** not in this pass. The whole `mcp` plugin still gets the standard
plugin first-run enablement (it's a built-in, on by default with empty servers).
Adding a server is an explicit config edit by the user (or a future installer),
which is itself the consent act. Per-server consent prompts can layer on later
without changing the wire/adapter/manager.

## D4 — Added: programmatic add/remove of servers (beyond 0025's toggle-only scope)

**Plan:** 0025 specified enable/disable toggling only (setup menu + `arc mcp`).

**Built (on request):** a programmatic **registration** API so servers can be
added without hand-editing YAML — important for 0024, where the orchestrator
service should self-register:
- `arc.setup.writer.write_mcp_server_add(config_path, *, name, transport, url,
  command, env, cwd, tool_prefix, tools_allow, tools_deny, enabled)` — importable
  core. Upsert semantics; creates the `mcp` plugin entry if absent; validates the
  spec (via `parse_mcp_config`) before writing; comment-preserving.
- `write_mcp_server_remove(config_path, *, name)`.
- CLI: `arc mcp add <name> --transport http|stdio [--url … | --command "…" --env K=V …]
  [--tool-prefix …] [--tools-allow a,b] [--tools-deny …] [--disabled]` and
  `arc mcp remove <name>`.

**Gotcha fixed:** the `--command` option initially auto-derived `dest="command"`,
colliding with the top-level subcommand dest and misrouting `arc mcp add` to the
interactive TUI. Pinned `dest="mcp_command"`.
