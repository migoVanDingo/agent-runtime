"""Plugins section — wraps setup/plugin_menu.py.

Same pattern as provider_model: read-only summary + Enter opens the modal
checkbox menu via run_in_terminal so the hub stays alive.
"""
from __future__ import annotations

from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import FormattedTextControl

from arc.setup.sections import Section


_cache: dict = {"rows": None, "mtime": 0.0}


def _rows(ctx):
    """Cached plugin discovery — entry-point walk is non-trivial."""
    try:
        mtime = ctx.config_path.stat().st_mtime
    except OSError:
        mtime = 0.0
    if _cache["rows"] is None or _cache["mtime"] != mtime:
        from arc.setup.plugin_menu import collect_rows
        _cache["rows"] = collect_rows(ctx.config_path)
        _cache["mtime"] = mtime
    return _cache["rows"]


def _format_text(ctx) -> list:
    out: list = []
    out.append(("class:hub.accent", "  installed plugins\n"))
    out.append(("class:hub.dim",   "  ─────────────────\n"))
    try:
        rows = _rows(ctx)
        if not rows:
            out.append(("class:hub.dim", "  (none discovered)\n"))
        for r in rows:
            mark = "●" if r.enabled else "○"
            mark_style = "class:arc.success" if r.enabled else "class:hub.dim"
            out.append((mark_style, f"  {mark} "))
            out.append(("class:hub.value", f"{r.name:<28}"))
            if r.kind == "dangling":
                out.append(("class:arc.warning", "not installed\n"))
            elif r.kind == "discovered":
                version = f" v{r.version}" if r.version else ""
                out.append(("class:hub.dim", f"{r.package or 'external'}{version}\n"))
            else:
                out.append(("class:hub.dim", "built-in\n"))
    except Exception as exc:
        out.append(("class:arc.error", f"  error: {exc}\n"))
    out.append(("", "\n"))
    out.append(("class:hub.accent", "  [ ⏎ toggle plugins ]\n"))
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
        try:
            rows = _rows(ctx)
            enabled = sum(1 for r in rows if r.enabled)
            return f"{enabled} of {len(rows)} enabled"
        except Exception:
            return "—"

    return Section(
        name="plugins",
        title="Plugins",
        summary=summary,
        container=container,
        focusable=True,
    )


def _run_menu(ctx) -> None:
    from arc.setup.plugin_menu import run_menu

    def _go():
        run_menu(ctx.config_path)

    if ctx.run_modal is not None:
        ctx.run_modal(_go)
