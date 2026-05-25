"""Themes section — pick a theme, preview swatches, save to config.yml.

Live preview: as the user moves through the list, the right side of the
content pane renders a sample of every arc.* named style in the focused
theme. Press Enter to save the choice to tui.theme.
"""
from __future__ import annotations

from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, VSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension as D

from arc.setup.sections import Section
from arc.tui.themes import REGISTRY, list_themes, load_theme, set_active


# Module-level state for the focused theme (per-hub-session). Not great
# style but simpler than threading a closure through every callback.
_focus_idx = 0
_status_msg: str = ""


def _format_list(ctx) -> list:
    """Left column — themes list with focus indicator and current marker."""
    try:
        current_name = ctx.load_config().tui.theme
    except Exception:
        current_name = "default"

    out: list = []
    out.append(("class:hub.label", "  pick a theme\n"))
    out.append(("class:hub.dim",   "  ────────────\n"))
    for i, theme in enumerate(list_themes()):
        marker = "●" if theme.name == current_name else "○"
        line = f"  {marker} {theme.name:<16}"
        if i == _focus_idx:
            out.append(("class:hub.sidebar.item.selected", line + "\n"))
        else:
            out.append(("class:hub.sidebar.item", line + "\n"))
    out.append(("", "\n"))
    out.append(("class:hub.dim", "  ↑↓ focus  ⏎ save\n"))
    if _status_msg:
        out.append(("", "\n"))
        out.append(("class:arc.success", f"  {_status_msg}\n"))
    return out


def _format_preview(ctx) -> list:
    """Right column — sample of every arc.* named style under the focused theme.

    Live preview is achieved by temporarily pushing the focused theme's
    rich_theme into Rich isn't useful here (we're inside prompt_toolkit),
    so we just show the literal color/attribute strings.  prompt_toolkit
    can't render arc.* styles unless the hub's PT style includes them,
    which it doesn't (deliberately — the active theme drives chrome only).

    For the preview we render labels in their *literal* styles by walking
    the focused theme's rich_theme.styles dict and emitting them with the
    underlying ANSI mappings the hub's style sheet declares (none — they'd
    inherit the active theme's classes).

    So: we show the theme name, description, code_theme, and a sample of
    the named styles as literal label/value pairs.
    """
    themes = list_themes()
    if _focus_idx >= len(themes):
        return []
    theme = themes[_focus_idx]

    out: list = []
    out.append(("class:hub.accent", f"  {theme.name}\n"))
    out.append(("class:hub.dim", f"  {theme.description}\n"))
    out.append(("", "\n"))
    out.append(("class:hub.label", "  code_theme  "))
    out.append(("class:hub.value", f"{theme.code_theme}\n"))
    out.append(("", "\n"))
    out.append(("class:hub.accent", "  named styles\n"))
    out.append(("class:hub.dim",   "  ────────────\n"))
    for key in sorted(theme.rich_theme.styles):
        if not key.startswith("arc."):
            continue
        style = theme.rich_theme.styles[key]
        out.append(("class:hub.label", f"  {key:<28}"))
        out.append(("class:hub.value", f"{style}\n"))
    return out


def build(ctx) -> Section:
    global _focus_idx, _status_msg
    _focus_idx = 0
    _status_msg = ""

    left_window = Window(
        content=FormattedTextControl(
            lambda: _format_list(ctx),
            focusable=True,
            key_bindings=_build_keybindings(ctx),
            show_cursor=False,
        ),
        width=D.exact(28),
        style="class:hub.content",
    )
    right_window = Window(
        content=FormattedTextControl(
            lambda: _format_preview(ctx),
            focusable=False,
        ),
        style="class:hub.content",
    )
    container = VSplit([
        left_window,
        Window(width=D.exact(1), char="│", style="class:hub.divider"),
        right_window,
    ])

    def summary() -> str:
        try:
            return ctx.load_config().tui.theme
        except Exception:
            return "default"

    return Section(
        name="themes",
        title="Themes",
        summary=summary,
        container=container,
        focusable=True,
    )


def _build_keybindings(ctx) -> KeyBindings:
    kb = KeyBindings()

    @kb.add("up")
    def _(event):
        global _focus_idx
        n = len(list_themes())
        _focus_idx = (_focus_idx - 1) % n
        if ctx.request_redraw:
            ctx.request_redraw()

    @kb.add("down")
    def _(event):
        global _focus_idx
        n = len(list_themes())
        _focus_idx = (_focus_idx + 1) % n
        if ctx.request_redraw:
            ctx.request_redraw()

    @kb.add("enter")
    def _(event):
        global _status_msg
        theme = list_themes()[_focus_idx]
        try:
            _write_theme(ctx.config_path, theme.name)
            set_active(load_theme(theme.name))
            # Re-apply pt style to the live Application so chrome updates
            try:
                event.app.style = theme.pt_style
            except Exception:
                pass
            _status_msg = f"saved tui.theme = {theme.name}"
        except Exception as exc:
            _status_msg = f"save failed: {exc}"
        if ctx.request_redraw:
            ctx.request_redraw()

    return kb


def _write_theme(config_path, theme_name: str) -> None:
    """Comment-preserving update of tui.theme in config.yml via ruamel."""
    from ruamel.yaml import YAML
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    with open(config_path, "r", encoding="utf-8") as fh:
        data = yaml.load(fh)
    if data is None:
        raise ValueError("config.yml is empty")
    tui = data.get("tui")
    if tui is None:
        raise ValueError("config.yml has no `tui:` section")
    tui["theme"] = theme_name
    with open(config_path, "w", encoding="utf-8") as fh:
        yaml.dump(data, fh)
