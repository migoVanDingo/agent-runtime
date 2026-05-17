# 0083g — ChatLog + streaming + ToolCard

> **Read first:** `_plans/0083-decoupled-ui-textual.md` §4.2.
> Depends on: **0083f** (Textual skeleton).

## Goal

Replace the raw event-log placeholder from Phase 0083f with real chat UI:

- **`ChatLog`** — scrollable widget that renders user bubbles, streaming agent
  responses (plain text while streaming, Markdown on `MessageComplete`), and
  embeds `ToolCard` widgets.
- **`ToolCard`** — collapsible widget showing tool name, args preview, status
  (running / done / failed), and result preview.

After this phase, a user message sent through the service produces a
visually complete turn in the TUI — streamed text that settles into rendered
Markdown, with collapsible tool-call cards.

## Files to create / modify

| File | Action | Purpose |
|------|--------|---------|
| `src/ui/widgets/chat_log.py` | **Create** | `ChatLog` widget |
| `src/ui/widgets/tool_card.py` | **Create** | `ToolCard` widget |
| `src/ui/screens/chat.py` | **Modify** | Replace RichLog skeleton with `ChatLog` |

## Detailed implementation

### `src/ui/widgets/tool_card.py`

```python
"""ToolCard — collapsible widget for a single tool invocation.

Shown inside ChatLog each time a ToolCallStarted event arrives.
Updated when ToolCallCompleted arrives.
"""
from __future__ import annotations

try:
    from textual.app import ComposeResult
    from textual.widgets import Collapsible, Static, Label
    from textual.reactive import reactive
except ImportError as exc:
    raise ImportError("Textual not installed") from exc


class ToolCard(Collapsible):
    """Collapsible card that displays one tool invocation.

    Starts collapsed. The user can press Enter or click the header to expand
    and see full args / result text.

    Status values: "running" | "done" | "failed"
    """

    DEFAULT_CSS = """
    ToolCard {
        margin: 0 0 0 2;
        border: round $border;
        background: $surface;
    }
    ToolCard > .tool-header {
        color: $text-dim;
    }
    ToolCard.-running > .tool-header {
        color: $warning;
    }
    ToolCard.-done > .tool-header {
        color: $success;
    }
    ToolCard.-failed > .tool-header {
        color: $error;
    }
    ToolCard .tool-body {
        color: $text-dim;
        padding: 0 2;
    }
    """

    status: reactive[str] = reactive("running")

    def __init__(
        self,
        tool_name: str,
        tool_call_id: str,
        args_preview: str = "",
        **kwargs,
    ) -> None:
        # Title shown in the collapsed header line.
        header = f"  {tool_name}"
        super().__init__(title=header, collapsed=True, **kwargs)
        self.tool_name = tool_name
        self.tool_call_id = tool_call_id
        self._args_preview = args_preview
        self._result_text = ""

    def compose(self) -> ComposeResult:
        yield Static(
            f"[dim]call id:[/dim] {self.tool_call_id}\n"
            f"[dim]args:[/dim] {self._args_preview or '(none)'}",
            classes="tool-body",
            id="tool-args",
        )
        yield Static("", classes="tool-body", id="tool-result")

    def watch_status(self, status: str) -> None:
        """Update CSS class and header label when status changes."""
        self.remove_class("-running", "-done", "-failed")
        self.add_class(f"-{status}")
        # Update the collapsible title to reflect new status.
        icon = {"running": "...", "done": "ok", "failed": "ERR"}.get(status, "")
        self.title = f"  {self.tool_name}  [{icon}]"

    def complete(self, result_preview: str, error: str = "") -> None:
        """Called when ToolCallCompleted arrives for this card."""
        if error:
            self.status = "failed"
            self._result_text = f"[red]Error:[/red] {error}"
        else:
            self.status = "done"
            self._result_text = result_preview or "(no output)"

        result_widget = self.query_one("#tool-result", Static)
        result_widget.update(
            f"[dim]result:[/dim] {self._result_text[:500]}"
        )
```

### `src/ui/widgets/chat_log.py`

The central widget. Key decisions:
- Uses `RichLog` as the scrollable base (not a `ScrollableContainer` of
  `Static` widgets) because `RichLog` handles streaming appends efficiently.
- Streaming: each `TokenChunk` writes raw text into a buffer and the widget
  appends incrementally via `_log.write(chunk, end="")`.
- On `MessageComplete`: the streaming buffer is used as the source for a
  Markdown re-render. `RichLog.write(Markdown(text))` replaces the last
  streaming region with rendered output.
- In practice, "replacing" in `RichLog` is not trivial — the approach used
  here is: track whether we are mid-stream; on `MessageComplete`, write a
  separator and the Markdown block. This means the plain-text tokens appear
  then below them the formatted version appears. If RichLog supports
  clearing to a marked point in Textual 8.x, use that instead.

**NOTE:** Textual 8.x `RichLog` does not expose a "replace last line" API.
The strategy here is: stream tokens as plain text, then on `MessageComplete`
write a horizontal rule followed by the Markdown-rendered version. The
plain-text region serves as a loading indicator; the Markdown is the final
output. The TUI feels responsive without a complex widget-swap operation.
Confirm with Textual 8.x docs if a cleaner API exists (e.g. `RichLog.pop()`
or per-line mutation) and use it.

```python
"""ChatLog — scrollable markdown-aware chat history widget.

Renders:
  - User message bubbles (right-aligned, accent color)
  - Agent streaming bubbles (plain text → Markdown on MessageComplete)
  - ToolCard widgets embedded between content blocks

Event handling:
  handle_turn_started()      → add user bubble
  handle_token_chunk()       → append text to streaming buffer + write
  handle_message_complete()  → render full Markdown block
  handle_tool_call_started() → mount a ToolCard
  handle_tool_call_completed() → update the ToolCard
"""
from __future__ import annotations

from typing import AsyncIterator

try:
    from rich.markdown import Markdown
    from rich.text import Text
    from textual.app import ComposeResult
    from textual.containers import ScrollableContainer, VerticalScroll
    from textual.widget import Widget
    from textual.widgets import RichLog, Static
except ImportError as exc:
    raise ImportError("Textual not installed") from exc

from service.events import (
    AgentEvent, TurnStarted, TokenChunk, MessageComplete,
    ToolCallStarted, ToolCallCompleted,
)
from ui.widgets.tool_card import ToolCard


class ChatLog(Widget):
    """Scrollable chat history.

    Contains a RichLog for text output and mounts ToolCard widgets
    inline between turns.
    """

    DEFAULT_CSS = """
    ChatLog {
        height: 1fr;
        overflow-y: scroll;
    }
    ChatLog .user-bubble {
        background: $surface;
        color: $accent;
        margin: 1 0 0 4;
        padding: 0 1;
        border-left: thick $primary;
    }
    ChatLog .agent-bubble {
        color: $text;
        margin: 0 0 1 0;
        padding: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield RichLog(id="chat-richlog", highlight=False, markup=True, wrap=True)

    def _log(self) -> RichLog:
        return self.query_one("#chat-richlog", RichLog)

    # ── User bubble ────────────────────────────────────────────────────────────

    def add_user_message(self, text: str) -> None:
        """Render a user message bubble."""
        self._log().write(Text(""))  # blank line separator
        self._log().write(Text(f"You: {text}", style="bold #00ff87"))
        self._log().write(Text(""))

    # ── Streaming agent response ───────────────────────────────────────────────

    def begin_agent_response(self) -> None:
        """Called when TurnStarted arrives (or just before first TokenChunk)."""
        self._streaming_buffer = []
        self._streaming_started = False

    def append_token(self, text: str) -> None:
        """Called for each TokenChunk. Writes plain text to the log."""
        if not self._streaming_started:
            self._log().write(Text("Agent: ", style="dim"))
            self._streaming_started = True
        self._streaming_buffer.append(text)
        # Write incrementally so the user sees progress.
        self._log().write(text, end="")

    def finalize_response(self, full_text: str) -> None:
        """Called on MessageComplete. Re-renders the full text as Markdown.

        In Textual 8.x, RichLog.write() accepts Rich renderables including
        Markdown objects. We write a separator then the rendered version.
        The plain-text stream above it acts as a loading preview.
        """
        self._streaming_started = False
        self._streaming_buffer = []
        # Write a blank line to visually separate the streaming preview
        # from the final Markdown render.
        self._log().write(Text(""))
        try:
            self._log().write(Markdown(full_text))
        except Exception:
            # Fallback: plain text if Markdown rendering fails.
            self._log().write(Text(full_text))
        self._log().write(Text(""))  # trailing blank line

    # ── Tool cards ────────────────────────────────────────────────────────────

    def _tool_card_id(self, tool_call_id: str) -> str:
        # Sanitize tool_call_id to a valid CSS id.
        return "tc-" + "".join(c if c.isalnum() else "_" for c in tool_call_id)

    def add_tool_card(self, event: ToolCallStarted) -> None:
        """Mount a new ToolCard for a starting tool call."""
        card = ToolCard(
            tool_name=event.tool_name,
            tool_call_id=event.tool_call_id,
            args_preview=event.args_preview,
            id=self._tool_card_id(event.tool_call_id),
        )
        self.mount(card)

    def update_tool_card(self, event: ToolCallCompleted) -> None:
        """Update an existing ToolCard with the completion result."""
        card_id = self._tool_card_id(event.tool_call_id)
        try:
            card = self.query_one(f"#{card_id}", ToolCard)
            card.complete(result_preview=event.result_preview, error=event.error)
        except Exception:
            pass  # Card may not exist if events arrived out of order.

    # ── Initialise streaming state ────────────────────────────────────────────

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._streaming_buffer: list[str] = []
        self._streaming_started: bool = False
```

### `src/ui/screens/chat.py` (modified)

Replace the RichLog skeleton with `ChatLog`. Add `handle_agent_event()`
dispatch logic that routes each event to the appropriate `ChatLog` method.

```python
"""ChatScreen — primary screen of arc-tui.

Composes ChatLog (chat history) + StatusBar (bottom bar) + InputBox (input).
InputBox is added in Phase 0083h; this phase uses a placeholder.
"""
from __future__ import annotations

try:
    from textual.app import ComposeResult
    from textual.screen import Screen
    from textual.widgets import Static
except ImportError as exc:
    raise ImportError("Textual not installed") from exc

from service.events import (
    AgentEvent,
    TurnStarted, TurnCompleted, TurnFailed, TurnCancelled,
    TokenChunk, MessageComplete,
    ToolCallStarted, ToolCallCompleted,
    StageStarted, StageCompleted,
)
from ui.widgets.chat_log import ChatLog


class ChatScreen(Screen):
    """Main chat interface."""

    CSS = """
    #status-bar {
        height: 1;
        background: $primary;
        color: $bg;
        padding: 0 1;
    }
    #input-placeholder {
        height: 3;
        background: $surface;
        color: $text-dim;
        border-top: solid $border;
        padding: 1;
    }
    """

    BINDINGS = [
        ("ctrl+c", "app.quit", "Quit"),
        ("escape", "request_pause", "Pause"),
    ]

    def compose(self) -> ComposeResult:
        yield Static("arc-tui  |  idle", id="status-bar")
        yield ChatLog(id="chat-log")
        # InputBox replaces this placeholder in Phase 0083h.
        yield Static(
            "[dim]Input disabled in Phase 0083g — use service_repl.py[/dim]",
            id="input-placeholder",
        )

    def _chat_log(self) -> ChatLog:
        return self.query_one("#chat-log", ChatLog)

    def _status(self) -> Static:
        return self.query_one("#status-bar", Static)

    def handle_agent_event(self, event: AgentEvent) -> None:
        """Dispatch an AgentEvent to the appropriate widget update."""
        t = event.type

        if t == "turn.started":
            msg = getattr(event, "message_preview", "")
            self._chat_log().add_user_message(msg)
            self._chat_log().begin_agent_response()
            self._status().update("arc-tui  |  thinking...")

        elif t == "content.token_chunk":
            self._chat_log().append_token(getattr(event, "text", ""))

        elif t == "content.message_complete":
            self._chat_log().finalize_response(getattr(event, "text", ""))

        elif t == "tool.call.started":
            self._chat_log().add_tool_card(event)
            self._status().update(
                f"arc-tui  |  tool: {getattr(event, 'tool_name', '')}"
            )

        elif t == "tool.call.completed":
            self._chat_log().update_tool_card(event)

        elif t == "stage.started":
            stage = getattr(event, "stage", "")
            self._status().update(f"arc-tui  |  {stage}")

        elif t in ("turn.completed", "turn.cancelled"):
            self._status().update("arc-tui  |  idle")

        elif t == "turn.failed":
            err = getattr(event, "error", "unknown error")
            self._status().update(f"arc-tui  |  ERROR: {err[:60]}")

    async def action_request_pause(self) -> None:
        """ESC toggles pause/resume. Implemented fully in Phase 0083h."""
        svc = self.app.service
        if svc.is_busy:
            await svc.pause()
            self._status().update("arc-tui  |  paused (ESC to resume)")
        else:
            await svc.resume()
```

## Verification

```bash
# 1. Existing tests still pass
pytest -x -q

# 2. Manual: launch the TUI, send a message via service_repl.py
#    (or wire a temporary test button in the app), and observe:
#    - User bubble appears with accent color
#    - Agent text streams in plain text
#    - On MessageComplete, Markdown block appears below
#    - Tool calls show as collapsed ToolCard widgets
#    - Status bar updates with stage/tool names

# 3. Import discipline
python - <<'EOF'
import pathlib, sys
violations = []
for f in pathlib.Path("src/ui").rglob("*.py"):
    src = f.read_text()
    for bad in ["from runtime", "import runtime", "from agent import", "import agent\n"]:
        if bad in src:
            violations.append((str(f), bad))
if violations:
    for v in violations:
        print("VIOLATION:", v)
    sys.exit(1)
print("Import discipline ok.")
EOF
```

## Done when

- [ ] `src/ui/widgets/chat_log.py` and `src/ui/widgets/tool_card.py` created.
- [ ] `ChatScreen` uses `ChatLog` instead of raw `RichLog`.
- [ ] User messages appear as styled bubbles.
- [ ] `TokenChunk` events produce incremental plain-text output.
- [ ] `MessageComplete` event produces a Markdown-rendered block below the stream.
- [ ] `ToolCallStarted` mounts a collapsed `ToolCard`.
- [ ] `ToolCallCompleted` updates the card status and shows result preview.
- [ ] Status bar reflects current stage / tool name / idle state.
- [ ] No `ui/` → `runtime/` imports.
- [ ] `pytest` green.

## Out of scope for this phase

- Multi-line input box (Phase 0083h).
- Slash command autocomplete (Phase 0083h).
- Theme CSS variables (Phase 0083i — widget styles use placeholder colors for now).
- `/artifacts` modal for large tool output (deferred per design doc §9 Q5).
