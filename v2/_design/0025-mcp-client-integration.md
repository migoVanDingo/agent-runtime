# 0025 — MCP client integration (consuming MCP servers)

## Status: Design (not yet implemented). Prerequisite for 0024's MCP seam.

## Motivation

arc extends itself through **in-process plugins** (`arc.plugins` entry points):
lightweight, observable, no server to run. That is the right default and stays
the default. But it can only consume capabilities written *as arc plugins*.

The container-orchestration service (0024) is deliberately a **standalone,
arc-agnostic service** — the wrong thing to bury in an in-process plugin. Its
model-facing seam should be an **MCP server**, because MCP is client-agnostic
(arc today, other agents tomorrow) and matches the existing proxmox-MCP pattern
already connected to this environment. arc therefore needs to **consume MCP
servers** — a capability it does not yet have.

This design adds that: arc as an **MCP client**, surfacing MCP tools into the
registry as first-class, observable, gated arc tools.

## Guiding principle: MCP complements plugins, it does not replace them

We intentionally did **not** MCP-ify the existing plugins (ghidra, gcs,
briefbot, websearch, angr). For arc-specific, lightweight capabilities an
in-process plugin is strictly cheaper than a server-per-tool, and running many
servers is a real operational tax. The decision rule:

- **In-process plugin** — the capability is arc-specific and light; it wants
  arc-native hooks/events; it has no life outside arc.
- **MCP server** — the capability is a **standalone service** (the 0024
  orchestrator), **third-party**, or **shared across clients**. It has its own
  lifecycle and would exist whether or not arc did.

MCP is additive for that second class. It is not a migration target for the
first.

## The model: MCP as a third tool source via a bridge

arc's tool registry already merges **built-in tools + plugin tools** and raises
on name collision. MCP becomes a **third source**, introduced with minimal new
surface:

> A single built-in `mcp` **bridge** reads a top-level `mcp:` config, connects
> to N configured servers, discovers each server's tools, and **adapts every MCP
> tool into an arc `Tool`** (namespaced by server). Those tools merge into the
> registry like any other.

Because the adapted tools are real arc `Tool`s, they inherit for free:

- the **guard / safety-gate** hooks — an MCP tool call is a tool call, subject to
  the same allow/block/escalate policy;
- the generic **`tool.call.started/completed/failed/denied`** events, and
  therefore **replay** (results already land in the event stream);
- the **permission / first-run consent** flow.

On top of that the bridge emits **MCP-specific events** (below) so MCP is as
observable as anything native — the point of difference vs opaque MCP clients.

### Bridge, not parallel subsystem

The bridge registers as a **built-in plugin builder** (`_BUILTIN_BUILDERS` in
`arc/plugins/__init__.py`) so it plugs into the existing tool-contribution +
lifecycle + config machinery. But its real logic lives in its own subsystem
module (`arc/mcp/`), not crammed into a plugin file: connection management,
discovery, adaptation, per-server isolation. The plugin builder is a thin
adapter over the subsystem.

## Per-server failure isolation (critical)

A single flaky server must **disable only itself** — emit
`mcp.server.quarantined`, drop its tools — while every other server keeps
serving. The bridge catches per-server errors internally and does **not** let
one server's failure raise into arc's whole-plugin quarantine (which would take
the entire bridge down). arc's plugin quarantine only fires for a bridge-level
bug, which should be rare.

## Transports

Both first-class (the `mcp` SDK provides both):

- **stdio (subprocess)** — arc spawns the server as a child process for the
  session, JSON-RPC over stdin/stdout, and **kills it on session end.** This is
  the real answer to "I don't want a million servers running": a stdio server
  runs **only during a session that uses it**, and only if enabled. Reuse the
  hard-won subprocess lessons (v1 JVM/Ghidra): kill on close, don't block
  shutdown, timeouts on calls.
- **Streamable HTTP (+ legacy SSE)** — arc connects to an already-running
  service. This is what the 0024 orchestrator uses (a persistent service arc
  connects to, doesn't own).

## Config (`config.yml`)

New top-level `mcp:` section, parsed by `_parse_mcp` → `McpConfig` on `Config`,
following the `PluginsConfig` pattern. **All keys default-on-missing** so older
configs keep loading (per the config-compat convention).

```yaml
mcp:
  enabled: true
  failure_threshold: 3            # per-server strikes before quarantine
  servers:
    - name: container             # the 0024 orchestrator
      transport: http
      url: http://127.0.0.1:8770/mcp
      enabled: true
      tool_prefix: container      # -> container_<toolname> in the registry
      tools_allow: []             # optional allowlist (empty = all)
      tools_deny: []
      timeout_seconds: 30
    - name: proxmox
      transport: stdio
      command: ["uvx", "proxmox-mcp"]
      env: { PROXMOX_URL: "..." }
      enabled: true
      tool_prefix: proxmox
```

`arc mcp [list|status]` mirrors `arc plugins` (non-interactive list; connection
status; discovered tools per server). First-run consent per server mirrors
plugin first-run enablement — connecting an MCP server (esp. stdio = local code
execution, or a remote HTTP endpoint) is a trust decision the user approves once.

## Setup hub + TUI (enable/disable like plugins)

MCP servers must be manageable from the `arc setup` hub exactly like plugins
are — a first-class parity requirement, not an afterthought. The hub already
renders one section per module in `arc/setup/sections/`, each exporting
`build(ctx) -> Section`, registered (and ordered) in `Hub._build_sections()`.
The Plugins section (`sections/plugins.py`) renders enabled ●/○ rows, offers a
`[ ⏎ toggle ]` action that opens a checkbox menu via `run_in_terminal` (rows
from `setup/plugin_menu.py::collect_rows`), and persists toggles through the
**comment-preserving** `setup/writer.py`. We mirror that shape:

- **`arc/setup/sections/mcp.py`** — `build(ctx) -> Section`, registered in
  `Hub._build_sections()` right after Plugins (sidebar order). Summary line:
  `"{enabled} of {total} servers enabled"`, plus per-server connection status
  and tool counts (richer than plugins, because MCP has live state):
  `● container   http   connected   12 tools`
  `○ proxmox     stdio  disabled`
- **`arc/setup/mcp_menu.py::collect_rows`** — reads the `mcp:` config servers
  into rows (name, transport, enabled, live status, tool count), analogous to
  `plugin_menu.collect_rows`.
- **Toggle** flips each server's `enabled` flag and **persists via the same
  `setup/writer.py`** comment-preserving writer, so hand-written `mcp:` config
  (comments, ordering) survives a toggle — identical guarantee to the plugins
  menu.
- **Enable → connect on next session; disable → don't connect + drop its tools.**
  The toggle only edits config; the manager acts on it at session start (no live
  connect/disconnect from the hub, matching how plugin toggles take effect).
- First-run consent (above) surfaces here too: a newly-discovered `mcp:` server
  shows unconfirmed until enabled.

This reuses the entire plugins-menu apparatus (Section, checkbox modal,
comment-preserving writer) — the MCP section is a sibling of the Plugins
section, not new UI machinery.

## Tool adaptation & namespacing

- Each MCP tool → an arc `Tool`: `name = f"{tool_prefix}_{mcp_tool_name}"`,
  `description` and `input_schema` from the MCP tool definition, `execute()`
  routes to the server over the connection.
- **Prefixing avoids collisions** (arc raises on duplicate tool names) and signals
  provenance to the model.
- `tools_allow` / `tools_deny` filter which of a server's tools are surfaced —
  important when a server exposes more than you want the agent to reach.

## Observability (events)

New `EventType` constants (+ `log_writer/formatter.py` dispatch entries, per the
"new event type" convention):

```
mcp.servers.configured     mcp.server.connected      mcp.server.disconnected
mcp.tools.discovered       mcp.tool.called           mcp.tool.result
mcp.server.error           mcp.server.quarantined
```

Generic `tool.call.*` events fire around every MCP tool call as well (double
coverage: MCP-specific + the uniform tool path). Every MCP interaction is thus in
`events.jsonl` and the human `session.log`.

## Security / trust boundary

MCP servers are external code and their **tool descriptions and results are
model-visible untrusted content** — a prompt-injection surface. Treat MCP
metadata and outputs as untrusted:

- per-server **consent** on first enable (local code / remote endpoint);
- MCP tool calls flow through the **guard / safety-gate** policy like any tool;
- default **timeouts** and (for stdio) process isolation; no implicit host
  access beyond what the server itself has.

## Replay

MCP tool calls are non-deterministic external calls (like LLM calls, bash). No
special machinery needed: because adapted MCP tools are ordinary `Tool`s, their
results are recorded in `tool.call.completed` events, and replay reconstructs
from the event stream without re-calling the server — the existing byte-faithful
replay contract already covers them. (Confirm the recorder captures MCP tool
outputs identically; it should, since they are Tools.)

## Dependency

The official **`mcp` Python SDK** (client side — handles JSON-RPC, stdio /
streamable-HTTP transports, sessions, `list_tools`, `call_tool`). Do not
hand-roll the protocol. Ship it behind an **`arc[mcp]` extra** so minimal installs
stay lean. If `mcp:` config is present but the SDK isn't installed, degrade
gracefully: emit `mcp.server.error`, skip, warn — never crash the session (the
plugin graceful-disable pattern).

## Code layout

```
src/arc/mcp/
  __init__.py
  config.py        McpConfig / McpServerConfig (or fold into arc/config.py _parse_mcp)
  manager.py       connection lifecycle, discovery, per-server isolation, reaping
  transport.py     stdio subprocess + streamable-http clients (over the mcp SDK)
  adapter.py       MCP tool def -> arc Tool; call routing; event emission
  bridge.py        the built-in plugin builder (thin) registered in _BUILTIN_BUILDERS
src/arc/runtime/events.py   + MCP_* constants
src/arc/plugins/log_writer/formatter.py  + MCP_* formatters
src/arc/cli.py   + `arc mcp [list|status]`
src/arc/setup/sections/mcp.py   setup-hub section (parity with sections/plugins.py)
src/arc/setup/mcp_menu.py       collect_rows for the checkbox toggle
src/arc/setup/hub.py            register the section in _build_sections()
# reuse: src/arc/setup/writer.py (comment-preserving), the checkbox modal
```

## Build order

1. **Config + events + SDK dep** — `_parse_mcp`, `McpConfig`, `MCP_*` event
   constants + formatters, `arc[mcp]` extra.
2. **Manager + stdio transport** — connect one stdio server, discover tools,
   per-server isolation, clean subprocess shutdown.
3. **Adapter** — MCP tool → arc Tool, call routing, event emission, gate
   integration; merged into the registry via the bridge builder.
4. **HTTP transport** — connect a long-lived Streamable-HTTP server (this is what
   0024 needs).
5. **`arc mcp` CLI + setup-hub section (`sections/mcp.py` + `mcp_menu.py`,
   enable/disable parity with the Plugins section) + first-run consent +
   allow/deny filtering.**
6. **Validate against a real server** — e.g. the already-present proxmox MCP, or
   a trivial local echo server — end-to-end: config → connect → discover →
   agent calls a tool → events recorded → replayable.

Then 0024 proceeds: its orchestrator exposes an MCP server, and arc consumes it
through this integration.

## Decisions locked

- MCP is a **third tool source**, surfaced via a single built-in **`mcp` bridge**;
  logic lives in an `arc/mcp/` subsystem, the plugin builder is thin.
- **Per-server failure isolation**, not whole-bridge quarantine.
- **Both transports** (stdio spawned per-session; streamable-HTTP for standing
  services). stdio-on-demand is the answer to "don't run a million servers."
- MCP tools are **real arc Tools** → inherit gates, `tool.call.*` events, replay,
  consent; plus **MCP-specific events** for full observability.
- Official **`mcp` SDK**, behind an **`arc[mcp]` extra**; graceful-disable if
  absent.
- **MCP complements in-process plugins**; it is not a replacement for arc-native
  lightweight tools.
- **Setup-hub parity with plugins** — a `sections/mcp.py` section toggles servers
  enabled/disabled via the same checkbox modal + comment-preserving writer as the
  Plugins section; toggles take effect at next session start.

## Open questions

- **Top-level `mcp:` vs a plugin config block** — leaning top-level for
  discoverability; the bridge reads it. Confirm.
- **Resources & prompts** — MCP has three primitives; this scopes **tools only**.
  Resources (→ a `read_resource` tool or arc's context surface) and prompts (→
  arc skills / slash commands) are future work.
- **Dynamic tool refresh** — MCP servers can change their tool list at runtime
  (`notifications/tools/list_changed`). v1: discover at connect (session start).
  Live refresh is future.
- **Health / reconnect** for HTTP servers that restart mid-session — retry with
  backoff, re-discover; interplay with per-server quarantine.

## State

Design only. No code. Blocks the 0024 MCP seam; 0024 proceeds once tools + HTTP
transport (build-order steps 1–4) land.
