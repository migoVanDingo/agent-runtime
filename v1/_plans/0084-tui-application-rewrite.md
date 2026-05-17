# 0084 — TUI Rewrite: Full-Screen Application + Drift Remediation

> **Audience:** Implementer with full codebase access, no prior context beyond reading
> this document and the sibling phase docs `0084a` … `0084j`.
> Read this document end-to-end first. Then execute phases in order.

---

## 0. Context

The 0083 work introduced the service layer (`src/service/`) and attempted a Textual-based
UI that was subsequently abandoned for a `prompt_toolkit.PromptSession` approach. That
approach has three unresolvable limitations in the current form:

1. **Input not anchored** — the prompt cursor lives right after the last printed output,
   not at the bottom of the terminal. Empty space accumulates below.
2. **No inline spinner** — status lives only in the bottom toolbar, which goes blank
   during long single-stage operations. No animation signals liveness.
3. **Multi-line cursor navigation broken** — arrow keys traverse characters, not lines.

Additionally, 0083 intentionally deferred work that is now blocking:

- **Spinner in stage signatures** — 7 stage files + ToolLoop + ToolCallExecutor still
  receive and call a `spinner` object. `NoopSpinner` is injected to silence it under
  the service layer. This is technical debt, not the intended final state.
- **`ASK_USER` pipeline flow** — `Pipeline.user_input_fn` returns `""` in TUI mode.
  The agent silently gets an empty clarification response. This is a bug.
- **`_service_checkpoint` naming** — the field on `PipelineContext` leaks a
  service-layer concept into a runtime-layer type.
- **Resume conversation loading** — the session picker works but does not actually
  load prior conversation messages into the agent.

This document specifies the complete rewrite and all drift remediation in phases.

---

## 1. Goals

### UI
- `prompt_toolkit.Application` in `full_screen=True` mode with `mouse_support=False`.
  Native terminal text selection (click-drag) works in iTerm2 / Terminal.app.
- Input area anchored to the bottom of the terminal at all times.
- Conversation scrollable above the input via Page Up/Down.
- Multi-line input: Enter = submit, Shift+Enter = newline, arrow keys navigate lines.
- Inline animated spinner between the submitted message and the next input prompt:
  dots cycling `·` `··` `···`, color pulsing between shades of cyan/gray.
- Two-color prompt arrows: submitted messages keep green `▶`, active input shows blue `▶`.
- Static footer: `arc  /help  ESC: pause  Ctrl+D: exit`. Never changes except escalation.
- Session picker: shows session ID, date, and preview so sessions are distinguishable.

### Drift remediation
- Remove `spinner=` from all stage signatures; remove `NoopSpinner`; remove all
  `TODO(0083-cleanup)` markers related to spinner.
- Fix `ASK_USER` flow with a proper blocking `TUIInputGate`.
- Rename `_service_checkpoint` to `_pause_check` on `PipelineContext`.
- Load conversation messages on session resume.
- Document EventBus dual-purpose (telemetry + service) in CLAUDE.md.

### Non-goals
- Themes / settings modal (deferred).
- `/resume` as a full modal DataTable (text picker with session ID is enough for now).
- HTTP/WebSocket service transport (future).

---

## 2. Architecture

### 2.1 Layout

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│  Conversation area (FormattedTextControl, scrollable)        │
│                                                              │
│    ▶  user message                    ← green, submitted     │
│                                                              │
│    Agent                                                     │
│    response text…                                            │
│                                                              │
├──────────────────────────────────────────────────────────────┤  ← only when active
│    ⚙  Planning ··                     ← inline spinner       │
├──────────────────────────────────────────────────────────────┤
│  ──────────────────────────────────────────────────────────  │  separator
├──────────────────────────────────────────────────────────────┤
│    ▶  _                               ← blue, active input   │
│                                       multiline, Shift+Enter │
├──────────────────────────────────────────────────────────────┤
│  arc  /help  ESC: pause  Ctrl+D: exit                        │  footer (static)
└──────────────────────────────────────────────────────────────┘
```

### 2.2 Key component map

| Component | Location | Responsibility |
|---|---|---|
| `ArcApp` | `ui/app.py` | `Application` instance, layout, key bindings, entry point |
| `ConversationModel` | `ui/conversation.py` | Formatted text storage, auto/manual scroll |
| `SpinnerModel` | `ui/spinner_model.py` | Dot animation state, color cycling |
| `InputModel` | `ui/input_model.py` | Buffer, submit handler, escalation routing |
| `ConsumeEvents` task | `ui/app.py` | Drains `service.events()`, updates models, calls `app.invalidate()` |
| `SpinnerTick` task | `ui/app.py` | Fires every 400 ms when active, cycles dots, calls `app.invalidate()` |
| `service/` | unchanged | Service layer, event types, in-process impl — no changes in this plan |
| `runtime/stages/` | phase 0084h | Spinner removed from all signatures |

### 2.3 ConversationModel scrolling

`FormattedTextControl(get_formatted_text, focusable=True)` with a `[SetCursorPosition]`
token placed at the scroll target. The Window renders the cursor position as the focal
point for scrolling.

- **Auto-scroll** (default): cursor marker placed at the end of all chunks.
  New content always becomes visible.
- **Manual scroll**: cursor marker moved earlier in the chunk list by Page Up/Down
  key bindings. Auto-scroll resumes when cursor returns to end.

Scroll granularity approximation: each Page Up/Down moves the marker by `terminal_height`
chunks. This is an approximation because chunk sizes vary; exact line counting is
deferred (see Phase 0084j open questions).

### 2.4 Inline spinner

A `ConditionalContainer(Window(...), filter=Condition(lambda: spinner.active))` sits
between the conversation window and the separator. It is **zero height** when inactive —
no vertical space is consumed. When active it renders a single line:

```
  ⚙  {stage_name}  {dots}
```

Dots cycle: `·` → `··` → `···` → `·` (each state held for 400 ms).
Color pulses: `ansicyan` → `ansibrightcyan` → `ansicyan` → `ansigray` → repeat.

The spinner message is updated by `stage.started` and `tool.call.started` events from
the service event stream.

### 2.5 Multi-line input

`Buffer(multiline=True, name="input")` with:
- **Enter**: custom binding calls `buffer.validate_and_handle()` → submit.
- **Shift+Enter**: calls `buffer.newline()` → inserts newline.
- **Up/Down arrows**: native `Buffer` multi-line navigation (moves cursor between lines).
- **Left/Right arrows**: character navigation within a line.
- Active `▶` prefix rendered via `BeforeInput` processor with `ansiblue` style.
- On submit: message added to `ConversationModel` with green `▶`, buffer cleared.

### 2.6 Escalation in full-screen

When `TUIUserGate.pending_escalation` is set:
- The `BeforeInput` processor dynamically changes prefix to red `Allow? [y/n]`.
- The footer content changes to the escalation hint.
- The Enter key binding routes to the escalation handler instead of submit.
- `ConversationModel` shows escalation details (tool, reason, inputs) when the
  `_escalation_poller` detects a new pending escalation.

All state changes trigger `app.invalidate()` to force redraw.

### 2.7 Spinner removal from stages

The spinner was always a UI concern passed down through the runtime as a side-channel.
The correct model (consistent with 0079 "Runtime as God"): stages emit `RuntimeEvent`s;
UI subscribes to those events and decides how to render status.

The pipeline runner already emits `stage.started` / `stage.finished` via the event bus.
The tool executor already emits `tool.call.started` / `tool.call.completed`. These are
exactly what the spinner needs.

Removal plan:
1. Delete `spinner` parameter from `__init__` of all 7 stages + `ToolLoop` + `ToolCallExecutor`.
2. Delete all `self._spinner.*` calls inside stages (these are redundant since stage events
   fire via the bus).
3. Delete `NoopSpinner` from `service/inprocess.py`.
4. Delete `agent.spinner = NoopSpinner()` assignment in `InProcessAgentService.__init__`.
5. Remove `spinner=p.spinner` from all `_build_pipeline` stage constructor calls in `agent.py`.
6. Keep the `Spinner` class in `src/ui/spinner.py` intact — the **legacy CLI** (`arc`)
   still uses it via `agent.spinner.start/stop/update` in `agent.call()`. Only remove
   it from stage constructors.

Wait — the legacy `arc` CLI path drives the spinner via `agent.spinner` set in
`Agent.__init__`. The stages call `self._spinner` which IS `agent.spinner`. Under the
service layer, we inject `NoopSpinner`. Under the legacy CLI, the real `Spinner` is used.

Revised removal plan: remove spinner from stage **constructors** and from **ToolLoop**,
but keep `Agent.spinner` (the instance attribute) for the legacy CLI path. Stages will
no longer receive or call a spinner directly — the `agent.spinner` in `Agent.call()`
handles legacy CLI feedback. Under the service layer, `NoopSpinner` is still set on
`agent.spinner` so legacy spinner calls are silenced, but stages no longer call it.

This removes the spinner as a **dependency injected into stages** while preserving the
legacy CLI experience. `NoopSpinner` stays (it silences `agent.spinner` calls during
the agent turn), but the `TODO(0083-cleanup)` comment is updated to reflect that
removing it entirely would require removing spinner from `agent.call()` too, which
would break the legacy CLI.

### 2.8 `ASK_USER` fix

Create `TUIInputGate` parallel to `TUIUserGate`:

```python
class TUIInputGate:
    """Blocks the worker thread; TUI supplies the answer."""
    def __init__(self): ...
    def ask(self, question: str) -> str:
        self.pending_question = question
        self._event.clear()
        self._event.wait()
        return self._answer
    def supply_answer(self, text: str):
        self._answer = text
        self._event.set()
```

`InProcessAgentService` stores a `TUIInputGate` instance. `build_service()` passes it
to `agent._pipeline._user_input_fn`. The app shows the question in the conversation
and routes the next Enter submission to `supply_answer()` instead of `service.send()`.

### 2.9 `_service_checkpoint` rename

`PipelineContext._service_checkpoint` → `_pause_check`. The name `_pause_check` is
runtime-neutral (it's a "callable to invoke at yield points to check for cooperative
pause"). It does not reference the service layer. All callers updated.

---

## 3. File layout changes

```
src/ui/
├── app.py                ← full rewrite (Application-based)
├── conversation.py       ← NEW: ConversationModel
├── spinner_model.py      ← NEW: SpinnerModel (dots + color cycling)
├── input_model.py        ← NEW: InputModel (Buffer, submit, escalation routing)
├── spinner.py            ← KEEP: Spinner class, used by legacy arc CLI
└── commands/
    └── builtin.py        ← updated handlers (no Rich console param)

src/runtime/
├── pipeline_context.py   ← _service_checkpoint → _pause_check
├── pipeline.py           ← checkpoint field rename
├── tool_loop.py          ← remove spinner= param
├── stages/
│   ├── execution.py      ← remove spinner= param and calls
│   ├── direct_execution.py  ← remove spinner= param and calls
│   ├── planning.py       ← remove spinner= param and calls
│   ├── council.py        ← remove spinner= param and calls
│   ├── synthesizer.py    ← remove spinner= param and calls
│   ├── continuation.py   ← remove spinner= param and calls
│   └── skill_hint.py     ← remove spinner= param and calls
└── tool_executor.py      ← remove spinner= param and calls

src/service/
├── inprocess.py          ← TUIInputGate added; spinner comments cleaned
└── builder.py            ← TUIInputGate wired, _tui_safe_input removed

src/agent.py              ← remove spinner= from _build_pipeline stage calls
```

---

## 4. Drift register (complete)

| ID | Location | Drift | Phase |
|---|---|---|---|
| D1 | `runtime/stages/*.py` (7 files) | `spinner=` in stage constructors; stages call `self._spinner` directly | 0084h |
| D2 | `runtime/tool_loop.py` | `spinner=` in `ToolLoop.__init__`; calls `self._spinner.update()` | 0084h |
| D3 | `runtime/tool_executor.py` | `spinner=` in `ToolCallExecutor.__init__` | 0084h |
| D4 | `agent.py` | `_build_pipeline` passes `spinner=p.spinner` to stages | 0084h |
| D5 | `runtime/pipeline_context.py` | `_service_checkpoint` leaks service concept into runtime type | 0084j |
| D6 | `runtime/pipeline.py` | references `_service_checkpoint` by name | 0084j |
| D7 | `service/builder.py` | `_tui_safe_input` returns `""` for `ASK_USER` — silent bug | 0084i |
| D8 | `ui/app.py` | `_escalation_poller` polling loop — replace with `app.invalidate()` push | 0084e |
| D9 | `ui/app.py` | Session resume picker doesn't load conversation into agent | 0084g |
| D10 | `service/inprocess.py` | `NoopSpinner` comment still says "TODO remove spinner from stage signatures" — update after D1–D4 done | 0084h |

---

## 5. Phase breakdown

| Phase | Title | Depends on | Primary files |
|---|---|---|---|
| **0084a** | Full-screen Application skeleton | — | `ui/app.py` (new), `ui/conversation.py`, `ui/spinner_model.py` |
| **0084b** | ConversationModel: formatted text + scroll | 0084a | `ui/conversation.py` |
| **0084c** | Multi-line input area | 0084a | `ui/input_model.py`, `ui/app.py` |
| **0084d** | Animated inline spinner | 0084a, 0084b | `ui/spinner_model.py`, `ui/app.py` |
| **0084e** | Event consumer integration | 0084b, 0084c, 0084d | `ui/app.py` |
| **0084f** | Escalation in full-screen | 0084e | `ui/app.py`, `ui/input_model.py` |
| **0084g** | Session picker + resume conversation load | 0084e | `ui/app.py`, `service/builder.py`, `service/inprocess.py` |
| **0084h** | Spinner removal from stages | 0084e | 9 runtime files, `agent.py` |
| **0084i** | `ASK_USER` fix: `TUIInputGate` | 0084f | `service/inprocess.py`, `service/builder.py`, `ui/app.py` |
| **0084j** | Cleanup: rename, comments, CLAUDE.md | all | `runtime/pipeline_context.py`, `runtime/pipeline.py`, `CLAUDE.md` |

---

### 0084a — Full-screen Application skeleton

**Scope:** Replace `PromptSession + patch_stdout` with `Application(full_screen=True,
mouse_support=False)`. Wire a minimal layout that launches, shows a static placeholder
conversation area, accepts input at the bottom, and exits on Ctrl+D.

**No service integration** — the event consumer is not wired yet. This phase only
validates that the layout is correct and the app runs cleanly.

**Key implementation:**

```python
from prompt_toolkit.application import Application
from prompt_toolkit.layout import Layout, HSplit, Window, ConditionalContainer
from prompt_toolkit.layout.controls import FormattedTextControl, BufferControl
from prompt_toolkit.layout.dimension import Dimension as D
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition

app = Application(
    layout=layout,
    key_bindings=kb,
    full_screen=True,
    mouse_support=False,
    color_depth=ColorDepth.TRUE_COLOR,
)
await app.run_async()
```

**Verification:** `arc-tui` launches, shows the separator and footer, input accepts
text, Ctrl+D exits cleanly.

---

### 0084b — ConversationModel: formatted text + scroll

**Scope:** `src/ui/conversation.py`. Stores conversation as list of `(style, text)` tuples.
Auto-scroll via `[SetCursorPosition]` at end. Page Up/Down key bindings move the
cursor marker to earlier positions for manual scroll.

```python
class ConversationModel:
    _chunks: list[tuple[str, str]]
    _cursor_idx: int          # index of [SetCursorPosition] marker
    _auto_scroll: bool

    def add(self, style: str, text: str) -> None
    def add_ansi(self, ansi_text: str) -> None   # Rich ANSI → FormattedText tuples
    def begin_streaming(self) -> None
    def append_token(self, text: str) -> None
    def finalize_streaming(self, full_text: str) -> None
    def scroll_up(self, lines: int = 10) -> None
    def scroll_down(self, lines: int = 10) -> None
    def get_formatted_text(self) -> list           # called by FormattedTextControl
```

**Verification:** Page Up shows earlier content; Page Down returns to bottom; new
content auto-scrolls when at bottom; manual scroll position held while reading.

---

### 0084c — Multi-line input area

**Scope:** `src/ui/input_model.py`. `Buffer(multiline=True)` with key bindings:
- `enter` → `buffer.validate_and_handle()` (submits)
- `s-enter` → `buffer.newline()`
- Up/Down arrows navigate naturally within multi-line buffer (no override needed — 
  prompt_toolkit handles this for `multiline=True` buffers natively)
- `BeforeInput` processor shows `▶` with style that changes between blue (idle) and
  red `Allow? [y/n]` (escalation pending)

**Verification:** Type a 3-line message using Shift+Enter; arrow keys move cursor
between lines; Enter submits the full buffer; buffer clears after submit.

---

### 0084d — Animated inline spinner

**Scope:** `src/ui/spinner_model.py` + integration in `ui/app.py`.

```python
class SpinnerModel:
    active: bool
    msg: str
    _frame: int

    _DOTS = ["·", "··", "···"]
    _COLORS = ["ansicyan", "ansibrightcyan", "ansicyan", "ansigray"]

    def get_formatted_text(self) -> list
    def tick(self) -> None         # advance frame
    def start(self, msg: str) -> None
    def stop(self) -> None
```

Asyncio spinner task:
```python
async def _spinner_tick(spinner: SpinnerModel, app: Application):
    while True:
        if spinner.active:
            spinner.tick()
            app.invalidate()
        await asyncio.sleep(0.4)
```

**Verification:** After submitting a message (without service wired), manually toggle
`spinner.active` and verify dots animate and color cycles. Spinner window adds/removes
vertical space cleanly.

---

### 0084e — Event consumer integration

**Scope:** `ui/app.py` — wire `service.events()` to `ConversationModel` + `SpinnerModel`.

Event routing:

| Event | Action |
|---|---|
| `turn.started` | Add user message to conv (green `▶`); spinner.start("Thinking") |
| `stage.started` | spinner.msg = stage label |
| `tool.call.started` | spinner.msg = tool_name |
| `content.token_chunk` | conv.append_token; spinner.stop() on first token |
| `content.message_complete` | conv.finalize_streaming (renders Markdown) |
| `turn.completed` | conv.add timer line; spinner.stop(); drain pending queue |
| `turn.failed` | conv.add error line (red); spinner.stop() |
| `turn.cancelled` | conv.add cancelled line; spinner.stop() |

Replace the `_escalation_poller` background task with event-push model: the escalation
is detected when the `TUIUserGate.pending_escalation` is set, which happens synchronously
from the worker thread via `call_soon_threadsafe`. Add an `asyncio.Event` to
`TUIUserGate` that the app awaits; when set, show the escalation in the conversation
and call `app.invalidate()`.

**Verification:** Send a message, see it appear in conversation with green arrow, see
spinner animate with stage names, see response appear, see timer.

---

### 0084f — Escalation in full-screen

**Scope:** `ui/app.py`, `ui/input_model.py`.

When `input_gate.pending_escalation` is set:
1. `BeforeInput` returns red `Allow? [y/n]` prefix (dynamic callable, re-evaluated on render).
2. Footer `FormattedTextControl` returns escalation hint text.
3. Enter binding routes to `_handle_escalation_input(text)` instead of `service.send()`.
4. Conversation shows escalation details (reason, tool, inputs formatted flat — `path: proc` not `{'path': 'proc'}`).
5. On y/n answer: call `gate.supply_answer(approved)`, restore normal mode.

**Verification:** Trigger ghidra_analyze, see escalation appear inline, type y/n, see
Allowed/Denied in conversation, agent continues.

---

### 0084g — Session picker + resume conversation load

**Scope:** `ui/app.py`, `service/inprocess.py`, `service/builder.py`.

**Picker display format:**
```
  1  SES01KRB... | 2026-05-11 09:28  |  "take a look at the executable proc..."
  2  SES01KRA... | 2026-05-11 08:47  |  "what about blogs or news..."
```

Session ID always shown in full or truncated to 16 chars.

**Resume conversation load:** After session is selected, call
`service.load_conversation(session_id)` which reads messages from the artifact store
and injects them into `agent.messenger`. Display last N messages in the conversation
window so user has context. Add `load_conversation(session_id)` method to
`InProcessAgentService`.

**Verification:** Resume a known session; conversation shows prior messages; next
submitted message continues correctly in context.

---

### 0084h — Spinner removal from stages (D1–D4, D10)

**Scope:** 9 runtime files + `agent.py`.

For each stage: remove `spinner` from `__init__` signature, remove `self._spinner`
assignment, delete all `self._spinner.update/start/stop` calls. Stages already emit
`stage.started/finished` via the pipeline runner and `tool.call.started/completed` via
the executor — those are the signals the spinner now uses.

For `ToolLoop`: remove `checkpoint` and `spinner` params. Wait — `checkpoint` must
stay (it's the pause mechanism). Only remove `spinner`.

For `ToolCallExecutor`: remove `spinner` param. The `self._spinner.stop()` and
`self._spinner.start()` calls around escalation prompts must be replaced with a
`self._user_gate.prompt()` call that no longer needs spinner control since the TUI
handles its own display.

For `agent.py` `_build_pipeline`: remove `spinner=p.spinner` from all stage
constructor calls.

Update `InProcessAgentService.__init__` comment on `NoopSpinner`: change from
"TODO remove" to "Silences agent.spinner calls during turns; agent.spinner remains
for legacy arc CLI path."

**Verification:** All existing tests pass. `arc` (legacy CLI) still shows spinner.
`arc-tui` shows inline spinner. No stage calls `self._spinner` anywhere.

---

### 0084i — `ASK_USER` fix: `TUIInputGate` (D7)

**Scope:** `service/inprocess.py`, `service/builder.py`, `ui/app.py`.

`TUIInputGate` class in `service/inprocess.py`:
- `ask(question: str) -> str`: blocks worker thread, shows question in conv, waits
- `supply_answer(text: str)`: called from UI, unblocks worker
- `pending_question: str | None`: checked by app on each Enter submission

In `build_service()`: create `TUIInputGate`, pass as `user_input_fn` to pipeline.
Remove `_tui_safe_input` function entirely.

In `ui/app.py`: when `input_gate.pending_question` is set, Enter routes to
`input_gate.supply_answer(text)` instead of `service.send(text)`. The conversation
shows the question inline (similar to escalation display).

**Verification:** Trigger an `ASK_USER` flow (requires a stage that returns it); verify
question appears in conversation; user types an answer and the agent continues with it.

---

### 0084j — Cleanup: rename, comments, CLAUDE.md (D5, D6)

**Scope:** `runtime/pipeline_context.py`, `runtime/pipeline.py`, `CLAUDE.md`.

- Rename `_service_checkpoint` → `_pause_check` everywhere (2 files).
- Remove all remaining `TODO(0083-cleanup)` markers; replace with permanent comments
  explaining the current design intent.
- `CLAUDE.md`: add section on EventBus dual purpose (telemetry sinks + service
  subscribers); document the `_pause_check` contract; document `TUIUserGate` /
  `TUIInputGate` threading model.
- Final grep: `grep -rn "TODO(0083-cleanup)" src/` should return 0 results.

**Verification:** `grep -rn "TODO(0083-cleanup)" src/` returns nothing.
`pytest -x -q` green. `arc-tui` launches and a full conversation works end to end.

---

## 6. Open questions (resolve before or during implementation)

**Q1.** `SynthesizerStage.run()` currently calls `self._spinner.stop()` before
streaming, then `self._spinner.start("Synthesizing response...")` on the non-streaming
path. With spinner removed from stage, who stops any CLI spinner before streaming
begins? Answer: `agent.call()` owns `agent.spinner`. `SynthesizerStage` should emit a
`stage.started` event (which the pipeline already does via `_run_stage`). The CLI
spinner is controlled by `agent.call()` indirectly via the pipeline. After removal,
the CLI spinner keeps running through synthesis (minor visual change for the CLI;
acceptable for now, fixable in a later cleanup pass).

**Q2.** Page Up/Down scroll granularity: the ConversationModel moves the cursor marker
by `N` chunks, not `N` visual lines. For now, use a fixed estimate (e.g. each Page Up
moves 30 chunks). Exact visual-line counting deferred.

**Q3.** Conversation window max size: for very long conversations (hundreds of
exchanges), the `_chunks` list grows unbounded. For now, no cap. A ring-buffer cap
(keep last 2000 chunks) can be added later without changing the interface.

---

## 7. Implementation reading order

1. `0084a` — get the app running, validate layout.
2. `0084b` + `0084c` together — conversation model and input are coupled in the layout.
3. `0084d` — spinner is independent; can be done in parallel with b+c.
4. `0084e` — requires a, b, c, d all working.
5. `0084f` — requires e.
6. `0084g` — requires e; largely independent of f.
7. `0084h` — requires e (to verify stages still emit correct events). Can be done
   after e but before f/g if desired.
8. `0084i` — requires f (escalation pattern is similar).
9. `0084j` — last, after all code changes are stable.

Stop and verify manually after `0084e` before continuing. That's the first milestone
where a full conversation works end-to-end.
