"""Default theme — the look arc has shipped with since day one.

Selecting this is a no-op in terms of visible change. New themes are
diffed against this one.
"""
from __future__ import annotations

from prompt_toolkit.styles import Style as PTStyle
from rich.theme import Theme as RichTheme

from arc.tui.themes import Theme


_RICH = RichTheme({
    # brand / chrome
    "arc.brand":            "bold cyan",
    "arc.accent":           "cyan",
    "arc.dim":              "dim",
    # user
    "arc.user":             "magenta",
    "arc.user.prefix":      "bold magenta",
    # assistant
    "arc.assistant.glyph":  "bold cyan",
    "arc.assistant.label":  "bold dim",
    # thinking
    "arc.thinking":         "dim italic",
    "arc.thinking.glyph":   "dim cyan",
    # tool call
    "arc.tool.arrow":       "bold yellow",
    "arc.tool.call":        "yellow",
    # tool result
    "arc.tool.ok":          "dim green",
    "arc.tool.ok.arrow":    "bold green",
    "arc.tool.fail":        "dim red",
    "arc.tool.fail.arrow":  "bold red",
    "arc.tool.denied":      "red",
    "arc.tool.denied.arrow": "bold red",
    # status / messages
    "arc.error":            "red",
    "arc.warning":          "yellow",
    "arc.success":          "green",
    "arc.info":             "cyan",
    # subagent
    "arc.subagent":         "cyan",
    "arc.subagent.name":    "bold cyan",
    "arc.subagent.ok.glyph":   "bold green",
    "arc.subagent.warn.glyph": "bold yellow",
    "arc.subagent.fail.glyph": "bold red",
    "arc.subagent.ok":      "green",
    "arc.subagent.warn":    "yellow",
    "arc.subagent.fail":    "red",
    # tables
    "arc.table.header":     "bold cyan",
    # resume marker
    "arc.resume":           "bold magenta",
})

_PT = PTStyle.from_dict({
    "":                            "",
    # dialogs (prompt_toolkit defaults work OK here; subtle tweaks only)
    "dialog":                      "bg:#1c1c1c",
    "dialog.body":                 "bg:#1c1c1c fg:#dddddd",
    "dialog frame.label":          "fg:#8aa0c0 bold",
    "dialog shadow":               "bg:#000000",
    "button":                      "bg:#2c2c2c fg:#dddddd",
    "button.focused":              "bg:#8aa0c0 fg:#000000 bold",
    "radio":                       "fg:#dddddd",
    "radio-selected":              "fg:#8aa0c0 bold",
    "radio-checked":               "fg:#8aa0c0 bold",
    "checkbox":                    "fg:#dddddd",
    "checkbox-selected":           "fg:#8aa0c0 bold",
    "checkbox-checked":            "fg:#8aa0c0 bold",
    "frame.label":                 "fg:#8aa0c0 bold",
    # bottom toolbar (preserved verbatim from app.py — this IS the default)
    "bottom-toolbar":              "noreverse fg:#7a7a7a bg:default",
    "toolbar.provider":            "fg:#8aa0c0 bg:default",
    "toolbar.sid":                 "fg:#7a7a7a bg:default",
    "toolbar.turn":                "fg:#7a7a7a bg:default",
    "toolbar.tokens":              "fg:#8a8a6a bg:default",
    "toolbar.cost":                "fg:#7a9a7a bg:default",
    "toolbar.sep":                 "fg:#4a4a4a bg:default",
    # setup hub chrome
    "hub.title":                   "fg:#8aa0c0 bold",
    "hub.sidebar":                 "bg:default fg:#dddddd",
    "hub.sidebar.item":            "fg:#dddddd",
    "hub.sidebar.item.selected":   "fg:#000000 bg:#8aa0c0 bold",
    "hub.content":                 "bg:default fg:#dddddd",
    "hub.divider":                 "fg:#3a3a3a",
    "hub.footer":                  "fg:#7a7a7a",
    "hub.accent":                  "fg:#8aa0c0 bold",
    "hub.dim":                     "fg:#7a7a7a",
    "hub.section.title":           "fg:#8aa0c0 bold underline",
    "hub.value":                   "fg:#dddddd bold",
    "hub.label":                   "fg:#7a7a7a",
})


THEME = Theme(
    name="default",
    description="What arc has shipped with since day one — cyan brand, soft greys.",
    pt_style=_PT,
    rich_theme=_RICH,
    code_theme="monokai",
)
