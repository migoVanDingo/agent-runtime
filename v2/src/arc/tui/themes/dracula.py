"""Dracula theme — dark with purple/pink/green accents.

Palette: https://draculatheme.com/contribute
  bg          #282a36
  current     #44475a
  fg          #f8f8f2
  comment     #6272a4
  cyan        #8be9fd
  green       #50fa7b
  orange      #ffb86c
  pink        #ff79c6
  purple      #bd93f9
  red         #ff5555
  yellow      #f1fa8c
"""
from __future__ import annotations

from prompt_toolkit.styles import Style as PTStyle
from rich.theme import Theme as RichTheme

from arc.tui.themes import Theme


BG       = "#282a36"
CURRENT  = "#44475a"
FG       = "#f8f8f2"
COMMENT  = "#6272a4"
CYAN     = "#8be9fd"
GREEN    = "#50fa7b"
ORANGE   = "#ffb86c"
PINK     = "#ff79c6"
PURPLE   = "#bd93f9"
RED      = "#ff5555"
YELLOW   = "#f1fa8c"


_RICH = RichTheme({
    "arc.brand":            f"bold {PURPLE}",
    "arc.accent":           PURPLE,
    "arc.dim":              COMMENT,
    "arc.user":             PINK,
    "arc.user.prefix":      f"bold {PINK}",
    "arc.assistant.glyph":  f"bold {CYAN}",
    "arc.assistant.label":  f"bold {COMMENT}",
    "arc.thinking":         f"italic {COMMENT}",
    "arc.thinking.glyph":   COMMENT,
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
    "arc.info":             CYAN,
    "arc.subagent":         PURPLE,
    "arc.subagent.name":    f"bold {PURPLE}",
    "arc.subagent.ok.glyph":   f"bold {GREEN}",
    "arc.subagent.warn.glyph": f"bold {ORANGE}",
    "arc.subagent.fail.glyph": f"bold {RED}",
    "arc.subagent.ok":      GREEN,
    "arc.subagent.warn":    ORANGE,
    "arc.subagent.fail":    RED,
    "arc.table.header":     f"bold {PURPLE}",
    "arc.resume":           f"bold {PINK}",
})

_PT = PTStyle.from_dict({
    "":                            f"fg:{FG} bg:default",
    "dialog":                      f"bg:{BG}",
    "dialog.body":                 f"bg:{BG} fg:{FG}",
    "dialog frame.label":          f"fg:{PURPLE} bold",
    "dialog shadow":               "bg:#000000",
    "button":                      f"bg:{CURRENT} fg:{FG}",
    "button.focused":              f"bg:{PURPLE} fg:{BG} bold",
    "radio":                       f"fg:{FG}",
    "radio-selected":              f"fg:{PURPLE} bold",
    "radio-checked":               f"fg:{PINK} bold",
    "checkbox":                    f"fg:{FG}",
    "checkbox-selected":           f"fg:{PURPLE} bold",
    "checkbox-checked":            f"fg:{GREEN} bold",
    "frame.label":                 f"fg:{PURPLE} bold",
    "bottom-toolbar":              f"noreverse fg:{COMMENT} bg:default",
    "toolbar.provider":            f"fg:{CYAN} bg:default",
    "toolbar.sid":                 f"fg:{COMMENT} bg:default",
    "toolbar.turn":                f"fg:{COMMENT} bg:default",
    "toolbar.tokens":              f"fg:{YELLOW} bg:default",
    "toolbar.cost":                f"fg:{GREEN} bg:default",
    "toolbar.sep":                 f"fg:{CURRENT} bg:default",
    "hub.title":                   f"fg:{PURPLE} bold",
    "hub.sidebar":                 f"bg:default fg:{FG}",
    "hub.sidebar.item":            f"fg:{FG}",
    "hub.sidebar.item.selected":   f"fg:{BG} bg:{PURPLE} bold",
    "hub.content":                 f"bg:default fg:{FG}",
    "hub.divider":                 f"fg:{CURRENT}",
    "hub.footer":                  f"fg:{COMMENT}",
    "hub.accent":                  f"fg:{PINK} bold",
    "hub.dim":                     f"fg:{COMMENT}",
    "hub.section.title":           f"fg:{PURPLE} bold underline",
    "hub.value":                   f"fg:{CYAN} bold",
    "hub.label":                   f"fg:{COMMENT}",
})


THEME = Theme(
    name="dracula",
    description="Dark with purple, pink, and green accents.",
    pt_style=_PT,
    rich_theme=_RICH,
    code_theme="dracula",
)
