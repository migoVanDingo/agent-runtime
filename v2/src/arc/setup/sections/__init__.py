"""Hub sections.

Each module here exports `build(ctx) -> Section`. The hub assembles a
fixed-order list of sections at startup.

See _design/0023-setup-hub-and-themes.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from prompt_toolkit.layout import Container


@dataclass
class Section:
    """One navigable section in the hub.

    summary: callable so the sidebar hint can reflect live state
    container: prompt_toolkit Container shown in the content pane when active
    on_enter / on_leave: optional callbacks for focus changes
    focusable: True if the content pane is interactive (sidebar focus moves
        right on Enter); False for read-only sections (focus stays in sidebar)
    """
    name: str          # short id (sidebar key)
    title: str         # display title in sidebar + content header
    summary: Callable[[], str]
    container: Container
    focusable: bool = True
    on_enter: Callable[[], None] | None = None
    on_leave: Callable[[], None] | None = None


__all__ = ["Section"]
