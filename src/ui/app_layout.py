"""Layout and widget construction for the arc-tui Application.

Provides build_app() which assembles the prompt_toolkit widgets, layout,
style, and returns a ready-to-run Application plus its input Buffer.
"""
from __future__ import annotations

import asyncio

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding.bindings.emacs import load_emacs_bindings
from prompt_toolkit.key_binding import merge_key_bindings
from prompt_toolkit.layout import Layout, HSplit, Window, ConditionalContainer
from prompt_toolkit.layout.controls import FormattedTextControl, BufferControl
from prompt_toolkit.layout.dimension import Dimension as D
from prompt_toolkit.layout.processors import BeforeInput
from prompt_toolkit.output import ColorDepth
from prompt_toolkit.styles import Style

from service import AgentService
from ui.conversation import ConversationModel
from ui.spinner_model import SpinnerModel
from ui.input_model import InputModel
from ui.app_keybindings import build_key_bindings


def build_app(
    conv: ConversationModel,
    spinner: SpinnerModel,
    input_model: InputModel,
    service: AgentService,
    app_state: dict,
) -> tuple[Application, Buffer]:
    """Construct layout, widgets, and style. Key bindings imported from app_keybindings."""

    # ── Input buffer ──────────────────────────────────────────────────────────
    def _on_accept(buff: Buffer) -> bool:
        from ui.app_input_router import handle_input
        text = buff.text.strip()
        buff.reset()
        # In picker mode, even an empty submission has meaning (= choose #1).
        # Otherwise skip processing entirely when input is blank.
        if text or input_model.pending_session_options is not None:
            asyncio.get_event_loop().create_task(
                handle_input(text, conv, spinner, input_model, service, app_state)
            )
        return True  # True = clear the buffer after accept

    input_buf = Buffer(
        name="input",
        multiline=True,
        accept_handler=_on_accept,
        history=InMemoryHistory(),
    )

    # ── Key bindings ──────────────────────────────────────────────────────────
    kb = build_key_bindings(input_model, conv, service, app_state)

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
    # custom visual line navigation in app_keybindings to compute positions correctly.
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
