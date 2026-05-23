# arc — agent runtime

```
 █████╗ ██████╗  ██████╗
██╔══██╗██╔══██╗██╔════╝
███████║██████╔╝██║      v2
██╔══██║██╔══██╗██║
██║  ██║██║  ██║╚██████╗
╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝
```

A minimal, pluggable, fully-observable LLM agent runtime. Built with one
principle: **the runtime mediates, the model drives, plugins extend.**

Every event the agent emits is recorded canonically and fully reproducible.
The minimal core is small (~1,300 lines); every other capability is an
optional plugin that can be toggled in `config.yml`.

| | |
|---|---|
| **Source** | ~6,900 lines Python |
| **Tests** | 362 passing (unit + real-API integration) |
| **Providers** | Gemini (`google-genai`), Anthropic (`anthropic` SDK) |
| **TUI** | prompt_toolkit + Rich, inline mode, slash commands, bottom toolbar with live cost |

---

## Quick start

```bash
make dev                          # install package + dev deps
cp .env.example .env              # add GEMINI_API_KEY
arc bootstrap                     # create ~/.arc/ + default config
arc                               # interactive TUI
```

One-shot, non-interactive use:

```bash
arc run "list the files in /tmp and tell me what's there"
```

After any session, inspect what happened:

```bash
arc sessions                          # list all recorded sessions
arc log <session_id>                  # human-readable log
arc show <session_id>                 # event-level view
arc replay <session_id>               # verify byte-identical reproduction
arc resume <session_id> --prompt X    # continue the conversation
```

---

## Design principles

1. **Runtime as mediator, not director.** The runtime sees and can intercept
   every LLM call, tool call, and event. The model drives; the runtime mediates.
2. **Observability is king.** Every event is recorded canonically. Sessions
   are replayable byte-identical. You can pause, branch, and re-run.
3. **Pluggable everything.** The minimal core is `model + tools + ReAct loop +
   telemetry`. Every other capability is a plugin, toggleable in `config.yml`.
4. **No hardcoded user-tunables.** If a value is user-tunable, it lives in
   `config.yml`. If you can't grep for the key, the knob doesn't exist.
5. **The recording is the source of truth.** Replay, resume, branch, rerun,
   the human log, and meta files all rebuild from one file: `events.jsonl`.

---

## Architecture

Three layers, increasing in optionality:

### Layer 1 — minimal core (always present)

```
src/arc/runtime/
  loop.py        the ReAct loop
  events.py      RuntimeEvent + EventType catalog
  scope.py       session/turn/scope contextvars
  bus.py         HookRegistry + EventBus
  hooks.py       12 Protocol definitions
  ids.py         self-contained ULID generator
```

This is what's always running. It holds conversation state, calls the LLM
provider, dispatches tool calls, emits canonical events at every observable
moment, fires hooks at 12 named extension points, enforces caps + cycle
detection, and cooperatively yields to `pause_check` between iterations.

**With zero plugins registered, this still works.**

### Layer 2 — built-in plugins (enabled by default)

| Plugin | Hooks | What it does |
|--------|-------|--------------|
| `jsonl-recorder` | `on_session_start`, `on_event`, `on_session_end` | Persists every event to `events.jsonl` canonically. Source of truth for replay. |
| `guard` | `before_tool_call` | Allowlist tools bypass; blocklist patterns deny; escalation patterns prompt via UserGate. |
| `pause-resume` | `pause_check` | Watches signal file + in-process flag. Raises PauseRequested at next checkpoint. |
| `log-writer` | `on_session_start`, `on_event`, `on_session_end` | Writes human-readable `session.log` per session, v1-style format. |
| `sliding-window-context` | `pack_context` | Drops oldest user-turn fragments when message budget is exceeded. Keeps the system prompt and recent context intact. |

All five are plugins. All five are optional. Disabling any one is a single
config-line edit; nothing else breaks.

### Layer 3 — supporting code

```
src/arc/
  cli.py                arc entry point + every subcommand
  bootstrap.py          ARC_HOME resolution + config bootstrap
  config.py             frozen dataclasses + YAML loader
  defaults.py           the canonical default config
  user_gate.py          UserGate Protocol + NoOpGate + TUIGate
  providers/
    base.py             LLMProvider Protocol
    gemini.py           GeminiProvider (google-genai SDK)
    anthropic.py        AnthropicProvider (anthropic SDK, thinking-block support)
  tools/
    base.py             Tool Protocol + ToolRegistry
    ls.py               list directory contents
    bash_exec.py        execute shell commands
  tui/
    app.py              prompt_toolkit Application (inline mode)
    render.py           Rich rendering for chat + logo + banner
    pricing.py          LiteLLM-backed token cost lookup (cached weekly)
  replay/               replay engine (modes 2 + 3)
  resume/               message reconstruction for resume + branch
  rerun/                user-input extraction for mode 5
  plugins/
    jsonl_recorder/
    guard/
    pause_resume/
    log_writer/
    sliding_window_context/
```

---

## How a turn works

User types `"list /tmp"` and hits enter. Inside `run_turn()`:

```
1. on_turn_start hook → plugin can rewrite user input
2. User message appended; turn.started event emitted
3. Loop:
     - Check caps (max_iterations, max_tool_calls)
     - Check for cycles (3+ identical tool calls → force wrap-up)
     - pause_check → may raise PauseRequested
     - pack_context → plugin filters messages
     - Build LLMRequest, fire before_llm_call
     - Emit llm.call.started (canonical content for replay)
     - provider.chat()                      ← real network call
     - Emit llm.call.completed
     - after_llm_call hook
     - Append assistant message
     - For each tool_use in response:
         - before_tool_call → guard may deny
         - Emit tool.call.started, tool.execute(), emit tool.call.completed
         - after_tool_call hook
         - Append tool result
     - If no tool_use blocks: break
4. Emit turn.ended event
5. on_turn_end hook
6. Return TurnOutcome
```

Identity (`session_id`, `turn_id`, `scope`, `parent_event_id`) flows via
contextvars. Every emitted event auto-fills them. tool.call.* events are
parented to their llm.call.* so causation chains reconstruct cleanly.

---

## Tools (currently 2)

**`ls`** — list directory contents. Configurable max recursion depth + show
hidden. Returns sorted entries, one per line.

**`bash_exec`** — run shell commands via `subprocess.run(shell=True)`.
Captures stdout + stderr. Per-call timeout, output truncation, cwd override.
Returns `Error: exit code N\n...` on failure.

> **Note.** No sandbox isolation yet — `bash_exec` runs on the host. The
> `guard` plugin is the only safety layer. A sandbox plugin (wrapping
> `sandbox-exec` on macOS or `firejail` on Linux) is a planned next step.

---

## Replay-mode catalog (all 5 implemented)

| Mode | CLI | What it does |
|------|-----|--------------|
| 1 — Time-travel | `touch <session>/pause` or Ctrl+C in TUI, then `arc resume <id>` | Pause mid-run, resume later |
| 2 — Deterministic replay | `arc replay <id>` | Stubbed LLM + stubbed tools; asserts byte-identical event log |
| 3 — Test prompt change | `arc replay <id> --live-llm` | Live LLM, stubbed tools — see if prompt/model change breaks the scenario |
| 4 — Branch | `arc resume <id> --at-turn N --prompt "..."` | Fork after turn N, take a different path |
| 5 — Rerun | `arc rerun <id>` | Replay user inputs against fresh agent; regression test |

Sessions carry chain metadata (`replay_of`, `resumed_from`, `branched_at_turn`,
`rerun_of`) so you can follow any session back through its lineage.

---

## CLI surface

```
arc                                  start interactive TUI
arc bootstrap [--force]              create ~/.arc/ + write default config
arc run "<prompt>"                   one-shot turn, prints reply
arc sessions                         list recorded sessions
arc show <id>                        pretty-print events
arc log <id> [--tail N]              human-readable session.log
arc config show / arc config path    inspect resolved config
arc replay <id> [--live-llm]         modes 2 + 3
arc resume <id> [--at-turn N]        modes 1 + 4
arc rerun <id> [--stop-on-error]     mode 5
arc --home <path> <subcommand>       override ARC_HOME for one invocation
arc --version
```

---

## Storage layout

```
$ARC_HOME/                         (default: ~/.arc; override via ARC_HOME)
  config.yml
  sessions/
    index.jsonl                    one line per session (started/ended/provider/model)
    <session_id>/
      events.jsonl                 canonical, every event
      session.log                  human, v1-format
      meta.json                    session metadata + chain markers
      config.snapshot.yml          config at session start (replay uses this)
      pause                        signal file — touch to pause
```

Each session is self-contained. No shared databases, no rolling logs.

---

## Configuration

`$ARC_HOME/config.yml`. Bootstrap writes a fully-populated default. Every
user-tunable value is here; nothing is hardcoded. Sections:

| Section | What it controls |
|---------|------------------|
| `runtime` | workspace, caps (iteration, tool-call), system prompt, cycle detection, wrap-up messages |
| `provider` | name, model, api_key_env, retry policy, params (temperature, max_tokens, etc.) |
| `tools` | which tools are enabled + per-tool config (`ls.max_depth`, `bash_exec.timeout_seconds`, ...) |
| `plugins` | which plugins are enabled + per-plugin config + hook composition order |
| `tui` | theme, prompt prefix, inline mode, token-count display |
| `bootstrap` | one-time bootstrap behavior |

---

## What's intentionally NOT in v2 yet

The whole point of v2 was to NOT carry forward v1's orchestration mistakes.
The following are deliberate omissions, each a future capability plugin:

- **No planner** (the model plans inline)
- **No monitor** beyond cycle detection
- **No council/critic** (the model decides)
- **No skills** (no fixed step expansions)
- **No RAG** (no semantic retrieval)
- **No artifact store** (just files in the workspace)
- **No sub-agents** (single-agent)
- **No sandbox isolation** (host backend; `guard` and `safety_gate` are the only safety layers)
- **No async runtime** (sync; concurrency added deliberately when needed)
- **No workspace snapshotting** for branch/replay (filesystem is forward-only)

Each of these is a capability plugin waiting to be built when there's a real need.

---

## Phase status

| Phase | Status | What landed |
|-------|--------|-------------|
| 0 — Design | ✅ | Spec, hook catalog, plugin protocol |
| 1 — Minimal core | ✅ | ReAct loop, recorder, TUI, hello-world acceptance |
| 2.0.5 — Replay validation | ✅ | Modes 2 + 3 |
| 2.1 — Bash + guards | ✅ | `bash_exec`, guard plugin, escalation flow |
| 2.1.5 — Pause + resume | ✅ | Mode 1 (time-travel) |
| 2.2 — Branch + rerun | ✅ | Modes 4 + 5 |
| 2.3 — Logging polish | ✅ | log-writer plugin, `session.log`, `arc log` |
| 3.0 — Context manager | ✅ | `sliding_window_context` plugin (`pack_context` hook), per-fragment eviction |
| 3.1 — Anthropic provider | ✅ | `AnthropicProvider`, thinking-block translation + signature echo |
| 3.2 — TUI polish | ✅ | `/clear` `/sessions` slash commands, tab complete, history, bottom toolbar with cost |
| 3.3 — Doc pass | ✅ | `_architecture/` guides for plugins/providers/tools/config/CLI |
| 3.4 — Destructive-action gate | next | `safety_gate` plugin: user confirmation for `rm`, force pushes, etc. |
| 4.x — Capability plugins | future | sub-agents, sandbox isolation, planner, RAG |

Per-phase design docs live in [`_design/`](_design/) — start with
[`0001-foundation-phase0-design.md`](_design/0001-foundation-phase0-design.md)
for the contract everything else is built against, then walk the numbered
list for what each phase added.

---

## Project layout

```
v2/
  _design/                      design docs, one per phase
  _architecture/                authoring guides + reference docs
    plugin-authoring.md
    provider-authoring.md
    tool-authoring.md
    config-reference.md
    cli-reference.md
  _tests/                       integration scenarios + ad-hoc experiments
  src/arc/                      the package
  tests/
    unit/                       fast, no network
    integration/                real Gemini / Anthropic API; auto-skipped without key
  Makefile                      install / test / lint / format / clean / bootstrap / run
  pyproject.toml                package metadata, entry point `arc = "arc.cli:main"`
  .env.example                  template; copy to .env and add GEMINI_API_KEY / ANTHROPIC_API_KEY
```

Start with the [`_architecture/`](_architecture/) guides if you're extending
arc. Start with [`_design/`](_design/) if you want the history of *why* each
subsystem looks the way it does.

---

## Contributing patterns

**Adding a tool.** Make a new file in `src/arc/tools/` with a class
implementing the `Tool` Protocol (name, description, `input_schema`,
`execute`). Add a `from_config` classmethod that takes the dict from
`tools.config.<name>`. Register the builder in `arc/tools/__init__.py`'s
`_BUILDERS` dict. Add a `<name>:` section to `defaults.py`. Done.

**Adding a plugin.** Make a new directory under `src/arc/plugins/` with
a class implementing whatever hook methods you need (any subset of the
12 Protocols in `runtime/hooks.py`). Add a `_build_*` function in
`arc/plugins/__init__.py` and an entry in `_BUILDERS`. Add a `- name:`
entry under `plugins.enabled` in `defaults.py` with `hooks_order:`. Done.

The hook catalog from `_design/0001` is the contract — if your plugin
needs an extension point that doesn't exist, propose a new hook (and a
new design doc, like every other phase).

---

## License

MIT.
