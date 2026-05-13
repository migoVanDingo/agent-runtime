# arc — Agent Runtime Codebase Guide

This document describes the architectural conventions, entry points, and import
discipline rules for the arc agent runtime and its Textual TUI.

---

## CLI entry points

| Command   | Use for                                                              |
|-----------|----------------------------------------------------------------------|
| `arc`     | Scripts, CI, pipes, non-TTY, legacy compatibility                    |
| `arc-tui` | Interactive sessions — full TUI with Markdown, themes, slash commands |

Both are installed by `pip install ".[tui]"`. `arc` alone requires no extras.

---

## Import discipline rule (0083 — UI/service boundary)

The codebase has a hard architectural boundary:

```
ui/*       ← must NOT import from → runtime/*, agent.py, tools/*
service/*  ← must NOT import from → ui/*
runtime/*  ← must NOT import from → ui/*, service/*
```

**Rationale:** `service/` and `runtime/` must be installable without Textual.
`runtime/` must not know about any UI framework. Only `ui/` depends on Textual.

### Allowed imports

- `ui/*` → `service/*` (UI talks to the service layer)
- `service/*` → `runtime/*` (InProcessAgentService wraps agent/runtime)
- `runtime/*` → `runtime/*` (internal runtime imports)

### Forbidden imports

- `ui/*` → `runtime/*`, `agent.py`, `tools/*`
- `service/*` → `ui/*`
- `runtime/*` → `ui/*`, `service/*`

### Enforcement

Run the import check manually during code review:

```bash
python - <<'EOF'
import pathlib, sys
violations = []
for f in pathlib.Path("src/ui").rglob("*.py"):
    src = f.read_text()
    for bad in ["from runtime", "import runtime", "from agent import", "import agent\n",
                "from tools", "import tools"]:
        if bad in src:
            violations.append((str(f), bad))
if violations:
    print("IMPORT VIOLATIONS:", violations)
    sys.exit(1)
else:
    print("Import discipline ok.")
EOF
```

Optional: configure `import-linter` (see `[tool.importlinter]` in `pyproject.toml`)
for automated CI enforcement.

---

## Deferred cleanup

1. **`CLIUserGate` vs `TUIUserGate`** — the agent currently has both. The
   service layer injects `TUIUserGate`; the legacy CLI path keeps `CLIUserGate`.
   Once `arc` is fully deprecated, `CLIUserGate` can be removed.

---

## Event Bus Contract

The `EventBus` in `runtime/events/bus.py` serves two purposes:
1. **Telemetry sinks** — `JsonlEventSink` writes structured events to disk for analysis.
2. **Service layer subscribers** — `InProcessAgentService` subscribes to translate
   `RuntimeEvent` → `AgentEvent` for the UI event stream.

Both purposes are intentional. Subscribers are `O(1)` callbacks that enqueue and return.
They must never block or raise. Errors are swallowed.

---

## _pause_check Contract

`PipelineContext._pause_check` is an optional `Callable[[], None]` set by
`InProcessAgentService` before each agent turn. It is called at cooperative yield
points (between pipeline stages, between tool-loop iterations) and may raise
`TurnCancelledError` to abort the turn. It is `None` in the legacy CLI path.

---

## TUIUserGate / TUIInputGate Threading Model

Both gates follow the same pattern:
- Worker thread calls `gate.prompt(esc)` / `gate.ask(question)`, which blocks on `threading.Event`
- TUI (async event loop) calls `gate.supply_answer(approved/text)`, which sets the event
- Worker thread unblocks and continues

`InProcessAgentService.close()` calls `gate.supply_answer(False/"")` before shutting
down the executor to ensure the worker thread can exit cleanly.

---

## Import Discipline

`ui/` must never import from `agent.py`, `runtime/`, or `tools/`. The `service/`
layer is the only permitted bridge. `service/builder.py` is the only file in `service/`
that imports from `agent.py` and `runtime/`. Verified with:

```bash
python3 -c "
import ast, pathlib, sys
violations = []
for f in pathlib.Path('src/ui').rglob('*.py'):
    try:
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.ImportFrom) and node.module:
                    if node.module.startswith(('runtime', 'agent', 'tools')):
                        violations.append(f'{f}:{node.lineno}')
    except: pass
print('VIOLATIONS:', violations) if violations else print('Import discipline OK')
"
```

---

## Project structure

```
src/
  agent.py             — top-level agent entry point (CLI flow)
  main.py              — `arc` CLI entry point (arc = "main:main")
  runtime/             — agent pipeline, tool loop, stages
  service/             — AgentService interface + InProcessAgentService
    events.py          — AgentEvent taxonomy (discriminated union)
    interface.py       — AgentService protocol
    inprocess.py       — InProcessAgentService implementation
    builder.py         — build_service() factory
  ui/                  — Textual TUI (arc-tui = "ui.app:run")
    app.py             — ArcApp (Textual App)
    screens/           — ChatScreen, SettingsScreen, modals
    widgets/           — ChatLog, ToolCard, InputBox
    commands/          — CommandRegistry, built-in slash commands
    themes/            — TCSS theme files (bundled)
    theme_loader.py    — ThemeLoader — discover and apply themes
    theme_generator.py — palette generation (hex, LLM, image)
    settings_store.py  — SettingsStore — Pydantic + YAML persistence
```

---

## Service layer interface

```python
from service import AgentService, AgentEvent, TokenChunk, MessageComplete, ...
from service.inprocess import InProcessAgentService, NoopSpinner, TUIUserGate
from service.builder import build_service, ServiceOptions

# Key methods:
service.send(text)          # async → TurnHandle
service.pause()             # async
service.resume()            # async
service.cancel_current_turn()  # async
service.is_busy             # bool property
service.events()            # async generator of AgentEvent
service.close()             # async
```

---

## Theme system

Built-in themes are in `src/ui/themes/*.tcss`. User themes go in `~/.arc/themes/*.tcss`.

All widget CSS must use variables from `_vars.tcss` — never literal hex colors.
This is what makes `/theme dracula` work without touching any widget code.

```bash
# Switch theme
/theme dracula

# List themes
/theme

# Generate a new theme
/theme generate
```

---

## Settings

Settings persist to `~/.arc/settings.yml`. Edit via:

```bash
/set theme dracula
/set history_size 200
/settings   # opens the settings modal
```

Or `Ctrl+,` to open the settings modal directly.
