"""Rich-based rendering primitives for the TUI.

Pure rendering functions — they take data, produce Rich renderables.
No state. Composed by the TUI app.
"""
from __future__ import annotations

from typing import Any

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


def render_tool_result(tool_name: str, output: str, ok: bool,
                       max_lines: int = 30) -> Group:
    """A tool's output. Collapses to a summary when too long.

    Long output gets a "(N lines, M chars — full output in session.log)"
    summary instead of dumping into the terminal. Full bytes stay in
    events.jsonl + session.log. Critical for Ghidra-class tools whose
    outputs can be tens of thousands of chars per call.
    """
    style = "dim green" if ok else "dim red"
    arrow_style = "bold green" if ok else "bold red"
    arrow = "←" if ok else "✖"

    lines = output.splitlines()
    n_lines = len(lines)
    n_chars = len(output)

    if n_lines > max_lines:
        # Show first ~5 lines + last ~5 lines, with elision in the middle
        head_n = min(5, max_lines // 2)
        tail_n = min(5, max_lines - head_n)
        head = "\n".join(lines[:head_n])
        tail = "\n".join(lines[-tail_n:])
        display = (
            f"{head}\n"
            f"  ⋮ [+{n_lines - head_n - tail_n} lines elided — see session.log for full output]\n"
            f"{tail}"
        )
        summary = f"{tool_name} ({n_lines} lines, {n_chars:,} chars — collapsed)"
    else:
        display = output
        summary = tool_name

    return Group(
        Text.assemble(
            (f"{arrow} ", arrow_style),
            (summary, "bold"),
        ),
        Text(display, style=style) if display else Text(),
    )


def render_tool_denied(tool_name: str, reason: str) -> Group:
    return Group(
        Text.assemble(
            ("⊘ ", "bold red"),
            (f"{tool_name} denied: {reason}", "red"),
        ),
    )


def render_session_banner(provider: str, model: str, session_id: str,
                          home: str, tools: list[str],
                          resumed_from: str | None = None) -> Group:
    """Printed once at session start. Logo above, session info in a panel.

    `resumed_from` is shown when the TUI was launched via `arc resume <id>` —
    helps the user know this isn't a fresh session.
    """
    body = Text()
    body.append("provider  ", style="dim")
    body.append(f"{provider} / {model}\n", style="cyan")
    body.append("session   ", style="dim")
    body.append(f"{session_id}\n", style="cyan")
    if resumed_from:
        body.append("resumed   ", style="dim")
        body.append(f"from {resumed_from}\n", style="bold magenta")
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
                      n_events: int, show_events: bool) -> Group:
    """One-line summary for after a turn, with a leading blank line so it
    doesn't sit flush against the assistant's last response."""
    parts = [f"tokens in/out: {tokens_in}/{tokens_out}"]
    if show_events:
        parts.append(f"events: {n_events}")
    return Group(
        Text(),
        Text("  ·  ".join(parts), style="dim"),
    )


def render_help() -> Group:
    return Group(
        Text(),
        Text("slash commands", style="bold"),
        Text("  /help                 show this message"),
        Text("  /exit, /quit          end the session"),
        Text("  /clear                reset the conversation in place "
             "(same session_id, audit trail captures the clear)"),
        Text("  /sessions             list past sessions in a table"),
        Text("  /replay               cross-provider replay menu — pick a session "
             "and re-run it against any provider/model (0019)"),
        Text(),
        Text("keybinds", style="bold"),
        Text("  Tab                   autocomplete slash commands"),
        Text("  ↑ / ↓                 recall input history"),
        Text("  Ctrl+C                pause running turn / cancel current input"),
        Text("  Ctrl+D                end the session"),
        Text(),
        Text("env vars", style="bold"),
        Text("  ARC_HOME              full path to arc dir (default: ~/.arc)"),
        Text("  GEMINI_API_KEY        Gemini API key"),
        Text("  ANTHROPIC_API_KEY     Anthropic API key"),
        Text("  OLLAMA_API_KEY        Ollama key (placeholder; stock Ollama ignores it)"),
        Text("  LLAMA_CPP_API_KEY     llama-server key (honored when --api-key is set)"),
        Text(),
        Text("config (see ", style="bold", end="") + Text("$ARC_HOME/config.yml", style="bold cyan") + Text(")", style="bold"),
        Text("  provider.name         'gemini' | 'anthropic' | 'ollama' | 'llama_cpp'"),
        Text("  provider.model        e.g. claude-haiku-4-5, gemini-2.5-flash, llama3.1:8b"),
        Text("  runtime.system_prompt the base prompt the agent operates under"),
        Text("  tools.enabled         which tools are available this session"),
        Text("  tui.show_thinking     render extended-thinking blocks in TUI"),
        Text("  tui.toolbar_enabled   bottom toolbar with provider/tokens/$ cost"),
        Text(),
        Text("arc home (see ", style="bold", end="") + Text("$ARC_HOME/", style="bold cyan") + Text(")", style="bold"),
        Text("  config.yml            main config (above)"),
        Text("  catalog.yml           model menu shown by `arc setup` (0017)"),
        Text("  llm_servers.yml       llama-server registry for `arc llm` (0018)"),
        Text("  sessions/             per-session events.jsonl + session.log"),
        Text("  llm/                  current.pid + current.log for the local inference server"),
        Text(),
        Text("more CLI commands", style="bold"),
        Text("  arc setup             interactive provider/model picker — "
             "drops into a session after writing config (0017)"),
        Text("  arc llm <action>      manage local llama-server: list, status, "
             "start <id>, stop, restart <id>, logs (0018)"),
        Text("  arc log <id>          print a session's session.log"),
        Text("  arc replay <id>       byte-identical replay (mode 2) / --live-llm (mode 3) / "
             "--override-provider/--override-model / --against / --max-cost-usd (0019)"),
        Text("  arc compare <id …>    side-by-side summary of 2+ recorded sessions (0019)"),
        Text("  arc resume <id>       continue a recorded session"),
        Text("  arc rerun <id>        replay user inputs against fresh agent"),
        Text("  arc wipe              clean sessions (default) / --all / --llm / "
             "--history / --dry-run"),
        Text(),
    )


def render_thinking(text: str) -> Group:
    """Anthropic 3.7+/4+ thinking block, rendered in dim italic so it
    visually subordinates to the assistant's actual response."""
    if not text.strip():
        return Group()
    return Group(
        Text(),
        Text.assemble(("◇ ", "dim cyan"), ("thinking", "dim italic")),
        Text(text, style="dim italic"),
    )


def render_turn_separator() -> Text:
    """Subtle horizontal line between turns. Renders full terminal width."""
    return Text("─" * 80, style="dim")


def render_sessions_table(sessions_dir, index_path) -> Any:
    """Render sessions/index.jsonl as a Rich table for /sessions command."""
    import json
    from rich.table import Table

    table = Table(show_header=True, header_style="bold cyan", box=None)
    table.add_column("session_id", style="cyan", no_wrap=True)
    table.add_column("started_at", style="dim")
    table.add_column("provider/model")
    table.add_column("chain", style="dim")

    n = 0
    for line in index_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        sid = entry.get("session_id", "?")
        started = entry.get("started_at", "?")[:19].replace("T", " ")
        provider = entry.get("provider", "?")
        model = entry.get("model", "?")

        # Try to read meta.json for chain markers
        chain_bits = []
        meta_path = sessions_dir / sid / "meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
                if meta.get("resumed_from"):
                    chain_bits.append(f"resumed from {meta['resumed_from'][:12]}...")
                if meta.get("replay_of"):
                    chain_bits.append(f"replay of {meta['replay_of'][:12]}...")
                if meta.get("rerun_of"):
                    chain_bits.append(f"rerun of {meta['rerun_of'][:12]}...")
                if meta.get("branched_at_turn") is not None:
                    chain_bits.append(f"branch @ turn {meta['branched_at_turn']}")
            except Exception:
                pass

        table.add_row(sid, started, f"{provider}/{model}", " · ".join(chain_bits) or "—")
        n += 1

    if n == 0:
        return Text("no sessions recorded yet", style="dim")
    return Group(
        Text(),
        Text(f"{n} session(s)", style="bold dim"),
        table,
        Text(),
    )
