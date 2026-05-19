"""Rich-based rendering primitives for the TUI.

Pure rendering functions — they take data, produce Rich renderables.
No state. Composed by the TUI app.
"""
from __future__ import annotations

from typing import Any

from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text


# ── Logo ────────────────────────────────────────────────────────────────────
# ANSI Shadow figlet font, hand-trimmed. Six lines for the wordmark, with a
# small "v2" tag tucked into the right side of the third line so it doesn't
# add vertical bulk.

_LOGO_LINES = [
    " █████╗ ██████╗  ██████╗",
    "██╔══██╗██╔══██╗██╔════╝",
    "███████║██████╔╝██║      v2",
    "██╔══██║██╔══██╗██║",
    "██║  ██║██║  ██║╚██████╗",
    "╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝",
]


def render_logo() -> Group:
    """ARC wordmark + v2 tag. Cyan glyphs, dim v2."""
    lines: list[Text] = []
    for raw in _LOGO_LINES:
        # Split out the "v2" tag if present so we can style it differently
        if "v2" in raw:
            head, _, _ = raw.partition("v2")
            t = Text(head, style="bold cyan")
            t.append("v2", style="dim italic")
            lines.append(t)
        else:
            lines.append(Text(raw, style="bold cyan"))
    return Group(*lines)


def render_user_prefix(prompt_prefix: str) -> Text:
    """The chip that goes before each printed user turn."""
    return Text(prompt_prefix, style="bold magenta")


def render_user_message(text: str, prompt_prefix: str) -> Group:
    """A user's input as it lands in scrollback (after they hit enter).

    Use Text.assemble so the prefix and the typed text get their own styles
    while still rendering on a single line with a normal trailing newline.
    """
    return Group(
        Text(),  # blank line above
        Text.assemble(
            (prompt_prefix, "bold magenta"),
            (text, "magenta"),
        ),
    )


def render_assistant_text(text: str) -> Group:
    """The assistant's text response. Rendered as Markdown for code blocks etc."""
    if not text.strip():
        return Group()
    return Group(
        Text(),
        Text.assemble(("◆ ", "bold cyan"), ("assistant", "bold dim")),
        Markdown(text, code_theme="monokai"),
    )


def render_tool_call(tool_name: str, tool_input: dict) -> Group:
    """A tool call about to execute."""
    args = ", ".join(f"{k}={v!r}" for k, v in tool_input.items())
    if len(args) > 80:
        args = args[:77] + "..."
    return Group(
        Text(),
        Text.assemble(
            ("→ ", "bold yellow"),
            (f"{tool_name}({args})", "yellow"),
        ),
    )


def render_tool_result(tool_name: str, output: str, ok: bool) -> Group:
    """A tool's output. Truncated if very long."""
    style = "dim green" if ok else "dim red"
    arrow_style = "bold green" if ok else "bold red"
    arrow = "←" if ok else "✖"

    # Truncate long output in display; the full thing is in events.jsonl
    display = output
    if len(display) > 1000:
        display = display[:1000] + f"\n... [truncated; {len(output)} chars total]"

    return Group(
        Text.assemble(
            (f"{arrow} ", arrow_style),
            (tool_name, "bold"),
        ),
        Text(display, style=style),
    )


def render_tool_denied(tool_name: str, reason: str) -> Group:
    return Group(
        Text.assemble(
            ("⊘ ", "bold red"),
            (f"{tool_name} denied: {reason}", "red"),
        ),
    )


def render_session_banner(provider: str, model: str, session_id: str,
                          home: str, tools: list[str]) -> Group:
    """Printed once at session start. Logo above, session info in a panel."""
    body = Text()
    body.append("provider  ", style="dim")
    body.append(f"{provider} / {model}\n", style="cyan")
    body.append("session   ", style="dim")
    body.append(f"{session_id}\n", style="cyan")
    body.append("home      ", style="dim")
    body.append(f"{home}\n", style="cyan")
    body.append("tools     ", style="dim")
    body.append(", ".join(tools) or "(none)", style="cyan")
    info = Panel(body, border_style="dim", expand=False)
    return Group(
        Text(),       # blank line above the logo
        render_logo(),
        Text(),       # blank line between logo and info
        info,
    )


def render_footer_line(tokens_in: int, tokens_out: int,
                      n_events: int, show_events: bool) -> Text:
    """One-line summary for after a turn."""
    parts = [f"tokens in/out: {tokens_in}/{tokens_out}"]
    if show_events:
        parts.append(f"events: {n_events}")
    return Text("  ·  ".join(parts), style="dim")


def render_help() -> Group:
    return Group(
        Text(),
        Text("commands:", style="bold"),
        Text("  /help           show this message"),
        Text("  /exit, /quit    end the session"),
        Text("  /clear          clear the conversation (start fresh)"),
        Text("  /sessions       list past sessions"),
        Text("  Ctrl+D          end the session"),
        Text(),
    )
