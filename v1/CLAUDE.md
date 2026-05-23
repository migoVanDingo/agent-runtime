# arc v1 — agent runtime (legacy)

> **Status: legacy.** v2 (`../v2/`) is a ground-up rewrite that replaces this.
> v1 still works and is the active install for reverse-engineering workflows
> (Ghidra/Briefbot integrations, persisted SQLite DAL). Most new feature work
> lives in v2. Use v1 for: bug fixes, Ghidra/Briefbot, anything that depends
> on the SQLModel-backed persistence layer.

## What it is

Multi-stage agent runtime with a Textual TUI, pluggable providers, skill-based
planning, RAG, and a persistent SQLite store. The whole thing is built around
a **pipeline** of stages (planner → context → tools → response) with hooks at
each boundary. v1's design lesson — and the reason v2 exists — is that the
orchestration grew brittle: too many stages reasoning about each other.

## CLI entry points

| Command   | Use for                                                              |
|-----------|----------------------------------------------------------------------|
| `arc`     | Scripts, CI, pipes, non-TTY                                          |
| `arc-tui` | Interactive Textual TUI (Markdown, themes, slash commands)           |

Both installed by `pip install ".[tui]"`. `arc` alone needs no extras.

## Layout (orient first, then dive)

```
src/
  agent.py             top-level agent (legacy CLI flow)
  main.py              `arc` entry point
  runtime/             pipeline, tool loop, stages, events
  service/             AgentService Protocol + InProcessAgentService
    builder.py         the ONLY file in service/ that imports agent/runtime
  ui/                  Textual TUI (`arc-tui`)
  db/                  SQLModel models + Alembic migrations
  plugins/             skill, planner, RAG, observability plugins
  providers/           Gemini / Anthropic / OpenAI adapters
  tools/               bash, file, Ghidra, Briefbot, etc.
```

## Hard architectural rule: import discipline

```
ui/*       MUST NOT import runtime/*, agent.py, tools/*
service/*  MUST NOT import ui/*
runtime/*  MUST NOT import ui/*, service/*
```

`service/` is the only bridge. `service/builder.py` is the only file in
`service/` that touches agent/runtime. If you need to wire a UI feature to
agent behavior, route it through an `AgentEvent` on the bus or a new
`AgentService` method — never a direct import.

Verify after touching `ui/`:
```bash
python3 -c "
import ast, pathlib
violations = []
for f in pathlib.Path('src/ui').rglob('*.py'):
    for n in ast.walk(ast.parse(f.read_text())):
        if isinstance(n, ast.ImportFrom) and n.module:
            if n.module.startswith(('runtime', 'agent', 'tools')):
                violations.append(f'{f}:{n.lineno}')
print('VIOLATIONS:' if violations else 'OK', violations or '')
"
```

## EventBus contract (`runtime/events/bus.py`)

Two consumer types:
1. **Sinks** (`JsonlEventSink`) — write structured events to disk.
2. **Service subscribers** — `InProcessAgentService` translates
   `RuntimeEvent` → `AgentEvent` for the UI stream.

Subscribers are O(1) callbacks that enqueue and return. **They must never
block or raise.** Errors are swallowed by the bus.

## Pause/cancel contract

`PipelineContext._pause_check` is an optional `Callable[[], None]` set by
`InProcessAgentService` before each turn. Called at cooperative yield points
(stage boundaries, tool-loop iterations). Raises `TurnCancelledError` to
abort. It is `None` in the legacy `arc` CLI path — only `arc-tui` wires it.

## TUIUserGate / TUIInputGate threading

Worker thread blocks on `threading.Event`; the Textual async loop calls
`gate.supply_answer(...)` to unblock. `InProcessAgentService.close()` MUST
call `gate.supply_answer(False/"")` before shutting down the executor so the
worker thread can exit — otherwise the process hangs on shutdown.

## ORM / DAL

- **SQLModel + Alembic.** Schema in `src/db/models.py`, migrations in
  `src/db/alembic/versions/`.
- **`agent_db_url` respects `ARC_HOME`** — don't hardcode paths.
- **Alembic auto-runs on bootstrap.** `arc bootstrap` runs `upgrade head`
  before the first session. Don't bypass it — the install hook depends on it.
- **Persistence flag** in config controls whether sessions are written to
  the DB at all (default on); Briefbot integration depends on it.

## Known gotchas (chronic — keep these in mind)

### JVM / TUI interaction (Ghidra)

- Use **fd-level capture** for Ghidra subprocess output, not pipe redirection.
  Pipe redirection deadlocks the JVM under the Textual TUI's stdout takeover.
- **Hard-exit on Ctrl+C.** Don't try to clean-shutdown the JVM — it owns
  too many threads. `os._exit(130)` in the Ctrl+C handler.
- **Call JVM shutdown in `finalize`**, not in atexit. atexit fires too late
  vs Textual's screen teardown and corrupts the terminal.

### Skill / PlanValidator ordering

`PlanValidator` must defer concrete-tool checks pre-expansion when `skill:*`
steps are present in the plan. Otherwise the validator rejects skills before
the skill expander has a chance to rewrite them into tool calls. If you
touch `PlanValidator`, run the skill expansion tests specifically.

### Sub-agent spinner inheritance

When dispatching a sub-agent, **inherit the parent's spinner** rather than
creating a fresh one. A fresh real Spinner under the TUI corrupts the
alt-screen render (overlapping spinners). Either pass `None` (no spinner)
or hand the parent's spinner down.

### Pyghidra in default install

Pyghidra is in the default install set (not an extra). If install breaks
on a Linux box, that's usually the culprit — confirm a JDK is present.

## Deferred cleanup

- `CLIUserGate` vs `TUIUserGate` duplication. Once `arc` is fully deprecated
  in favor of `arc-tui` + v2, `CLIUserGate` can be removed.

## When to use v1 vs v2

| Need | Use |
|---|---|
| Ghidra / reverse-engineering flows | v1 (Ghidra tools, JVM safety wired up) |
| Briefbot integration | v1 (DAL-backed) |
| Persisted SQL state across sessions | v1 |
| New plugin / experiment | **v2** (cleaner extension surface) |
| Replay / deterministic re-run | **v2** (modes 1–5 first-class) |
| Multi-provider | **v2** (Gemini + Anthropic) |

If you're not sure, ask the user.

## More

- README.md — long-form architecture
- `_plans/` — design plans (0083 = decoupled TUI, 0087 = telemetry, 0088 = plugins,
  0089 = context strategies, 0090 = context discipline + sub-agents)
- `config.yml` — user-tunable settings (do NOT add hardcoded knobs in code)
