"""Solarized Dark — the classic muted dark palette.

Palette: https://ethanschoonover.com/solarized
  base03  #002b36   darkest bg
  base02  #073642
  base01  #586e75   secondary fg
  base00  #657b83
  base0   #839496   primary fg
  base1   #93a1a1
  base2   #eee8d5
  base3   #fdf6e3   lightest
  yellow  #b58900
  orange  #cb4b16
  red     #dc322f
  magenta #d33682
  violet  #6c71c4
  blue    #268bd2
  cyan    #2aa198
  green   #859900
"""
from __future__ import annotations

from prompt_toolkit.styles import Style as PTStyle
from rich.theme import Theme as RichTheme

from arc.tui.themes import Theme


BASE03   = "#002b36"
BASE02   = "#073642"
BASE01   = "#586e75"
BASE00   = "#657b83"
BASE0    = "#839496"
BASE1    = "#93a1a1"
YELLOW   = "#b58900"
ORANGE   = "#cb4b16"
RED      = "#dc322f"
MAGENTA  = "#d33682"
VIOLET   = "#6c71c4"
BLUE     = "#268bd2"
CYAN     = "#2aa198"
GREEN    = "#859900"


_RICH = RichTheme({
    "arc.brand":            f"bold {BLUE}",
    "arc.accent":           BLUE,
    "arc.dim":              BASE01,
    "arc.user":             MAGENTA,
    "arc.user.prefix":      f"bold {MAGENTA}",
    "arc.assistant.glyph":  f"bold {BLUE}",
    "arc.assistant.label":  f"bold {BASE01}",
    "arc.thinking":         f"italic {BASE01}",
    "arc.thinking.glyph":   VIOLET,
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
    "arc.subagent":         VIOLET,
    "arc.subagent.name":    f"bold {VIOLET}",
    "arc.subagent.ok.glyph":   f"bold {GREEN}",
    "arc.subagent.warn.glyph": f"bold {ORANGE}",
    "arc.subagent.fail.glyph": f"bold {RED}",
    "arc.subagent.ok":      GREEN,
    "arc.subagent.warn":    ORANGE,
    "arc.subagent.fail":    RED,
    "arc.table.header":     f"bold {BLUE}",
    "arc.resume":           f"bold {MAGENTA}",
})

_PT = PTStyle.from_dict({
    "":                            f"fg:{BASE0} bg:default",
    "dialog":                      f"bg:{BASE03}",
    "dialog.body":                 f"bg:{BASE03} fg:{BASE0}",
    "dialog frame.label":          f"fg:{BLUE} bold",
    "dialog shadow":               "bg:#000000",
    "button":                      f"bg:{BASE02} fg:{BASE0}",
    "button.focused":              f"bg:{BLUE} fg:{BASE03} bold",
    "radio":                       f"fg:{BASE0}",
    "radio-selected":              f"fg:{BLUE} bold",
    "radio-checked":               f"fg:{CYAN} bold",
    "checkbox":                    f"fg:{BASE0}",
    "checkbox-selected":           f"fg:{BLUE} bold",
    "checkbox-checked":            f"fg:{GREEN} bold",
    "frame.label":                 f"fg:{BLUE} bold",
    "bottom-toolbar":              f"noreverse fg:{BASE01} bg:default",
    "toolbar.provider":            f"fg:{BLUE} bg:default",
    "toolbar.sid":                 f"fg:{BASE01} bg:default",
    "toolbar.turn":                f"fg:{BASE01} bg:default",
    "toolbar.tokens":              f"fg:{YELLOW} bg:default",
    "toolbar.cost":                f"fg:{GREEN} bg:default",
    "toolbar.sep":                 f"fg:{BASE02} bg:default",
    "hub.title":                   f"fg:{BLUE} bold",
    "hub.sidebar":                 f"bg:default fg:{BASE0}",
    "hub.sidebar.item":            f"fg:{BASE0}",
    "hub.sidebar.item.selected":   f"fg:{BASE03} bg:{BLUE} bold",
    "hub.content":                 f"bg:default fg:{BASE0}",
    "hub.divider":                 f"fg:{BASE02}",
    "hub.footer":                  f"fg:{BASE01}",
    "hub.accent":                  f"fg:{CYAN} bold",
    "hub.dim":                     f"fg:{BASE01}",
    "hub.section.title":           f"fg:{BLUE} bold underline",
    "hub.value":                   f"fg:{BASE1} bold",
    "hub.label":                   f"fg:{BASE01}",
})


THEME = Theme(
    name="solarized-dark",
    description="Ethan Schoonover's classic muted dark palette.",
    pt_style=_PT,
    rich_theme=_RICH,
    code_theme="solarized-dark",
)
