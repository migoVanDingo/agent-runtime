"""Parse a recorded session into typed ReplayData for the replay engine.

A session on disk is:
  sessions/<id>/
    events.jsonl         — one canonical event per line
    meta.json            — session-level metadata
    config.snapshot.yml  — the config at session start (replay uses this!)

This loader produces a `ReplayData` with the queues + tables the rest of
the replay engine consumes. It does NOT mutate the original session.

Tool outputs are stored two ways for the two replay modes:
  - `tool_outputs_in_order`     — FIFO per tool name, for mode 2
  - `tool_outputs_by_call`      — table keyed by (name, canonical_input), for mode 3
"""
from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from arc.replay.errors import MissingRecordingError
from arc.runtime.events import EventType
from arc.runtime.hooks import ContentBlock, LLMResponse


@dataclass
class ReplayData:
    """Everything the replay engine needs from one recorded session.

    Built once at start of replay; consumed by ReplayProvider + replay tools
    as the runtime makes calls.
    """
    source_session_id: str
    source_dir: Path
    config_snapshot_yaml: str
    user_inputs: list[str]
    llm_responses: deque[LLMResponse]
    tool_outputs_in_order: dict[str, deque[str]]
    tool_outputs_by_call: dict[tuple[str, str], deque[str]]
    # Original tool description + input_schema, by tool name. We capture these
    # from the recorded llm.call.started events so replay stubs can mirror
    # them — otherwise the runtime emits llm.call.started with the stub's
    # generic description and the new event log spuriously diverges from
    # the original.
    tool_specs: dict[str, dict]  # name → {"description": str, "input_schema": dict}
    raw_events: list[dict]  # for the diff layer


def load(session_dir: Path) -> ReplayData:
    """Parse a session directory into ReplayData. Raises MissingRecordingError
    on any structural problem so the CLI can show a clear message."""
    if not session_dir.is_dir():
        raise MissingRecordingError(f"not a directory: {session_dir}")

    events_path = session_dir / "events.jsonl"
    snapshot_path = session_dir / "config.snapshot.yml"
    if not events_path.is_file():
        raise MissingRecordingError(f"missing events.jsonl in {session_dir}")
    if not snapshot_path.is_file():
        raise MissingRecordingError(
            f"missing config.snapshot.yml in {session_dir} — "
            f"can't replay without the original config"
        )

    # Parse raw events
    raw_events: list[dict] = []
    for i, line in enumerate(events_path.read_text(encoding="utf-8").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            raw_events.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise MissingRecordingError(
                f"events.jsonl:{i+1} is not valid JSON: {e}"
            ) from e

    # Walk events to build the typed structures
    user_inputs = _extract_user_inputs(raw_events)
    llm_responses = _extract_llm_responses(raw_events)
    tool_outputs_in_order, tool_outputs_by_call = _extract_tool_outputs(raw_events)
    tool_specs = _extract_tool_specs(raw_events)

    return ReplayData(
        source_session_id=_session_id_from_events(raw_events, session_dir.name),
        source_dir=session_dir,
        config_snapshot_yaml=snapshot_path.read_text(encoding="utf-8"),
        user_inputs=user_inputs,
        llm_responses=llm_responses,
        tool_outputs_in_order=tool_outputs_in_order,
        tool_outputs_by_call=tool_outputs_by_call,
        tool_specs=tool_specs,
        raw_events=raw_events,
    )


# ── Extraction helpers ─────────────────────────────────────────────────────


def _session_id_from_events(events: list[dict], fallback: str) -> str:
    for e in events:
        if e.get("session_id"):
            return e["session_id"]
    return fallback


def _extract_user_inputs(events: list[dict]) -> list[str]:
    """One per turn — from turn.started.content.user_input.

    The loop emits turn.started with the user text in content. Each turn
    becomes one entry in the queue; the CLI feeds them to run_turn() in order.
    """
    return [
        e["content"]["user_input"]
        for e in events
        if e.get("type") == EventType.TURN_STARTED
        and "user_input" in e.get("content", {})
    ]


def _extract_llm_responses(events: list[dict]) -> deque[LLMResponse]:
    """Reconstruct LLMResponse objects from llm.call.completed events.

    The content.response_content field holds the canonical block list we wrote.
    """
    out: deque[LLMResponse] = deque()
    for e in events:
        if e.get("type") != EventType.LLM_CALL_COMPLETED:
            continue
        payload = e.get("payload", {})
        content = e.get("content", {})
        blocks = [_block_from_dict(b) for b in content.get("response_content", [])]
        out.append(LLMResponse(
            content=blocks,
            stop_reason=payload.get("stop_reason", "end_turn"),
            input_tokens=payload.get("input_tokens", 0),
            output_tokens=payload.get("output_tokens", 0),
            raw=content.get("raw_provider_response", {}),
        ))
    return out


def _extract_tool_outputs(
    events: list[dict],
) -> tuple[dict[str, deque[str]], dict[tuple[str, str], deque[str]]]:
    """Build both lookup structures from tool.call.completed events.

    in_order:  tool_name → FIFO of outputs (for mode 2)
    by_call:   (tool_name, canonical_input_json) → FIFO of outputs (for mode 3)
    """
    in_order: dict[str, deque[str]] = defaultdict(deque)
    by_call: dict[tuple[str, str], deque[str]] = defaultdict(deque)

    # We need each tool.call.completed paired with its preceding tool.call.started
    # (which carries the input). Walk both at once.
    last_started_input: dict[str, dict] = {}  # tool_call_id → input

    for e in events:
        t = e.get("type")
        if t == EventType.TOOL_CALL_STARTED:
            tcl_id = e.get("payload", {}).get("tool_call_id", "")
            last_started_input[tcl_id] = e.get("content", {}).get("input", {})
        elif t == EventType.TOOL_CALL_COMPLETED:
            name = e.get("payload", {}).get("tool_name", "")
            tcl_id = e.get("payload", {}).get("tool_call_id", "")
            output = e.get("content", {}).get("output", "")
            in_order[name].append(output)
            input_dict = last_started_input.pop(tcl_id, {})
            key = (name, _canonical(input_dict))
            by_call[key].append(output)

    return dict(in_order), dict(by_call)


def _extract_tool_specs(events: list[dict]) -> dict[str, dict]:
    """Pull tool description + input_schema out of recorded llm.call.started events.

    The runtime emits llm.call.started with content.tools = [{name, description,
    input_schema}, ...]. We grab the first occurrence per tool name so replay
    stubs can mirror it.
    """
    specs: dict[str, dict] = {}
    for e in events:
        if e.get("type") != EventType.LLM_CALL_STARTED:
            continue
        for tool_dict in e.get("content", {}).get("tools", []):
            name = tool_dict.get("name")
            if name and name not in specs:
                specs[name] = {
                    "description": tool_dict.get("description", ""),
                    "input_schema": tool_dict.get("input_schema",
                                                  {"type": "object", "properties": {},
                                                   "required": []}),
                }
    return specs


def _block_from_dict(d: dict) -> ContentBlock:
    """Inverse of loop._block_to_dict. Decodes base64'd metadata bytes."""
    metadata = d.get("metadata")
    if metadata:
        decoded: dict[str, Any] = {}
        for k, v in metadata.items():
            if isinstance(v, dict) and "__bytes_b64__" in v:
                import base64
                decoded[k] = base64.b64decode(v["__bytes_b64__"])
            else:
                decoded[k] = v
        metadata = decoded
    return ContentBlock(
        type=d.get("type", "text"),
        text=d.get("text"),
        tool_use_id=d.get("tool_use_id"),
        tool_name=d.get("tool_name"),
        tool_input=d.get("tool_input"),
        metadata=metadata,
    )


def _canonical(input_dict: dict) -> str:
    """Stable JSON form of a dict — used as the lookup key for mode 3."""
    return json.dumps(input_dict, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)
