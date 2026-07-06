# arc v2 — agent runtime

A minimal, pluggable, fully-observable LLM agent runtime. Ground-up rewrite
of v1 (`../v1/`) that drops v1's brittle multi-stage orchestration in favor
of: **the runtime mediates, the model drives, plugins extend.**

| | |
|---|---|
| Source | ~19,600 lines Python |
| Tests | 768 unit passing (+ real-API integration, auto-skip without keys) |
| Providers | Gemini + Vertex Gemini (`google-genai`), Anthropic, Ollama, llama.cpp (OpenAI-compat) |
| Sub-agents | dispatch as a tool (0020); external specs via `arc.subagents` entry-point |
| MCP | consumes external MCP servers via the built-in `mcp` plugin (0025) |
| TUI | prompt_toolkit + Rich, inline mode (scrollback works) |
| Persistence | None — each session is a self-contained dir under `$ARC_HOME/sessions/<sid>/` |

## Read first

- **`README.md`** — long-form architecture, design principles, replay catalog.
- **`_architecture/`** — authoring guides + reference:
  - `plugin-authoring.md` — 12-hook protocol catalog, builder pattern
  - `provider-authoring.md` — Provider Protocol, byte-fidelity contract
  - `tool-authoring.md` — Tool Protocol, ToolError, output conventions
  - `config-reference.md` — every config key, type, default
  - `cli-reference.md` — every subcommand, sessions dir layout, event taxonomy
- **`_design/`** — phase-by-phase design docs (00xx, chronological). Start
  with `0001-foundation-phase0-design.md` for the contract everything else
  is built against.

## Three-layer architecture

```
src/arc/
  runtime/                  Layer 1 — minimal core (always present)
    loop.py                   ReAct loop
    events.py                 RuntimeEvent + EventType catalog
    hooks.py                  12 Protocol definitions
    bus.py                    HookRegistry + EventBus
    scope.py                  session/turn/scope contextvars
    ids.py                    self-contained ULID generator
  plugins/                  Layer 2 — built-in plugins (all optional)
    jsonl_recorder/           byte-faithful events.jsonl writer
    guard/                    tool-call policy (allow/block/escalate)
    safety_gate/              destructive-action confirmation (0012)
    pause_resume/             pause checkpoint + signal file
    log_writer/               human-readable session.log
    sliding_window_context/   pack_context — drops oldest user-turn fragments
  mcp/                      MCP client subsystem (0025) — config, transport,
                            manager (bg asyncio loop + per-server actors),
                            adapter (MCP tool → arc Tool), bridge (the `mcp` plugin)
  providers/                Layer 3 — supporting code
    base.py / gemini.py / anthropic.py
  tools/                    ls, bash_exec
  tui/                      app.py, render.py, pricing.py (LiteLLM cost lookup)
  replay/ resume/ rerun/    replay-mode engines
  cli.py bootstrap.py config.py defaults.py user_gate.py
```

## Design principles (don't violate without discussion)

1. **Runtime mediates, model drives, plugins extend.** Don't put policy in
   the runtime. Put it in a plugin.
2. **Observability is king.** Every observable moment emits an event. Events
   are the source of truth — replay, resume, branch, rerun, the human log,
   and meta files all rebuild from `events.jsonl`.
3. **No hardcoded user-tunables.** If a value is user-tunable, it lives in
   `config.yml` (via `defaults.py`). If you can't grep for the key in
   `defaults.py`, the knob doesn't exist.
4. **Byte-faithful replay.** Every `LLMResponse` must include `.raw` (the
   provider's full response as a JSON-faithful dict). Replay reconstructs
   from it without re-calling the API.
5. **Plugin failure ≠ session crash.** Plugins are quarantined after
   `plugins.failure_threshold` exceptions (default 3). Don't catch exceptions
   defensively in your plugin — the runtime handles it.

## CLI surface

```
arc                          interactive TUI
arc bootstrap [--force]      create $ARC_HOME + default config
arc setup                    interactive setup hub (sidebar + content; see 0023)
arc setup --picker           classic provider/model picker only (0017)
arc setup --section NAME     open hub focused on a specific section
arc run "<prompt>"           one-shot non-interactive turn
arc sessions                 list recorded sessions
arc show <id>                pretty-print events
arc log <id> [--tail N]      human-readable session.log
arc config show / path       inspect resolved config
arc plugins [list]           list (non-interactive); no args → hub on Plugins
arc subagents [list|show|enable|disable] no args → hub on Sub-agents
arc mcp [list|status|add|remove]  MCP servers; no args → hub on MCP Servers (0025)
arc llm [list|status|start|stop|restart|logs] no args → hub on LLM Server
arc replay <id> [--live-llm] mode 2 (deterministic) / mode 3 (live LLM)
arc replay                   no id → hub on Replay
arc resume <id> [--at-turn N --prompt "..."]   mode 1 (time-travel) / mode 4 (branch)
arc rerun <id>               mode 5 (rerun user inputs vs fresh agent)
arc wipe [--all|--sessions|--llm|--history|--pricing-cache|--dry-run]
arc --home <path> <cmd>      override ARC_HOME for one invocation
```

## Out-of-tree plugins

arc supports **external** plugins shipped as pip-installable packages.
They register via the `arc.plugins` entry-point group and arc discovers
them at startup. The contract:

- **Public API:** `arc.plugin_api` (v0.1) is the single stable import path.
  See `src/arc/plugin_api.py` — re-exports `Tool`, `ToolError`,
  `RuntimeEvent`, `SessionContext`, `PluginBuildContext`, hook payloads.
  Plugin authors MUST NOT import from `arc.tools.base`, `arc.runtime.hooks`,
  etc. — those can move.
- **Discovery:** `arc/plugins/discovery.py` walks `entry_points(group="arc.plugins")`.
  Each entry resolves to a `build(config, build_ctx) -> object` callable.
  Built-ins always win on name conflict; failures are isolated to one plugin.
- **First-run enablement:** `arc/plugins/enablement.py` + `_apply_first_run_enablement`
  in `cli.py`. Interactive mode prompts on discovery of a new plugin and
  persists the answer to `config.yml`. Headless mode skips the prompt.
- **Tool contribution:** plugins can implement `provides_tools() -> list[Tool]`.
  Tools are merged into the registry by `AgentSession._merge_plugin_tools()`
  after `on_session_start` fires.
- **Tool bus binding:** any tool that defines `bind_bus(bus)` gets the
  event bus injected by `AgentSession._bind_bus_to_tools()`.
- **Hooks_order auto-fill:** plugins with `hooks_order: {}` in config (the
  shape persisted by first-run enablement) get every hook method they
  implement auto-registered at `DEFAULT_PLUGIN_HOOK_PRIORITY=50`. Built-ins
  with explicit `hooks_order` are unaffected. See `_resolve_hooks_order`
  in `arc/plugins/__init__.py`.

**Existing external plugins** (forks of `arc-plugin-template`):
- `arc-plugin-briefbot` — read-only tools over a local Briefbot SQLite corpus
- `arc-plugin-websearch` — `web_search` / `read_url` / `http_request` / `extract_html`
  with pluggable backends

`arc plugins` opens a checkbox menu (built-ins + external + dangling
entries from uninstalled packages). `arc plugins list` is the non-
interactive print. Both use the comment-preserving writer at
`arc/setup/writer.py`.

## ARC_HOME resolution

1. `--home <path>` flag
2. `ARC_HOME` env var
3. `./.arc/` (cwd, if exists — for per-project configs)
4. `~/.arc/` (default)

## Conventions when working in this tree

- **Use Edit/Write, not bash heredocs.** The harness has dedicated tools.
- **Run `python3 -m pytest tests/ -q`** after non-trivial changes. Tests
  are fast (~90s for the full unit + integration suite with API keys).
- **Tests structure:**
  - `tests/unit/` — fast, no network
  - `tests/integration/` — real Gemini/Anthropic; auto-skip without API key
- **Don't break replay.** If you change provider translation or event shape,
  run replay tests specifically and update fixtures if needed (intentional)
  or fix the regression (not intentional).
- **New built-in plugin = builder + `_BUILTIN_BUILDERS` entry + `defaults.py` entry + tests.**
  See `_architecture/plugin-authoring.md`. `_BUILDERS` is now a derived dict
  populated by `_refresh_builders()` at import time — don't edit it directly.
- **New external plugin = its own repo, forked from `arc-plugin-template`.**
  Don't add it to this tree.
- **New event type = `events.py` constant + `log_writer/formatter.py`
  dispatch entry.** Don't skip the formatter — session.log loses fidelity.
- **Comments: minimal.** No multi-paragraph docstrings, no obvious comments.
  WHY-only when non-obvious; let names carry the WHAT.
- **New theme = drop a module in `arc/tui/themes/` + add it to `_THEMES` in
  `arc/tui/themes/__init__._build_registry()`.** Cover the full `RICH_STYLE_KEYS`
  and `PT_STYLE_KEYS` namespaces; the `test_themes.py` parametrized tests
  enforce it. See `_design/0023-setup-hub-and-themes.md`.
- **New hub section = drop a module in `arc/setup/sections/` exporting
  `build(ctx) -> Section` + register it in `Hub._build_sections()`.** Layout
  stays sidebar+content; sections only own the right pane.

## Common gotchas

- **Existing user `.arc/config.yml` files don't auto-pick up new plugins.**
  The loader is strict by design — adding a new plugin to `defaults.py`
  doesn't silently mutate users' configs. They need `arc bootstrap --force`
  or to paste the new block in manually.
- **New optional config keys must default-on-missing in `_parse_*` functions
  in `config.py`** so older configs keep loading. See the `_parse_tui` pattern
  for how new fields were added.
- **macOS SSL caveat for pricing.** Stock macOS Python often lacks a CA
  bundle, so `PricingTable._fetch_upstream()` fails. Cost segment in the
  TUI toolbar simply disappears in that case. Fix at the user level:
  `pip install --upgrade certifi`.
- **Headless mode auto-denies safety_gate + guard escalations.** `arc run`
  uses `NoOpGate` by design. Batch jobs that need destructive ops should
  set `bypass_mode: true` on safety_gate in a scratch config.
- **Sub-agents must inherit the parent's spinner.** (Carried over from v1.)
  A fresh `Spinner` under the TUI corrupts the alt-screen render. Pass
  `None` or hand the parent's spinner down.
- **prompt_toolkit's bottom_toolbar updates only between prompts.** It does
  not push updates mid-input. This is fine for our use case; don't try to
  work around it.
- **MCP is a built-in *plugin* (`mcp`), not a top-level config section.** Its
  servers live in the plugin's config block (`plugins.enabled[mcp].config.servers`)
  — chosen because `build_plugins` runs at 6+ sites and `PluginBuildContext` has
  no `Config` handle. See `_deviations/0001-mcp-client-integration.md`.
- **The `mcp` SDK is async; arc is sync.** The manager runs a background asyncio
  loop with one **actor coroutine per server** (open/use/close in a single task —
  anyio cancel-scope safe) + `run_coroutine_threadsafe` bridges. Don't try to
  `await` MCP calls from the runtime; go through `McpManager.call_tool` (sync).
  The `mcp` SDK is an optional extra (`arc[mcp]`); the plugin graceful-disables
  if it's absent.
- **Register MCP servers programmatically** via `arc.setup.writer.write_mcp_server_add`
  (comment-preserving, upsert, validates) — the same core `arc mcp add` uses. A
  CLI `--command` option must NOT use the default dest (`command` collides with
  the top-level subcommand dest); the mcp one is pinned to `mcp_command`.
- **MCP `tool_prefix: ""` means NO prefix; unset means fall back to server name.**
  `McpServerConfig.tool_prefix` is `str | None` (None = unset). This lets a
  server like `cos` expose its tools under native names (`container_run`) instead
  of `container_container_run`. The writer persists an explicit `""` (guards are
  `if tool_prefix is not None`, not `if tool_prefix`). Needed so out-of-tree
  sub-agents can reference MCP tools by a stable name in their allowlist.
- **Don't escalate to the user mid-task** when working autonomously. The
  user's standing instruction is "knock it out, I'll review later." Make
  defensible judgment calls and document them in the design doc.
- **Gemini tool schemas are sanitized** before the call (`_gemini_translation.
  sanitize_gemini_schema`): `anyOf`/`additionalProperties` (which MCP/FastMCP
  emit for optional/dict fields) are stripped/flattened — Gemini 400s on them.
  Applies to both `gemini` and `vertex_gemini`.
- **Policy hooks fail CLOSED** (`bus.py`): a throwing `before_tool_call` becomes a
  `ToolDenial` (deny by default), and plugins with `critical = True` (guard,
  safety_gate) are never auto-quarantined — disabling a gate would re-open the
  bypass. Other hooks still pass through on error. `_mitigation/10`.
- **Sub-agents inherit a hard-denylist guard** (`subagents/runner._child_policy_guard`),
  built from the parent's `guard.blocklist_patterns` — so a sub-agent's tools are
  policed (rm -rf, dd, **docker**, …). Deliberately NOT the escalation patterns or
  safety_gate (would prompt the parent gate from the child thread / block the
  sub-agent's `curl` + file writes). See `_mitigation/07`.
- **`guard.delegate_only_tools`** (glob → owner sub-agent tool) denies a tool in
  the MAIN session and routes it through the owner; `inside_subagent()`-gated so
  it no-ops in the child. Fails open if the owner isn't registered.
- **Ctrl+C during a sub-agent dispatch is two-stage** (`subagents/cancel.py` +
  the TUI SIGINT handler): 1st cancels the running sub-agent (trips its
  `cancel_flag`, observed at the child's next iteration boundary), 2nd pauses the
  turn. ESC/Ctrl+D can't interrupt a dispatch (input-mode keys). `_mitigation/08`.
- **`tui.subagent_activity`** (default on) streams a child's tool calls into the
  scrollback as nested `↳` lines instead of only a spinner.
- **Sub-agent cost** resolves via a curated static fallback in `tui/pricing.py`
  (`_STATIC_RATES`) when the LiteLLM fetch fails or the model is too new
  (gemini-3.5-flash). Keep rates in sync with provider price pages.

## User preferences (carried across sessions)

- **Senior engineer.** Terse responses. Skip the "as you can see" framing.
- **Pragmatic over pure.** Bias toward "knock it out and test" rather than
  long design conversations.
- **Reverse-engineering is the primary use case.** Tools for binary analysis,
  long shell outputs, persistence of state across sessions all matter.
- **`/loop` / `arc` is `arc` here.** When user says "the agent" they mean
  this thing. When they say "v1" or "v2" they mean these directories.

## Phase status (most recent first)

| Phase | Doc | What landed |
|---|---|---|
| 5.3 | `_design/0026-interactive-time-travel.md` | In-TUI time travel: `/rewind` (arrow-key turn walker, branch-on-submit), `/retry`, `/model` (session-scoped provider swap, effective-config snapshot), tabs (`/tab`, alt+1..9, `tui.tabs_max`). Lineage via `stamp_session_meta` + `session.branched`/`provider.swapped` events. 0027 (visual timeline) not built yet. |
| 5.2 | `_design/0025-mcp-client-integration.md` | `mcp` plugin: consume external MCP servers (stdio + streamable-HTTP), tools→registry, `arc mcp add/remove/list/status`, setup section. Impl notes below. |
| 5.1 | `_design/0023-setup-hub-and-themes.md` | `arc setup` sidebar+content hub, themes |
| 5.0 | `_design/0020-subagent-dispatch.md` (+0021 gcs, 0022 video) | sub-agent dispatch as a tool, GCS spillover |
| (design) | `_design/0024-container-orchestration-and-job-dispatch.md` | Job-dispatch engine backends over a Docker service — NOT built yet |
| 3.4 | `_design/0012-destructive-action-gate.md` | `safety_gate` plugin, 12 default patterns, per-session remember cache |
| 3.3 | (this CLAUDE.md + `_architecture/`) | Doc pass — 5 authoring/reference guides |
| 3.2 | `_design/0011-tui-polish.md` | Slash commands, tab complete, history, bottom toolbar with cost, thinking blocks |
| 3.1 | `_design/0010-anthropic-provider.md` | Anthropic provider, thinking-block translation |
| 3.0 | `_design/0009-context-manager-sliding-window.md` | `sliding_window_context` plugin |
| 2.3 | `_design/0008-foundation-logging.md` | log_writer plugin, `arc log` |
| 2.2 | `_design/0007-foundation-phase2.2-branch-and-rerun.md` | Branch + rerun (modes 4, 5) |
| 2.1.5 | `_design/0006-foundation-phase2.1.5-pause-and-resume.md` | Pause + resume (mode 1) |
| 2.1 | `_design/0005-foundation-phase2.1-bash-and-guards.md` | bash_exec + guard plugin |
| 2.0.5 | `_design/0004-foundation-phase2.0.5-replay.md` | Replay engine (modes 2, 3) |
| 1 | `_design/0003-foundation-phase1-implementation.md` | Minimal core, recorder, TUI |
| 0 | `_design/0001-foundation-phase0-design.md` | Hook catalog, plugin protocol, the contract |

## When NOT in this directory

The user often has another Claude session open in v1 next door. If something
they say doesn't match what you see (e.g. "platform-common", "the service
deploy", "the SQLModel migration"), they may be referring to that one. Ask
before inventing context.
