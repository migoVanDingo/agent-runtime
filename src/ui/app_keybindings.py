"""Key binding factory for the arc-tui Application.

Provides build_key_bindings() which creates the KeyBindings object for:
- Enter (submit), Escape+Enter / Ctrl+N (newline), Ctrl+D (exit), ESC (pause/resume)
- Visual-line-aware Up/Down/PageUp/PageDown navigation
"""
from __future__ import annotations

import asyncio

from prompt_toolkit.key_binding import KeyBindings

from ui.conversation import ConversationModel
from ui.input_model import InputModel
from service import AgentService


def _prefix_width(input_model: InputModel) -> int:
    """Width of the BeforeInput prompt prefix on the FIRST visual line."""
    gate = input_model.escalation_gate
    igate = getattr(input_model, "input_gate", None)
    if gate and gate.pending_escalation:
        return 16  # "  Allow? [y/n]  "
    if igate and igate.pending_question:
        return 12  # "  Clarify:  "
    return 5      # "  ▶  "


def _effective_width(event, input_model: InputModel) -> int:
    """Number of characters that fit on one visual row of the input."""
    try:
        cols = event.app.output.get_size().columns
    except Exception:
        cols = 80
    return max(cols - _prefix_width(input_model), 20)


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


def build_key_bindings(
    input_model: InputModel,
    conv: ConversationModel,
    service: AgentService,
    app_state: dict,
) -> KeyBindings:
    """Visual-line nav + submit/newline + ESC pause + Ctrl+D exit."""
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

    @kb.add("up")
    def _visual_up(event):
        buf = event.current_buffer
        if buf is None:
            return
        # Empty input → arrows scroll the conversation (intuitive when you
        # have nothing to edit). Non-empty input → cursor navigation within
        # the multiline buffer.
        if not buf.text:
            conv.scroll_up(3)
            event.app.invalidate()
            return
        width = _effective_width(event, input_model)
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
        # Empty input → arrows scroll the conversation.
        if not buf.text:
            conv.scroll_down(3)
            event.app.invalidate()
            return
        width = _effective_width(event, input_model)
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

    return kb
