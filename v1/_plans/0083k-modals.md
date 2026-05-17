# 0083k — Resume picker + command palette modals

> **Read first:** `_plans/0083-decoupled-ui-textual.md` §10 phase 0083k description.
> Depends on: **0083h** (InputBox + commands), **0083j** (settings store).

## Goal

Two modal screens:

1. **`ResumePickerScreen`** — replaces `_pick_resume_session()` from `main.py`
   in the TUI flow. Shows resumable sessions in a scrollable list; selection
   loads the session. Launched by `arc-tui --resume` or the `/resume` command.

2. **`CommandPaletteScreen`** — Ctrl+K fuzzy search over all slash commands.
   The VS Code-style command palette for discoverability.

The legacy CLI (`main.py`) keeps its inline `_pick_resume_session()` function
unchanged — the TUI replacement is additive, not a modification of the existing
flow.

## Files to create / modify

| File | Action |
|------|--------|
| `src/ui/screens/resume_picker.py` | **Create** — session picker modal |
| `src/ui/screens/command_palette.py` | **Create** — fuzzy command search modal |
| `src/ui/app.py` | **Modify** — launch `ResumePickerScreen` when `--resume` is passed |
| `src/ui/commands/builtin.py` | **Modify** — wire `/resume` to modal; `/help` to command palette |

## Detailed implementation

### `src/ui/screens/resume_picker.py`

```python
"""ResumePickerScreen — interactive session picker for arc-tui.

Shown when:
  - User launches with `arc-tui --resume`
  - User runs the /resume slash command

Displays a scrollable list of recent sessions. Selecting one dismisses the
modal with the session_id; cancelling (Escape) dismisses with None.

The actual session loading (calling store.load_session()) happens in the
caller (ArcApp or the /resume handler) after the modal returns.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

try:
    from textual.app import ComposeResult
    from textual.screen import ModalScreen
    from textual.widgets import DataTable, Footer, Label, Static
    from textual import on
except ImportError as exc:
    raise ImportError("Textual not installed") from exc


class ResumePickerScreen(ModalScreen[Optional[str]]):
    """Modal that returns the selected session_id or None on cancel.

    Type parameter: Optional[str] — the return value passed to dismiss().
    """

    CSS = """
    ResumePickerScreen {
        align: center middle;
    }
    #picker-dialog {
        width: 80;
        height: 24;
        background: $bg-elevated;
        border: round $primary;
        padding: 1;
    }
    #picker-title {
        text-style: bold;
        color: $primary;
        margin-bottom: 1;
    }
    #session-table {
        height: 1fr;
    }
    #picker-hint {
        color: $text-dim;
        height: 1;
        margin-top: 1;
    }
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("enter", "select_row", "Select"),
    ]

    def __init__(self, sessions: list[dict]) -> None:
        """
        Args:
            sessions: List of session dicts. Each dict has at minimum:
                session_id: str
                started_at: float (unix timestamp)
                preview: str (first user message preview)
                artifact_count: int
        """
        super().__init__()
        self._sessions = sessions

    def compose(self) -> ComposeResult:
        from textual.containers import Vertical
        with Vertical(id="picker-dialog"):
            yield Static("Resume a session", id="picker-title")
            yield DataTable(id="session-table", cursor_type="row")
            yield Static(
                "Enter to resume  •  Escape to start a new session",
                id="picker-hint",
            )

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("Date/Time", "Preview", "Artifacts")
        for s in self._sessions:
            ts = datetime.fromtimestamp(s.get("started_at", 0)).strftime("%b %d %H:%M")
            preview = (s.get("preview") or "(no preview)")[:50]
            count = str(s.get("artifact_count", 0))
            table.add_row(ts, preview, count, key=s.get("session_id", ""))

    @on(DataTable.RowSelected)
    def on_row_selected(self, event: DataTable.RowSelected) -> None:
        session_id = str(event.row_key.value) if event.row_key else None
        self.dismiss(session_id)

    def action_select_row(self) -> None:
        table = self.query_one(DataTable)
        row_key = table.cursor_row
        if row_key is not None:
            session_id = table.get_row_at(row_key)[0]   # first column — adjust if needed
            # Actually get the row key value:
            # In Textual 8.x, row_key.value holds the key passed to add_row().
            # Check the DataTable API for the correct attribute.
            pass

    def action_cancel(self) -> None:
        self.dismiss(None)
```

**Implementation note:** Textual 8.x `DataTable.RowSelected` event carries
`row_key` which holds the value passed as `key=` to `add_row()`. Use
`event.row_key.value` to get the `session_id`. Confirm the exact attribute
name by reading Textual 8.x docs or source before implementing.

### Wire `--resume` in `ArcApp`

In `_run_async()` in `src/ui/app.py`, after building the service, check
`args.resume`:

```python
async def _run_async(args: argparse.Namespace) -> None:
    ...
    # Build service.
    session_id = generate_id("session")
    init_runtime_events(session_id, project_id="arc-tui")
    agent = Agent(verbose=False)
    service = InProcessAgentService(agent, session_id=session_id)

    if args.print:
        handle = await service.send(args.print)
        await _headless_run(service, args.print)
        await service.close()
        return

    app = ArcApp(service=service)

    if args.resume is not None:
        # Show the resume picker before the main event loop starts.
        # Push it as the first screen; when it dismisses, push ChatScreen.
        from runtime.artifact_store import get_artifact_store, init_store
        from pathlib import Path
        project_root = Path(__file__).resolve().parent.parent.parent
        store_enabled = True   # read from config in real impl
        if store_enabled:
            init_store(
                db_path=project_root / "_store" / "artifacts.db",
                data_dir=project_root / "_store" / "data",
                inline_threshold=8192,
            )
            store = get_artifact_store()
            options = store.list_resumable_sessions(limit=20)
            sessions_dicts = [
                {
                    "session_id": s.session_id,
                    "started_at": s.started_at,
                    "preview": s.preview,
                    "artifact_count": s.artifact_count,
                }
                for s in options
            ]
            app._pending_resume_sessions = sessions_dicts
            app._show_resume_on_mount = True

    await app.run_async()
```

In `ArcApp.on_mount()`:

```python
def on_mount(self) -> None:
    ...
    if getattr(self, "_show_resume_on_mount", False):
        from ui.screens.resume_picker import ResumePickerScreen
        sessions = getattr(self, "_pending_resume_sessions", [])
        self.push_screen(
            ResumePickerScreen(sessions),
            callback=self._on_resume_selected,
        )
    else:
        self.push_screen(ChatScreen())

def _on_resume_selected(self, session_id: str | None) -> None:
    """Called when the ResumePickerScreen dismisses."""
    if session_id:
        # Load the session into the agent (read messages from artifact store).
        # This requires access to the store — wire accordingly.
        self.notify(f"Resuming session {session_id[:8]}...")
        # TODO: call store.load_conversation(session_id) and inject into agent.
    self.push_screen(ChatScreen())
```

### `/resume` slash command

```python
async def _cmd_resume(app: "ArcApp", args: str) -> None:
    """Open the session picker, or resume a specific session by ID."""
    from ui.screens.resume_picker import ResumePickerScreen
    from runtime.artifact_store import get_artifact_store

    parts = args.strip().split()
    if parts:
        # Direct resume by session ID.
        session_id = parts[0]
        app.notify(f"Resuming session {session_id[:8]}...")
        # TODO: load conversation from artifact store.
        return

    # Interactive picker.
    store = get_artifact_store()
    options = store.list_resumable_sessions(limit=20)
    sessions = [
        {"session_id": s.session_id, "started_at": s.started_at,
         "preview": s.preview, "artifact_count": s.artifact_count}
        for s in options
    ]
    if not sessions:
        app.notify("No resumable sessions found.")
        return

    await app.push_screen(ResumePickerScreen(sessions), callback=app._on_resume_selected)
```

### `src/ui/screens/command_palette.py`

```python
"""CommandPaletteScreen — fuzzy search over all slash commands.

Opened with Ctrl+K. Lets the user discover and execute commands without
typing the full slash command name.
"""
from __future__ import annotations

from typing import Optional

try:
    from textual.app import ComposeResult
    from textual.screen import ModalScreen
    from textual.widgets import Input, ListView, ListItem, Label
    from textual import on
except ImportError as exc:
    raise ImportError("Textual not installed") from exc

from ui.commands.registry import Command, CommandRegistry


class CommandPaletteScreen(ModalScreen[Optional[str]]):
    """Fuzzy-search command palette. Returns the selected command name or None."""

    CSS = """
    CommandPaletteScreen {
        align: center middle;
    }
    #palette-dialog {
        width: 60;
        height: 20;
        background: $bg-elevated;
        border: round $primary;
        padding: 1;
    }
    #palette-input {
        margin-bottom: 1;
        border: solid $primary;
    }
    #palette-list {
        height: 1fr;
    }
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(self, registry: CommandRegistry) -> None:
        super().__init__()
        self._registry = registry
        self._all_commands = registry.all_commands()

    def compose(self) -> ComposeResult:
        from textual.containers import Vertical
        with Vertical(id="palette-dialog"):
            yield Input(placeholder="> type to search commands...", id="palette-input")
            yield ListView(id="palette-list")

    def on_mount(self) -> None:
        self._render_list(self._all_commands)
        self.query_one("#palette-input", Input).focus()

    def _render_list(self, commands: list[Command]) -> None:
        lv = self.query_one(ListView)
        lv.clear()
        for cmd in commands:
            lv.append(
                ListItem(
                    Label(f"/{cmd.name}  [dim]{cmd.description}[/dim]"),
                    id=f"cmd-{cmd.name}",
                )
            )

    @on(Input.Changed, "#palette-input")
    def on_search_changed(self, event: Input.Changed) -> None:
        query = event.value.strip().lstrip("/").lower()
        if not query:
            self._render_list(self._all_commands)
            return
        # Simple fuzzy: commands whose name contains the query string.
        matches = [
            cmd for cmd in self._all_commands
            if query in cmd.name.lower() or query in cmd.description.lower()
        ]
        self._render_list(matches)

    @on(ListView.Selected)
    def on_item_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id or ""
        if item_id.startswith("cmd-"):
            cmd_name = item_id[4:]
            self.dismiss(cmd_name)

    def action_cancel(self) -> None:
        self.dismiss(None)
```

Wire Ctrl+K in `ArcApp` and route the result to the command handler:

```python
# In ArcApp
BINDINGS = [
    ("ctrl+k", "command_palette", "Command palette"),
    ("ctrl+comma", "open_settings", "Settings"),
]

async def action_command_palette(self) -> None:
    from ui.screens.command_palette import CommandPaletteScreen
    from ui.commands.builtin import DEFAULT_REGISTRY
    result = await self.push_screen_wait(CommandPaletteScreen(DEFAULT_REGISTRY))
    if result:
        cmd = DEFAULT_REGISTRY.get(result)
        if cmd:
            await cmd.handler(self, "")
```

Update `/help` built-in to open the palette:

```python
async def _cmd_help(app: "ArcApp", args: str) -> None:
    from ui.screens.command_palette import CommandPaletteScreen
    from ui.commands.builtin import DEFAULT_REGISTRY
    result = await app.push_screen_wait(CommandPaletteScreen(DEFAULT_REGISTRY))
    if result:
        cmd = DEFAULT_REGISTRY.get(result)
        if cmd:
            await cmd.handler(app, "")
```

## Verification

```bash
# 1. Existing tests pass
pytest -x -q

# 2. Manual: launch arc-tui --resume
#    - Picker modal appears listing recent sessions
#    - Arrow keys navigate the list
#    - Enter selects and loads the session (or "no sessions" if none exist)
#    - Escape starts a new session

# 3. Manual: in arc-tui, press Ctrl+K
#    - Command palette opens with all commands listed
#    - Typing "th" filters to /theme commands
#    - Enter executes the selected command

# 4. /resume from input box opens the picker

# 5. Import discipline
python - <<'EOF'
import pathlib, sys
for f in pathlib.Path("src/ui").rglob("*.py"):
    src = f.read_text()
    if "from runtime" in src and "TYPE_CHECKING" not in src:
        # Allow runtime imports inside TYPE_CHECKING blocks.
        print(f"Possible violation: {f}")
print("Check complete.")
EOF
```

## Done when

- [ ] `ResumePickerScreen` created; shows sessions in a `DataTable`; selection dismisses with `session_id`.
- [ ] `arc-tui --resume` opens the picker; selecting a session starts the app in that session.
- [ ] `/resume` command opens the picker from within the TUI.
- [ ] `CommandPaletteScreen` created; Ctrl+K opens it; fuzzy search filters commands.
- [ ] Executing a command from the palette dispatches to the correct handler.
- [ ] `/help` opens the command palette.
- [ ] `pytest` green.

## Out of scope for this phase

- Full session-context restoration in the chat log (showing old conversation history).
  The session is loaded at the service/agent level; the chat log in this phase shows
  only new turns started after resuming. Full history rendering is a future UX improvement.
- Sorting / filtering sessions by project in the resume picker.
