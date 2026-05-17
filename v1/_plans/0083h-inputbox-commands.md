# 0083h — InputBox + slash commands + message queue

> **Read first:** `_plans/0083-decoupled-ui-textual.md` §4.2 (InputBox), §4.3 (queue), §4.4 (slash commands).
> Depends on: **0083f** (Textual skeleton), **0083g** (ChatLog).

## Goal

Replace the input placeholder from Phase 0083g with a fully functional `InputBox`:
- Multi-line TextArea subclass; Enter inserts newline, Ctrl+Enter submits
- Slash-command autocomplete popup when user types `/`
- Command history navigation (Ctrl+Up / Ctrl+Down)
- Multi-line paste (bracketed paste mode — Textual handles this natively)

Plus:
- `CommandRegistry` and built-in slash commands (`/exit`, `/pause`, `/resume`,
  `/cancel`, `/clear`, `/help`, `/theme`, `/set`, `/settings`, `/resume`)
- Type-while-busy message queue with visible queued-message badge in the chat log

## Files to create / modify

| File | Action |
|------|--------|
| `src/ui/widgets/input_box.py` | **Create** — `InputBox` widget |
| `src/ui/commands/__init__.py` | **Create** — package marker |
| `src/ui/commands/registry.py` | **Create** — `CommandRegistry` |
| `src/ui/commands/builtin.py` | **Create** — built-in slash commands |
| `src/ui/screens/chat.py` | **Modify** — swap placeholder for `InputBox`, add queue |

## Detailed implementation

### `src/ui/commands/registry.py`

```python
"""Slash command registry for arc-tui.

Commands are registered as async callables with signature:
    async def handler(app: ArcApp, args: str) -> None

The registry is keyed by command name (without leading slash).
Commands can register aliases.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Awaitable, TYPE_CHECKING

if TYPE_CHECKING:
    from ui.app import ArcApp


@dataclass
class Command:
    name: str
    description: str
    handler: Callable[["ArcApp", str], Awaitable[None]]
    aliases: list[str] = field(default_factory=list)
    usage: str = ""


class CommandRegistry:
    """Maps slash command names → Command objects.

    Usage:
        registry = CommandRegistry()
        registry.register(Command("exit", "Exit arc-tui", _exit_handler))
        cmd = registry.get("exit")
        completions = registry.completions_for("/ex")
    """

    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}

    def register(self, cmd: Command) -> None:
        self._commands[cmd.name] = cmd
        for alias in cmd.aliases:
            self._commands[alias] = cmd

    def get(self, name: str) -> Command | None:
        """Look up by name (without leading slash)."""
        return self._commands.get(name.lstrip("/"))

    def completions_for(self, prefix: str) -> list[Command]:
        """Return commands whose name starts with prefix (after stripping /).

        Returns unique commands (aliases do not produce duplicate entries).
        """
        clean = prefix.lstrip("/").lower()
        seen: set[str] = set()
        results: list[Command] = []
        for name, cmd in self._commands.items():
            if name.startswith(clean) and cmd.name not in seen:
                seen.add(cmd.name)
                results.append(cmd)
        return results

    def all_commands(self) -> list[Command]:
        """All unique registered commands (no duplicates from aliases)."""
        seen: set[str] = set()
        result: list[Command] = []
        for cmd in self._commands.values():
            if cmd.name not in seen:
                seen.add(cmd.name)
                result.append(cmd)
        return result
```

### `src/ui/commands/builtin.py`

```python
"""Built-in slash commands for arc-tui.

Each handler is an async function: async def handler(app, args: str) -> None.
Handlers may call service methods, push screens, or update settings.
"""
from __future__ import annotations

from typing import TYPE_CHECKING
from ui.commands.registry import Command, CommandRegistry

if TYPE_CHECKING:
    from ui.app import ArcApp


async def _cmd_exit(app: "ArcApp", args: str) -> None:
    await app.service.close()
    app.exit()


async def _cmd_pause(app: "ArcApp", args: str) -> None:
    await app.service.pause()
    app.notify("Paused")


async def _cmd_resume(app: "ArcApp", args: str) -> None:
    await app.service.resume()
    app.notify("Resumed")


async def _cmd_cancel(app: "ArcApp", args: str) -> None:
    await app.service.cancel_current_turn()
    app.notify("Turn cancelled")


async def _cmd_clear(app: "ArcApp", args: str) -> None:
    from ui.widgets.chat_log import ChatLog
    log = app.screen.query_one(ChatLog)
    log.query_one("#chat-richlog").clear()
    app.notify("Chat log cleared")


async def _cmd_help(app: "ArcApp", args: str) -> None:
    """Show the command palette (Phase 0083k). For now, notify."""
    app.notify(
        "Commands: /exit /pause /resume /cancel /clear /theme /set /settings /help",
        timeout=4,
    )


async def _cmd_theme(app: "ArcApp", args: str) -> None:
    """List themes or switch to a named theme.

    Usage:
      /theme           → list available themes
      /theme dracula   → switch to dracula theme
      /theme generate  → open theme generator screen (Phase 0083l)
    """
    from ui.app import ArcApp as _App
    parts = args.strip().split()
    if not parts:
        # List available themes (Phase 0083i wires the loader).
        available = getattr(app, "_theme_names", ["default"])
        app.notify("Themes: " + ", ".join(available), timeout=4)
        return
    name = parts[0]
    if name == "generate":
        app.notify("Theme generator coming in Phase 0083l")
        return
    # Phase 0083i wires this to actually reload TCSS.
    app.notify(f"Theme switch to '{name}' — wired in Phase 0083i")


async def _cmd_set(app: "ArcApp", args: str) -> None:
    """Update a setting. Usage: /set <key> <value>"""
    parts = args.strip().split(maxsplit=1)
    if len(parts) < 2:
        app.notify("Usage: /set <key> <value>", severity="warning")
        return
    key, value = parts
    # Phase 0083j wires this to the SettingsStore.
    app.notify(f"Set {key}={value!r} — wired in Phase 0083j")


async def _cmd_settings(app: "ArcApp", args: str) -> None:
    """Open the settings modal. Wired in Phase 0083j."""
    app.notify("Settings modal — coming in Phase 0083j")


def build_registry() -> CommandRegistry:
    """Create and populate the default CommandRegistry."""
    reg = CommandRegistry()
    cmds = [
        Command("exit",     "Exit arc-tui",              _cmd_exit,     aliases=["quit"]),
        Command("pause",    "Pause the running turn",    _cmd_pause),
        Command("resume",   "Resume a paused turn",      _cmd_resume),
        Command("cancel",   "Cancel the running turn",   _cmd_cancel),
        Command("clear",    "Clear the chat log",        _cmd_clear),
        Command("help",     "Show command list",         _cmd_help),
        Command("theme",    "List or switch themes",     _cmd_theme),
        Command("set",      "Update a setting",          _cmd_set,
                usage="/set <key> <value>"),
        Command("settings", "Open settings modal",       _cmd_settings),
    ]
    for cmd in cmds:
        reg.register(cmd)
    return reg


# Module-level singleton — imported by the App.
DEFAULT_REGISTRY = build_registry()
```

### `src/ui/widgets/input_box.py`

```python
"""InputBox — multi-line input widget with slash-command autocomplete.

Key bindings:
  Enter          → insert newline
  Ctrl+Enter     → submit the message (or /command)
  Escape         → clear input (or close autocomplete popup)
  Ctrl+Up        → previous history entry
  Ctrl+Down      → next history entry
  /              → trigger autocomplete popup when at start of line

Slash command autocomplete:
  Typing /ex shows a popup of matching commands.
  Tab or arrow keys navigate; Enter selects.
"""
from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

try:
    from textual.app import ComposeResult
    from textual.binding import Binding
    from textual.events import Key
    from textual.widgets import TextArea, ListView, ListItem, Label
    from textual.widget import Widget
    from textual import on
except ImportError as exc:
    raise ImportError("Textual not installed") from exc

if TYPE_CHECKING:
    from ui.app import ArcApp

from ui.commands.registry import CommandRegistry, Command


_MAX_HISTORY = 100


class CompletionPopup(Widget):
    """Floating completion list shown above the InputBox when typing a slash command."""

    DEFAULT_CSS = """
    CompletionPopup {
        background: $bg-elevated;
        border: round $primary;
        height: auto;
        max-height: 8;
        width: 40;
        overflow-y: auto;
    }
    CompletionPopup ListItem {
        padding: 0 1;
    }
    CompletionPopup ListItem.--highlight {
        background: $primary;
        color: $bg;
    }
    """

    def __init__(self, commands: list[Command], **kwargs) -> None:
        super().__init__(**kwargs)
        self._commands = commands

    def compose(self) -> ComposeResult:
        items = [
            ListItem(Label(f"/{cmd.name}  [dim]{cmd.description}[/dim]"))
            for cmd in self._commands
        ]
        lv = ListView(*items)
        yield lv

    def selected_command(self) -> Command | None:
        lv = self.query_one(ListView)
        idx = lv.index
        if idx is not None and 0 <= idx < len(self._commands):
            return self._commands[idx]
        return None


class InputBox(Widget):
    """Multi-line input with slash-command autocomplete and history.

    Emits a custom `Submit` message when the user presses Ctrl+Enter.
    The parent screen handles Submit to call service.send() or dispatch a command.
    """

    class Submit(Widget.Message):
        """Posted when the user submits the input (Ctrl+Enter)."""
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    DEFAULT_CSS = """
    InputBox {
        height: auto;
        min-height: 3;
        max-height: 8;
        background: $surface;
        border-top: solid $primary;
    }
    InputBox TextArea {
        background: $surface;
        color: $text;
        border: none;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("ctrl+enter", "submit", "Submit", show=True),
        Binding("ctrl+up",    "history_prev", "Prev history"),
        Binding("ctrl+down",  "history_next", "Next history"),
        Binding("escape",     "clear_or_close", "Clear/Close"),
    ]

    def __init__(self, registry: CommandRegistry, **kwargs) -> None:
        super().__init__(**kwargs)
        self._registry = registry
        self._history: deque[str] = deque(maxlen=_MAX_HISTORY)
        self._history_idx: int = -1
        self._completion_popup: CompletionPopup | None = None

    def compose(self) -> ComposeResult:
        yield TextArea(id="text-area")

    def _text_area(self) -> TextArea:
        return self.query_one("#text-area", TextArea)

    @property
    def text(self) -> str:
        return self._text_area().text

    # ── Key handling ──────────────────────────────────────────────────────────

    def on_text_area_changed(self, event) -> None:
        """Update completion popup when text changes."""
        text = self._text_area().text
        # Show completion only when input starts with / and has no newlines.
        if text.startswith("/") and "\n" not in text:
            self._show_completions(text)
        else:
            self._hide_completions()

    def _show_completions(self, prefix: str) -> None:
        matches = self._registry.completions_for(prefix)
        if not matches:
            self._hide_completions()
            return
        if self._completion_popup is not None:
            self.remove(self._completion_popup)
        popup = CompletionPopup(matches, id="completion-popup")
        self._completion_popup = popup
        self.mount(popup, before=self.query_one(TextArea))

    def _hide_completions(self) -> None:
        if self._completion_popup is not None:
            self._completion_popup.remove()
            self._completion_popup = None

    # ── Actions ───────────────────────────────────────────────────────────────

    def action_submit(self) -> None:
        text = self._text_area().text.strip()
        if not text:
            return
        self._hide_completions()
        self._history.appendleft(text)
        self._history_idx = -1
        self._text_area().clear()
        self.post_message(self.Submit(text))

    def action_history_prev(self) -> None:
        if not self._history:
            return
        self._history_idx = min(self._history_idx + 1, len(self._history) - 1)
        self._text_area().load_text(self._history[self._history_idx])

    def action_history_next(self) -> None:
        if self._history_idx <= 0:
            self._history_idx = -1
            self._text_area().clear()
            return
        self._history_idx -= 1
        self._text_area().load_text(self._history[self._history_idx])

    def action_clear_or_close(self) -> None:
        if self._completion_popup is not None:
            self._hide_completions()
        else:
            self._text_area().clear()
```

### `src/ui/screens/chat.py` — modifications for Phase 0083h

Add `InputBox` and message queue handling to `ChatScreen`:

```python
# Additional imports
from ui.widgets.input_box import InputBox
from ui.commands.registry import CommandRegistry
from ui.commands.builtin import DEFAULT_REGISTRY
from collections import deque

class ChatScreen(Screen):
    ...

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._message_queue: deque[str] = deque()
        self._registry: CommandRegistry = DEFAULT_REGISTRY

    def compose(self) -> ComposeResult:
        yield Static("arc-tui  |  idle", id="status-bar")
        yield ChatLog(id="chat-log")
        yield InputBox(registry=self._registry, id="input-box")

    @on(InputBox.Submit)
    async def on_submit(self, event: InputBox.Submit) -> None:
        text = event.text.strip()
        if not text:
            return

        # Slash command dispatch
        if text.startswith("/"):
            parts = text[1:].split(maxsplit=1)
            name = parts[0] if parts else ""
            args = parts[1] if len(parts) > 1 else ""
            cmd = self._registry.get(name)
            if cmd:
                await cmd.handler(self.app, args)
                return
            else:
                self.app.notify(f"Unknown command: /{name}", severity="warning")
                return

        # Regular message
        if self.app.service.is_busy:
            # Queue with visual badge
            self._message_queue.append(text)
            self._chat_log().add_queued_badge(text)
            return

        await self._send_message(text)

    async def _send_message(self, text: str) -> None:
        await self.app.service.send(text)
        # After turn completes, drain the message queue.
        # The event handler for turn.completed calls _drain_queue().

    def handle_agent_event(self, event: AgentEvent) -> None:
        # ... existing dispatch ...
        if event.type in ("turn.completed", "turn.cancelled", "turn.failed"):
            self._status().update("arc-tui  |  idle")
            # Drain queued messages.
            self.call_after_refresh(self._drain_queue)

    async def _drain_queue(self) -> None:
        if self._message_queue and not self.app.service.is_busy:
            next_msg = self._message_queue.popleft()
            await self._send_message(next_msg)
```

Add `add_queued_badge()` to `ChatLog`:

```python
def add_queued_badge(self, text: str) -> None:
    """Show a 'queued' indicator for a message that will send after the current turn."""
    preview = text[:60] + "..." if len(text) > 60 else text
    self._log().write(Text(f"[queued] {preview}", style="dim yellow"))
```

## Verification

```bash
# 1. Existing tests pass
pytest -x -q

# 2. Manual: launch arc-tui
#    - Type a message, press Ctrl+Enter → message is sent
#    - Type /pause, press Ctrl+Enter → "Paused" notification appears
#    - Type /ex, observe completion popup showing /exit
#    - Type while agent is running → "queued" badge appears; fires after turn
#    - Ctrl+Up navigates history; Ctrl+Down goes forward
#    - Paste a multi-line block (Textual handles bracketed paste) → appears intact
```

## Done when

- [ ] `InputBox` composes with a `TextArea` and submits on Ctrl+Enter.
- [ ] Slash commands that start with `/` are routed to the registry; unknown commands show a warning.
- [ ] History navigation works (Ctrl+Up / Ctrl+Down).
- [ ] Completion popup appears when typing `/` and disappears on Escape or selection.
- [ ] Messages submitted while the service is busy are queued and fire after the turn completes.
- [ ] Queued messages are shown in the chat log with a yellow `[queued]` badge.
- [ ] All built-in commands (`/exit`, `/pause`, `/resume`, `/cancel`, `/clear`, `/help`) execute without error.
- [ ] `pytest` green.

## Out of scope for this phase

- Nested completion (e.g., `/set <key>` tab-completion of setting keys) — deferred.
- Command palette modal (Phase 0083k).
- ESC → pause keybinding full wiring (partially wired in 0083g, complete in this phase via `service.pause()`).
