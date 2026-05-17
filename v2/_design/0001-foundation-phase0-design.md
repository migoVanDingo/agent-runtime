# 0001 — Foundation (Phase 0): Plugin Protocol, Event Schema, Config

**Status:** draft
**Phase:** 0 (design only — no code)
**Author:** initial draft by Claude, for review
**Supersedes:** none (v2 greenfield)

---

## 1. Goals

Phase 0 produces design artifacts only. By the end of phase 0 we have:

1. **A hook catalog** — the named extension points the runtime exposes. This is v2's public contract; everything else is built against it.
2. **A plugin protocol** — how plugins declare themselves, register hooks, fail safely, get composed.
3. **An event schema** — the canonical record of everything that happens in a session. Replay, telemetry, persistence all read from this one source.
4. **A storage format** — JSONL, with concrete file layout.
5. **A config schema** — one `config.yml`, plugin-scoped key conventions.
6. **A "hello world" spec** — the test v2.0 must pass before phase 1 is considered done.

Phase 0 does NOT produce code. The deliverable is this doc and any follow-up docs it spawns.

## 2. Non-goals

Explicit deferrals so we don't argue about them later:

- **No sub-agents.** Single-agent only until phase 5+.
- **No orchestration plugins** (planner, monitor, council, validator) until we observe a failure that justifies one.
- **No skills.** Skills are a plugin pattern that may emerge later.
- **No RAG, no artifact store, no context manager** in the minimal core. Each is a candidate plugin.
- **No multi-provider abstraction.** One provider (Google Gemini) end-to-end before we build a multi-provider layer. The provider is configurable via `config.yml`, but only Gemini gets exercised end-to-end in phases 1–5.
- **No sandboxing** in phase 1; host backend only. Sandbox is a `before_tool_call` plugin in phase 3.
- **No filesystem snapshotting** for branch/time-travel. Filesystem is forward-only in v2.0–v2.2; document the limitation, revisit when it hurts.
- **No async runtime.** Synchronous core. Concurrency added deliberately when needed.

## 3. The minimal core

The core is what's always present, not pluggable, and small enough to read in one sitting (~200–400 lines target).

**Core responsibilities:**
- Hold conversation state (the message list)
- Hold the tool registry (read-only after init)
- Invoke the LLM provider with the current messages + tools
- Dispatch tool calls returned by the LLM
- Run the ReAct loop: think → maybe-tool-call → maybe-tool-result → repeat until done
- Emit events to the event bus at well-defined points
- Maintain identity/scope contextvars for every event
- Fire hooks at well-defined points
- Cooperative pause-check at well-defined points

**Core does NOT:**
- Plan ahead (no separate planner)
- Critique its own decisions (no separate critic)
- Validate its own output (no separate validator)
- Compress/reshape context (no separate context manager)
- Page tool outputs (no separate paging)
- Make routing decisions (model decides)

Anything in the second list is a plugin if it exists at all.

**Hello-world invariant:** with zero plugins registered, the core completes a one-tool-call task end-to-end, records every event, and the recording replays byte-identical. This is the gate for phase 1 completion.

**No-hardcoded-defaults principle:** every user-tunable value lives in `config.yml`. No magic numbers in code for: model name, timeouts, paths, token limits, retry counts, max iterations, escalation thresholds, anything else a user might reasonably want to change. The code reads from config; the config has sensible defaults. If you can't point at a config key for a given knob, the knob doesn't exist yet — go add it before writing the code that uses it.

## 4. Hook catalog

Twelve hooks. Each is optional. Multiple plugins can register per hook (composed in config-specified order). Each hook receives a typed value and returns either a transformed value or `None` (= pass through unchanged).

### 4.1 Lifecycle

| Hook | Fires | Receives | Returns |
|------|-------|----------|---------|
| `on_session_start(ctx)` | Once at session boot | `SessionContext` | `None` (observe only) |
| `on_session_end(ctx)` | Once at session exit | `SessionContext` with outcome | `None` |
| `on_turn_start(ctx, user_input)` | Each user turn begins | `TurnContext`, `UserInput` | `UserInput | None` |
| `on_turn_end(ctx, outcome)` | Turn finishes (success, error, cancelled) | `TurnContext`, `TurnOutcome` | `None` |

### 4.2 LLM boundary

| Hook | Fires | Receives | Returns |
|------|-------|----------|---------|
| `before_llm_call(ctx, req)` | Before each provider call | `TurnContext`, `LLMRequest` | `LLMRequest | None` |
| `after_llm_call(ctx, req, resp)` | After each provider call | `TurnContext`, `LLMRequest`, `LLMResponse` | `LLMResponse | None` |

`LLMRequest` contains: messages, system prompt, tools, model, params (temp, max_tokens, etc.), provider-specific extras. **Plugins can swap models, augment system prompts, filter tool lists, etc.** — but the runtime threads the result through, so multiple plugins compose.

### 4.3 Tool boundary

| Hook | Fires | Receives | Returns |
|------|-------|----------|---------|
| `before_tool_call(ctx, call)` | Before each tool execution | `TurnContext`, `ToolCall` | `ToolCall | ToolDenial | None` |
| `after_tool_call(ctx, call, result)` | After each tool execution | `TurnContext`, `ToolCall`, `ToolResult` | `ToolResult | None` |

`ToolDenial` is a special return that short-circuits — the runtime treats it as the tool's result and skips actual execution. Used by guards/escalation. The denial message goes to the model as if it were the tool's response.

### 4.4 Context & step

| Hook | Fires | Receives | Returns |
|------|-------|----------|---------|
| `pack_context(ctx, messages, query)` | When packing messages for next LLM call | `TurnContext`, `list[Message]`, `query` | `list[Message] | None` |
| `assess_step(ctx, step, result)` | After each "step" boundary | `TurnContext`, `Step`, `result` | `StepAssessment | None` |

`pack_context` is where AFM/truncate/sliding strategies live. The runtime passes the full message history; the plugin returns what should actually be sent. If no plugin registers, the runtime sends everything.

`assess_step` — only fires if there's a notion of "step" in scope. The minimal ReAct loop has no explicit step boundary. A planner plugin (when one exists) defines steps and triggers this hook. If no plugin defines steps, this hook never fires.

### 4.5 Observability & control

| Hook | Fires | Receives | Returns |
|------|-------|----------|---------|
| `on_event(ctx, event)` | Every telemetry event | `SessionContext`, `RuntimeEvent` | `None` |
| `pause_check(ctx)` | At cooperative yield points | `TurnContext` | `None` or raises `PauseRequested`/`Cancelled` |

`on_event` is how recorders, persisters, and external monitors observe. **The JSONL recorder is itself an `on_event` plugin** — the core just emits events, doesn't know they're being persisted.

`pause_check` is the contract for time-travel. Plugins read external signal (file flag, signal handler, IPC), raise `PauseRequested` to checkpoint, `Cancelled` to abort. If no plugin registers, checkpoints are no-ops.

### 4.6 Hook firing order in a turn

```
on_turn_start(user_input)
  ↓ (loop)
  pause_check
  pack_context(messages, query) → packed
  before_llm_call(req) → req
  [provider.chat(req)]
  after_llm_call(req, resp) → resp
  [for each tool_use in resp:]
    before_tool_call(call) → call | denial
    [if not denied:] [tool.execute(call)]
    after_tool_call(call, result) → result
  [if no tool calls: exit loop]
on_turn_end(outcome)
```

`on_event` and `pause_check` can fire at any time and are not shown explicitly.

## 5. Plugin protocol

### 5.1 Plugin shape

A plugin is a Python class implementing one or more hook methods. Method names match hook names exactly. Plugins are discovered by config, not by entry-point scanning.

```python
class MyPlugin:
    name = "my-plugin"
    version = "1.0.0"

    def before_llm_call(self, ctx, req):
        # Modify the request, or return None for no-change.
        ...

    def after_tool_call(self, ctx, call, result):
        ...
```

### 5.2 Plugin manifest

Every plugin declares a manifest. Manifest can live in the same file as the class (class attribute) or in a separate `manifest.yml` adjacent to the module.

```yaml
name: my-plugin
version: 1.0.0
description: One-line summary
hooks:
  - before_llm_call
  - after_tool_call
config_keys:
  - my_plugin.timeout_seconds
  - my_plugin.enabled  # convention: every plugin reads its own .enabled
python_dependencies:
  - requests >= 2.30
optional_dependencies: []
provides: []  # named capabilities others can depend on
requires: []  # named capabilities this plugin needs
```

The runtime validates the manifest at load:
- Python deps importable → ok, else **skip the plugin and emit a warning event**
- `requires` capabilities satisfied → ok, else **skip and warn**
- Hook methods actually exist on the class → if not, **fail loudly at load** (programmer error)

### 5.3 Registration & composition

Plugins are registered in `config.yml`:

```yaml
plugins:
  enabled:
    - name: guard
      hooks_order:
        before_tool_call: 10  # lower runs earlier
    - name: paging
      hooks_order:
        after_tool_call: 20
    - name: jsonl-recorder
      hooks_order:
        on_event: 100
```

Order is explicit. When two plugins register the same hook, the lower number runs first. Ties broken by registration order. No default order — config must specify.

### 5.4 Failure isolation

If a hook raises:
- The runtime catches the exception
- Emits a `plugin.hook.failed` event with full traceback
- Uses the **pre-hook value** as if the plugin returned `None`
- Continues to the next plugin in the chain

Exception: `pause_check` raising `PauseRequested` or `Cancelled` is the contract — those are not failures, they're signals.

A plugin that fails repeatedly (configurable threshold, default 3 in a turn) gets **disabled for the rest of the session** with a `plugin.disabled` event. Prevents one broken plugin from spamming failure events forever.

### 5.5 Hook versioning

Hook names include version: there's no `before_llm_call`, there's `before_llm_call_v1`. When we change a signature, we add `before_llm_call_v2` and keep `_v1` working as long as practical. Plugins declare which version they target in their manifest. Runtime calls the right version.

This prevents the "we evolved the hook and 5 plugins silently broke" problem.

### 5.6 The minimal core's relationship to hooks

The core's job is to fire hooks at the right places with the right data. The core does not know what plugins do. With zero plugins registered, the core works — `pack_context` returns the input unchanged, `before_llm_call` is a no-op, etc.

## 6. Event schema

Every observable moment in the runtime is an event. Events are immutable, ordered, identified.

### 6.1 Event envelope

```jsonc
{
  "event_id": "evt_01HXYZ...",            // ULID
  "session_id": "ses_01HXYZ...",
  "turn_id": "trn_01HXYZ...",             // null if pre-turn
  "scope": "main",                         // "main" | "subagent:<name>" | other
  "parent_event_id": "evt_01HXYZ...",     // for nesting (e.g. tool inside turn)
  "ts": "2026-05-17T09:30:15.123456Z",    // ISO8601 UTC, microsecond precision
  "ts_monotonic_ns": 1234567890123,        // monotonic clock for ordering
  "type": "llm.call.completed",            // dotted type
  "stage": "core",                         // emitter
  "severity": "info",                      // debug|info|warn|error
  "duration_ms": 1234,                     // for completed events
  "payload": { /* small searchable fields */ },
  "content": { /* large blob, may be paged out */ },
  "schema_version": 1
}
```

### 6.2 Event types (initial set)

- `session.started`, `session.ended`
- `turn.started`, `turn.ended`
- `llm.call.started`, `llm.call.completed`, `llm.call.failed`
- `tool.call.started`, `tool.call.completed`, `tool.call.failed`
- `tool.call.denied` (guard short-circuit)
- `hook.fired` (one per hook invocation; payload includes plugin name)
- `plugin.hook.failed`, `plugin.disabled`
- `pause.checkpoint.passed`, `pause.requested`, `pause.resumed`
- `event.emitted` (meta-event for things core wants to record but doesn't fit elsewhere)

### 6.3 LLM call event content (the critical one for replay)

`llm.call.started`:
```jsonc
{
  "payload": {
    "provider": "anthropic",
    "model": "claude-sonnet-4-7",
    "message_count": 12,
    "tool_count": 5,
    "estimated_input_tokens": 8420
  },
  "content": {
    "messages": [/* full canonical message list */],
    "system": "...",
    "tools": [/* canonical tool schemas */],
    "params": {"temperature": 0, "max_tokens": 4096, "...": "..."}
  }
}
```

`llm.call.completed`:
```jsonc
{
  "payload": {
    "provider": "anthropic",
    "model": "claude-sonnet-4-7",
    "stop_reason": "end_turn",
    "input_tokens": 8420,
    "output_tokens": 312,
    "duration_ms": 2840
  },
  "content": {
    "response_content": [/* full canonical response blocks */],
    "raw_provider_response": {/* the unwrapped HTTP body, for true byte-fidelity */}
  }
}
```

**Critical:** `content` must be **canonical-byte-faithful** to what was sent and received. No pretty-printing, no key reordering, no float reformatting. This is what makes deterministic replay possible. If we mangle bytes for human-readability, replay is forever broken.

For the human view, we generate a pretty rendering at view-time from the canonical content. The stored bytes are the truth.

### 6.4 Tool call event content

`tool.call.started`:
```jsonc
{
  "payload": {
    "tool_name": "ls",
    "tool_call_id": "toolcall_01HXYZ..."
  },
  "content": {
    "input": {/* exact bytes the model produced */}
  }
}
```

`tool.call.completed`:
```jsonc
{
  "payload": {
    "tool_name": "ls",
    "tool_call_id": "toolcall_01HXYZ...",
    "ok": true,
    "output_bytes": 412
  },
  "content": {
    "output": "/* exact tool output string */"
  }
}
```

### 6.5 Identity & scope

Every event carries:
- `session_id` — never changes within a session
- `turn_id` — set when a turn starts, null between turns
- `scope` — `"main"` always for v2.0–v2.2; sub-agents later get `"subagent:<name>"`
- `parent_event_id` — the event that caused this one (e.g., a tool.call.completed's parent is the llm.call.completed that requested it)

This gives us:
- Filter by session/turn for replay
- Filter by scope to separate main from sub-agent activity
- Trace causation via parent_event_id chains

The contextvar pattern from v1 was right and we keep the shape.

## 7. Storage format

### 7.1 Layout

Home directory is configurable (see §7.4 below). Default path: `$HOME/.arc-v2`.

```
<ARC_HOME>/
  config.yml                  # active config (read on every `arc` invocation)
  sessions/index.jsonl        # one line per session, cheap "list all" view
  sessions/<session_id>/
    events.jsonl              # one event per line, append-only
    meta.json                 # session metadata (started_at, ended_at, provider, model, etc.)
    config.snapshot.yml       # config at session start (for replay)
    workspace/                # optional workspace snapshot (deferred)
    branches/
      <branch_id>/            # if this session is a branch of another
        events.jsonl
        meta.json
        parent.json           # {parent_session_id, fork_at_event_id}
```

One file per session (no rotation in v2.0–v2.2; deferred). Append-only writes from the JSONL recorder plugin.

### 7.2 JSONL specifics

- One JSON object per line
- UTF-8, no BOM
- Lines never exceed (decision: 1 MB? 10 MB? — TBD; default to "no limit, but warn at 10MB")
- `content` field may grow large (full LLM messages); accept this
- No compression at write time (simpler; compress at archive time if needed)

### 7.3 Index file (cross-session)

Single `~/.arc-v2/sessions/index.jsonl` (one line per session) for cheap "list all sessions, find by tag, etc." Each line:

```jsonc
{"session_id": "ses_...", "started_at": "...", "ended_at": "...", "n_turns": 5, "tags": []}
```

Built/updated by the recorder plugin on `session.ended`. Not a queryable database — just a flat list for human/CLI consumption.

If we later want richer queries (find session with tool X, etc.), we add a SQLite index that's derived from the JSONL — JSONL stays the source of truth.

### 7.4 Configurable home directory

Three layers of override, last one wins:

| Layer | Mechanism | Default |
|-------|-----------|---------|
| 1. Environment — parent dir | `ARC_V2_HOME` | `$HOME` |
| 2. Environment — folder name | `ARC_V2_DIRNAME` | `.arc-v2` |
| 3. CLI flag — full override | `arc --home <path>` | (none) |

Resolution: `final_home = $ARC_V2_HOME / $ARC_V2_DIRNAME`, unless `--home <path>` is passed in which case `final_home = <path>` directly.

A `runtime.home` key in `config.yml` is NOT supported for this — `config.yml` lives *inside* the home dir, so the home dir can't depend on it. This is a chicken-and-egg avoidance.

`arc bootstrap` resolves the final home dir and creates the layout if missing. `arc` (no subcommand) auto-bootstraps if it sees an empty/missing home dir.

## 8. Config schema

### 8.1 Single config file

`<ARC_HOME>/config.yml`. One file, all settings. Below is the fully-populated default that `arc bootstrap` writes. Every value in the codebase that's user-tunable shows up here — adding a new tunable means adding a key here first, then reading it from code.

```yaml
# ── Runtime ─────────────────────────────────────────────────────────────────
runtime:
  workspace: "."                    # working directory the agent operates in
  max_iterations: 50                # ReAct loop cap per turn
  max_tool_calls_per_turn: 30       # safety cap; agent forced to wrap up after this
  show_thinking: true               # render <thinking> blocks in TUI
  log_level: "info"                 # debug | info | warn | error

# ── Provider ────────────────────────────────────────────────────────────────
provider:
  name: gemini                      # only "gemini" is exercised in v2.0–v2.2
  model: gemini-3.1-flash-live-preview
  api_key_env: GEMINI_API_KEY       # env var name to read the key from
  base_url: null                    # null = SDK/library default
  timeout_seconds: 60
  retry:
    max_attempts: 3
    backoff_base_seconds: 2
    backoff_max_seconds: 32
  params:
    temperature: 0
    max_tokens: 4096
    top_p: 1.0
    # provider-specific knobs go here, passed through verbatim

# ── Tools ───────────────────────────────────────────────────────────────────
tools:
  enabled: [ls]                     # explicit list; unknown names cause startup error
  config:
    ls:
      max_depth: 2
      show_hidden: false
    bash_exec:                      # added in v2.1
      timeout_seconds: 30
      max_output_chars: 50000
      working_directory: null       # null = inherit runtime.workspace

# ── Plugins ─────────────────────────────────────────────────────────────────
# Order specified per hook. Lower numbers run earlier in the chain.
plugins:
  enabled:
    - name: jsonl-recorder
      config: {}                    # uses ARC_HOME/sessions/ by default
      hooks_order:
        on_event: 100
    # Plugins added in later phases. Listed here with `enabled: false` so the
    # config shape is stable from day 1 — phase 1 just flips the flag.
    - name: guard
      enabled: false                # turned on in v2.1
      config:
        allowlist_tools: [ls, echo, cat, pwd, env]
        blocklist_patterns:
          - 'rm\s+-rf'
          - 'dd\s+if='
          - ':\(\)\s*\{'             # fork bomb
        escalation_required_patterns:
          - 'curl\s+'
          - 'wget\s+'
      hooks_order:
        before_tool_call: 10
    - name: pause-resume
      enabled: false                # turned on in v2.1.5
      config:
        signal_dir: null            # null = ARC_HOME/signals
      hooks_order:
        pause_check: 50

# ── TUI ─────────────────────────────────────────────────────────────────────
tui:
  enabled: true                     # false = headless CLI mode (also used by replay)
  theme: default
  inline_mode: true                 # true = scrollback works; false = alt-screen (don't)
  spinner_style: dots
  prompt_prefix: "❯ "
  show_token_counts: true
  show_event_count: false           # debug aid; off by default

# ── Bootstrap defaults (used by `arc bootstrap`) ────────────────────────────
bootstrap:
  create_workspace_dir: false       # whether to create runtime.workspace if missing
  write_example_session: false      # whether to seed an example for replay testing
```

### 8.2 Key conventions

- Top-level keys: `runtime`, `provider`, `tools`, `plugins`, `tui`, `bootstrap`
- Each tool's config nested under `tools.config.<tool_name>`
- Each plugin's config nested under `plugins.enabled[].config`
- Plugins read their own config via the runtime API; no scanning the config tree
- Every section name plus key path is referenced exactly once in code — grep finds it

### 8.3 Validation behavior

- Unknown top-level key → error with line number
- Unknown key under a known section → warning (lets us add experimental keys without breaking older configs)
- Required key missing → error listing all missing
- Tool listed in `tools.enabled` but no implementation registered → error at startup
- Plugin listed under `plugins.enabled` but module not importable AND `enabled: true` → error
- Plugin with `enabled: false` is skipped entirely, no validation of its config keys
- Aim: misconfiguration causes a clear error at startup, never a silent runtime failure

## 9. Operational layer: CLI, Makefile, bootstrap

### 9.1 The `arc` CLI

Single entry point: `arc`. Subcommands are explicit verbs; bare `arc` opens the interactive (TUI) session.

| Command | Effect |
|---------|--------|
| `arc` | Start an interactive session in the TUI. Auto-bootstraps home dir if missing. |
| `arc bootstrap` | Create the home dir layout if it doesn't exist, write a default `config.yml`, exit. Idempotent. |
| `arc bootstrap --force` | Overwrite existing `config.yml` with defaults. Sessions untouched. |
| `arc --home <path>` | Override `ARC_V2_HOME / ARC_V2_DIRNAME` resolution. Works with all subcommands. |
| `arc run "<prompt>"` | One-shot, non-interactive. Prints final response to stdout, full event log to ARC_HOME. For scripts and pipes. |
| `arc replay <session_id>` | Deterministic replay of a recorded session. Asserts byte-identical output. Exit code reflects pass/fail. |
| `arc sessions` | List sessions from `sessions/index.jsonl` with summary info. |
| `arc show <session_id>` | Render a recorded session for human reading (pretty-printed from canonical events). |
| `arc config show` | Print the resolved config (after env/CLI overrides) to stdout. |
| `arc config path` | Print the resolved config file path. |
| `arc --version` | Print version. |
| `arc --help` | Standard help. |

Phase 1 implements: `arc`, `arc bootstrap`, `arc run`, `arc --home`, `arc --version`, `arc --help`. Other subcommands land in their respective phases (replay → v2.0.5, sessions/show → v2.0 if cheap, config → v2.0).

### 9.2 Bootstrap behavior

`arc bootstrap` does:

1. Resolve `ARC_HOME` (env vars + flags)
2. If `ARC_HOME` doesn't exist, create it
3. If `ARC_HOME/config.yml` doesn't exist, write the default config (§8.1)
4. If `ARC_HOME/sessions/` doesn't exist, create it
5. If `ARC_HOME/sessions/index.jsonl` doesn't exist, create it empty
6. Print a one-line summary of what was created (or "nothing to do")

`arc bootstrap` is idempotent and safe to run anytime. Other subcommands implicitly call it if the home dir is missing.

### 9.3 Makefile targets

Conventional targets; all driven from the v2 project root.

```makefile
install:        # pip install -e . — dev install
install-prod:   # pip install . — non-editable install
dev:            # install + install dev/test deps
test:           # run pytest
test-fast:      # pytest -x -q (stop on first failure)
lint:           # ruff check
format:         # ruff format
typecheck:      # mypy (optional, if we adopt it)
bootstrap:      # arc bootstrap (creates ARC_HOME)
run:            # arc (interactive session)
clean:          # remove __pycache__, .pyc, etc.
clean-sessions: # rm -rf $ARC_HOME/sessions/* (with confirmation)
help:           # list targets
```

`make help` lists all targets with one-line descriptions. `make` with no target runs `help`.

### 9.4 Python project layout

```
v2/
  pyproject.toml          # package metadata, deps, entry points
  Makefile
  README.md               # quick start, link to _design
  .env.example            # template; user copies to .env (gitignored)
  _design/                # design docs (this file lives here)
  _architecture/          # high-level architecture overviews
  _tests/                 # integration / replay tests (not pytest unit tests)
  src/
    arc/                  # the package; importable as `arc`
      __init__.py
      __main__.py         # enables `python -m arc`
      cli.py              # `arc` entry point (argparse, subcommands)
      bootstrap.py        # `arc bootstrap` logic
      config.py           # config loading + validation
      runtime/
        __init__.py
        loop.py           # the ReAct loop
        events.py         # event types, identity, scope contextvars
        bus.py            # event bus + hook registry
        hooks.py          # Protocol definitions for all 12 hooks
      providers/
        __init__.py
        base.py
        gemini.py
      tools/
        __init__.py
        base.py
        ls.py
      plugins/
        __init__.py
        jsonl_recorder/
          __init__.py
          plugin.py
          manifest.yml
      tui/
        __init__.py
        app.py            # prompt_toolkit Application, inline mode
        scrollback.py     # the "print formatted to scrollback" helper
  tests/
    unit/                 # pytest
    integration/          # pytest, end-to-end
```

`pyproject.toml` registers `arc = "arc.cli:main"` as the CLI entry point.

## 10. Hello-world spec (the v2.0 acceptance test)

The test that proves the foundation is real.

### 10.1 Setup
- Config: `provider.name=gemini`, `provider.model=gemini-3.1-flash-live-preview`, `tools.enabled=[ls]`, `plugins.enabled=[jsonl-recorder]`
- API key: `GEMINI_API_KEY` set via `.env` or environment
- Workspace: a directory with three files (`a.txt`, `b.txt`, `c.txt`)
- User input: `"What files are in this directory?"`

### 10.2 Expected behavior
- Agent receives input
- LLM responds with one `ls` tool call (input: workspace path)
- Runtime dispatches `ls`, gets back `["a.txt", "b.txt", "c.txt"]`
- LLM responds with the file list in prose
- Turn ends

### 10.3 Acceptance criteria
1. **Functional:** the agent answers correctly
2. **Recording:** an `events.jsonl` exists with at least: `session.started`, `turn.started`, `llm.call.started`, `llm.call.completed`, `tool.call.started`, `tool.call.completed`, `llm.call.started`, `llm.call.completed`, `turn.ended`, `session.ended`
3. **Canonical content:** the recorded LLM messages exactly match what was sent on the wire (no pretty-printing drift)
4. **Replay (v2.0.5 gate):** running the recording through `arc replay <session_id>` produces byte-identical output and zero new LLM calls

If all four pass, phase 1 is done. If any fails, phase 1 isn't done — even if the agent "works."

## 11. Open questions (resolve as we go, don't block on these)

- **JSONL line size hard limit?** **Resolved:** no hard limit; warn at 10 MB per line.
- **Workspace snapshot for branch/time-travel?** **Resolved:** defer to post-v2.2; document forward-only filesystem.
- **Spinner: live region or scrollback?** **Resolved:** live region; doesn't pollute scrollback.
- **TUI rendering library?** **Resolved:** Rich for formatting, prompt_toolkit for live input + spinner + footer, inline mode (no alt-screen).
- **Error events vs raise + catch?** **Resolved:** both — errors raised, runtime catches, emits event, decides continue/abort by severity.
- **Provider SDK: official Google SDK or our own HTTP wrapper?** **Open** — official SDK is faster to ship; HTTP wrapper gives byte-fidelity. Decide at start of phase 1 once we see how much normalization the SDK does. If SDK is byte-faithful, use it; else wrap HTTP.
- **Session ID generator: ULID, UUIDv7, custom?** **Resolved:** ULID (sortable, compact, `python-ulid` is well-maintained).
- **Hook return = `None` for no-change: clean or risky?** **Open** — leaning toward adding a `PASS_THROUGH` sentinel for clarity. Decide when we write the first plugin.
- **`arc` command-name collision with v1?** **Resolved:** v2 takes the name. Only one version installed at a time.
- **v2/ folder location long-term?** **Open** — currently `agent-runtime/v2/`. May move to a sibling once v1 is archived.

## 12. What this document IS NOT

- Not the implementation. Phase 1 writes code against this contract.
- Not the final word. Anything here can change in review, but changes must be reflected here before phase 1 starts.
- Not the only design doc — phase 1 will spawn `0002-<feature>-<phase1>-<title>.md` for any non-obvious implementation decisions discovered along the way.

## 13. Review checklist (for you)

Read with these questions in mind:
- Is the hook catalog the right shape? Missing hooks? Extra hooks?
- Does the plugin protocol give you enough control without being too rigid?
- Is the event schema rich enough for replay? Anything you'd want to query that you can't?
- Is JSONL + the directory layout reasonable, or do you want SQLite from the start?
- Is the config schema convention clear? Would you organize it differently?
- Is the CLI subcommand set complete? Anything missing or redundant?
- Is the Python project layout sensible? Any folders you'd add/remove?
- Is the hello-world spec the right gate, or too easy/too hard?
- What's missing?
