"""Rewind mode — the ←/→ turn walker (0026 phase b).

A dedicated inline prompt_toolkit Application with its own key bindings, so
the main session's history/typing behavior is untouched. Not a subprocess
(unlike the 0019 replay menu): this is a one-line status bar + key loop,
no alt screen, and the main PromptSession is not running while it is.

Each ←/→ step PRINTS the turn card you land on — the inline TUI is
append-only, so stepping backwards literally re-reads the conversation in
reverse. Navigation and "scrolling to find the spot" are the same gesture.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


def walk_turns(
    turns: list[Any],
    *,
    print_card: Callable[[int], None],
) -> int | None:
    """Walk the turn list interactively. 1-based positions; starts at the tip.

    ← older · → newer · Enter select · Esc / q / Ctrl+C cancel.

    Returns the selected turn number, or None on cancel. `print_card(i)`
    renders turn i into scrollback (injected so this module stays free of
    Rich/render imports and tests can fake it).
    """
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.layout.controls import FormattedTextControl

    total = len(turns)
    state = {"i": total}

    def _status():
        return [
            ("class:toolbar.turn", f" ⏪ turn {state['i']}/{total} "),
            ("class:toolbar.sep",
             "— ←/→ step · Enter branch here · Esc cancel "),
        ]

    kb = KeyBindings()

    def _step(app, delta: int) -> None:
        j = state["i"] + delta
        if not 1 <= j <= total:
            return
        state["i"] = j
        # run_in_terminal suspends the app's renderer so the card lands
        # cleanly in scrollback above the status line.
        from prompt_toolkit.application import run_in_terminal
        run_in_terminal(lambda: print_card(j))
        app.invalidate()

    @kb.add("left")
    def _(event):
        _step(event.app, -1)

    @kb.add("right")
    def _(event):
        _step(event.app, +1)

    @kb.add("enter")
    def _(event):
        event.app.exit(result=state["i"])

    @kb.add("escape", eager=True)
    def _(event):
        event.app.exit(result=None)

    @kb.add("q")
    def _(event):
        event.app.exit(result=None)

    @kb.add("c-c")
    def _(event):
        event.app.exit(result=None)

    @kb.add("c-d")
    def _(event):
        event.app.exit(result=None)

    from arc.tui.themes import active as _active_theme

    app: Application[int | None] = Application(
        layout=Layout(Window(FormattedTextControl(_status), height=1)),
        key_bindings=kb,
        style=_active_theme().pt_style,
        full_screen=False,
    )
    # Orient: show where the cursor starts (the tip) before any keypress.
    print_card(total)
    return app.run()
