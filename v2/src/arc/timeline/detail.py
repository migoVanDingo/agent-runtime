"""Full per-session detail for session.html (0027 phase b).

Unlike summarize.py (truncated summaries for the forest), this reads a
session's events.jsonl and produces the *complete* turn-by-turn content —
user prompts, assistant text, and each tool call's input + output — capped
only by full_output_max_chars. Written once at session end; immutable after.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from arc.runtime.events import EventType

DEFAULT_FULL_OUTPUT_MAX_CHARS = 20000


def build_session_detail(session_dir: Path, *, full_output_max_chars: int = DEFAULT_FULL_OUTPUT_MAX_CHARS) -> dict[str, Any]:
    """One pass over events.jsonl → a JSON-serializable detail dict.

    Shape: {sid, turns: [{index, user, assistant, thinking, tools: [{name,
    input, output}]}]}. Tool outputs over the cap are truncated with a marker.
    """
    sid = session_dir.name
    events = _load(session_dir / "events.jsonl")

    turns: list[dict] = []
    cur: dict | None = None
    pending_tool: dict | None = None  # tool.call.started awaiting its completed

    for e in events:
        t = e.get("type", "")
        payload = e.get("payload", {}) or {}
        content = e.get("content", {}) or {}

        if t == EventType.TURN_STARTED:
            if cur is not None:
                turns.append(cur)
            cur = {"index": len(turns) + 1,
                   "user": str(content.get("user_input", "")),
                   "assistant": "", "thinking": "", "tools": []}
        elif cur is None:
            continue
        elif t == EventType.LLM_CALL_COMPLETED:
            blocks = content.get("response_content", []) or []
            txt = _join(blocks, "text")
            think = _join(blocks, "thinking")
            if txt:
                cur["assistant"] = txt
            if think:
                cur["thinking"] = think
        elif t == EventType.TOOL_CALL_STARTED:
            pending_tool = {
                "name": payload.get("tool_name", "?"),
                "input": content.get("input", {}),
                "output": "",
            }
            cur["tools"].append(pending_tool)
        elif t in (EventType.TOOL_CALL_COMPLETED, EventType.TOOL_CALL_FAILED,
                   EventType.TOOL_CALL_DENIED):
            out = content.get("output")
            if out is None:
                out = payload.get("error_message") or payload.get("reason") or ""
            out = _cap(str(out), full_output_max_chars, sid)
            if pending_tool is not None:
                pending_tool["output"] = out
                pending_tool = None
            elif cur["tools"]:
                cur["tools"][-1]["output"] = out

    if cur is not None:
        turns.append(cur)

    return {"sid": sid, "turns": turns}


# ── helpers ─────────────────────────────────────────────────────────────────


def _load(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _join(blocks: list, kind: str) -> str:
    parts = [b.get("text", "") for b in blocks
             if isinstance(b, dict) and b.get("type") == kind and b.get("text")]
    return "\n".join(parts).strip()


def _cap(s: str, n: int, sid: str) -> str:
    if len(s) <= n:
        return s
    return s[:n] + f"\n… (truncated; full output: arc log {sid})"
