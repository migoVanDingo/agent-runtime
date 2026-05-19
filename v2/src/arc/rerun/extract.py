"""Extract user inputs from a recorded session for `arc rerun` (mode 5).

Rerun replays just the user inputs (one per recorded turn) against a fresh
agent. No message restoration. No stubbing. Live LLM + live tools. Used as
a scenario-level regression test: "does this still work with my current
config / model / prompt?"
"""
from __future__ import annotations

import json
from pathlib import Path

from arc.runtime.events import EventType


def user_inputs_from_session(session_dir: Path) -> list[str]:
    """Return the original user inputs in turn order.

    Raises FileNotFoundError if events.jsonl is missing.
    """
    events_path = session_dir / "events.jsonl"
    if not events_path.is_file():
        raise FileNotFoundError(f"no events.jsonl in {session_dir}")

    out: list[str] = []
    for line in events_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("type") == EventType.TURN_STARTED:
            text = e.get("content", {}).get("user_input")
            if isinstance(text, str) and text:
                out.append(text)
    return out
