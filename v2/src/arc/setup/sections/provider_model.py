"""Provider & Model section — wraps setup/picker.py.

The picker uses prompt_toolkit modal dialogs (radiolist_dialog) that block
the hub's event loop. To preserve that flow without rebuilding the
existing tested code, this section shows a read-only summary plus a
"[ Change … ]" hint. Hitting Enter exits the hub temporarily, runs the
modal picker, then re-enters the hub at the same section.

Phase 3 hooks Enter to run_setup; until then this is a no-op stub that
just displays the current selection.
"""
from __future__ import annotations

from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension as D

from arc.setup.sections import Section


def _format_text(ctx) -> list:
    out: list = []
    try:
        cfg = ctx.load_config()
        out.append(("class:hub.label", "  provider  "))
        out.append(("class:hub.value", f"{cfg.provider.name}\n"))
        out.append(("class:hub.label", "  model     "))
        out.append(("class:hub.value", f"{cfg.provider.model}\n"))
        if cfg.provider.base_url:
            out.append(("class:hub.label", "  base_url  "))
            out.append(("class:hub.value", f"{cfg.provider.base_url}\n"))
        out.append(("class:hub.label", "  api_key   "))
        out.append(("class:hub.value", f"{cfg.provider.api_key_env}\n"))
    except Exception as exc:
        out.append(("class:arc.error", f"  could not read config: {exc}\n"))
    out.append(("", "\n"))
    out.append(("class:hub.accent", "  [ ⏎ change provider/model ]\n"))
    out.append(("", "\n"))
    out.append(("class:hub.dim",
                "  opens the provider picker — writes config.yml then returns here.\n"))
    return out


def build(ctx) -> Section:
    kb = KeyBindings()

    @kb.add("enter")
    def _(event):
        _run_picker(ctx)

    control = FormattedTextControl(
        lambda: _format_text(ctx),
        focusable=True,
        key_bindings=kb,
        show_cursor=False,
    )
    container = Window(content=control, style="class:hub.content")

    def summary() -> str:
        try:
            cfg = ctx.load_config()
            return f"{cfg.provider.name} / {cfg.provider.model}"
        except Exception:
            return "unset"

    return Section(
        name="provider",
        title="Provider & Model",
        summary=summary,
        container=container,
        focusable=True,
    )


def _run_picker(ctx) -> None:
    """Suspend the hub, run the modal picker, then the hub re-enters here."""
    from arc.setup import run_setup

    def _go():
        run_setup(home=ctx.home, print_only=False)

    if ctx.run_modal is not None:
        ctx.run_modal(_go)
