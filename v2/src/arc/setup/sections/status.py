"""Status / diagnostics — read-only snapshot of the current arc install.

Shows ARC_HOME path, currently-selected provider/model, llama-server state,
plugin counts, and version. Refreshed each time the section is entered.
"""
from __future__ import annotations

from prompt_toolkit.layout import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension as D

from arc.setup.sections import Section


def _format_lines(ctx) -> list:
    """Build the [(style, text), ...] tuples for the FormattedTextControl."""
    from arc import __version__

    out: list = []

    def row(label: str, value: str, value_style: str = "class:hub.value") -> None:
        out.append(("class:hub.label", f"  {label:<14}"))
        out.append((value_style, f"{value}\n"))

    out.append(("class:hub.accent", " ARC version\n"))
    row("arc", __version__)
    row("home", str(ctx.home))
    row("config", str(ctx.config_path) + ("" if ctx.config_path.exists() else "  (missing)"))

    # Provider / model
    out.append(("", "\n"))
    out.append(("class:hub.accent", " Provider\n"))
    try:
        cfg = ctx.load_config()
        row("provider", cfg.provider.name)
        row("model", cfg.provider.model)
        if cfg.provider.base_url:
            row("base_url", cfg.provider.base_url)
        row("theme", cfg.tui.theme)
    except Exception as exc:  # bad/missing config — still useful to render
        row("error", str(exc), "class:arc.error")

    # llama-server
    out.append(("", "\n"))
    out.append(("class:hub.accent", " Local inference server\n"))
    try:
        from arc.bootstrap import paths_for
        from arc.llm.process import status as proc_status
        ps = proc_status(llm_dir=paths_for(ctx.home).llm_dir)
        if ps is None:
            row("status", "not running", "class:hub.dim")
        else:
            row("status", "running", "class:arc.success")
            row("pid", str(ps.pid))
            row("model", ps.model_id)
    except Exception:
        row("status", "unavailable", "class:hub.dim")

    # Plugins
    out.append(("", "\n"))
    out.append(("class:hub.accent", " Plugins\n"))
    try:
        from arc.setup.sections.plugins import _rows as _cached_rows
        rows = _cached_rows(ctx)
        builtins = sum(1 for r in rows if r.kind == "builtin")
        discovered = sum(1 for r in rows if r.kind == "discovered")
        dangling = sum(1 for r in rows if r.kind == "dangling")
        enabled = sum(1 for r in rows if r.enabled)
        row("enabled", str(enabled))
        row("built-in", str(builtins))
        row("external", str(discovered))
        if dangling:
            row("dangling", str(dangling), "class:arc.warning")
    except Exception as exc:
        row("error", str(exc), "class:arc.error")

    out.append(("", "\n"))
    out.append(("class:hub.dim", "  (read-only — esc to return)\n"))
    return out


def build(ctx) -> Section:
    # FormattedTextControl with a thunk so the snapshot refreshes on redraw
    control = FormattedTextControl(
        lambda: _format_lines(ctx),
        focusable=False,
    )
    container = Window(content=control, style="class:hub.content")

    def summary() -> str:
        try:
            cfg = ctx.load_config()
            return f"{cfg.provider.name}/{cfg.provider.model}"
        except Exception:
            return "configuration unreadable"

    return Section(
        name="status",
        title="Status",
        summary=summary,
        container=container,
        focusable=False,
    )
