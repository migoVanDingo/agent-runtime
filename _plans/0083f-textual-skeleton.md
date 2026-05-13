# 0083f — Textual app skeleton + ChatScreen

> **Read first:** `_plans/0083-decoupled-ui-textual.md` §4.1.
> Depends on: **0083c** (InProcessAgentService).
> The `[tui]` extra (Phase 0083m) is not yet wired — install Textual manually
> for this phase: `pip install textual`.

## Goal

Create the Textual application skeleton:
- `ui/app.py` — `App` subclass, entry point `run(args)`, headless mode
- `ui/screens/chat.py` — `ChatScreen` (placeholder widgets, event type log)
- Wire `service.events()` subscription to a dispatcher task
- `arc-tui` launches the TUI showing event types as they fire
- Headless mode: `arc-tui --print "message"` runs one turn, prints, exits

No real chat rendering yet — that's Phase 0083g. The chat area just shows raw
event type strings during this phase. This milestone lets the implementer
confirm the architecture "feels right" before going further.

## Files to create

| File | Purpose |
|------|---------|
| `src/ui/__init__.py` | Package marker |
| `src/ui/app.py` | `ArcApp` (App subclass) + `run()` entry point + headless mode |
| `src/ui/screens/__init__.py` | Package marker |
| `src/ui/screens/chat.py` | `ChatScreen` — placeholder chat screen |
| `src/ui/widgets/__init__.py` | Package marker |

## Dependency guard

Every file in `src/ui/` must guard against Textual not being installed:

```python
# At the top of any file that imports from textual:
try:
    from textual.app import App, ComposeResult
    # ... other textual imports
except ImportError as exc:
    raise ImportError(
        "Textual is not installed. Install with: pip install 'arc[tui]'"
    ) from exc
```

This ensures that importing `service/` or `runtime/` from a web container
(where Textual is not installed) never fails.

## Detailed implementation

### `src/ui/app.py`

```python
"""Textual TUI application entry point.

Exports:
  run(args) — the arc-tui entry point; called by the pyproject.toml script.

Architecture:
  ArcApp holds a reference to AgentService (passed in at construction).
  On mount, it starts an async task that drains service.events() and
  dispatches each event to the active screen's handle_agent_event() method.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import os

try:
    from textual.app import App, ComposeResult
    from textual.widgets import Footer, Header
except ImportError as exc:
    raise ImportError(
        "Textual is not installed. Install the [tui] extra: pip install 'arc[tui]'"
    ) from exc

# Service imports — must not import from runtime/ or agent.py directly.
from service import AgentService, AgentEvent
from service.inprocess import InProcessAgentService


class ArcApp(App):
    """The main Textual application.

    Holds the AgentService and manages the event-dispatch loop.
    The active screen receives events via handle_agent_event().
    """

    TITLE = "arc"
    SUB_TITLE = "agent runtime"

    # CSS is minimal for the skeleton — full themes in Phase 0083i.
    CSS = """
    Screen {
        background: #1e1e1e;
        color: #d4d4d4;
    }
    #event-log {
        height: 1fr;
        background: #252526;
        color: #858585;
        padding: 1;
    }
    #status {
        height: 1;
        background: #007acc;
        color: #ffffff;
        padding: 0 1;
    }
    """

    def __init__(self, service: AgentService, **kwargs) -> None:
        super().__init__(**kwargs)
        self.service = service

    def on_mount(self) -> None:
        """Start the event dispatch loop when the app mounts."""
        self._event_task = asyncio.create_task(self._dispatch_events())

    async def _dispatch_events(self) -> None:
        """Drain service.events() and forward each to the active screen."""
        async for event in self.service.events():
            # Let the currently active screen handle each event.
            # Screens that care about agent events implement handle_agent_event().
            screen = self.screen
            if hasattr(screen, "handle_agent_event"):
                try:
                    screen.handle_agent_event(event)
                except Exception:
                    pass  # never let event dispatch crash the app

    async def on_unmount(self) -> None:
        """Clean up the event task and shut down the service."""
        if hasattr(self, "_event_task"):
            self._event_task.cancel()
        await self.service.close()


# ── Headless mode ──────────────────────────────────────────────────────────────

async def _headless_run(service: InProcessAgentService, message: str) -> None:
    """Run one turn, print the response to stdout, exit.

    This is the arc-tui --print "..." mode. Useful for scripting.
    Streams tokens to stdout as they arrive; final newline after completion.
    """
    streaming_started = False

    async for event in service.events():
        if event.type == "content.token_chunk":
            text = getattr(event, "text", "")
            if not streaming_started:
                streaming_started = True
            print(text, end="", flush=True)
        elif event.type == "content.message_complete":
            if not streaming_started:
                # No streaming — print the full text now.
                print(getattr(event, "text", ""))
            else:
                print()  # final newline
            break
        elif event.type in ("turn.failed", "turn.cancelled"):
            err = getattr(event, "error", "") or getattr(event, "at_stage", "")
            print(f"\nError: {err}", file=sys.stderr)
            break


async def _run_async(args: argparse.Namespace) -> None:
    """Async entry point — builds the service and runs headless or TUI."""
    # Bootstrap the agent and service.
    # Import agent here (not at module top) to keep ui/ free of runtime/ imports.
    from agent import Agent
    from runtime.events import init_runtime_events
    from utils import generate_id

    session_id = generate_id("session")
    init_runtime_events(session_id, project_id="arc-tui")

    agent = Agent(verbose=False)
    service = InProcessAgentService(agent, session_id=session_id)

    if args.print:
        # Headless mode: send one message, print response, exit.
        handle = await service.send(args.print)
        await _headless_run(service, args.print)
        await service.close()
        return

    # Interactive TUI mode.
    # NOTE: Assumed push_screen(ChatScreen()) is the right startup path for
    # Textual 8.x. Confirm with `textual --version` output if behavior differs.
    from ui.screens.chat import ChatScreen
    app = ArcApp(service=service)
    await app.run_async()


def run(argv: list[str] | None = None) -> None:
    """Entry point for arc-tui. Called by pyproject.toml [project.scripts]."""
    parser = argparse.ArgumentParser(
        prog="arc-tui",
        description="arc agent — Textual TUI",
    )
    parser.add_argument(
        "--print",
        metavar="MESSAGE",
        default=None,
        help="Headless mode: run one turn, print the response, and exit.",
    )
    parser.add_argument(
        "--resume",
        nargs="?",
        const="__pick__",
        default=None,
        help="Resume a prior session (interactive picker if no ID given).",
    )
    args = parser.parse_args(argv)

    try:
        asyncio.run(_run_async(args))
    except KeyboardInterrupt:
        pass
```

### `src/ui/screens/chat.py`

The skeleton `ChatScreen` for this phase. A `RichLog` widget displays
raw event type strings as they arrive. Real chat rendering comes in Phase 0083g.

```python
"""ChatScreen — primary screen of the ArcTUI.

Skeleton implementation for Phase 0083f. Displays raw event type strings
from the service in a RichLog widget. Full rendering is added in Phase 0083g.
"""
from __future__ import annotations

try:
    from textual.app import ComposeResult
    from textual.screen import Screen
    from textual.widgets import RichLog, Static
except ImportError as exc:
    raise ImportError("Textual not installed") from exc

from service.events import AgentEvent


class ChatScreen(Screen):
    """The main chat screen. Renders event types during Phase 0083f skeleton."""

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield Static("arc-tui [skeleton]", id="status")
        yield RichLog(id="event-log", highlight=True, markup=True)

    def on_mount(self) -> None:
        self.query_one("#event-log", RichLog).write(
            "[dim]Waiting for events…[/dim]"
        )

    def handle_agent_event(self, event: AgentEvent) -> None:
        """Called by ArcApp._dispatch_events() for each event."""
        log = self.query_one("#event-log", RichLog)
        t = event.type
        turn = getattr(event, "turn_id", None) or "-"

        if t == "content.token_chunk":
            # Don't log every token — just write the text inline.
            log.write(getattr(event, "text", ""), end="")
            return

        color_map = {
            "turn.started":             "green",
            "turn.completed":           "green",
            "turn.failed":              "red",
            "turn.cancelled":           "yellow",
            "stage.started":            "cyan",
            "stage.completed":          "cyan",
            "content.message_complete": "blue",
            "tool.call.started":        "magenta",
            "tool.call.completed":      "magenta",
            "session.started":          "white",
            "session.ended":            "white",
        }
        color = color_map.get(t, "dim")
        log.write(f"[{color}]{t}[/{color}]  [dim]turn={turn}[/dim]")

    def action_quit(self) -> None:
        self.app.exit()
```

### Wiring `ChatScreen` as the default screen

In `ArcApp`, add `SCREENS` and push `ChatScreen` on mount:

```python
# In ArcApp, after the CSS block:
from ui.screens.chat import ChatScreen

class ArcApp(App):
    SCREENS = {"chat": ChatScreen}

    def on_mount(self) -> None:
        self.push_screen(ChatScreen())
        self._event_task = asyncio.create_task(self._dispatch_events())
```

## Manual test for this phase

There is no automated test for the TUI itself. Verify manually:

```bash
# 1. Launch the TUI (requires API key and Textual installed)
arc-tui
# Expected: terminal clears, shows "[skeleton]" header + "Waiting for events…"

# 2. Headless mode
arc-tui --print "what is 2+2"
# Expected: prints "4" to stdout and exits

# 3. Import guard (no Textual in a clean venv without [tui])
pip install .   # without [tui]
python -c "from service import AgentService"  # must succeed
python -c "from ui.app import run"            # must raise ImportError with helpful message
```

## Verification

```bash
# Service layer still works (no regression)
pytest -x -q

# Headless mode works
arc-tui --print "what is 1+1" | grep -q "2" && echo "PASS" || echo "FAIL"

# Import discipline check (no ui/ → runtime/ imports)
python - <<'EOF'
import ast, sys, pathlib

ui_dir = pathlib.Path("src/ui")
violations = []
for f in ui_dir.rglob("*.py"):
    src = f.read_text()
    if "from runtime" in src or "import runtime" in src:
        violations.append(str(f))
    if "from agent import" in src or "import agent" in src:
        violations.append(str(f))

if violations:
    print("IMPORT VIOLATIONS:", violations)
    sys.exit(1)
else:
    print("Import discipline ok — no ui/ -> runtime/ imports found.")
EOF
```

## Done when

- [ ] `src/ui/__init__.py`, `src/ui/app.py`, `src/ui/screens/__init__.py`,
      `src/ui/screens/chat.py`, `src/ui/widgets/__init__.py` created.
- [ ] `arc-tui` entry point launches (wired in pyproject.toml; see Phase 0083m — for now test with `python src/ui/app.py`).
- [ ] Event types appear in the RichLog widget as turns run.
- [ ] `arc-tui --print "hello"` prints a response and exits without showing the TUI.
- [ ] Importing `ui.app` without Textual installed raises `ImportError` with a helpful message.
- [ ] No `ui/` file imports from `runtime/`, `agent.py`, or `tools/`.
- [ ] `pytest` green.

## Out of scope for this phase

- Real chat bubble rendering (Phase 0083g).
- Input box / slash commands (Phase 0083h).
- Theme system (Phase 0083i).
- Session resume picker (Phase 0083k).
- pyproject.toml `[tui]` extra (Phase 0083m).
