"""Gruvbox Dark — warm, high-contrast retro palette.

Palette: https://github.com/morhetz/gruvbox
"""
from __future__ import annotations

from prompt_toolkit.styles import Style as PTStyle
from rich.theme import Theme as RichTheme

from arc.tui.themes import Theme


BG0_H    = "#1d2021"
BG0      = "#282828"
BG1      = "#3c3836"
BG2      = "#504945"
BG3      = "#665c54"
FG0      = "#fbf1c7"
FG1      = "#ebdbb2"
FG2      = "#d5c4a1"
FG4      = "#a89984"
GRAY     = "#928374"
RED      = "#fb4934"
GREEN    = "#b8bb26"
YELLOW   = "#fabd2f"
BLUE     = "#83a598"
PURPLE   = "#d3869b"
AQUA     = "#8ec07c"
ORANGE   = "#fe8019"


_RICH = RichTheme({
    "arc.brand":            f"bold {ORANGE}",
    "arc.accent":           ORANGE,
    "arc.dim":              GRAY,
    "arc.user":             PURPLE,
    "arc.user.prefix":      f"bold {PURPLE}",
    "arc.assistant.glyph":  f"bold {AQUA}",
    "arc.assistant.label":  f"bold {GRAY}",
    "arc.thinking":         f"italic {GRAY}",
    "arc.thinking.glyph":   BLUE,
    "arc.tool.arrow":       f"bold {YELLOW}",
    "arc.tool.call":        YELLOW,
    "arc.tool.ok":          GREEN,
    "arc.tool.ok.arrow":    f"bold {GREEN}",
    "arc.tool.fail":        RED,
    "arc.tool.fail.arrow":  f"bold {RED}",
    "arc.tool.denied":      ORANGE,
    "arc.tool.denied.arrow": f"bold {ORANGE}",
    "arc.error":            RED,
    "arc.warning":          ORANGE,
    "arc.success":          GREEN,
    "arc.info":             AQUA,
    "arc.subagent":         BLUE,
    "arc.subagent.name":    f"bold {BLUE}",
    "arc.subagent.ok.glyph":   f"bold {GREEN}",
    "arc.subagent.warn.glyph": f"bold {YELLOW}",
    "arc.subagent.fail.glyph": f"bold {RED}",
    "arc.subagent.ok":      GREEN,
    "arc.subagent.warn":    YELLOW,
    "arc.subagent.fail":    RED,
    "arc.table.header":     f"bold {ORANGE}",
    "arc.resume":           f"bold {PURPLE}",
})

_PT = PTStyle.from_dict({
    "":                            f"fg:{FG1} bg:default",
    "dialog":                      f"bg:{BG0}",
    "dialog.body":                 f"bg:{BG0} fg:{FG1}",
    "dialog frame.label":          f"fg:{ORANGE} bold",
    "dialog shadow":               "bg:#000000",
    "button":                      f"bg:{BG1} fg:{FG1}",
    "button.focused":              f"bg:{ORANGE} fg:{BG0} bold",
    "radio":                       f"fg:{FG1}",
    "radio-selected":              f"fg:{ORANGE} bold",
    "radio-checked":               f"fg:{YELLOW} bold",
    "checkbox":                    f"fg:{FG1}",
    "checkbox-selected":           f"fg:{ORANGE} bold",
    "checkbox-checked":            f"fg:{GREEN} bold",
    "frame.label":                 f"fg:{ORANGE} bold",
    "bottom-toolbar":              f"noreverse fg:{GRAY} bg:default",
    "toolbar.provider":            f"fg:{AQUA} bg:default",
    "toolbar.sid":                 f"fg:{GRAY} bg:default",
    "toolbar.turn":                f"fg:{GRAY} bg:default",
    "toolbar.tokens":              f"fg:{YELLOW} bg:default",
    "toolbar.cost":                f"fg:{GREEN} bg:default",
    "toolbar.sep":                 f"fg:{BG2} bg:default",
    "hub.title":                   f"fg:{ORANGE} bold",
    "hub.sidebar":                 f"bg:default fg:{FG1}",
    "hub.sidebar.item":            f"fg:{FG1}",
    "hub.sidebar.item.selected":   f"fg:{BG0} bg:{ORANGE} bold",
    "hub.content":                 f"bg:default fg:{FG1}",
    "hub.divider":                 f"fg:{BG2}",
    "hub.footer":                  f"fg:{GRAY}",
    "hub.accent":                  f"fg:{YELLOW} bold",
    "hub.dim":                     f"fg:{GRAY}",
    "hub.section.title":           f"fg:{ORANGE} bold underline",
    "hub.value":                   f"fg:{FG0} bold",
    "hub.label":                   f"fg:{GRAY}",
})


THEME = Theme(
    name="gruvbox",
    description="Warm, high-contrast retro dark palette.",
    pt_style=_PT,
    rich_theme=_RICH,
    code_theme="gruvbox-dark",
)
