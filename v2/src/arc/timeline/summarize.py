"""One session's events.jsonl → a node-cache dict.

The node cache (`<sid>/timeline.node.json`) holds per-turn summaries + totals
so a forest rebuild is meta-scan + read-N-small-JSONs, never re-parsing every
session's events. Written at session end; this module is the builder.

Field locations matter: the runtime writes big fields under `content`
(user_input, response_content) and small ones under `payload` (tokens,
tool_name). `compare.summarize_session` reads some of these from the wrong
dict — we read the right ones here.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from arc.runtime.events import EventType

DEFAULT_SUMMARY_MAX_CHARS = 400


def build_node_cache(session_dir: Path, *, summary_max_chars: int = DEFAULT_SUMMARY_MAX_CHARS) -> dict[str, Any]:
    """Parse one session into its node-cache dict. One pass over events.jsonl.

    Returns the SessionNode-shaped dict WITHOUT lineage (that comes from
    meta in scan) — session totals, status, and per-turn summaries.
    """
    events = _load_events(session_dir / "events.jsonl")

    provider = model = "?"
    turns: list[dict] = []
    cur: dict | None = None
    cur_started_ts: str | None = None
    in_tokens = out_tokens = 0
    aborted = False
    saw_session_end = False

    def _flush(end_ts: str | None) -> None:
        nonlocal cur, cur_started_ts
        if cur is None:
            return
        cur["duration_s"] = _ts_delta(cur_started_ts, end_ts)
        turns.append(cur)
        cur = None
        cur_started_ts = None

    for e in events:
        t = e.get("type", "")
        payload = e.get("payload", {}) or {}
        content = e.get("content", {}) or {}
        ts = e.get("ts")

        if t == EventType.SESSION_STARTED:
            provider = str(payload.get("provider", "?"))
            model = str(payload.get("model", "?"))
        elif t == EventType.SESSION_ABORTED:
            aborted = True
        elif t == EventType.SESSION_ENDED:
            saw_session_end = True
        elif t == EventType.TURN_STARTED:
            _flush(ts)  # defensive: turn.started without a prior turn.ended
            cur = {
                "index": len(turns) + 1,
                "user_summary": _short(content.get("user_input", ""), summary_max_chars),
                "assistant_summary": "",
                "tool_calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
            }
            cur_started_ts = ts
        elif t == EventType.TOOL_CALL_STARTED and cur is not None:
            cur["tool_calls"] += 1
        elif t == EventType.LLM_CALL_COMPLETED:
            it = int(payload.get("input_tokens", 0) or 0)
            ot = int(payload.get("output_tokens", 0) or 0)
            in_tokens += it
            out_tokens += ot
            if cur is not None:
                cur["input_tokens"] += it
                cur["output_tokens"] += ot
                text = _assistant_text(content.get("response_content", []))
                if text:
                    cur["assistant_summary"] = _short(text, summary_max_chars)
        elif t == EventType.TURN_ENDED:
            _flush(ts)

    _flush(events[-1].get("ts") if events else None)

    status = _status(events, saw_session_end, aborted, turns)

    return {
        "provider": provider,
        "model": model,
        "turn_count": len(turns),
        "input_tokens": in_tokens,
        "output_tokens": out_tokens,
        "status": status,
        "turns": turns,
    }


def write_node_cache(session_dir: Path, *, summary_max_chars: int = DEFAULT_SUMMARY_MAX_CHARS) -> dict[str, Any]:
    """Build and persist the node cache; return it. Called at session end."""
    cache = build_node_cache(session_dir, summary_max_chars=summary_max_chars)
    (session_dir / "timeline.node.json").write_text(
        json.dumps(cache, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return cache


def load_or_build_node_cache(session_dir: Path, *, summary_max_chars: int = DEFAULT_SUMMARY_MAX_CHARS) -> dict[str, Any]:
    """Read the cached node dict if present and parseable, else build fresh.

    The scanner uses this so only the just-ended session re-parses events;
    everyone else is a cheap JSON read.
    """
    cache_path = session_dir / "timeline.node.json"
    if cache_path.is_file():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass  # stale/corrupt cache → rebuild
    return build_node_cache(session_dir, summary_max_chars=summary_max_chars)


# ── helpers ─────────────────────────────────────────────────────────────────


def _load_events(path: Path) -> list[dict]:
    if not path.is_file():
        return []
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


def _assistant_text(blocks: list) -> str:
    parts = [b.get("text", "") for b in blocks
             if isinstance(b, dict) and b.get("type") == "text" and b.get("text")]
    return " ".join(parts).strip()


def _short(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _status(events: list[dict], saw_end: bool, aborted: bool, turns: list) -> str:
    if aborted:
        return "aborted"
    if not turns and not saw_end:
        return "empty"
    if not saw_end:
        return "running"  # no session.ended → still live or hard-killed
    # completed: check the last turn's success if we can infer it; default ok
    return "completed"


def _ts_delta(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.0
    from datetime import datetime
    try:
        return max(0.0, (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds())
    except ValueError:
        return 0.0
