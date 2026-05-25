"""Mono — terminal-default colors only, no hex codes.

For dumb terminals, accessibility users, and screen readers. Uses Rich
named colors (which respect the terminal's actual palette) and falls back
to "default" bg everywhere so the terminal's own colorscheme drives.
"""
from __future__ import annotations

from prompt_toolkit.styles import Style as PTStyle
from rich.theme import Theme as RichTheme

from arc.tui.themes import Theme


_RICH = RichTheme({
    "arc.brand":            "bold",
    "arc.accent":           "bold",
    "arc.dim":              "dim",
    "arc.user":             "bold",
    "arc.user.prefix":      "bold",
    "arc.assistant.glyph":  "bold",
    "arc.assistant.label":  "dim",
    "arc.thinking":         "italic dim",
    "arc.thinking.glyph":   "dim",
    "arc.tool.arrow":       "bold",
    "arc.tool.call":        "",
    "arc.tool.ok":          "dim",
    "arc.tool.ok.arrow":    "bold",
    "arc.tool.fail":        "bold",
    "arc.tool.fail.arrow":  "bold reverse",
    "arc.tool.denied":      "bold",
    "arc.tool.denied.arrow": "bold reverse",
    "arc.error":            "bold reverse",
    "arc.warning":          "bold",
    "arc.success":          "bold",
    "arc.info":             "",
    "arc.subagent":         "bold",
    "arc.subagent.name":    "bold underline",
    "arc.subagent.ok.glyph":   "bold",
    "arc.subagent.warn.glyph": "bold",
    "arc.subagent.fail.glyph": "bold reverse",
    "arc.subagent.ok":      "",
    "arc.subagent.warn":    "bold",
    "arc.subagent.fail":    "bold reverse",
    "arc.table.header":     "bold underline",
    "arc.resume":           "bold underline",
})

_PT = PTStyle.from_dict({
    "":                            "",
    "dialog":                      "bg:default",
    "dialog.body":                 "bg:default",
    "dialog frame.label":          "bold",
    "dialog shadow":               "",
    "button":                      "",
    "button.focused":              "reverse bold",
    "radio":                       "",
    "radio-selected":              "bold",
    "radio-checked":               "bold",
    "checkbox":                    "",
    "checkbox-selected":           "bold",
    "checkbox-checked":            "bold",
    "frame.label":                 "bold",
    "bottom-toolbar":              "noreverse",
    "toolbar.provider":            "bold",
    "toolbar.sid":                 "",
    "toolbar.turn":                "",
    "toolbar.tokens":              "",
    "toolbar.cost":                "bold",
    "toolbar.sep":                 "",
    "hub.title":                   "bold underline",
    "hub.sidebar":                 "",
    "hub.sidebar.item":            "",
    "hub.sidebar.item.selected":   "reverse bold",
    "hub.content":                 "",
    "hub.divider":                 "",
    "hub.footer":                  "",
    "hub.accent":                  "bold",
    "hub.dim":                     "",
    "hub.section.title":           "bold underline",
    "hub.value":                   "bold",
    "hub.label":                   "",
})


THEME = Theme(
    name="mono",
    description="Terminal-default colors only — for dumb terminals or accessibility.",
    pt_style=_PT,
    rich_theme=_RICH,
    code_theme="bw",
)
