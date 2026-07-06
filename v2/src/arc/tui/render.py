"""Rich-based rendering primitives for the TUI.

Pure rendering functions — they take data, produce Rich renderables.
No state. Composed by the TUI app.

Color names are addressed through the arc.* named style namespace defined
in arc.tui.themes. Swapping a theme can only change colors, never layout.
"""
from __future__ import annotations

from typing import Any

from rich.console import Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from arc.tui.themes import active as _active_theme

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
    """ARC wordmark + v2 tag, themed via arc.brand + arc.dim."""
    lines: list[Text] = []
    for raw in _LOGO_LINES:
        if "v2" in raw:
            head, _, _ = raw.partition("v2")
            t = Text(head, style="arc.brand")
            t.append("v2", style="arc.dim")
            lines.append(t)
        else:
            lines.append(Text(raw, style="arc.brand"))
    return Group(*lines)


def render_user_prefix(prompt_prefix: str) -> Text:
    """The chip that goes before each printed user turn."""
    return Text(prompt_prefix, style="arc.user.prefix")


def render_user_message(text: str, prompt_prefix: str) -> Group:
    """A user's input as it lands in scrollback (after they hit enter)."""
    return Group(
        Text(),
        Text.assemble(
            (prompt_prefix, "arc.user.prefix"),
            (text, "arc.user"),
        ),
    )


def render_assistant_text(text: str) -> Group:
    """The assistant's text response. Rendered as Markdown for code blocks etc."""
    if not text.strip():
        return Group()
    code_theme = _active_theme().code_theme
    return Group(
        Text(),
        Text.assemble(("◆ ", "arc.assistant.glyph"), ("assistant", "arc.assistant.label")),
        Markdown(text, code_theme=code_theme),
    )


def render_tool_call(tool_name: str, tool_input: dict) -> Group:
    """A tool call about to execute."""
    args = ", ".join(f"{k}={v!r}" for k, v in tool_input.items())
    if len(args) > 80:
        args = args[:77] + "..."
    return Group(
        Text(),
        Text.assemble(
            ("→ ", "arc.tool.arrow"),
            (f"{tool_name}({args})", "arc.tool.call"),
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
    body_style = "arc.tool.ok" if ok else "arc.tool.fail"
    arrow_style = "arc.tool.ok.arrow" if ok else "arc.tool.fail.arrow"
    arrow = "←" if ok else "✖"

    lines = output.splitlines()
    n_lines = len(lines)
    n_chars = len(output)

    if n_lines > max_lines:
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
        Text(display, style=body_style) if display else Text(),
    )


def render_tool_denied(tool_name: str, reason: str) -> Group:
    return Group(
        Text.assemble(
            ("⊘ ", "arc.tool.denied.arrow"),
            (f"{tool_name} denied: {reason}", "arc.tool.denied"),
        ),
    )


def render_session_banner(provider: str, model: str, session_id: str,
                          home: str, tools: list[str],
                          resumed_from: str | None = None) -> Group:
    """Printed once at session start. Logo above, session info in a panel."""
    body = Text()
    body.append("provider  ", style="arc.dim")
    body.append(f"{provider} / {model}\n", style="arc.info")
    body.append("session   ", style="arc.dim")
    body.append(f"{session_id}\n", style="arc.info")
    if resumed_from:
        body.append("resumed   ", style="arc.dim")
        body.append(f"from {resumed_from}\n", style="arc.resume")
    body.append("home      ", style="arc.dim")
    body.append(f"{home}\n", style="arc.info")
    body.append("tools     ", style="arc.dim")
    body.append(", ".join(tools) or "(none)", style="arc.info")
    info = Panel(body, border_style="arc.dim", expand=False)
    return Group(
        Text(),
        render_logo(),
        Text(),
        info,
    )


def render_footer_line(tokens_in: int, tokens_out: int,
                      n_events: int, show_events: bool) -> Group:
    """One-line summary for after a turn."""
    parts = [f"tokens in/out: {tokens_in}/{tokens_out}"]
    if show_events:
        parts.append(f"events: {n_events}")
    return Group(
        Text(),
        Text("  ·  ".join(parts), style="arc.dim"),
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
        Text("time travel (0026)", style="bold"),
        Text("  /rewind               show the turn map of this conversation"),
        Text("  /rewind N             arm a branch at turn N — the next prompt "
             "forks the conversation there (empty input cancels)"),
        Text("  /retry                re-ask the last prompt on a fresh branch "
             "(same question, new roll)"),
        Text("  /model X | prov/X     continue this conversation on another "
             "model — session-scoped, config.yml untouched"),
        Text("  /tab [N]              list open tabs / switch (also alt+1…9); "
             "branches open in a new tab, /exit closes one"),
        Text("  note: branching starts a new session; the original stays "
             "recorded and resumable"),
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
        Text("config (see ", style="bold", end="") + Text("$ARC_HOME/config.yml", style="bold arc.info") + Text(")", style="bold"),
        Text("  provider.name         'gemini' | 'anthropic' | 'ollama' | 'llama_cpp'"),
        Text("  provider.model        e.g. claude-haiku-4-5, gemini-2.5-flash, llama3.1:8b"),
        Text("  runtime.system_prompt the base prompt the agent operates under"),
        Text("  tools.enabled         which tools are available this session"),
        Text("  tui.show_thinking     render extended-thinking blocks in TUI"),
        Text("  tui.toolbar_enabled   bottom toolbar with provider/tokens/$ cost"),
        Text("  tui.theme             color theme name (see `arc setup` → Themes)"),
        Text(),
        Text("arc home (see ", style="bold", end="") + Text("$ARC_HOME/", style="bold arc.info") + Text(")", style="bold"),
        Text("  config.yml            main config (above)"),
        Text("  catalog.yml           model menu shown by `arc setup` (0017)"),
        Text("  llm_servers.yml       llama-server registry for `arc llm` (0018)"),
        Text("  sessions/             per-session events.jsonl + session.log"),
        Text("  llm/                  current.pid + current.log for the local inference server"),
        Text(),
        Text("more CLI commands", style="bold"),
        Text("  arc setup             interactive setup hub — navigate sections "
             "(provider, plugins, themes, sub-agents, replay, llm, wipe, …)"),
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


def render_turn_map(turns: list[Any]) -> Group:
    """Compact one-line-per-turn map for /rewind — index, user ask, answer.

    `turns` are replay.compare.Turn objects (duck-typed: index, user_input,
    assistant_text).
    """
    def _short(s: str, n: int = 58) -> str:
        s = " ".join((s or "").split())
        return s if len(s) <= n else s[: n - 1] + "…"

    lines: list[Text] = [Text(), Text("turn map", style="bold")]
    for t in turns:
        row = Text()
        row.append(f"  {t.index:>3}  ", style="arc.brand")
        row.append(_short(t.user_input), style="arc.user.prefix")
        lines.append(row)
        answer = _short(t.assistant_text)
        if answer:
            lines.append(Text(f"       {answer}", style="arc.dim"))
    lines.append(Text())
    lines.append(Text("  /rewind N to branch at turn N", style="arc.dim"))
    return Group(*lines)


def render_turn_card(turn: Any, total: int) -> Group:
    """Two-line card for one turn — printed by rewind mode on each ←/→ step."""
    def _short(s: str, n: int = 70) -> str:
        s = " ".join((s or "").split())
        return s if len(s) <= n else s[: n - 1] + "…"

    return Group(
        Text.assemble(
            ("┌ ", "arc.dim"),
            (f"turn {turn.index}/{total}", "arc.brand"),
            (" ── you: ", "arc.dim"),
            (_short(turn.user_input), "arc.user.prefix"),
        ),
        Text.assemble(
            ("└ arc: ", "arc.dim"),
            (_short(turn.assistant_text), "arc.dim"),
        ),
    )


def render_branch_notice(source_sid: str, at_turn: int, new_sid: str,
                         n_messages: int) -> Group:
    """Divider printed after a /rewind or /retry branch lands."""
    return Group(
        Text(),
        Text.assemble(
            ("⑂ ", "arc.brand"),
            (f"branched {source_sid} @ turn {at_turn} → ", "arc.dim"),
            (new_sid, "arc.info"),
            (f"  ({n_messages} messages restored)", "arc.dim"),
        ),
    )


def render_thinking(text: str) -> Group:
    """Anthropic 3.7+/4+ thinking block, visually subordinate to assistant text."""
    if not text.strip():
        return Group()
    return Group(
        Text(),
        Text.assemble(("◇ ", "arc.thinking.glyph"), ("thinking", "arc.thinking")),
        Text(text, style="arc.thinking"),
    )


def render_subagent_dispatched(
    *, spec_name: str, provider: str, model: str, child_session_id: str,
) -> Group:
    """Header line printed when a sub-agent dispatch begins."""
    short_sid = child_session_id[:14] + "…" if len(child_session_id) > 14 else child_session_id
    return Group(
        Text(""),
        Text.assemble(
            ("↻ subagent ", "arc.subagent"),
            (spec_name, "arc.subagent.name"),
            (f"  ({provider}/{model})", "arc.dim"),
            (f"  child={short_sid}", "arc.dim"),
        ),
    )


def render_subagent_activity(
    *, message: str, tool_name: str | None = None, tool_input: dict | None = None,
    failed: bool = False,
) -> Text:
    """One nested line of a sub-agent's live activity (a child tool call).

    Indented under the dispatch header so the child's trace reads as a
    sub-level of the main transcript, not as the parent's own tool calls.
    """
    glyph_style = "arc.subagent.fail.glyph" if failed else "arc.subagent"
    if tool_name:
        args = ", ".join(f"{k}={v!r}" for k, v in (tool_input or {}).items())
        if len(args) > 72:
            args = args[:69] + "..."
        body = f"{tool_name}({args})" if not failed else message
    else:
        body = message
    return Text.assemble(
        ("   ↳ ", glyph_style),
        (body, "arc.dim" if not failed else "arc.subagent.fail"),
    )


def render_subagent_done(
    *,
    spec_name: str,
    status: str,
    turns: int,
    tool_calls: int,
    cost_usd: float,
    wallclock_s: float,
    error_message: str | None = None,
) -> Group:
    """One-line summary printed when a sub-agent dispatch ends."""
    if status == "ok":
        glyph = ("✓", "arc.subagent.ok.glyph")
        status_color = "arc.subagent.ok"
    elif status in ("timeout", "cancelled", "user_cancelled"):
        glyph = ("✗", "arc.subagent.warn.glyph")
        status_color = "arc.subagent.warn"
    else:
        glyph = ("✗", "arc.subagent.fail.glyph")
        status_color = "arc.subagent.fail"

    parts = [
        glyph,
        (" subagent ", ""),
        (spec_name, "bold"),
        (" → ", "arc.dim"),
        (status, status_color),
        ("  (", "arc.dim"),
        (f"{turns} turns", "arc.dim"),
        (", ", "arc.dim"),
        (f"{tool_calls} tool calls", "arc.dim"),
        (", ", "arc.dim"),
        (f"${cost_usd:.4f}" if cost_usd >= 0.0001 else "<$0.0001", "arc.dim"),
        (", ", "arc.dim"),
        (f"{wallclock_s:.1f}s", "arc.dim"),
        (")", "arc.dim"),
    ]
    lines = [Text.assemble(*parts)]
    if error_message:
        lines.append(Text(f"  {error_message[:200]}", style="arc.error"))
    return Group(*lines)


def render_turn_separator() -> Text:
    """Subtle horizontal line between turns. Renders full terminal width."""
    return Text("─" * 80, style="arc.dim")


def render_sessions_table(sessions_dir, index_path) -> Any:
    """Render sessions/index.jsonl as a Rich table for /sessions command."""
    import json

    from rich.table import Table

    table = Table(show_header=True, header_style="arc.table.header", box=None)
    table.add_column("session_id", style="arc.info", no_wrap=True)
    table.add_column("started_at", style="arc.dim")
    table.add_column("provider/model")
    table.add_column("chain", style="arc.dim")

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
        return Text("no sessions recorded yet", style="arc.dim")
    return Group(
        Text(),
        Text(f"{n} session(s)", style="bold arc.dim"),
        table,
        Text(),
    )
