"""Side-by-side comparison of N recorded sessions (0019).

Used by `arc compare <id1> <id2> [<id3> ...]` and by the batch driver
which auto-launches compare at the end of a multi-target replay.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SessionSummary:
    """Per-session metrics extracted from events.jsonl + meta.json."""
    session_id: str
    provider: str = "?"
    model: str = "?"
    user_input: str = ""           # first user message (for the comparison header)
    turns: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    wallclock_seconds: float = 0.0
    final_response: str = ""
    final_stop_reason: str = ""
    aborted_reason: str | None = None


@dataclass
class Turn:
    """One turn extracted for the turn-by-turn diff view."""
    index: int
    user_input: str = ""
    assistant_text: str = ""
    tool_calls: list[tuple[str, dict]] = field(default_factory=list)


def summarize_session(session_dir: Path) -> SessionSummary:
    """Walk events.jsonl + meta.json; produce a SessionSummary."""
    events_path = session_dir / "events.jsonl"
    if not events_path.is_file():
        return SessionSummary(session_id=session_dir.name)

    events = _load_events(events_path)
    if not events:
        return SessionSummary(session_id=session_dir.name)

    s = SessionSummary(session_id=session_dir.name)

    first_ts = events[0].get("ts")
    last_ts = events[-1].get("ts")
    s.wallclock_seconds = _ts_delta_seconds(first_ts, last_ts)

    for e in events:
        etype = e.get("type", "")
        payload = e.get("payload", {}) or {}

        if etype == "session.started":
            s.provider = str(payload.get("provider", "?"))
            s.model = str(payload.get("model", "?"))
        elif etype == "turn.started":
            s.turns += 1
            if s.turns == 1:
                s.user_input = str(payload.get("user_input", ""))
        elif etype == "tool.call.started":
            s.tool_calls += 1
        elif etype == "llm.call.completed":
            s.input_tokens += int(payload.get("input_tokens", 0) or 0)
            s.output_tokens += int(payload.get("output_tokens", 0) or 0)
            # Track the running last response text — overwritten each turn
            content = payload.get("content") or []
            text_parts = []
            for b in content:
                if isinstance(b, dict) and b.get("type") == "text":
                    text_parts.append(b.get("text") or "")
            if text_parts:
                s.final_response = " ".join(text_parts)
            stop = payload.get("stop_reason")
            if stop:
                s.final_stop_reason = str(stop)
        elif etype == "session.aborted":
            s.aborted_reason = str(payload.get("reason", "?"))
            s.cost_usd = float(payload.get("running_usd", s.cost_usd))

    return s


def cost_for_summary(summary: SessionSummary, pricing_table) -> float:
    """Look up rates and compute cost from accumulated tokens if not already
    set by a session.aborted event."""
    if summary.cost_usd:
        return summary.cost_usd
    cost = pricing_table.estimate_cost_usd(
        provider=summary.provider,
        model=summary.model,
        input_tokens=summary.input_tokens,
        output_tokens=summary.output_tokens,
    )
    return cost or 0.0


def extract_turns(session_dir: Path) -> list[Turn]:
    """Walk events.jsonl producing per-turn user/assistant/tool snapshots."""
    events_path = session_dir / "events.jsonl"
    if not events_path.is_file():
        return []
    events = _load_events(events_path)

    turns: list[Turn] = []
    current: Turn | None = None
    for e in events:
        etype = e.get("type", "")
        payload = e.get("payload", {}) or {}
        # The runtime writes the big fields in `content`, not `payload` —
        # same envelope split the replay loader and resume read.
        content = e.get("content", {}) or {}

        if etype == "turn.started":
            if current is not None:
                turns.append(current)
            current = Turn(
                index=len(turns) + 1,
                user_input=str(content.get("user_input")
                               or payload.get("user_input", "")),
            )
        elif etype == "tool.call.started" and current is not None:
            tool_name = str(payload.get("tool_name") or payload.get("name", "?"))
            tool_input = content.get("input") or payload.get("input") or {}
            current.tool_calls.append((tool_name, dict(tool_input) if isinstance(tool_input, dict) else {}))
        elif etype == "llm.call.completed" and current is not None:
            blocks = content.get("response_content") or payload.get("content") or []
            text_parts = []
            for b in blocks:
                if isinstance(b, dict) and b.get("type") == "text":
                    t = b.get("text")
                    if t:
                        text_parts.append(t)
            if text_parts:
                current.assistant_text = " ".join(text_parts)
        elif etype == "turn.ended" and current is not None:
            turns.append(current)
            current = None

    if current is not None:
        turns.append(current)
    return turns


# ── Rich rendering ─────────────────────────────────────────────────────────


def render_summary_table(
    summaries: list[SessionSummary],
    *,
    pricing_table=None,
) -> str:
    """Render the multi-session summary table as a string (Rich-formatted)."""
    from io import StringIO

    from rich.console import Console
    from rich.table import Table

    table = Table(title=None, show_header=True, header_style="bold")
    table.add_column("Metric", style="dim")
    for s in summaries:
        # Truncate session ids visually so wide tables fit
        label = f"{s.session_id[:14]}\n{s.provider}/{s.model}"
        if s.aborted_reason:
            label += f"\n[red]aborted: {s.aborted_reason}[/red]"
        table.add_column(label)

    table.add_row("Turns",        *[str(s.turns) for s in summaries])
    table.add_row("Tool calls",   *[str(s.tool_calls) for s in summaries])
    table.add_row("Input tokens", *[f"{s.input_tokens:,}" for s in summaries])
    table.add_row("Output tokens",*[f"{s.output_tokens:,}" for s in summaries])

    costs = [
        cost_for_summary(s, pricing_table) if pricing_table else s.cost_usd
        for s in summaries
    ]
    table.add_row("Cost (USD)",   *[f"${c:.4f}" for c in costs])
    table.add_row("Wall time",    *[f"{s.wallclock_seconds:.1f}s" for s in summaries])
    table.add_row("Stop reason",  *[s.final_stop_reason for s in summaries])
    table.add_row(
        "Final response",
        *[(s.final_response[:60] + "…") if len(s.final_response) > 60 else s.final_response
          for s in summaries],
    )

    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=140)
    console.print(table)
    return buf.getvalue()


def render_turn_diff(a: list[Turn], b: list[Turn], *,
                     label_a: str = "A", label_b: str = "B") -> str:
    """Two-column turn-by-turn diff as a Rich-rendered string."""
    from io import StringIO

    from rich.console import Console

    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=140)

    max_turns = max(len(a), len(b))
    for i in range(max_turns):
        ta = a[i] if i < len(a) else None
        tb = b[i] if i < len(b) else None
        idx = i + 1
        ui = (ta.user_input if ta else None) or (tb.user_input if tb else None) or ""
        console.print(f"\n[bold]Turn {idx}[/bold] — user: [italic]{_short(ui, 80)}[/italic]")

        console.print(f"  [cyan]{label_a}:[/cyan] {_short(ta.assistant_text if ta else '(missing)', 100)}")
        if ta:
            for name, inp in ta.tool_calls:
                console.print(f"        → tool: {name}({_short(json.dumps(inp), 60)})")

        console.print(f"  [magenta]{label_b}:[/magenta] {_short(tb.assistant_text if tb else '(missing)', 100)}")
        if tb:
            for name, inp in tb.tool_calls:
                console.print(f"        → tool: {name}({_short(json.dumps(inp), 60)})")

    return buf.getvalue()


def render_full_comparison(session_dirs: list[Path], *, pricing_table=None) -> str:
    """End-to-end: summary table + (if exactly 2 sessions) turn-by-turn diff."""
    summaries = [summarize_session(d) for d in session_dirs]

    out_parts: list[str] = []
    if summaries:
        first = summaries[0]
        out_parts.append(
            f"arc compare — {first.session_id} ({first.provider}/{first.model})"
        )
        if first.user_input:
            out_parts.append(f'    "{_short(first.user_input, 80)}"')
        out_parts.append("")

    out_parts.append(render_summary_table(summaries, pricing_table=pricing_table))

    if len(session_dirs) == 2:
        a_turns = extract_turns(session_dirs[0])
        b_turns = extract_turns(session_dirs[1])
        label_a = f"{summaries[0].provider}/{summaries[0].model}"
        label_b = f"{summaries[1].provider}/{summaries[1].model}"
        out_parts.append("")
        out_parts.append("Turn-by-turn:")
        out_parts.append(render_turn_diff(a_turns, b_turns,
                                          label_a=label_a, label_b=label_b))

    return "\n".join(out_parts)


# ── Helpers ────────────────────────────────────────────────────────────────


def _load_events(path: Path) -> list[dict]:
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _ts_delta_seconds(first_ts, last_ts) -> float:
    if not first_ts or not last_ts:
        return 0.0
    try:
        from datetime import datetime
        a = datetime.fromisoformat(first_ts.replace("Z", "+00:00"))
        b = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
        return (b - a).total_seconds()
    except (ValueError, AttributeError):
        return 0.0


def _short(s: str, n: int) -> str:
    s = (s or "").strip().replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"
