"""Replay section — wraps tui/replay_menu.py."""
from __future__ import annotations

from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import FormattedTextControl

from arc.setup.sections import Section


def _sessions_dir(ctx):
    return ctx.home / "sessions"


def _format_text(ctx) -> list:
    out: list = []
    sd = _sessions_dir(ctx)
    n = 0
    if sd.exists():
        try:
            n = sum(1 for p in sd.iterdir() if p.is_dir())
        except Exception:
            n = 0
    out.append(("class:hub.label", "  sessions  "))
    out.append(("class:hub.value", f"{n} recorded\n"))
    out.append(("class:hub.label", "  dir       "))
    out.append(("class:hub.value", f"{sd}\n"))
    out.append(("", "\n"))
    out.append(("class:hub.accent", "  [ ⏎ open replay menu ]\n"))
    out.append(("", "\n"))
    out.append(("class:hub.dim",
                "  pick a session, mode (deterministic vs live), provider/model,\n"
                "  optional batch targets, and max cost — see 0019.\n"))
    return out


def build(ctx) -> Section:
    kb = KeyBindings()

    @kb.add("enter")
    def _(event):
        _run_menu(ctx)

    control = FormattedTextControl(
        lambda: _format_text(ctx),
        focusable=True,
        key_bindings=kb,
        show_cursor=False,
    )
    container = Window(content=control, style="class:hub.content")

    def summary() -> str:
        sd = _sessions_dir(ctx)
        if not sd.exists():
            return "no sessions yet"
        try:
            return f"{sum(1 for p in sd.iterdir() if p.is_dir())} sessions"
        except Exception:
            return "—"

    return Section(
        name="replay",
        title="Replay",
        summary=summary,
        container=container,
        focusable=True,
    )


def _run_menu(ctx) -> None:
    from arc.tui.replay_menu import run_replay_menu

    def _go():
        run_replay_menu(home=ctx.home, sessions_dir=_sessions_dir(ctx))

    if ctx.run_modal is not None:
        ctx.run_modal(_go)
