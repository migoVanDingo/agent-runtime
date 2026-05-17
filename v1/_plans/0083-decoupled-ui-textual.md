# 0083 — Decoupled UI Architecture: Service Layer + Textual TUI

> **Audience:** This document and its sibling phase docs (`0083a` … `0083n`)
> are written for an implementer who has full read access to the codebase but
> no prior context. Read this design doc, then execute the phases in
> order. Each phase doc is self-contained with file paths, before/after
> snippets, and a verification checklist.

---

## 0. Goals & Non-Goals

### Goals
- A Claude-Code-feeling TUI for the local CLI experience: scrollback, markdown rendering, multi-line editing, multi-line paste, slash commands with autocomplete, ESC-to-pause, type-while-agent-runs, themes, settings.
- A **clean architectural seam** between the agent runtime and any frontend, so the same agent can later be served from a FastAPI app in a containerized web service without touching agent code.
- The CLI frontend never imports from `agent.py` or `runtime/`; only from `service/`.
- The agent runtime never imports from `ui/` or any UI framework.
- Web/API deployments install **without** Textual (optional dependency via `[tui]` extra).

### Non-Goals (this design)
- FastAPI server implementation. Mentioned only to validate the boundary; see "Future Work."
- HTTP/WebSocket transport client. Same — the Protocol must support it; the impl is later work.
- Concurrent in-flight turns. The agent is single-turn; the Protocol forbids overlap. UI queues messages.
- Replacing the existing `arc` CLI. Both `arc` (legacy) and `arc-tui` (new) coexist during and after this work. Migration policy decided in Phase N.
- Removing the `Spinner` from stage signatures. Punted — left in place, replaced with a no-op when running under the service layer. Documented with TODOs at every touchpoint.

---

## 1. Core Concept: Frontend / Backend Decoupling

The web-dev pattern of "frontend talks to backend over a contract" applied to a CLI agent. The contract is a Python `Protocol`, not HTTP — but the discipline is identical. Multiple frontends (CLI today, FastAPI tomorrow) implement against one interface. Multiple service implementations (in-process today, HTTP-backed tomorrow) satisfy the same interface.

Three layers, top to bottom:

```
┌──────────────────────────────────────────────────────────────┐
│  Frontend                                                     │
│   • CLI TUI       (this work)                                 │
│   • FastAPI app   (future)                                    │
│   • Slack bot     (someday)                                   │
│   • SDK calls     (programmatic embedding)                    │
└──────────────────────┬───────────────────────────────────────┘
                       │
                       │   AgentService Protocol
                       │   (the contract — async + event stream)
                       │
┌──────────────────────▼───────────────────────────────────────┐
│  Service Layer                                                │
│   • InProcessAgentService   (this work)                       │
│   • HttpAgentService        (future)                          │
│                                                               │
│   Wraps agent.call(), translates RuntimeEvent → AgentEvent,   │
│   manages pause/cancel, manages backpressure.                 │
└──────────────────────┬───────────────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────────────┐
│  Agent Runtime  (existing — touched only at the bus seam)     │
│   • agent.py, runtime/, tools/, planning/, providers/         │
│   • Emits RuntimeEvent to the existing bus                    │
└──────────────────────────────────────────────────────────────┘
```

**Discipline rule:** any import edge from `ui/*` to `runtime/*`, `agent.py`, or `tools/*` is a bug. Enforced by convention now; consider an import-linter rule later (see Phase N).

---

## 2. Repository Layout (target)

Single repo. Optional dependencies separate runtime install from UI install.

```
agent-runtime/
├── src/
│   ├── agent.py                    ← unchanged (mostly)
│   ├── runtime/                    ← unchanged
│   ├── planning/                   ← unchanged
│   ├── providers/                  ← unchanged
│   ├── tools/                      ← unchanged
│   │
│   ├── service/                    ← NEW: the contract + in-process impl
│   │   ├── __init__.py
│   │   ├── events.py               ← AgentEvent dataclasses
│   │   ├── interface.py            ← AgentService + TurnHandle Protocols
│   │   ├── inprocess.py            ← InProcessAgentService
│   │   ├── translator.py           ← RuntimeEvent → AgentEvent
│   │   └── queue.py                ← BoundedDropQueue
│   │
│   ├── ui/                         ← NEW: the Textual TUI
│   │   ├── __init__.py
│   │   ├── app.py                  ← App entrypoint (run() function)
│   │   ├── screens/
│   │   │   ├── chat.py
│   │   │   ├── settings.py
│   │   │   ├── resume_picker.py
│   │   │   └── theme_picker.py
│   │   ├── widgets/
│   │   │   ├── chat_log.py         ← scrollable markdown log
│   │   │   ├── input_box.py        ← multi-line input + completion
│   │   │   ├── status_bar.py       ← bottom toolbar
│   │   │   └── tool_card.py        ← collapsible tool-call display
│   │   ├── commands/
│   │   │   ├── registry.py         ← slash command registry
│   │   │   └── builtin.py          ← /exit, /resume, /pause, /theme, /set
│   │   ├── themes/
│   │   │   ├── default.tcss
│   │   │   ├── dracula.tcss
│   │   │   ├── nord.tcss
│   │   │   ├── tokyo-night.tcss
│   │   │   └── _vars.tcss          ← shared CSS variables / contract
│   │   ├── theme_generator.py      ← /theme generate logic
│   │   └── settings_store.py       ← reads/writes ~/.arc/settings.yml
│   │
│   ├── main.py                     ← legacy CLI entrypoint (kept)
│   └── (api/ — future, not in this work)
│
├── pyproject.toml                  ← extras: [tui], [api], [dev]
└── _plans/0083*.md                 ← these docs
```

Entry points in `pyproject.toml`:

```toml
[project.scripts]
arc      = "main:main"        # legacy CLI, unchanged
arc-tui  = "ui.app:run"       # new Textual UI
```

---

## 3. The Service Layer

### 3.1 Event taxonomy (`service/events.py`)

Five families of typed dataclasses. All JSON-serializable. Discriminated by `type` field for client-side dispatch and HTTP transport.

| Family | Events | UI uses for |
|---|---|---|
| **Session** | `SessionStarted`, `SessionEnded` | banner, "resumed" toast |
| **Turn** | `TurnStarted`, `TurnCompleted`, `TurnFailed`, `TurnCancelled` | user-message bubble, completion state, error toast |
| **Stage** | `StageStarted`, `StageProgress`, `StageCompleted` | status bar / spinner-replacement widget |
| **Content** | `TokenChunk`, `MessageComplete` | streaming text into chat log; final markdown render |
| **Tool** | `ToolCallStarted`, `ToolCallCompleted` | collapsible tool-call cards |

Common base fields: `type: str`, `timestamp: datetime`, `session_id: str`, `turn_id: str | None`. All event types are exported as a discriminated union `AgentEvent`.

### 3.2 The `AgentService` Protocol (`service/interface.py`)

```
class AgentService(Protocol):
    @property
    session_id: str
    is_busy: bool

    async send(message: str) -> TurnHandle
    def    events() -> AsyncIterator[AgentEvent]
    async pause() -> None
    async resume() -> None
    async cancel_current_turn() -> None
    def    conversation_history() -> list[dict]
    async close() -> None

class TurnHandle(Protocol):
    @property
    turn_id: str

    def    events() -> AsyncIterator[AgentEvent]    # filtered to this turn
    async wait()  -> str                            # final response text
    async cancel() -> None
```

Two parallel ways to consume events:
- `service.events()` — global subscription. Used by the UI's main event loop.
- `turn_handle.events()` — narrowed to one turn. Useful in tests, future request handlers.

Both yield from the same underlying queue; the handle filters by `turn_id`.

### 3.3 `InProcessAgentService` (`service/inprocess.py`)

Wraps the existing sync `agent.call()` via `loop.run_in_executor`. Bridges the `on_token` callback to `TokenChunk` events. Subscribes to the existing `RuntimeEvent` bus and translates each event to its typed counterpart.

```
class InProcessAgentService:
    def __init__(self, agent: Agent, session_id: str):
        self._agent     = agent
        self._executor  = ThreadPoolExecutor(max_workers=1)
        self._subs      = []                      # list[BoundedDropQueue]
        self._loop      = asyncio.get_event_loop()
        self._pause     = asyncio.Event(); self._pause.set()  # set = running
        self._cancel    = asyncio.Event()
        self._current   = None

        # Replace agent.spinner with a no-op so stages don't write to stdout
        # when running under the service layer.
        # TODO(0083-cleanup): remove spinner from stage signatures entirely.
        self._agent.spinner = NoopSpinner()

        # Bridge: existing bus → typed events
        get_event_bus().subscribe(self._on_runtime_event)
```

Two thread bridges to be careful about:
- `on_token` callback fires on the worker thread → use `loop.call_soon_threadsafe(self._publish, …)`
- `RuntimeEvent` bus subscribers also fire on the worker thread → same treatment

### 3.4 RuntimeEvent → AgentEvent translation (`service/translator.py`)

A pure function `translate(event: RuntimeEvent) -> AgentEvent | None`. The single seam where the agent's internal event vocabulary maps to the external contract. Maintaining this function is the cost of changing internal bus events without breaking the UI.

| RuntimeEvent name | Translated to |
|---|---|
| `session.started` / `session.resumed` | `SessionStarted(resumed=…)` |
| `session.ended` | `SessionEnded` |
| `turn.started` | `TurnStarted` |
| `turn.completed` | (suppressed — emitted by service driver) |
| `turn.failed` | (suppressed — emitted by service driver) |
| `stage.{name}.started` | `StageStarted(stage=name, message=…)` |
| `stage.{name}.progress` | `StageProgress(...)` |
| `stage.{name}.completed` | `StageCompleted(...)` |
| `tool.invoked` | `ToolCallStarted` |
| `tool.completed` | `ToolCallCompleted` |
| (anything else) | `None` (not surfaced) |

Translation is best-effort. Missing fields default; unknown events return `None`. The service driver synthesizes `Turn*` events directly so they fire even if the bus is disabled.

### 3.5 Bounded queue with drop policy (`service/queue.py`)

A wrapper around `asyncio.Queue` that:
- Has a fixed max size (default 1000)
- On overflow, drops the **oldest `TokenChunk`** event. Never drops lifecycle events (`SessionStarted/Ended`, `TurnStarted/Completed/Failed/Cancelled`, `ToolCallStarted/Completed`).
- Increments a counter on drop; UI can show a "throttled" indicator if non-zero.

Rationale: `TokenChunk`s are the only high-frequency event class, and a slow consumer that misses a few tokens still gets `MessageComplete` with the full text. Lifecycle events must never be lost or the UI's state machine drifts.

### 3.6 Pause / cancel semantics

**Fine granularity** (per design decision). Cooperative yield points to be added at:
1. **Top of `Pipeline.run_stage()`** — between every stage transition.
2. **Inside `tool_loop.py`** — between every tool invocation.
3. **Inside the streaming token loop** — between yielded chunks (provider layer; needs care to avoid stalling the LLM TCP connection — see phase doc 0083e).

Each yield point is a one-liner that calls `self._service.checkpoint()` (a thin wrapper that awaits the pause event and raises `TurnCancelled` if cancel is set).

For the agent code (running on the worker thread) to interact with `asyncio.Event`s on the main loop, the checkpoint uses `asyncio.run_coroutine_threadsafe(...).result()` — blocking the worker thread until pause is released. This is intentional: paused = the agent literally stops doing work.

### 3.7 EventBus subscribe()

The current `EventBus` in `src/runtime/events/bus.py` only emits to sinks. It needs a `subscribe(callback)` / `unsubscribe(callback)` pair. Callbacks are invoked synchronously on the emitting thread; the service uses `call_soon_threadsafe` to hop back to the event loop. Existing sinks remain unchanged.

---

## 4. The UI Layer (Textual)

### 4.1 App structure

```
ChatScreen (default screen)
├── ChatLog        (scrollable, markdown-aware, RichLog subclass)
├── StatusBar      (top: agent state; bottom: keybinding hints)
└── InputBox       (multi-line TextArea with slash completion)

Modal screens (overlays):
├── ResumePickerScreen
├── SettingsScreen
├── ThemePickerScreen
└── CommandPaletteScreen   (Ctrl+K, like VS Code)
```

The `App` subclass holds:
- A reference to the `AgentService` (passed in at construction)
- A `MessageQueue` for type-while-busy (see 4.4)
- The active theme name (reactive attribute)
- The settings store

The app subscribes to `service.events()` once at startup; an `event_dispatcher` task routes by `event.type` to the appropriate widget update.

### 4.2 Widgets

**`ChatLog`** — Wraps Textual's `RichLog`. Each turn produces:
- A user bubble (right-aligned, themed `.user-message`)
- One or more tool-call cards (`ToolCard` widget; collapsible)
- A streaming agent bubble that grows as `TokenChunk`s arrive
- On `MessageComplete`, the bubble re-renders as Markdown via Textual's `Markdown` widget

**`InputBox`** — Subclass of Textual's `TextArea` with:
- Multi-line by default (Enter inserts newline; Ctrl+Enter or `\` + Enter submits — settable)
- Slash-command autocomplete dropdown (custom completion provider)
- History navigation with Ctrl+↑/↓
- Multi-line paste works natively — Textual handles bracketed paste mode

**`StatusBar`** — Reactive labels showing:
- Left: agent state (`Idle` / `Thinking…` / `Running tool: X` / `Paused`)
- Center: current stage name + elapsed timer
- Right: keybinding hints (`ESC pause • Ctrl+K cmd • Ctrl+, settings`)

**`ToolCard`** — Collapsible widget showing tool name, args preview, status (running/done/failed), and result preview. Click/Enter to expand for full output.

### 4.3 Type-while-busy (`MessageQueue`)

A simple FIFO at the UI layer (not the service layer). When `service.is_busy` is True:
- User typing into `InputBox` is unaffected
- Submitting a message enqueues it with a visual "queued" badge in the chat log
- An event-loop task watches `is_busy`; when it drops to False, it pops the next message and calls `service.send(message)`

The user can edit or delete queued messages before they fire.

### 4.4 Slash commands (`ui/commands/`)

A `CommandRegistry` mapping `/name` → handler. Each handler is `async def(app, args: str) -> None`. Built-ins:

| Command | Action |
|---|---|
| `/exit`, `/quit` | Save session, close app |
| `/pause` | `await service.pause()` |
| `/resume` | `await service.resume()` |
| `/cancel` | `await service.cancel_current_turn()` |
| `/clear` | Clear chat log (history preserved in service) |
| `/theme [name]` | List themes, or switch to `name` |
| `/theme generate` | Open theme generator screen |
| `/set <key> <value>` | Update setting |
| `/settings` | Open settings modal |
| `/resume [id]` | Switch session (opens picker if no ID) |
| `/help` | Show command palette |

Slash commands integrate with the `InputBox` autocompletion. Typing `/` opens a popup of matching commands with descriptions. `/set` uses a nested completer for keys and known values.

The **command palette** (Ctrl+K) is a fuzzy-search modal over all commands plus all settings — same UX as VS Code.

---

## 5. Theme System

### 5.1 TCSS structure

Themes are `.tcss` files with a fixed contract of CSS variables defined in `themes/_vars.tcss`. Each theme file overrides the variables.

`themes/_vars.tcss`:
```
$bg:           #1e1e1e;
$bg-elevated:  #252526;
$surface:      #2d2d30;
$primary:      #007acc;
$accent:       #00ff87;
$text:         #d4d4d4;
$text-dim:     #858585;
$success:      #4ec9b0;
$warning:      #dcdcaa;
$error:        #f48771;
$border:       #3e3e42;
```

`themes/dracula.tcss`:
```
$bg:           #282a36;
$bg-elevated:  #343746;
$surface:      #44475a;
$primary:      #bd93f9;
$accent:       #50fa7b;
$text:         #f8f8f2;
$text-dim:     #6272a4;
$success:      #50fa7b;
$warning:      #f1fa8c;
$error:        #ff5555;
$border:       #44475a;
```

Widget styles reference variables, never literal colors:
```
ChatLog .user-message  { color: $accent; }
ChatLog .tool-output   { color: $text-dim; }
InputBox               { background: $surface; border: tall $primary; }
StatusBar              { background: $bg-elevated; color: $text-dim; }
```

### 5.2 Built-in themes (ship with the package)
- `default` — neutral dark
- `dracula`
- `nord`
- `tokyo-night`
- `gruvbox-dark`
- `solarized-dark`
- `light` — neutral light

### 5.3 User-generated themes
- Stored in `~/.arc/themes/<name>.tcss`
- `/theme generate` opens a screen with three approaches:
  1. **Palette input** — user enters hex codes for accent/bg/text; the rest derive
  2. **From image** — pick an image; extract dominant colors via `colorthief`
  3. **From description** — natural-language prompt routed through the agent's runtime LLM ("generate a theme that feels like a misty forest at dawn") returning a JSON palette
- Generated themes write a `.tcss` file and immediately become selectable

### 5.4 Live reload
Textual reloads CSS when files change in dev mode. In production mode, `/theme <name>` calls `app.stylesheet.reload(…)` to swap atomically.

---

## 6. Settings System

### 6.1 Storage
- Project-level settings stay in existing `config.yml` (untouched).
- UI/user settings live in `~/.arc/settings.yml` (new file).
- `SettingsStore` class loads, validates (Pydantic), persists, and emits change events.

### 6.2 What goes where
| Setting | Location |
|---|---|
| LLM model, provider config | `config.yml` (project) |
| Tool registry / toolsets | `config.yml` (project) |
| Active theme | `~/.arc/settings.yml` (user) |
| Keybindings | `~/.arc/settings.yml` (user) |
| Input submit-key (Enter vs Ctrl+Enter) | `~/.arc/settings.yml` (user) |
| Status bar visibility / layout | `~/.arc/settings.yml` (user) |
| History size, scrollback length | `~/.arc/settings.yml` (user) |

### 6.3 Settings modal
A Textual screen with a left-hand category list (Appearance, Editor, Keybindings, Advanced) and a right-hand form. Bindings update reactive attributes; saving writes the YAML.

---

## 7. Packaging

`pyproject.toml`:

```
[project]
dependencies = [
    "anthropic", "openai", "sqlmodel", "rich",
    # ... existing core deps; NO textual, NO prompt_toolkit
]

[project.optional-dependencies]
tui = ["textual>=0.86", "textual-dev"]
api = ["fastapi>=0.110", "uvicorn[standard]", "websockets"]   # future
dev = ["pytest", "ruff", ...]

[project.scripts]
arc      = "main:main"
arc-tui  = "ui.app:run"
```

Install combinations:
- Local dev: `uv sync --extra tui --extra dev`
- Web container (future): `pip install ".[api]"` — Textual is **not** installed
- Power user: `pip install ".[tui,api,dev]"`

Optional: exclude `src/ui/` from the wheel when building the `[api]`-flavored container image. Probably overkill — extras alone are sufficient.

---

## 8. Decisions Made

| # | Decision | Rationale |
|---|---|---|
| D1 | Single repo | Solo dev, all-Python, Protocol enforces logical boundary. Splittable later if needed. |
| D2 | Textual over prompt_toolkit Application mode | Built-in scrollback, markdown, theming, modal screens, reactive widgets. Looks right. |
| D3 | Wrap sync `agent.call` in thread executor | Zero agent code changes initially. Async migration can come later if desired. |
| D4 | Translate existing RuntimeEvent → typed AgentEvent | Single seam to maintain. Stages don't change. |
| D5 | Fine-grained pause/cancel | Snappy UX. Cost: yield points in pipeline + tool loop + streaming. |
| D6 | Bounded queue with selective drop | Drop oldest `TokenChunk` only; never drop lifecycle events. UI shows throttled indicator. |
| D7 | Punt spinner refactor | `NoopSpinner` injected when running under service. TODO comments mark every spinner kwarg. |
| D8 | Optional `[tui]` extra for Textual | Web/API deployment installs lean. Architectural rule reinforces this. |
| D9 | Single-turn agent (no concurrent turns) | Matches current agent. UI queues messages instead. |
| D10 | Both `arc` and `arc-tui` coexist | Legacy CLI useful for scripts/CI/non-tty. Final migration policy in Phase N. |

---

## 9. Open Questions

- **Q1.** Should `/theme generate` from natural language use the agent's full pipeline or bypass it (direct LLM call)? Recommend bypass — simpler, faster, doesn't pollute conversation history.
- **Q2.** Do we want a "headless" mode for the TUI app (`arc-tui --print "do X"`) that runs one turn, prints the answer, and exits? Useful for scripting. Trivial to add.
- **Q3.** Should slash commands be available outside the input box (e.g., `arc-tui /resume` from shell)? Probably yes — but defer.
- **Q4.** Markdown rendering during streaming: render incrementally (jittery, true-feeling) or render only on `MessageComplete` (clean, slight delay)? Recommend: stream as plain text into the bubble; swap to Markdown render on completion.
- **Q5.** Where do session-level artifacts (file outputs, paged analysis) appear in the chat log? Recommend a `/artifacts` modal, not inline cards.

---

## 10. Phase Breakdown

Phases are ordered. Each phase is independently testable. Phases A–E are pure backend (no Textual) and can be exercised with a stdout-printing test harness (Phase D). Phases F onward bring up the UI.

| Phase | Title | Depends on | Touches |
|---|---|---|---|
| **0083a** | Event types + Protocols | — | `service/events.py`, `service/interface.py` |
| **0083b** | EventBus subscribe() | — | `runtime/events/bus.py` |
| **0083c** | InProcessAgentService + translator | a, b | `service/inprocess.py`, `service/translator.py`, `service/queue.py` |
| **0083d** | Service test harness | c | `scripts/service_repl.py` (new), `tests/integration/test_service.py` |
| **0083e** | Pause / cancel yield points | c | `runtime/pipeline.py`, `runtime/tool_loop.py`, provider streaming |
| **0083f** | Textual app skeleton + ChatScreen | c | `ui/app.py`, `ui/screens/chat.py`, minimal widgets, one theme |
| **0083g** | ChatLog + streaming + ToolCard | f | `ui/widgets/chat_log.py`, `ui/widgets/tool_card.py` |
| **0083h** | InputBox + slash commands + queue | f, g | `ui/widgets/input_box.py`, `ui/commands/*` |
| **0083i** | Theme system + built-in themes | f | `ui/themes/*.tcss`, theme loader |
| **0083j** | Settings store + settings modal | f, i | `ui/settings_store.py`, `ui/screens/settings.py` |
| **0083k** | Resume picker + command palette modals | h, j | `ui/screens/resume_picker.py`, `ui/screens/command_palette.py` |
| **0083l** | Theme generator | i, j | `ui/theme_generator.py`, `ui/screens/theme_picker.py` |
| **0083m** | pyproject extras + entry points | f | `pyproject.toml` |
| **0083n** | Migration & cleanup | all | docs, CLAUDE.md note, optional import-linter rule |

### 0083a — Event types + Protocols
**Scope:** Define dataclasses for all 5 event families. Define `AgentService` and `TurnHandle` Protocols. No implementations. Add JSON serializer / deserializer helpers.
**Out:** No bus changes. No agent changes.
**Verification:** Type checks pass. Round-trip serialize/deserialize tests for every event class.

### 0083b — EventBus subscribe()
**Scope:** Add `subscribe(callback)` / `unsubscribe(callback)` to `EventBus`. Maintain list of callbacks alongside sinks. Invoke synchronously on emit thread, swallow exceptions like sinks. Add unit test.
**Out:** No agent or service changes.
**Verification:** Existing event tests still pass. New test: subscriber receives every emitted event.

### 0083c — InProcessAgentService + translator
**Scope:** Implement `service/inprocess.py`, `service/translator.py`, `service/queue.py`. Wrap `agent.call` in `loop.run_in_executor`. Subscribe to bus, translate, publish. Implement `BoundedDropQueue`. Inject `NoopSpinner` into the agent at construction.
**Out:** No pause/cancel logic yet — those are no-ops here.
**Verification:** Phase 0083d harness produces a stream of typed events when sent a message.

### 0083d — Service test harness
**Scope:** A `scripts/service_repl.py` that:
- Builds an Agent + InProcessAgentService
- Reads stdin, calls `service.send(line)`
- Prints every event with type + summary as it arrives
Plus: `tests/integration/test_service.py` that drives a known prompt and asserts the event sequence.
**Verification:** Manual REPL session works. Test passes against real provider (skip if no API key).

### 0083e — Pause / cancel yield points
**Scope:** Add `service.checkpoint()` calls at the three yield points listed in §3.6. Implement `pause()`, `resume()`, `cancel_current_turn()`. Bridge via `run_coroutine_threadsafe` from worker thread.
**Out:** No UI integration yet — tested via REPL/test harness.
**Verification:** Test that triggers pause mid-execution observes a stall; resume continues; cancel raises `TurnCancelled` and the turn ends with a `TurnCancelled` event.

### 0083f — Textual app skeleton + ChatScreen
**Scope:** `ui/app.py` with App subclass. `ChatScreen` with placeholder widgets. Wire `service.events()` subscription to a dispatcher task that prints event types to a log widget. Render with the default theme. Add `arc-tui` entry point.
**Out:** No real chat rendering, no input handling, no commands.
**Verification:** `arc-tui` launches a TUI showing event types as they fire when something is sent (manual seed via Python REPL or a temporary test button).

### 0083g — ChatLog + streaming + ToolCard
**Scope:** Real `ChatLog` widget. User bubbles, agent bubbles that stream from `TokenChunk`s, swap to Markdown render on `MessageComplete`. `ToolCard` widget for tool calls — collapsible, expand to see full args/result (paged from artifact store via service).
**Verification:** Send a message via temp button; see streamed response render as markdown; see tool calls as collapsible cards.

### 0083h — InputBox + slash commands + queue
**Scope:** `InputBox` widget — multi-line, paste-friendly, Ctrl+Enter submits, Ctrl+↑/↓ history. `CommandRegistry` and built-in commands (/exit, /pause, /resume, /cancel, /clear, /help). Type-while-busy `MessageQueue` with visual queued badges.
**Verification:** Paste a 10-line block — appears intact. Type `/` — completion popup. Submit while agent runs — message queued and visibly badged; fires when turn completes.

### 0083i — Theme system + built-in themes
**Scope:** `themes/_vars.tcss` contract. 7 built-in themes. Theme loader. `/theme list` and `/theme switch <name>`. Live reload via `app.stylesheet.reload()`.
**Verification:** Switch theme; entire UI repaints. Custom user theme in `~/.arc/themes/` loads.

### 0083j — Settings store + settings modal
**Scope:** `SettingsStore` (Pydantic-validated, YAML-backed). `SettingsScreen` modal with categorized form. `/set` command. Reactive bindings so changes apply live.
**Verification:** Change theme via settings modal → UI repaints + `~/.arc/settings.yml` written. Restart app → setting persists.

### 0083k — Resume picker + command palette modals
**Scope:** `ResumePickerScreen` replaces `_pick_resume_session` from `main.py` (in TUI flow only — legacy CLI keeps its inline picker). `CommandPaletteScreen` (Ctrl+K) for fuzzy search across commands.
**Verification:** `arc-tui --resume` opens modal picker; selection loads session and renders restored conversation in chat log. Ctrl+K opens palette.

### 0083l — Theme generator
**Scope:** `ThemePickerScreen` and `theme_generator.py` with the three approaches in §5.3. `/theme generate` opens it. Generated themes write `.tcss` to `~/.arc/themes/`.
**Verification:** Generate a theme from a hex accent; new file created; appears in switcher.

### 0083m — pyproject extras + entry points
**Scope:** Add `[project.optional-dependencies]` table. Add `arc-tui` to `[project.scripts]`. Update lockfile. Update README install section.
**Verification:** Fresh venv. `pip install ".[api]"` — Textual not installed. `pip install ".[tui]"` — Textual installed. Both `arc` and `arc-tui` available as commands.

### 0083n — Migration & cleanup
**Scope:**
- Decide: does `arc` remain default, or is `arc-tui` the new default? (Proposal: keep `arc` as legacy fallback for non-tty / scripting; promote `arc-tui` in docs.)
- Add CLAUDE.md note about the import-discipline rule (`ui/` ↛ `agent/` / `runtime/` / `tools/`).
- Optional: configure `import-linter` rule to enforce the boundary in CI.
- Leave all `# TODO(0083-cleanup): remove spinner kwarg` comments in place. Filed as future cleanup.
**Verification:** Repo conventions documented. CI rule (if added) catches a deliberate violation.

---

## 11. Future Work (deferred — explicitly out of scope here)

- **`api/` — FastAPI server.** Implements `AgentService` over WebSocket/SSE. Container image installs `[api]` extra only. Authentication, multi-tenancy, session lifecycle over HTTP — all separate design.
- **`HttpAgentService` — client transport.** A frontend-side implementation of the Protocol that talks to the FastAPI server. Lets the same `ui/` code run against either local or remote agents. Trivial once the Protocol is stable.
- **Concurrent turns / multi-session UI.** Today: single in-flight turn, single session. Future: tabs for parallel sessions, agent-internal parallelism.
- **Web frontend (HTML/JS).** Separate repo when it happens; consumes the FastAPI/WebSocket events.
- **Spinner removal.** All `spinner=` kwargs across `runtime/stages/*.py` deleted; status surfaced exclusively via `Stage*` events. Tracked by TODO comments left in Phase 0083c.

---

## 12. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Textual learning curve eats schedule | Phases F–H are intentionally minimal. Defer polish to later phases. |
| Worker-thread / event-loop bridging bugs | Centralize all cross-thread hops in `InProcessAgentService` helpers (`_publish_threadsafe`, `_checkpoint`). Heavy unit tests in 0083c. |
| Pause-mid-stream stalls LLM TCP connection | Phase 0083e doc spells out provider-layer impact. Worst case: pause granularity drops to between-tool-calls; stream-level pause becomes "drain then pause." |
| Spinner output corrupts Textual display | `NoopSpinner` injected at service construction. Verified in 0083c. |
| Bus subscriber callback latency blocks emit thread | Subscriber callbacks are required to be O(1) — they just enqueue. Documented in `bus.py`. |
| Theme TCSS contract drifts as widgets are added | `themes/_vars.tcss` is the canonical contract. New widgets must use variables. Lint rule possible later. |
| Backpressure drops affect debugging | Drop counter exposed in status bar. Logs warn when drops occur. Lifecycle events never dropped. |

---

## 13. Reading Order for Implementer

1. This document end-to-end.
2. `0083a` — get the types in place; everything else depends on these.
3. `0083b`, `0083c`, `0083d` together — the service layer is one logical unit, validated by the harness.
4. `0083e` once the harness works — pause/cancel is its own concern.
5. `0083f` — first sight of the UI. Stop here, click around, confirm the architecture feels right before going further.
6. `0083g` … `0083l` in order. Each adds one user-visible capability.
7. `0083m`, `0083n` — packaging and cleanup.

Stop and ask before starting any phase whose scope feels larger than its doc suggests. The phase docs are the contract; this doc is the rationale.
