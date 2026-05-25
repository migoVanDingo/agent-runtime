"""Wipe / Reset section — interactive checkbox over wipe targets.

Wraps arc/wipe.py. Lets the user check which kinds of state to delete
(sessions, llm logs, input history, pricing cache) and confirm.
"""
from __future__ import annotations

from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import FormattedTextControl

from arc.setup.sections import Section


_TARGETS = [
    ("sessions",      "sessions/      (recorded events.jsonl + session.log)"),
    ("llm",           "llm/           (llama-server pid file + log)"),
    ("history",       "history        (TUI input-history file)"),
    ("pricing_cache", "pricing_cache  (LiteLLM cost lookup cache)"),
]

_state: dict = {"checked": set(), "focus": 0, "msg": "", "armed": False}


def _format_text(ctx) -> list:
    out: list = []
    out.append(("class:hub.dim", "  space toggle  ⏎ wipe selected\n"))
    out.append(("", "\n"))
    for i, (key, label) in enumerate(_TARGETS):
        mark = "☒" if key in _state["checked"] else "☐"
        mark_style = "class:arc.warning" if key in _state["checked"] else "class:hub.dim"
        line_style = "class:hub.sidebar.item.selected" if i == _state["focus"] else "class:hub.value"
        out.append((mark_style, f"  {mark} "))
        out.append((line_style, f"{label}\n"))

    out.append(("", "\n"))
    if _state["armed"]:
        out.append(("class:arc.warning",
                    f"  press ⏎ again to wipe {len(_state['checked'])} target(s) — esc cancels\n"))
    elif _state["checked"]:
        out.append(("class:hub.dim",
                    f"  {len(_state['checked'])} selected — ⏎ to wipe\n"))
    else:
        out.append(("class:hub.dim", "  nothing selected\n"))

    if _state["msg"]:
        out.append(("", "\n"))
        style = "class:arc.error" if _state["msg"].startswith("error") else "class:arc.success"
        out.append((style, f"  {_state['msg']}\n"))
    return out


def build(ctx) -> Section:
    _state["checked"] = set()
    _state["focus"] = 0
    _state["msg"] = ""
    _state["armed"] = False

    kb = _build_keybindings(ctx)
    control = FormattedTextControl(
        lambda: _format_text(ctx),
        focusable=True,
        key_bindings=kb,
        show_cursor=False,
    )
    container = Window(content=control, style="class:hub.content")

    def summary() -> str:
        n = len(_state["checked"])
        return f"{n} target(s) selected" if n else "no targets selected"

    def on_enter():
        _state["msg"] = ""
        _state["armed"] = False

    return Section(
        name="wipe",
        title="Wipe / Reset",
        summary=summary,
        container=container,
        focusable=True,
        on_enter=on_enter,
    )


def _build_keybindings(ctx) -> KeyBindings:
    kb = KeyBindings()

    @kb.add("up")
    def _(event):
        _state["focus"] = (_state["focus"] - 1) % len(_TARGETS)
        _state["armed"] = False
        if ctx.request_redraw:
            ctx.request_redraw()

    @kb.add("down")
    def _(event):
        _state["focus"] = (_state["focus"] + 1) % len(_TARGETS)
        _state["armed"] = False
        if ctx.request_redraw:
            ctx.request_redraw()

    @kb.add("space")
    def _(event):
        key = _TARGETS[_state["focus"]][0]
        if key in _state["checked"]:
            _state["checked"].discard(key)
        else:
            _state["checked"].add(key)
        _state["armed"] = False
        if ctx.request_redraw:
            ctx.request_redraw()

    @kb.add("enter")
    def _(event):
        if not _state["checked"]:
            _state["msg"] = "nothing selected"
            if ctx.request_redraw:
                ctx.request_redraw()
            return
        if not _state["armed"]:
            _state["armed"] = True
            if ctx.request_redraw:
                ctx.request_redraw()
            return
        _do_wipe(ctx)
        _state["armed"] = False
        if ctx.request_redraw:
            ctx.request_redraw()

    # Only intercept esc while armed — otherwise hub's esc handler runs
    # (which returns focus to the sidebar).
    from prompt_toolkit.filters import Condition

    @kb.add("escape", filter=Condition(lambda: _state["armed"]))
    def _(event):
        _state["armed"] = False
        _state["msg"] = "cancelled"
        if ctx.request_redraw:
            ctx.request_redraw()

    return kb


def _do_wipe(ctx) -> None:
    """Translate the checked set into a wipe.build_plan + execute_plan call."""
    try:
        from arc.wipe import WipeTargets, build_plan, execute_plan
        targets = WipeTargets(
            sessions="sessions" in _state["checked"],
            llm="llm" in _state["checked"],
            history="history" in _state["checked"],
            pricing_cache="pricing_cache" in _state["checked"],
        )
        plan = build_plan(ctx.home, targets)
        if plan.is_noop:
            _state["msg"] = "nothing to remove"
            return
        removed = execute_plan(plan)
        _state["msg"] = f"wiped {len(removed)} path(s)"
        _state["checked"] = set()
    except Exception as exc:
        _state["msg"] = f"error: {exc}"
