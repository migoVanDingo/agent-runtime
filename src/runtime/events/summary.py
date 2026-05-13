"""Session summary aggregation.

Aggregates the runtime.jsonl event stream for a session into a single
``session.summary.json`` document. Written once at session-end so analysts can
load a one-line index per session without scanning the full event log.

Schema (v1):
    {
      "session_id": "...",
      "model_run_id": null | "...",
      "started_at": "...",
      "ended_at": "...",
      "n_turns": int,
      "n_llm_calls": int,
      "n_tool_calls": int,
      "n_replans": int,
      "n_errors": int,
      "total_input_tokens": int,
      "total_output_tokens": int,
      "total_cache_input_tokens": int,
      "total_cost_usd": float,
      "p95_llm_latency_ms": int | null,
      "models_seen": [str, ...],
      "skills_used": [str, ...],
      "outcome": "completed" | "failed" | "cancelled",
      "first_user_message_preview": "...",
      "last_assistant_message_preview": "...",
      "system_prompt_hash": "..." | null
    }

The static system prompt (large, mostly constant per session) is captured once
on the first ``llm.call.started`` content blob; its sha1 hash is included so
analysts can dedupe across sessions when comparing models.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * pct / 100.0
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return s[int(k)]
    return s[f] + (s[c] - s[f]) * (k - f)


def _preview(text: str, limit: int) -> str:
    text = text.strip().replace("\n", " ")
    return text[:limit]


def write_session_summary(session_id: str, *, outcome: str = "completed") -> Path | None:
    """Aggregate the session's runtime.jsonl into session.summary.json.

    Returns the written path, or None when there's no event log to read.
    Safe to call multiple times — overwrites the existing summary file.
    """
    from session_paths import session_dir, events_dir

    jsonl = events_dir(session_id) / "runtime.jsonl"
    if not jsonl.exists():
        return None

    n_turns = n_llm = n_tool = n_replan = n_errors = 0
    total_in = total_out = total_cache_in = 0
    total_cost = 0.0
    llm_latencies: list[float] = []
    models: set[str] = set()
    skills: set[str] = set()
    started_at: str | None = None
    ended_at: str | None = None
    first_user: str = ""
    last_assistant: str = ""
    model_run_id: str | None = None
    system_prompt_hash: str | None = None

    with open(jsonl, "r", encoding="utf-8") as f:
        for line in f:
            try:
                ev = json.loads(line)
            except Exception:
                continue
            t = ev.get("event_type", "")
            ts = ev.get("ts")
            if ts and started_at is None:
                started_at = ts
            if ts:
                ended_at = ts
            if ev.get("model_run_id") and model_run_id is None:
                model_run_id = ev["model_run_id"]

            if t == "turn.started":
                n_turns += 1
            elif t == "llm.call.completed":
                n_llm += 1
                if ev.get("input_tokens"):
                    total_in += int(ev["input_tokens"])
                if ev.get("output_tokens"):
                    total_out += int(ev["output_tokens"])
                if ev.get("cache_input_tokens"):
                    total_cache_in += int(ev["cache_input_tokens"])
                if ev.get("cost_usd"):
                    total_cost += float(ev["cost_usd"])
                if ev.get("duration_ms"):
                    llm_latencies.append(float(ev["duration_ms"]))
                if ev.get("model"):
                    models.add(ev["model"])
            elif t == "llm.call.started" and system_prompt_hash is None:
                # Capture the system prompt hash once per session.
                sys_text = (ev.get("content") or {}).get("system")
                if isinstance(sys_text, str) and sys_text:
                    system_prompt_hash = hashlib.sha1(sys_text.encode("utf-8")).hexdigest()
            elif t == "tool.call.completed":
                n_tool += 1
            elif t == "replan.triggered":
                n_replan += 1
            elif t == "error.raised":
                n_errors += 1
            elif t == "skill.expanded":
                name = (ev.get("payload") or {}).get("skill_name")
                if name:
                    skills.add(name)
            elif t == "conversation.message.added":
                payload = ev.get("payload") or {}
                content = ev.get("content") or {}
                role = payload.get("role") or content.get("role")
                body = content.get("content")
                text = body if isinstance(body, str) else ""
                if role == "user" and not first_user:
                    first_user = _preview(text, 300)
                elif role == "assistant":
                    last_assistant = _preview(text, 300)

    summary: dict[str, Any] = {
        "session_id": session_id,
        "model_run_id": model_run_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "n_turns": n_turns,
        "n_llm_calls": n_llm,
        "n_tool_calls": n_tool,
        "n_replans": n_replan,
        "n_errors": n_errors,
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "total_cache_input_tokens": total_cache_in,
        "total_cost_usd": round(total_cost, 6),
        "p95_llm_latency_ms": int(_percentile(llm_latencies, 95.0) or 0) if llm_latencies else None,
        "models_seen": sorted(models),
        "skills_used": sorted(skills),
        "outcome": outcome,
        "first_user_message_preview": first_user,
        "last_assistant_message_preview": last_assistant,
        "system_prompt_hash": system_prompt_hash,
    }

    target = session_dir(session_id) / "session.summary.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    return target
