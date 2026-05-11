"""arc-tui — full-screen prompt_toolkit Application.

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
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

try:
    from prompt_toolkit.application import Application
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.document import Document
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.formatted_text import FormattedText, to_formatted_text, ANSI
    from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
    from prompt_toolkit.key_binding.bindings.emacs import load_emacs_bindings
    from prompt_toolkit.layout import Layout, HSplit, Window, ConditionalContainer
    from prompt_toolkit.layout.controls import FormattedTextControl, BufferControl
    from prompt_toolkit.layout.dimension import Dimension as D
    from prompt_toolkit.layout.processors import BeforeInput
    from prompt_toolkit.output import ColorDepth
    from prompt_toolkit.styles import Style
    from prompt_toolkit.history import InMemoryHistory
except ImportError as exc:
    raise ImportError(
        "prompt_toolkit is not installed. Install with: pip install 'arc[tui]'"
    ) from exc

from service import AgentService
from service.inprocess import InProcessAgentService
from ui.conversation import ConversationModel
from ui.spinner_model import SpinnerModel
from ui.input_model import InputModel

_STAGE_LABELS = {
    "RoutingStage":         "Routing",
    "PlanningStage":        "Planning",
    "ExecutionStage":       "Executing",
    "SynthesizerStage":     "Synthesizing",
    "DirectExecutionStage": "Working",
    "CouncilStage":         "Reviewing",
    "ContinuationStage":    "Evaluating",
    "RagContextStage":      "Memory",
    "EntityCriticStage":    "Entities",
    "ValidatorStage":       "Validating",
    "SkillHintStage":       "Skills",
}


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


# ── Application builder ───────────────────────────────────────────────────────

def _build_app(
    conv: ConversationModel,
    spinner: SpinnerModel,
    input_model: InputModel,
    service: AgentService,
    app_state: dict,
) -> tuple[Application, Buffer]:
    """Build and return the prompt_toolkit Application and input Buffer."""

    # ── Input buffer ──────────────────────────────────────────────────────────
    def _on_accept(buff: Buffer) -> bool:
        text = buff.text.strip()
        buff.reset()
        # In picker mode, even an empty submission has meaning (= choose #1).
        # Otherwise skip processing entirely when input is blank.
        if text or input_model.pending_session_options is not None:
            asyncio.get_event_loop().create_task(
                _handle_input(text, conv, spinner, input_model, service, app_state)
            )
        return True  # True = clear the buffer after accept

    input_buf = Buffer(
        name="input",
        multiline=True,
        accept_handler=_on_accept,
        history=InMemoryHistory(),
    )

    # ── Key bindings ──────────────────────────────────────────────────────────
    kb = KeyBindings()

    @kb.add("enter")
    def _enter(event):
        event.current_buffer.validate_and_handle()

    # Shift+Enter doesn't produce a distinct escape sequence in most terminals
    # so s-enter is not a valid prompt_toolkit key name. Use escape+enter as
    # the newline chord instead — press Esc then Enter to insert a newline.
    @kb.add("escape", "enter")
    def _newline(event):
        event.current_buffer.newline()

    # Also bind ctrl+n as a convenient newline alternative (Ctrl+J is enter).
    @kb.add("c-n")
    def _newline_ctrl(event):
        event.current_buffer.newline()

    @kb.add("c-d")
    def _exit(event):
        app_state["exit"] = True
        event.app.exit()

    @kb.add("c-c")
    def _ignore(event):
        # Ctrl+C is intercepted to prevent abrupt quit; user should use Ctrl+D or /exit
        pass

    @kb.add("escape")
    def _pause_resume(event):
        loop = asyncio.get_event_loop()
        if app_state.get("paused"):
            app_state["paused"] = False
            loop.create_task(service.resume())
        elif service.is_busy:
            app_state["paused"] = True
            loop.create_task(service.pause())

    # ── Visual line navigation for up/down arrows ─────────────────────────────
    # prompt_toolkit's Buffer.auto_up/auto_down work on LOGICAL lines (separated
    # by \n in the text), not visual rows from terminal wrapping. The Buffer has
    # no knowledge of terminal width — that's a Window/render-time concern.
    #
    # To match Claude Code behavior, we compute visual (row, col) ourselves using
    # the current terminal width and the active prompt prefix width. When the
    # user presses up/down, we move the cursor to the corresponding column on
    # the adjacent visual row, handling both wrapping AND embedded newlines.

    def _prefix_width() -> int:
        """Width of the BeforeInput prompt prefix on the FIRST visual line."""
        gate = input_model.escalation_gate
        igate = getattr(input_model, "input_gate", None)
        if gate and gate.pending_escalation:
            return 16  # "  Allow? [y/n]  "
        if igate and igate.pending_question:
            return 12  # "  Clarify:  "
        return 5      # "  ▶  "

    def _effective_width(event) -> int:
        """Number of characters that fit on one visual row of the input."""
        try:
            cols = event.app.output.get_size().columns
        except Exception:
            cols = 80
        return max(cols - _prefix_width(), 20)

    def _pos_to_visual(text: str, pos: int, width: int) -> tuple[int, int]:
        """Convert a character position to (visual_row, visual_col)."""
        row, col = 0, 0
        for i in range(min(pos, len(text))):
            if text[i] == "\n":
                row += 1
                col = 0
            else:
                col += 1
                if col == width:
                    row += 1
                    col = 0
        return row, col

    def _visual_to_pos(text: str, target_row: int, target_col: int, width: int) -> int:
        """Convert (visual_row, visual_col) back to a character position."""
        row, col = 0, 0
        for i in range(len(text)):
            if row == target_row and col == target_col:
                return i
            if text[i] == "\n":
                if row == target_row:
                    return i  # end of target row at newline
                row += 1
                col = 0
            else:
                col += 1
                if col == width:
                    if row == target_row:
                        return i + 1
                    row += 1
                    col = 0
        # Past end of text — clamp
        return len(text)

    @kb.add("up")
    def _visual_up(event):
        buf = event.current_buffer
        if buf is None:
            return
        width = _effective_width(event)
        row, col = _pos_to_visual(buf.text, buf.cursor_position, width)
        if row == 0:
            # At top visual row — fall through to history navigation
            buf.history_backward()
            return
        new_pos = _visual_to_pos(buf.text, row - 1, col, width)
        buf.cursor_position = new_pos

    @kb.add("down")
    def _visual_down(event):
        buf = event.current_buffer
        if buf is None:
            return
        width = _effective_width(event)
        row, col = _pos_to_visual(buf.text, buf.cursor_position, width)
        end_row, _ = _pos_to_visual(buf.text, len(buf.text), width)
        if row >= end_row:
            # At bottom visual row — fall through to history navigation
            buf.history_forward()
            return
        new_pos = _visual_to_pos(buf.text, row + 1, col, width)
        buf.cursor_position = new_pos

    @kb.add("pageup")
    def _pgup(event):
        conv.scroll_up(10)
        event.app.invalidate()

    @kb.add("pagedown")
    def _pgdn(event):
        conv.scroll_down(10)
        event.app.invalidate()

    # ── Layout ────────────────────────────────────────────────────────────────
    # focusable=False: the conversation window never takes keyboard focus.
    # This ensures event.current_buffer always refers to the input buffer when
    # up/down/auto_up/auto_down are called. PageUp/PageDown scroll the conversation
    # via explicit bindings that update the ConversationModel state directly.
    conv_control = FormattedTextControl(
        conv.get_formatted_text,
        focusable=False,
        show_cursor=False,
    )
    # get_line_prefix adds 2 spaces to EVERY visual line — first occurrence AND
    # wrapped continuation. Content in ConversationModel has no leading spaces,
    # so this creates a uniform 2-space left margin across the entire conversation.
    conv_window = Window(
        content=conv_control,
        wrap_lines=True,
        scroll_offsets=None,
        get_line_prefix=lambda lineno, wrap_count: [("", "  ")],
    )

    spinner_window = ConditionalContainer(
        content=Window(
            content=FormattedTextControl(spinner.get_formatted_text),
            height=1,
            dont_extend_height=True,
        ),
        filter=Condition(lambda: spinner.active),
    )

    separator = Window(height=1, char="─", style="ansigray")

    # get_line_prefix on the input window: all visual rows except the very first
    # (line 0, wrap 0) get an indent matching the BeforeInput prompt width. This
    # ensures every visual row has the same effective width — required for the
    # custom visual line navigation in _visual_up / _visual_down to compute
    # positions correctly.
    def _input_line_prefix(lineno: int, wrap_count: int):
        if lineno == 0 and wrap_count == 0:
            return []  # BeforeInput renders the prefix on the very first row
        # Match the width of whichever prompt prefix is currently active.
        gate = input_model.escalation_gate
        igate = getattr(input_model, "input_gate", None)
        if gate and gate.pending_escalation:
            return [("", "                ")]  # 16 chars for "  Allow? [y/n]  "
        if igate and igate.pending_question:
            return [("", "            ")]      # 12 chars for "  Clarify:  "
        if input_model.pending_session_options is not None:
            return [("", "          ")]        # 10 chars for "  Pick #  "
        return [("", "     ")]                 # 5 chars for "  ▶  "

    input_window = Window(
        content=BufferControl(
            buffer=input_buf,
            input_processors=[BeforeInput(input_model.get_prompt_prefix)],
            focusable=True,
        ),
        height=D(min=1, max=5),
        dont_extend_height=True,
        wrap_lines=True,
        get_line_prefix=_input_line_prefix,
    )

    footer = Window(
        content=FormattedTextControl(input_model.get_footer_text),
        height=1,
        dont_extend_height=True,
        style="bg:#1a1a1a",
    )

    layout = Layout(
        HSplit([conv_window, spinner_window, separator, input_window, footer]),
        focused_element=input_buf,
    )

    style = Style.from_dict({
        "completion-menu.completion":         "bg:#252526 #d4d4d4",
        "completion-menu.completion.current": "bg:#007acc #ffffff bold",
    })

    # Merge default Emacs bindings (cursor movement, up/down in multiline buffers,
    # word navigation, etc.) with our custom bindings. Our bindings take priority.
    merged_kb = merge_key_bindings([load_emacs_bindings(), kb])

    app = Application(
        layout=layout,
        key_bindings=merged_kb,
        full_screen=True,
        mouse_support=False,
        color_depth=ColorDepth.TRUE_COLOR,
        style=style,
    )
    return app, input_buf


# ── Command handler ───────────────────────────────────────────────────────────

async def _execute_command(
    name: str,
    args: str,
    conv: ConversationModel,
    spinner: SpinnerModel,
    input_model: InputModel,
    service: AgentService,
    app_state: dict,
) -> None:
    """Handle slash commands directly in the conversation model."""
    if name in ("exit", "quit"):
        app = app_state.get("app")
        if app:
            app.exit()

    elif name == "help":
        conv.add("bold", "\nCommands\n")
        rows = [
            ("/exit, /quit", "End the session"),
            ("/pause",       "Pause the running agent (also ESC)"),
            ("/resume",      "Unpause paused agent OR pick a session to restore"),
            ("/sessions",    "Pick a prior session to restore"),
            ("/cancel",      "Cancel the current turn"),
            ("/clear",       "Clear the screen"),
            ("/settings",    "Show current settings"),
            ("/help",        "Show this help"),
        ]
        for cmd, desc in rows:
            conv.add("ansicyan", f"  {cmd:<20}")
            conv.add("ansigray", f"  {desc}\n")
        conv.add("", "\n")

    elif name == "pause":
        await service.pause()
        app_state["paused"] = True
        conv.add("ansiyellow", "Paused.  /resume or ESC to continue.\n")

    elif name == "resume":
        # Smart routing: if the agent is paused mid-turn, /resume unpauses.
        # Otherwise treat /resume as "show me past sessions to restore."
        if app_state.get("paused"):
            await service.resume()
            app_state["paused"] = False
            conv.add("ansigreen", "Resumed.\n")
        else:
            await _handle_resume(service, conv, input_model, app_state)

    elif name == "sessions":
        # Alias for `/resume` — shows the resumable session picker.
        await _handle_resume(service, conv, input_model, app_state)

    elif name == "cancel":
        await service.cancel_current_turn()
        conv.add("ansiyellow", "Cancelling…\n")

    elif name == "clear":
        # Reset the conversation buffer without touching the model reference
        conv._chunks.clear()
        conv._cursor_idx = 0
        conv._auto_scroll = True

    elif name == "settings":
        try:
            from ui.settings_store import get_settings_store
            s = get_settings_store().load()
            conv.add("bold", "\nSettings\n")
            for k, v in s.model_dump().items():
                conv.add("ansicyan", f"  {k:<20}")
                conv.add("", f"  {v}\n")
            conv.add("", "\n")
        except Exception as e:
            conv.add("ansired", f"Settings error: {e}\n")

    else:
        conv.add("ansiyellow", f"Unknown command: /{name}  (try /help)\n")


# ── Input handler ─────────────────────────────────────────────────────────────

async def _handle_input(
    text: str,
    conv: ConversationModel,
    spinner: SpinnerModel,
    input_model: InputModel,
    service: AgentService,
    app_state: dict,
) -> None:
    app = app_state.get("app")

    # Session picker mode: the user is choosing which session to resume.
    # This MUST be checked before slash commands so that `/exit` etc. don't
    # accidentally get interpreted during picker mode (rare but possible).
    if input_model.pending_session_options is not None:
        await _handle_resume_selection(text, service, conv, input_model)
        if app:
            app.invalidate()
        return

    # Slash command
    if text.startswith("/"):
        parts = text[1:].split(maxsplit=1)
        name = parts[0].lower()
        cmd_args = parts[1] if len(parts) > 1 else ""
        await _execute_command(name, cmd_args, conv, spinner, input_model, service, app_state)
        if app:
            app.invalidate()
        return

    # Escalation response — route y/n to the gate, not the service
    gate = input_model.escalation_gate
    if gate and gate.pending_escalation:
        if text.lower() in ("y", "yes"):
            gate.supply_answer(True)
            conv.add("ansigreen", "✓  Allowed.\n\n")
        elif text.lower() in ("n", "no"):
            gate.supply_answer(False)
            conv.add("ansired", "✗  Denied.\n\n")
        else:
            conv.add("ansiyellow", "Type  y  to allow or  n  to deny\n")
        if app:
            app.invalidate()
        return

    # ASK_USER clarification response — route to TUIInputGate
    igate = getattr(input_model, "input_gate", None)
    if igate and igate.pending_question:
        igate.supply_answer(text)
        conv.add("ansigray", "✓  Clarification provided.\n\n")
        if app:
            app.invalidate()
        return

    # Queue if agent is busy with another turn
    if service.is_busy:
        input_model.queue_message(text)
        conv.add("ansigray", "(queued — will send after current turn)\n")
        if app:
            app.invalidate()
        return

    # Normal message — add user bubble then dispatch to service
    conv.add_user_message(text)
    if app:
        app.invalidate()
    await service.send(text)


# ── Event consumer ────────────────────────────────────────────────────────────

async def _consume_events(
    service: AgentService,
    conv: ConversationModel,
    spinner: SpinnerModel,
    input_model: InputModel,
    app_state: dict,
) -> None:
    """Drain service.events() and update conversation/spinner models accordingly.

    Escalation detection is handled exclusively by _escalation_watcher —
    this coroutine only processes AgentEvents.
    """
    streaming = False

    async for event in service.events():
        app = app_state.get("app")
        t = event.type

        if t == "turn.started":
            streaming = False
            spinner.start("Thinking")

        elif t == "stage.started":
            raw = getattr(event, "stage", "")
            label = _STAGE_LABELS.get(raw, raw.replace("Stage", "") or "Working")
            spinner.update(label)

        elif t == "tool.call.started":
            tool = getattr(event, "tool_name", "tool")
            spinner.update(tool)

        elif t == "content.token_chunk":
            if not streaming:
                spinner.stop()
                conv.begin_agent_response()
                streaming = True
            conv.append_token(getattr(event, "text", ""))

        elif t == "content.message_complete":
            if not streaming:
                spinner.stop()
                conv.begin_agent_response()
            conv.finalize_agent_response(getattr(event, "text", ""))
            streaming = False

        elif t == "turn.completed":
            spinner.stop()
            ms = getattr(event, "elapsed_ms", 0)
            tokens_in = getattr(event, "tokens_in", 0)
            tokens_out = getattr(event, "tokens_out", 0)
            # Accumulate session totals so the footer can show cumulative usage.
            input_model.total_tokens_in += tokens_in
            input_model.total_tokens_out += tokens_out
            if ms or tokens_in or tokens_out:
                conv.add_timer(ms, tokens_in, tokens_out)
            else:
                conv.add("", "\n")
            streaming = False
            # Drain one queued message now that the turn is done
            nxt = input_model.pop_pending()
            if nxt:
                conv.add_user_message(nxt)
                if app:
                    app.invalidate()
                await service.send(nxt)

        elif t == "turn.failed":
            spinner.stop()
            conv.add_error(getattr(event, "error", "unknown error"))
            streaming = False

        elif t == "turn.cancelled":
            spinner.stop()
            conv.add_cancelled()
            streaming = False

        if app:
            app.invalidate()


# ── Spinner animation task ────────────────────────────────────────────────────

async def _spinner_tick(spinner: SpinnerModel, app_state: dict) -> None:
    """Advance the spinner animation frame every 0.4 s and trigger a redraw."""
    while True:
        if spinner.active:
            spinner.tick()
            app = app_state.get("app")
            if app:
                app.invalidate()
        await asyncio.sleep(0.4)


# ── Escalation watcher ────────────────────────────────────────────────────────

async def _escalation_watcher(
    input_model: InputModel,
    conv: ConversationModel,
    app_state: dict,
) -> None:
    """Watch for pending escalations and ASK_USER questions; inject into conversation.

    Runs independently of _consume_events so display is not coupled
    to the event stream cadence.
    """
    shown_esc = None
    shown_q = None
    while True:
        app = app_state.get("app")

        # Escalation gate
        gate = input_model.escalation_gate
        if gate:
            esc = gate.pending_escalation
            if esc is not None and esc is not shown_esc:
                shown_esc = esc
                conv.add_escalation(esc)
                if app:
                    app.invalidate()
            elif esc is None and shown_esc is not None:
                shown_esc = None

        # ASK_USER input gate
        igate = getattr(input_model, "input_gate", None)
        if igate:
            q = igate.pending_question
            if q is not None and q is not shown_q:
                shown_q = q
                conv.add("ansiyellow bold", f"\n❓  {q}\n")
                conv.add("ansigray", "  (Type your clarification and press Enter)\n\n")
                if app:
                    app.invalidate()
            elif q is None and shown_q is not None:
                shown_q = None

        await asyncio.sleep(0.1)


# ── Main interactive loop ─────────────────────────────────────────────────────

async def _interactive(
    service: InProcessAgentService,
    info,
    args: argparse.Namespace,
) -> None:
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
    app, input_buf = _build_app(conv, spinner, input_model, service, app_state)
    app_state["app"] = app

    # Handle --resume: show session list and arm picker mode before UI starts.
    if args.resume is not None:
        await _handle_resume(service, conv, input_model, app_state)

    # Background tasks run concurrently with the TUI event loop
    event_task = asyncio.create_task(
        _consume_events(service, conv, spinner, input_model, app_state)
    )
    spinner_task = asyncio.create_task(_spinner_tick(spinner, app_state))
    escalation_task = asyncio.create_task(
        _escalation_watcher(input_model, conv, app_state)
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
    from service.builder import finalize_session
    finalize_session(info.session_id)

    w = 52
    print(f"\n{'─' * w}")
    print(f"  Session ended  |  ID: {info.session_id}")
    print(f"{'─' * w}\n")


# The pre-alt-screen banner was removed — ConversationModel.add_welcome now
# renders the landing screen inside the TUI itself (visible during the session,
# not just flashed before the alt-screen activates).


# ── Session resume ────────────────────────────────────────────────────────────

async def _handle_resume(
    service: InProcessAgentService,
    conv: ConversationModel,
    input_model: InputModel,
    app_state: dict,
) -> None:
    """Display a table of resumable sessions and arm the input picker mode.

    After rendering, sets input_model.pending_session_options so that the next
    user submission is interpreted as a session selection rather than a chat
    message. _handle_input handles the actual loading.
    """
    try:
        sessions = []
        if hasattr(service, "list_resumable_sessions"):
            sessions = service.list_resumable_sessions(limit=20)
        if not sessions:
            conv.add("ansigray", "\nNo resumable sessions found. Starting fresh.\n\n")
            return

        # ── Render a clean table ──────────────────────────────────────────────
        from datetime import datetime
        conv.add("ansicyan bold", "\nResumable sessions\n")
        conv.add("ansigray", "─" * 76 + "\n")
        # Header
        conv.add("ansigray bold",
                 f"  {'#':<3} {'Session ID':<30} {'Started':<18} {'Preview'}\n")
        conv.add("ansigray", "─" * 76 + "\n")
        for i, s in enumerate(sessions, 1):
            sid = getattr(s, "session_id", "?")
            preview = (getattr(s, "preview", "") or "").strip().replace("\n", " ")
            if len(preview) > 25:
                preview = preview[:22] + "…"
            started = getattr(s, "started_at", 0)
            date_str = (
                datetime.fromtimestamp(started).strftime("%Y-%m-%d %H:%M")
                if started else "       —        "
            )
            conv.add("ansicyan bold", f"  {i:<3}")
            conv.add("", f" {sid[:28]:<30}")
            conv.add("ansigray", f" {date_str:<18}")
            conv.add("", f" {preview}\n")
        conv.add("ansigray", "─" * 76 + "\n\n")

        # Arm the input picker mode — see _handle_input for the routing.
        input_model.pending_session_options = sessions

    except Exception as e:
        conv.add("ansired", f"Resume error: {e}\n\n")


async def _handle_resume_selection(
    text: str,
    service: InProcessAgentService,
    conv: ConversationModel,
    input_model: InputModel,
) -> None:
    """Handle the user's response to the session picker prompt."""
    sessions = input_model.pending_session_options or []
    # Clear picker mode regardless of outcome.
    input_model.pending_session_options = None

    text = text.strip().lower()
    if text in ("q", "quit", "cancel"):
        conv.add("ansigray", "Cancelled — starting fresh.\n\n")
        return

    # Empty submission defaults to #1 (most recent).
    idx = 0
    if text:
        try:
            idx = int(text) - 1
        except ValueError:
            conv.add("ansiyellow",
                     f"Not a valid number — starting fresh.\n\n")
            return

    if not (0 <= idx < len(sessions)):
        conv.add("ansiyellow",
                 f"#{idx + 1} is out of range (1–{len(sessions)}). Starting fresh.\n\n")
        return

    chosen = sessions[idx]
    sid = chosen.session_id
    conv.add("ansigreen", f"✓  Resuming {sid}\n")

    try:
        messages = service.load_conversation(sid)
        if not messages:
            conv.add("ansigray", "  (no prior messages found)\n\n")
            return
        conv.add("ansigray", f"  Loaded {len(messages)} prior message(s)\n\n")
        # Render the loaded conversation so the user sees the context.
        from rich.console import Console
        from rich.markdown import Markdown
        import io
        for msg in messages[-20:]:  # show last 20 to avoid screen flood
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if isinstance(content, list):
                # Anthropic format: list of content blocks; pull text only
                content = "".join(
                    b.get("text", "") for b in content if isinstance(b, dict)
                )
            if not content:
                continue
            if role == "user":
                conv.add_user_message(str(content))
            else:
                conv.add("ansicyan bold", "\nAgent\n")
                conv.add("", str(content) + "\n")
        conv.add("", "\n")
    except Exception as e:
        conv.add("ansired", f"  Failed to load conversation: {e}\n\n")


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
