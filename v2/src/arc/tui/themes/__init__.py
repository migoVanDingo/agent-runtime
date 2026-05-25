"""Theme system for arc's TUI and setup hub.

A theme carries three surfaces and a name/description:

  - pt_style    — prompt_toolkit Style for dialogs, hub, toolbar
  - rich_theme  — Rich Theme exposing the arc.* named style namespace
  - code_theme  — Pygments style name for Markdown code blocks

Swapping themes can only change colors. Layout, glyphs, and behavior are
unaffected by design — render.py addresses named styles and the hub uses
fixed structural classes.

Themes are built-in modules (one per theme). Registration is explicit;
there is no auto-discovery. To add a theme, drop a module in this package
and add it to `_THEMES` below.

See _design/0023-setup-hub-and-themes.md for the full namespace.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

from prompt_toolkit.styles import Style as PTStyle
from rich.theme import Theme as RichTheme


@dataclass(frozen=True)
class Theme:
    name: str
    description: str
    pt_style: PTStyle
    rich_theme: RichTheme
    code_theme: str


# ── Named style namespace ──────────────────────────────────────────────────
#
# Every key a theme MUST define for `rich_theme`. render.py addresses these
# names; missing keys would silently fall back to Rich's default (no style).
# Keeping the list explicit makes it grep-able and forces new themes to
# cover the surface.

RICH_STYLE_KEYS: tuple[str, ...] = (
    # brand / chrome
    "arc.brand",
    "arc.accent",
    "arc.dim",
    # user
    "arc.user",
    "arc.user.prefix",
    # assistant
    "arc.assistant.glyph",
    "arc.assistant.label",
    # thinking
    "arc.thinking",
    "arc.thinking.glyph",
    # tool call
    "arc.tool.arrow",
    "arc.tool.call",
    # tool result
    "arc.tool.ok",
    "arc.tool.ok.arrow",
    "arc.tool.fail",
    "arc.tool.fail.arrow",
    "arc.tool.denied",
    "arc.tool.denied.arrow",
    # status / messages
    "arc.error",
    "arc.warning",
    "arc.success",
    "arc.info",
    # subagent
    "arc.subagent",
    "arc.subagent.name",
    "arc.subagent.ok.glyph",
    "arc.subagent.warn.glyph",
    "arc.subagent.fail.glyph",
    "arc.subagent.ok",
    "arc.subagent.warn",
    "arc.subagent.fail",
    # tables
    "arc.table.header",
    # resume marker
    "arc.resume",
)

# Standard prompt_toolkit class names the hub + dialogs rely on. Themes
# should cover at least these so dialogs and hub chrome look consistent.
PT_STYLE_KEYS: tuple[str, ...] = (
    "",                              # default text
    "dialog",
    "dialog.body",
    "dialog frame.label",
    "dialog shadow",
    "button",
    "button.focused",
    "radio",
    "radio-selected",
    "radio-checked",
    "checkbox",
    "checkbox-selected",
    "checkbox-checked",
    "frame.label",
    # bottom toolbar (live TUI)
    "bottom-toolbar",
    "toolbar.provider",
    "toolbar.sid",
    "toolbar.turn",
    "toolbar.tokens",
    "toolbar.cost",
    "toolbar.sep",
    # setup hub chrome
    "hub.title",
    "hub.sidebar",
    "hub.sidebar.item",
    "hub.sidebar.item.selected",
    "hub.content",
    "hub.divider",
    "hub.footer",
    "hub.accent",
    "hub.dim",
    "hub.section.title",
    "hub.value",
    "hub.label",
)


# ── Registry ───────────────────────────────────────────────────────────────


def _build_registry() -> dict[str, Theme]:
    """Eager import of every built-in theme. Keeps the registry grep-able."""
    from arc.tui.themes import dracula, gruvbox, mono, solarized_dark
    from arc.tui.themes import default as _default

    themes = [
        _default.THEME,
        dracula.THEME,
        solarized_dark.THEME,
        gruvbox.THEME,
        mono.THEME,
    ]
    return {t.name: t for t in themes}


REGISTRY: dict[str, Theme] = _build_registry()


def list_themes() -> list[Theme]:
    """All registered themes in display order (default first)."""
    return list(REGISTRY.values())


def load_theme(name: str) -> Theme:
    """Resolve a theme by name. Unknown names fall back to `default` with a
    one-line stderr warning — bad config never crashes the TUI."""
    if name in REGISTRY:
        return REGISTRY[name]
    sys.stderr.write(
        f"warning: unknown tui.theme {name!r}; falling back to 'default'\n"
    )
    return REGISTRY["default"]


# ── Active theme (process-wide cache) ──────────────────────────────────────


_ACTIVE: Theme | None = None


def set_active(theme: Theme) -> None:
    """Cache the resolved theme for the process. Called once at startup."""
    global _ACTIVE
    _ACTIVE = theme


def active() -> Theme:
    """Current active theme. Falls back to `default` if nothing was set
    (useful in tests and one-shot CLI paths that skip startup wiring)."""
    if _ACTIVE is not None:
        return _ACTIVE
    return REGISTRY["default"]


def resolve_from_config(theme_name: str) -> Theme:
    """One-shot resolve + cache. Call from startup paths after config load."""
    t = load_theme(theme_name)
    set_active(t)
    return t


def resolve_from_home(home_override: str | None = None) -> Theme:
    """Best-effort theme resolution from ARC_HOME/config.yml.

    Used by CLI entry points (e.g. `arc setup`, `arc plugins`) that open
    themed dialogs without going through TUIApp. If config is missing or
    unreadable (e.g. before first `arc bootstrap`), silently falls back to
    `default` so commands never fail because of theming.
    """
    try:
        from arc.bootstrap import paths_for, resolve_home
        from arc.config import load
        p = paths_for(resolve_home(home_override))
        if not p.config_file.exists():
            return resolve_from_config("default")
        cfg = load(p.config_file)
        return resolve_from_config(cfg.tui.theme)
    except Exception:
        return resolve_from_config("default")


__all__ = [
    "Theme",
    "RICH_STYLE_KEYS",
    "PT_STYLE_KEYS",
    "REGISTRY",
    "list_themes",
    "load_theme",
    "active",
    "set_active",
    "resolve_from_config",
    "resolve_from_home",
]
