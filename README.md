# arc

> A self-hostable LLM agent runtime with a Claude-Code-style terminal UI,
> decoupled service layer, sandboxed tool execution, structured event
> telemetry, and persistent cross-session memory.

```
       ___    ____  ______
      /   |  / __ \/ ____/
     / /| | / /_/ / /
    / ___ |/ _, _/ /___
   /_/  |_/_/ |_|\____/
           agent runtime
```

---

## Why arc

Most agent frameworks lock you into one frontend, one provider, or one tool model.
arc separates those concerns cleanly so the same agent core can drive a terminal
UI today, a FastAPI deployment tomorrow, and a Slack bot the day after — all
through a single `AgentService` protocol.

- **Frontend / backend split** — service-layer boundary; `ui/` never imports from `runtime/`
- **Provider-agnostic** — Anthropic, OpenAI, Gemini, Grok, DeepSeek, Ollama all behind one interface
- **Sandboxed execution** — Docker isolation for shell + reversing tools by default
- **Council-reviewed plans** — multi-agent adversarial review catches bad plans before execution
- **Cross-session memory** — semantic recall of prior conversations and artifacts (LanceDB + SQLite)
- **Pluggable** — toolsets, skills, workflows, providers are all swappable at config time

---

## Install

Requires Python 3.10+, macOS or Linux. The Makefile detects your OS and uses
the right package manager (Homebrew on macOS, apt/dnf/pacman on Linux).

```bash
git clone <repo-url> arc && cd arc
make install            # Python deps + core system deps (radare2, r2ghidra) + ~/.arc/ data layout
```

Optional extras (install separately when you need them):

```bash
make install-angr       # symbolic execution tools (heavy native build, often fails)
make install-all        # install + install-angr in one shot
```

System dependencies installed automatically:

- **radare2** — binary disassembly backend
- **r2ghidra** — Ghidra-quality decompilation plugin for radare2

System dependencies you install manually if you want them:

- **Ghidra** — download from [github.com/NationalSecurityAgency/ghidra](https://github.com/NationalSecurityAgency/ghidra/releases), then set `GHIDRA_HOME=/path/to/ghidra` in `.env`

Then add your API keys to `.env`:

```bash
cp .env.example .env
# edit: ANTHROPIC_API_KEY=…, OPENAI_API_KEY=…, etc.
```

Verify everything is good:

```bash
make check
```

---

## Quick start

```bash
arc                     # → Textual-style TUI (default)
arc --cli               # → legacy text CLI
arc -t                  # → same as --cli
arc --print "what is 2+2"   # → headless: one turn, print, exit
arc --resume            # → open the session picker
arc wipe --help         # → cleanup runtime data
arc bootstrap           # → re-create ~/.arc/ data layout
arc plugin list         # → list installed plugins (tools, skills, toolsets)
arc plugin info <name>  # → details for one plugin
arc plugin install <pkg-or-path>   # → pip-install a package, or copy a local file
arc plugin remove <name>           # → remove a filesystem plugin
arc plugin doctor       # → diagnose plugin discovery (entry points + ~/.arc/plugins/)
arc subagent list       # → list registered sub-agent specs + effective config
arc subagent info <name>           # → full spec including system prompt + JSON schema
```

### Inside the TUI

| Key | Action |
|---|---|
| **Enter** | Submit message |
| **Shift+Enter** / **Ctrl+N** / **Esc Enter** | Newline (multi-line input) |
| **↑ / ↓** | Move cursor between lines (multi-line text or wrapped visual lines) |
| **Page Up / Page Down** | Scroll the conversation |
| **ESC** | Pause / resume a running turn |
| **Ctrl+D** | Graceful exit |
| **Ctrl+C** | Graceful exit; press again within 1s for hard exit (use when JVM/Ghidra is stuck) |

**Mouse:** native terminal mouse handling is preserved — click-drag selects text
(useful for copying session IDs, code snippets, error messages). Mouse scroll
wheel uses your terminal's scrollback buffer; to scroll the conversation
itself, use **PageUp/PageDown** or **↑/↓** with an empty input line.

### Slash commands

| Command | Effect |
|---|---|
| `/help` | List all commands |
| `/pause`, `/resume` | Pause or resume a running turn |
| `/resume` (when idle) | Open the session picker |
| `/sessions` | Open the session picker explicitly |
| `/cancel` | Cancel the current turn |
| `/clear` | Clear the conversation log |
| `/settings` | Show user settings |
| `/exit`, `/quit` | End the session |

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Frontend  (interchangeable)                                             │
│                                                                          │
│   ┌──────────────┐    ┌─────────────┐    ┌──────────────────────────┐    │
│   │  arc-tui     │    │   arc (CLI) │    │   FastAPI server         │    │
│   │  prompt_     │    │   legacy    │    │   (future, src/api/)     │    │
│   │  toolkit     │    │   text      │    │                          │    │
│   └──────────────┘    └─────────────┘    └──────────────────────────┘    │
└──────────────────────────────────┬───────────────────────────────────────┘
                                   │
                                   │  AgentService Protocol
                                   │  send / events / pause / resume / cancel
                                   ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  Service Layer  (src/service/)                                           │
│                                                                          │
│   InProcessAgentService                  Event translation                │
│   ─────────────────────                  ──────────────────                │
│   • wraps sync agent.call() in a         • RuntimeEvent → AgentEvent     │
│     thread executor                      • BoundedDropQueue              │
│   • bridges on_token → TokenChunk        • TUIUserGate (escalation)      │
│   • cooperative pause/cancel             • TUIInputGate (clarification)  │
└──────────────────────────────────┬───────────────────────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  Agent Runtime  (src/agent.py + src/runtime/)                            │
│                                                                          │
│   ┌────────────────────────────────────────────────────────────────┐    │
│   │  Pipeline                                                      │    │
│   │  ────────                                                      │    │
│   │  RoutingStage      → classify intent + optional inline answer  │    │
│   │  PlanningStage     → LLM planner generates multi-step plan     │    │
│   │  CouncilStage      → adversarial review of the plan            │    │
│   │  ExecutionStage    → step-by-step tool execution (ReAct loop)  │    │
│   │  ContinuationStage → decide if more work is needed             │    │
│   │  SynthesizerStage  → assemble final response                   │    │
│   │  DirectExecutionStage → fallback free-form loop                 │    │
│   └────────────────────────────────────────────────────────────────┘    │
│                                                                          │
│   Support: ToolLoop · Monitor · Guard · Critic · ContextManager ·         │
│            TokenTracker · ActionGuard · ExecutionMonitor                  │
└──────────────────────────────────┬───────────────────────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  Tools  (src/tools/)                                                     │
│                                                                          │
│  file_io · shell · data · web · document · artifacts · search · git ·     │
│  briefbot · crypto · reversing · symbolic · container · skill_*           │
│                                                                          │
│  Sandboxed: shell + reversing tools run in Docker by default (src/runtime/sandbox/) │
└──────────────────────────────────┬───────────────────────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  Storage  (~/.arc/  — overridable via ARC_HOME)                          │
│                                                                          │
│   sessions/<id>/    Per-session logs, council metrics, event stream      │
│   rag/              LanceDB vector store (global + per-session)          │
│   store/            Artifact registry (cross-session memory, decay)       │
│   ghidra/projects/  Cached Ghidra analysis                                │
│   analysis/         Paged tool outputs (heavy decompile results, etc.)    │
│   agent.db          SQLModel session/plan/artifact DB (Postgres-ready)    │
└──────────────────────────────────────────────────────────────────────────┘
```

### Data flow — one turn

```
User submits text
    │
    ▼
ArcApp.input_buffer  ── Enter ──→  _handle_input()
    │
    │  service.send(message)
    ▼
InProcessAgentService
    ├─→ emits  TurnStarted
    └─→ schedules agent.call() on worker thread
    │
    ▼
Agent.call(message, on_token=…, checkpoint_fn=…)
    │  builds PipelineContext, emits stage events
    ▼
Pipeline.run(context)
    │   ┌── checkpoint() before each stage ──┐
    │   ▼                                     │
    │  Routing      → classify intent         │
    │  Planning     → generate Plan           │
    │  Council      → adversarial review      │
    │  Execution    → ToolLoop (ReAct)        │
    │       ├─ checkpoint() per iteration     │
    │       ├─ tool.execute() (sandboxed)     │
    │       └─ on_token() ───────────────────────────→  TokenChunk events
    │  Continuation → more work needed?       │
    │  Synthesizer  → assemble response       │
    │                                         │
    └── may raise TurnCancelledError ─────────┘
    │
    ▼
InProcessAgentService
    ├─→ emits  MessageComplete
    └─→ emits  TurnCompleted (elapsed_ms, tokens_in, tokens_out)
    │
    ▼
TUI event consumer
    ├─→ conv.append_token()    on every TokenChunk
    ├─→ spinner.update(stage)  on stage.started
    ├─→ conv.add_timer()       on turn.completed
    └─→ footer updates with cumulative tokens
```

---

## Module map

### Top level

| Path | Role |
|---|---|
| `src/agent.py` | The `Agent` class — assembles the pipeline, owns provider, registry, gates |
| `src/main.py` | Legacy CLI entry point + `arc wipe` / `arc bootstrap` subcommands + dispatcher |
| `src/app_config.py` | Loads `config.yml` and `.env` once on startup |
| `src/config.py` | Pydantic config schema (runtime tuning, sandbox policy, RAG, etc.) |
| `src/settings.py` | `.env`-driven settings (API keys, DB URLs, `ARC_HOME`) |
| `src/session_paths.py` | All filesystem paths under `ARC_HOME` |
| `src/messenger.py` | Conversation history container |
| `src/logger.py` | Logging setup (file + console + ANSI stripping) |
| `src/embeddings.py` | Embedding model wrapper for semantic search |

### Runtime — the agent's brain

| Path | Role |
|---|---|
| `src/runtime/pipeline.py` | Ordered stage runner with `OK / DONE / RETRY / ASK_USER / ABORT` |
| `src/runtime/pipeline_context.py` | Shared mutable state passed through every stage |
| `src/runtime/stages/` | Individual pipeline stages (routing, planning, execution, council, …) |
| `src/runtime/tool_loop.py` | Shared ReAct loop used by `ExecutionStage` and `DirectExecutionStage` |
| `src/runtime/tool_executor.py` | Guard + escalation + execute helper for a single tool call |
| `src/runtime/guard.py` | `ActionGuard` — pre-flight safety policy for tool calls |
| `src/runtime/escalation.py` | `UserGate` — interactive y/n approval (CLI gate or TUI gate) |
| `src/runtime/monitor.py` | `ExecutionMonitor` — post-step analysis (retry / replan / abort) |
| `src/runtime/critic.py` | `PlanCritic` — LLM-driven plan review for council |
| `src/runtime/context_manager.py` | Non-destructive context packing with fidelity levels |
| `src/runtime/events/` | Structured event bus; subscribers + JSONL sinks |
| `src/runtime/artifact_store/` | SQLite-backed artifact registry — cross-session memory |
| `src/runtime/sandbox/` | Docker / host shell execution |
| `src/runtime/token_tracker.py` | Per-session input/output token counts (per stage and total) |
| `src/runtime/persistence.py` | Sync façade over async DAL (sessions / plans / steps) |

### Service — the boundary between agent and frontend

| Path | Role |
|---|---|
| `src/service/interface.py` | `AgentService` and `TurnHandle` Protocols |
| `src/service/events.py` | Typed `AgentEvent` dataclasses (Session / Turn / Stage / Content / Tool) |
| `src/service/inprocess.py` | `InProcessAgentService` — wraps `agent.call` in a thread executor |
| `src/service/builder.py` | Factory that wires Agent + logging + RAG + gates |
| `src/service/queue.py` | `BoundedDropQueue` — drops oldest `TokenChunk` on overflow |
| `src/service/translator.py` | `RuntimeEvent → AgentEvent` mapping |
| `src/service/errors.py` | `TurnCancelledError`, `TurnFailedError` |

### UI — `prompt_toolkit` Application

| Path | Role |
|---|---|
| `src/ui/app.py` | `ArcApp` entry point, layout, key bindings |
| `src/ui/conversation.py` | `ConversationModel` — formatted text with scroll + auto-follow |
| `src/ui/input_model.py` | Dynamic prompt prefix, footer text, message queue, session ID |
| `src/ui/spinner_model.py` | Inline animated spinner with live elapsed counter |
| `src/ui/settings_store.py` | YAML-backed user settings (`~/.arc/settings.yml`) |
| `src/ui/spinner.py` | Legacy CLI spinner (used by `arc --cli`, not by the TUI) |

### Tools, providers, planning, RAG, DB

| Path | Role |
|---|---|
| `src/tools/` | All tool implementations + registry + toolsets |
| `src/tools/implementations/file_io/` | `read_file`, `write_file`, `read_file_lines` (with virtual path resolution) |
| `src/tools/implementations/shell/` | Bash / search / find (sandboxed) |
| `src/tools/implementations/reversing/` | radare2, Ghidra, LLDB, objdump bindings |
| `src/tools/implementations/symbolic/` | angr-based symbolic execution (optional) |
| `src/tools/implementations/web/` | URL reading, HTML extraction, web search |
| `src/tools/implementations/document/` | PDF / DOCX / EPUB readers |
| `src/tools/implementations/artifacts/` | Set / get / recall / list artifacts |
| `src/tools/implementations/data/` | JSON query, diff, template render |
| `src/providers/` | LLM provider implementations behind one interface |
| `src/planning/` | `Planner` (LLM-driven) and `Plan` schema |
| `src/rag/` | LanceDB vector store + embedder + chunker |
| `src/skills/` | Pre-built deterministic workflows (skills) |
| `src/routing/` | `StaticRouter` — pick tools by message similarity |
| `src/db/` | Async SQLModel ORM (agent.db) for sessions / plans / steps |

---

## Storage layout

By default everything lives under `~/.arc/`. Override with `ARC_HOME=/path` in `.env`.

```
~/.arc/
├── sessions/<session_id>/
│   ├── logs/session.log          ← human-readable structured log
│   ├── logs/stderr.log           ← captured subprocess stderr (tokenizers, JVM, etc.)
│   ├── metrics/council.jsonl     ← per-turn council decisions for analysis
│   └── events/runtime.jsonl      ← structured event stream
│
├── rag/
│   ├── global/                   ← Tier-1 LanceDB warehouse (cross-session)
│   └── sessions/<session_id>/    ← Tier-2 chunk store (per-session)
│
├── store/
│   ├── artifacts.db              ← SQLite artifact registry (decay, recall, workflow discovery)
│   └── data/                     ← payload blobs for artifacts > inline threshold
│
├── ghidra/projects/              ← cached Ghidra .gpr / .rep files
├── analysis/<binary>/            ← paged tool outputs (heavy decompile etc.)
├── agent.db                      ← SQLModel database (sessions, plans, steps)
├── history                       ← prompt_toolkit input history
└── settings.yml                  ← user preferences (~/.arc/settings.yml)
```

The agent uses logical paths (`_analysis/<binary>/<file>`) in tool calls — those
are transparently rewritten by `runtime/path_resolver.py` to the real
`~/.arc/analysis/...` location at the write/read boundary.

---

## Configuration

### `.env` — secrets and machine-local paths

```bash
# LLM API keys (any subset)
ANTHROPIC_API_KEY=sk-ant-…
OPENAI_API_KEY=sk-…
GEMINI_API_KEY=…
GROK_API_KEY=…
DEEPSEEK_API_KEY=…
BRAVE_API_KEY=…                  # for web search

# Optional: override data location
ARC_HOME=/custom/data/path

# Optional: Ghidra (for ghidra_analyze / ghidra_decompile tools)
GHIDRA_HOME=/Applications/ghidra_11.0_PUBLIC

# Optional: switch the agent DB from SQLite to Postgres
AGENT_DB_URL=postgresql+asyncpg://user:pass@host/dbname

# Optional: enable per-session persistence to agent.db
ENABLE_SESSION_PERSISTENCE=true

# Optional: wall-clock cap (seconds) for individual Ghidra tool calls.
# Defaults to 600 (10 min). First-time analysis on a binary triggers the
# full Ghidra auto-analyzer pipeline, which can be slow on complex targets.
# Subsequent calls reuse the on-disk project cache at ~/.arc/ghidra/projects/.
ARC_GHIDRA_TIMEOUT=600
```

### `config.yml` — runtime tuning

Provider selection, model names, context budgets, sandbox policy, council
ensemble size, RAG embedding model, tool policy — all in one place. See the
file at the repo root for the full schema.

---

## Cleanup commands

```bash
arc wipe --all           # delete everything under ~/.arc/
arc wipe --sessions      # delete ~/.arc/sessions/
arc wipe --rag           # delete ~/.arc/rag/
arc wipe --analysis      # delete ~/.arc/analysis/
arc wipe --store         # delete ~/.arc/store/
arc wipe --legacy        # delete legacy project-dir data (pre-centralization)
arc wipe --yes           # skip confirmation
```

```bash
arc bootstrap            # (re)create the ~/.arc/ layout
arc bootstrap --migrate  # move legacy project-dir data into ~/.arc/
```

The `make migrate` Make target runs `arc bootstrap --migrate` for convenience.

---

## Make targets

```bash
make install              # Python deps + radare2 + r2ghidra + bootstrap
make install-all          # everything including angr (heavy native build)
make install-python       # just .venv + arc package
make install-system       # just radare2 + r2ghidra
make install-radare2      # radare2 only (brew / apt / dnf / pacman auto-detected)
make install-r2ghidra     # r2ghidra plugin (requires radare2)
make install-angr         # angr only (opt-in; may fail to build)
make bootstrap            # create ~/.arc/ layout
make migrate              # move legacy project-dir data → ~/.arc/
make check                # report status of all dependencies
make test                 # run pytest
make lint                 # compileall sanity check
make os-info              # show detected OS + package manager
```

---

## Development

### Running tests

```bash
make test
# or, more targeted:
.venv/bin/python3 -m pytest tests/integration/test_service.py -q
.venv/bin/python3 -m pytest tests/unit/ -q
```

### Adding a tool

1. Create `src/tools/implementations/<category>/<tool_name>.py`
2. Subclass `BaseTool` (see `src/tools/base.py`)
3. Register in the appropriate toolset under `src/tools/toolsets.py`
4. Update `config.yml` runtime.tool_policy if relevant

### Adding a provider

1. Implement `src/providers/<provider>.py` against `BaseProvider`
2. Register in `src/providers/factory.py`
3. Reference by name in `config.yml` under `llm.provider`

### Adding a pipeline stage

1. Subclass `Stage` (see `src/runtime/stage_base.py`)
2. Return a `StageResult` with `OK / DONE / RETRY / ASK_USER / ABORT`
3. Wire it into `_build_pipeline` in `src/agent.py`

### Architecture invariants

- **`ui/` never imports from `runtime/`, `agent.py`, or `tools/`** — only from
  `service/`. The boundary is enforced by convention; verify with:
  ```bash
  python3 -c "
  import ast, pathlib
  v = []
  for f in pathlib.Path('src/ui').rglob('*.py'):
      for node in ast.walk(ast.parse(f.read_text())):
          if isinstance(node, ast.ImportFrom) and node.module and \
             node.module.startswith(('runtime', 'agent', 'tools')):
              v.append(f'{f}:{node.lineno}')
  print('VIOLATIONS:', v) if v else print('OK')
  "
  ```

- **The runtime is universe / god** — `runtime/` decides retries, replans,
  escalations, and pause/cancel. Stages are passive participants that
  consult metadata and return `StageResult`s; they never drive control flow.

- **All runtime data goes to `ARC_HOME`** — never write to the project directory.
  Use `session_paths.arc_home()` and friends, never compute paths from
  `__file__`.

---

## Context strategies

The agent's context manager is **swappable at config-time** so research into
packing strategies doesn't require a code edit. Pick a strategy in
`config.yml` under `runtime.context.strategy`:

| Strategy | Behavior | Best for |
|---|---|---|
| `afm` (default) | AFM-inspired non-destructive packing — semantic similarity + recency + importance, with FULL/COMPRESSED/PLACEHOLDER fidelity tiers | General use; rich session preservation |
| `truncate` | Drop oldest messages until under budget; preserve first user message and tool pairs | Baseline; latency-sensitive |
| `sliding` | Keep last N messages verbatim; older messages collapse into a single LLM-generated summary | Long sessions where early context is summarisable |
| `rag` | Pack only past messages whose embeddings score above threshold for the current query | Long sessions where relevance is search-driven |

Each strategy reads its own params block under `runtime.context.params.<strategy>`:

```yaml
runtime:
  context:
    strategy: rag
    params:
      rag:
        top_k: 12
        score_threshold: 0.45
        keep_last_n: 8
```

Every `pack()` call emits `context.pack.started` / `context.pack.completed`
events with a `strategy` field, so the 0087 telemetry pipeline can compare
strategies in pandas:

```python
df = pd.read_json("~/.arc/sessions/<id>/events/runtime.jsonl", lines=True)
df[df.event_type == "context.pack.completed"].groupby("strategy")[
    ["duration_ms", "input_token_estimate", "output_token_estimate"]
].mean()
```

Custom strategies plug in via `runtime.context.factory.register_strategy(name, cls)`
— the future `arc.context_strategies` plugin entry-point group will wire
this up automatically.

The legacy `runtime.context_manager.*` config block continues to work via a
loader compat shim — it's translated into `runtime.context.params.afm` at
load time.

---

## Plugins

Third parties can extend arc with new tools, skills, and toolsets **without
forking the codebase**. There are two installation paths:

### 1. Packaged plugins (PyPI)

Plugin authors register their classes via Python entry points in `pyproject.toml`:

```toml
[project.entry-points."arc.tools"]
my_tool = "my_pkg:MyTool"

[project.entry-points."arc.skills"]
my_skill = "my_pkg:MySkill"

[project.entry-points."arc.toolsets"]
my_toolset = "my_pkg:MY_TOOLSET"
```

Users install with `pip install <package>` and restart arc.

### 2. Filesystem plugins (~/.arc/plugins/)

For local experiments, drop a `.py` file into `~/.arc/plugins/tools/` or
`~/.arc/plugins/skills/`:

```python
# ~/.arc/plugins/tools/word_count.py
from tools.base import BaseTool, InputSchema, ToolProperty

ARC_PLUGIN = {
    "name": "word-count",
    "version": "0.0.1",
    "extends_toolset": "data",        # optional — join an existing toolset
    "requires": {"python": ["regex>=2024.0"]},   # optional pre-flight checks
    "permissions": {"network": False, "filesystem_write": False},
}

class WordCountTool(BaseTool):
    name = "word_count"
    description = "Count words in a string"
    @property
    def input_schema(self):
        return InputSchema(
            properties={"text": ToolProperty(type="string", description="...")},
            required=["text"],
        )
    def execute(self, tool_input):
        return str(len(tool_input["text"].split()))
```

Restart arc, then check `arc plugin list` — the new tool is registered.

### Permission policy

A plugin's `permissions` block is consulted by the `ActionGuard` at invocation
time. `network: true` or `filesystem_write: true` causes the first call per
session to escalate for user approval, just like built-in tools that touch
sensitive surfaces.

### Conflict resolution

Built-in tool / skill names always win. If a plugin declares a tool named
`read_file`, the loader logs a warning and drops the plugin tool — the
built-in `read_file` remains active.

### Plugin telemetry

Every plugin discovery emits `plugin.loaded`, `plugin.disabled`, or
`plugin.dep_missing` events into `runtime.jsonl`, so you can audit which
plugins ran in a session and why some were skipped.

---

## Sub-agents

Specialised child agents that own their own context window, toolset, and
(optionally) provider/model. The main agent dispatches them as tools when
it needs to delegate context-heavy work — binary analysis, code
reconstruction, document summarisation — without bloating its own
working context.

### Why sub-agents exist

A long session where the agent reads a 12 KB Ghidra decompile, then a
12 KB find-constants report, then iterates on the analysis, can quickly
hit 100k+ tokens of context. Every subsequent LLM call sends that full
history. With sub-agents, the heavy reading + reasoning happens inside
a scoped child whose response back to the parent is a few hundred tokens
of structured JSON. The parent's context stays lean across turns.

This is the same idea behind Claude Code's `Task` tool: don't put
everything in the main loop's window; delegate to scoped sub-calls
that return small things.

### What ships in the box

- **`ghidra_analyst`** — reverse-engineering specialist. Has the
  Ghidra toolset + `bash_exec` for dynamic verification. Returns
  structured JSON: algorithm, mode, IV, key derivation, round function,
  constants, summary. Used by the `deep-disassembly` skill.

Run `arc subagent list` to see what's registered.

### How sub-agents are dispatched

From a tool / skill / plan step:

```yaml
# In a skill's expand():
Step(
    step=2,
    description="Delegate analysis of proc to ghidra_analyst.",
    action_type=ActionType.SUBAGENT,
    tool="subagent_ghidra_analyst",
)
```

The agent's tool call surface gets a `subagent_<name>` tool for every
registered spec. The tool takes a `task` string and returns the child's
response (text, or JSON serialised if the spec uses `response_format=json`).

### Provider specialisation per role

You can pin a sub-agent to a specific provider/model via `config.yml`:

```yaml
subagents:
  ghidra_analyst:
    provider: anthropic
    model: claude-opus-4-7
    timeout_seconds: 1200
    max_iterations: 30
```

Sub-agent uses Opus for deep analysis while the main agent stays on a
cheaper/faster model. Cost shows up separately in telemetry — the
`subagent.completed` event carries `tokens_in`, `tokens_out`, `cost_usd`
per child so you can split main-agent cost from delegated cost in pandas.

### Sub-agent isolation guarantees

- **No recursion.** A sub-agent cannot spawn its own sub-agent. Two-layer
  enforcement: the child's tool registry filters out all `SubAgentTool`
  instances, and a contextvar tripwire raises `SubAgentRecursionError`
  if any code path tries.
- **No shared conversation history.** The child starts with an empty
  messenger. Same `session_id` (so it can read the same RAG / artifact
  store) but its own `turn_id` for telemetry.
- **Parent owns lifecycle.** Synchronous dispatch — the parent blocks
  until the child returns. Timeouts and pause/cancel propagate from
  parent → child. The runtime-as-god tenet stays intact: child is a
  passive executor that returns data, never drives parent's flow.
- **Escalations route through parent's user gate.** If a child's tool
  hits `ESCALATE`, the user sees the prompt prefixed with
  `[subagent:<name>]` so they know where it came from.

### Context discipline (the budget side of 0090)

Sub-agent dispatch is one half of the 0090 work; the other half is
context discipline. Three improvements:

1. **Scope-aware AFM budget.** Runtime-classifier LLM calls
   (routing, skill_hint, monitor, importance scorer) now pack to a
   smaller budget (`runtime_message_budget_tokens: 12000`, vs the main
   `message_budget_tokens: 65536`) so they don't blow per-minute rate
   limits on the runtime provider as conversation history grows.
2. **System-prompt-aware packing.** AFM's `pack(messages, query,
   system_prompt_size=…)` now subtracts the system-prompt size from its
   budget so total LLM call stays under one cap.
3. **Analysis manifest cap.** `build_analysis_manifest()` enforces a
   4000-char hard cap so the manifest can't silently grow the system
   prompt across long sessions.

### Scope tagging (in logs and telemetry)

Every log line gets a scope prefix when not in main scope:

```
2026-05-17 12:00:00,000 [INFO] runtime.stages.routing: [runtime] mode=plan ...
2026-05-17 12:00:01,000 [INFO] runtime.stages.execution: step 3/12 ...
2026-05-17 12:00:02,000 [INFO] runtime.tool_loop: [subagent:ghidra_analyst] → ghidra_decompile ...
```

Every `runtime.jsonl` event gets an `agent_scope` top-level field
(`"main"`, `"runtime"`, `"subagent:<name>"`) so pandas can group by
scope directly:

```python
df = pd.read_json("~/.arc/sessions/<id>/events/runtime.jsonl", lines=True)
df.groupby("agent_scope")[["duration_ms", "cost_usd"]].sum()
```

### Building a new sub-agent

1. Write a `SubAgentSpec` in `src/tools/implementations/subagents/<name>.py`:
   ```python
   MY_SPEC = SubAgentSpec(
       name="code_writer",
       description="…",
       toolset_names=("file_io", "shell"),
       system_prompt=_MY_SYSTEM_PROMPT,
       response_format="json",
       response_schema={…},
       timeout_seconds=600.0,
   )
   register_spec(MY_SPEC)
   ```
2. Register a `SubAgentTool(MY_SPEC)` into the `subagent` toolset (see
   `tools/toolsets.py:_build_subagent_toolset()`).
3. Now any skill or planner step can dispatch via `tool="subagent_code_writer"`.
4. (Optional) Pin provider/model in `config.yml` under `subagents:`.

---

## Observability

Every session emits sidecar streams under `~/.arc/sessions/<id>/`:

- `logs/session.log` — human-readable timestamps + INFO/WARN/ERROR
- `metrics/council.jsonl` — one JSON per council run (model votes, final decision)
- `events/runtime.jsonl` — every structured event (stage transitions, LLM calls, tool calls, plan revisions, council votes, RAG hits, etc.)
- `events/blobs/<event_id>.json` — full content (prompts, tool I/O, plans) paged out when bigger than 4 KB
- `session.summary.json` — one-shot aggregate (n_turns, total cost, p95 latency, models seen, skills used) written at session end

### Event schema (v2)

Every event in `runtime.jsonl` carries flat top-level fields suitable for
direct pandas analysis — no `json_normalize` required:

```python
import pandas as pd
df = pd.read_json("~/.arc/sessions/<id>/events/runtime.jsonl", lines=True)
df.groupby("model")["duration_ms"].agg(["mean", "p95"])
df[df.event_family == "llm"]["cost_usd"].sum()
```

Top-level fields include: `event_type`, `event_family`, `ts`, `session_id`,
`turn_id`, `model`, `provider`, `temperature`, `duration_ms`, `input_tokens`,
`output_tokens`, `cache_input_tokens`, `cost_usd`, `stop_reason`,
`finish_reason_normalized`, `model_run_id`, `severity`. Large content
(prompts, tool I/O, full plan JSON, councillor raw responses) lives in
sibling blob files referenced by `raw_payload_ref`.

Cost is computed from a central pricing table at `src/runtime/cost.py`. Unknown
models leave `cost_usd` null rather than fabricating a figure.

### Replay and export

```bash
# Re-run a session's user inputs against a different model
python scripts/replay_session.py --source <session_id> --model gpt-4o --provider openai

# Bundle a session for sharing (applies stage-2 redaction: IPs, hostnames, paths)
python scripts/export_session.py <session_id>            # → ./session_<id>.tar.gz
python scripts/export_session.py <session_id> --out my.tar.gz
```

Replay sets a fresh `model_run_id` on every new event so analysts can join
the source and replay JSONLs:

```python
df = pd.concat([
    pd.read_json("~/.arc/sessions/<source_id>/events/runtime.jsonl", lines=True),
    pd.read_json("~/.arc/sessions/<replay_id>/events/runtime.jsonl", lines=True),
])
df.groupby(["model_run_id", "model"])["cost_usd"].sum()
```

Events whose payloads might carry secrets are redacted at emit time (API keys,
bearer tokens, JWTs, emails, home paths). The export script applies a stricter
second pass for sharing.

---

## License

(Add license info)
