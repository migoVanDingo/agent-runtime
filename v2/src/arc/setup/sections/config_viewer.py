"""Config viewer — read-only view of config.yml.

Shows the file path and raw YAML content. Scrollable. Edits happen through
the dedicated sections (Provider & Model, Plugins, Themes, …) and the
comment-preserving writer in setup/writer.py.
"""
from __future__ import annotations

from prompt_toolkit.layout import HSplit, ScrollablePane, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension as D

from arc.setup.sections import Section


def _format_text(ctx) -> list:
    path = ctx.config_path
    out: list = []
    out.append(("class:hub.label", "  path  "))
    out.append(("class:hub.value", f"{path}\n"))
    out.append(("", "\n"))
    if not path.exists():
        out.append(("class:arc.warning", f"  config.yml not found.\n"))
        out.append(("class:hub.dim", "  Run `arc bootstrap` to create one.\n"))
        return out
    try:
        body = path.read_text(encoding="utf-8")
    except Exception as exc:
        out.append(("class:arc.error", f"  could not read config: {exc}\n"))
        return out
    for line in body.splitlines():
        if line.lstrip().startswith("#"):
            out.append(("class:hub.dim", f"  {line}\n"))
        elif ":" in line and not line.startswith(" "):
            # Top-level key
            out.append(("class:hub.accent", f"  {line}\n"))
        else:
            out.append(("", f"  {line}\n"))
    return out


def build(ctx) -> Section:
    control = FormattedTextControl(
        lambda: _format_text(ctx),
        focusable=True,
        show_cursor=False,
    )
    body = Window(content=control, style="class:hub.content", wrap_lines=False)
    container = ScrollablePane(content=HSplit([body]))

    def summary() -> str:
        if not ctx.config_path.exists():
            return "config.yml missing"
        try:
            n = sum(1 for _ in ctx.config_path.read_text(encoding="utf-8").splitlines())
        except Exception:
            return "config.yml"
        return f"{n} lines"

    return Section(
        name="config",
        title="Config",
        summary=summary,
        container=container,
        focusable=True,
    )
