"""arc-tui — full-screen prompt_toolkit Application entry point.

Architecture:
  - Application(full_screen=True, mouse_support=False)
  - mouse_support=False → terminal handles mouse events natively → click-drag text
    selection works in iTerm2 / Terminal.app
  - Layout: conversation (scrollable) | spinner (conditional) | separator | input | footer
  - ConversationModel stores all formatted text; [SetCursorPosition] drives scroll
  - SpinnerModel animates inline between submitted query and next input
  - Input: Buffer(multiline=True), Enter=submit, Shift+Enter=newline
  - sys.stderr redirected to session log (prevents subprocess warning garble)

Import discipline: never imports from agent.py, runtime/, or tools/.

Submodule responsibilities:
  app_layout.py       — build_app(): widgets, layout, style
  app_keybindings.py  — build_key_bindings(): all key bindings
  app_commands.py     — execute_command(): slash command table
  app_input_router.py — handle_input(): picker/slash/escalation/queue/send
  app_event_bridge.py — consume_events(), spinner_tick(), escalation_watcher()
  app_resume.py       — handle_resume(), handle_resume_selection()
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

try:
    from prompt_toolkit.application import Application  # noqa: F401 — validates install
except ImportError as exc:
    raise ImportError(
        "prompt_toolkit is not installed. Install with: pip install 'arc[tui]'"
    ) from exc

from service.inprocess import InProcessAgentService
from ui.conversation import ConversationModel
from ui.spinner_model import SpinnerModel
from ui.input_model import InputModel

# _STAGE_LABELS is defined in app_event_bridge; re-exported here for discoverability.
from ui.app_event_bridge import _STAGE_LABELS  # noqa: F401


# ── Stderr suppressor ─────────────────────────────────────────────────────────
class _SuppressStderr:
    """Redirect sys.stderr to the session log file during the TUI session.

    Prevents subprocess warnings (HuggingFace tokenizers, Ghidra JVM, etc.)
    from garbling prompt_toolkit's terminal output. All stderr content is
    preserved in the session log for debugging.
    """

    def __init__(self, session_dir: str) -> None:
        log_path = Path(session_dir) / "logs" / "stderr.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = open(log_path, "a", buffering=1, encoding="utf-8", errors="replace")
        self._orig = sys.stderr

    def __enter__(self):
        sys.stderr = self._log
        return self

    def __exit__(self, *_):
        sys.stderr = self._orig
        self._log.close()


# ── Main interactive loop ─────────────────────────────────────────────────────

async def _interactive(
    service: InProcessAgentService,
    info,
    args: argparse.Namespace,
) -> None:
    from ui.app_layout import build_app
    from ui.app_event_bridge import consume_events, spinner_tick, escalation_watcher
    from ui.app_resume import handle_resume

    conv = ConversationModel()
    spinner = SpinnerModel()
    input_model = InputModel()
    input_model.escalation_gate = getattr(service, "user_gate", None)
    input_model.input_gate = getattr(service, "input_gate", None)
    input_model.session_id = info.session_id
    app_state: dict = {}

    # Populate the welcome banner — ARC logo, session info, command list.
    # Visible inside the TUI as the first thing in the conversation area.
    conv.add_welcome(info.session_id, info.session_dir, info.provider_line)

    # Build the prompt_toolkit Application
    app, input_buf = build_app(conv, spinner, input_model, service, app_state)
    app_state["app"] = app

    # Handle --resume: show session list and arm picker mode before UI starts.
    if args.resume is not None:
        await handle_resume(service, conv, input_model, app_state)

    # Background tasks run concurrently with the TUI event loop
    event_task = asyncio.create_task(
        consume_events(service, conv, spinner, input_model, app_state)
    )
    spinner_task = asyncio.create_task(spinner_tick(spinner, app_state))
    escalation_task = asyncio.create_task(
        escalation_watcher(input_model, conv, app_state)
    )

    with _SuppressStderr(info.session_dir):
        await app.run_async()

    # Graceful shutdown of all background tasks
    for task in (event_task, spinner_task, escalation_task):
        task.cancel()
    try:
        await asyncio.wait_for(
            asyncio.gather(event_task, spinner_task, escalation_task, return_exceptions=True),
            timeout=1.0,
        )
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass

    await service.close()

    # Print the exit banner BEFORE finalize_session — when a JVM was started
    # during this session, finalize_session calls os._exit(0) to force-kill
    # the process, and anything printed after that won't appear.
    w = 52
    print(f"\n{'─' * w}")
    print(f"  Session ended  |  ID: {info.session_id}")
    print(f"{'─' * w}\n")

    from service.builder import finalize_session
    finalize_session(info.session_id)


# The pre-alt-screen banner was removed — ConversationModel.add_welcome now
# renders the landing screen inside the TUI itself (visible during the session,
# not just flashed before the alt-screen activates).


# ── Headless mode ─────────────────────────────────────────────────────────────

async def _headless(service: InProcessAgentService, message: str) -> None:
    await service.send(message)
    streaming = False
    async for event in service.events():
        if event.type == "content.token_chunk":
            sys.stdout.write(getattr(event, "text", ""))
            sys.stdout.flush()
            streaming = True
        elif event.type == "content.message_complete":
            if not streaming:
                print(getattr(event, "text", ""))
            else:
                print()
            break
        elif event.type in ("turn.failed", "turn.cancelled"):
            print(f"\nError: {getattr(event, 'error', 'cancelled')}", file=sys.stderr)
            break


# ── Entry point ───────────────────────────────────────────────────────────────

async def _run_async(args: argparse.Namespace) -> None:
    from service.builder import build_service, ServiceOptions, finalize_session
    bundle = build_service(ServiceOptions(verbose=False))
    service = bundle.service
    info = bundle.info

    if args.print:
        await _headless(service, args.print)
        await service.close()
        finalize_session(info.session_id)
        return

    await _interactive(service, info, args)


def run(argv: list[str] | None = None) -> None:
    """Entry point for arc-tui (pyproject.toml [project.scripts])."""
    parser = argparse.ArgumentParser(prog="arc-tui", description="arc agent — terminal UI")
    parser.add_argument(
        "--print", metavar="MSG", default=None,
        help="Headless: run one turn, print response, exit",
    )
    parser.add_argument(
        "--resume", nargs="?", const="__pick__", default=None,
        help="Resume a prior session",
    )
    args = parser.parse_args(argv)
    try:
        asyncio.run(_run_async(args))
    except KeyboardInterrupt:
        pass
