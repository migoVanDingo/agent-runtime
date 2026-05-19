"""Reconstruct a conversation Message list from a recorded events.jsonl.

Used by `arc resume` to restore the prior conversation when continuing a
session. The recording IS the source of truth — there's no separate
pause_state.json or similar. Walk the events in order, append the right
Message kind for each completed boundary.

Message ordering (must match what the loop appends in real time):
  turn.started               → append Message(role="user", content=text)
  llm.call.completed         → append Message(role="assistant", content=blocks)
  tool.call.completed        → append Message(role="tool", content=[function_response], name=...)
  tool.call.denied           → append Message(role="tool", content=[function_response], name=...)

Events for things in flight at pause (turn.started without llm.call.completed,
or llm.call.started without llm.call.completed) are handled by stopping at
the last *completed* boundary — pause always happens between iterations,
so the last assistant or tool message is fully formed.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

from arc.runtime.events import EventType
from arc.runtime.hooks import ContentBlock, Message


def messages_from_session(
    session_dir: Path, *, max_turns: int | None = None,
) -> list[Message]:
    """Walk events.jsonl and return the conversation as a Message list.

    Raises FileNotFoundError if events.jsonl is missing.

    `max_turns` lets the caller restore only the first N completed turns
    — used by `arc resume --at-turn N` (mode 4: branch). None = unlimited.
    """
    events_path = session_dir / "events.jsonl"
    if not events_path.is_file():
        raise FileNotFoundError(f"no events.jsonl in {session_dir}")

    events: list[dict] = []
    for line in events_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            events.append(json.loads(line))

    return messages_from_events(events, max_turns=max_turns)


def count_completed_turns(session_dir: Path) -> int:
    """How many turns completed (turn.ended events) in this session.
    Useful for clamping --at-turn and for status messages.
    """
    events_path = session_dir / "events.jsonl"
    if not events_path.is_file():
        return 0
    n = 0
    for line in events_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("type") == EventType.TURN_ENDED:
            n += 1
    return n


def messages_from_events(
    events: list[dict], *, max_turns: int | None = None,
) -> list[Message]:
    """Same as messages_from_session but takes parsed events directly.

    `max_turns`: if set, stop after this many `turn.ended` events. The
    returned message list ends with the assistant's response to that
    turn (and any tool messages that fired during it). 0 = no messages.
    """
    if max_turns is not None and max_turns <= 0:
        return []

    out: list[Message] = []
    turns_completed = 0
    for e in events:
        t = e.get("type")
        if t == EventType.TURN_STARTED:
            user_text = e.get("content", {}).get("user_input")
            if user_text:
                out.append(Message(role="user", content=user_text))
        elif t == EventType.LLM_CALL_COMPLETED:
            blocks_data = e.get("content", {}).get("response_content", [])
            blocks = [_block_from_dict(b) for b in blocks_data]
            out.append(Message(role="assistant", content=list(blocks)))
        elif t in (EventType.TOOL_CALL_COMPLETED, EventType.TOOL_CALL_DENIED):
            payload = e.get("payload", {})
            name = payload.get("tool_name", "")
            if t == EventType.TOOL_CALL_COMPLETED:
                output = e.get("content", {}).get("output", "")
            else:
                output = f"Tool call denied: {payload.get('reason', '')}"
            out.append(Message(
                role="tool",
                content=[{
                    "function_response": {
                        "name": name,
                        "response": {"result": output},
                    },
                }],
                name=name,
            ))
        elif t == EventType.TOOL_CALL_FAILED:
            payload = e.get("payload", {})
            name = payload.get("tool_name", "")
            msg = payload.get("error_message", "(no message)")
            out.append(Message(
                role="tool",
                content=[{
                    "function_response": {
                        "name": name,
                        "response": {"result": f"Error: {msg}"},
                    },
                }],
                name=name,
            ))
        elif t == EventType.TURN_ENDED:
            turns_completed += 1
            if max_turns is not None and turns_completed >= max_turns:
                return out
    return out


def _block_from_dict(d: dict) -> ContentBlock:
    """Inverse of loop._block_to_dict. Decodes base64'd metadata bytes."""
    metadata = d.get("metadata")
    if metadata:
        decoded: dict = {}
        for k, v in metadata.items():
            if isinstance(v, dict) and "__bytes_b64__" in v:
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
