"""`arc show` — pretty-print a recorded session."""
from __future__ import annotations

import json
import sys


def _cmd_show(home_override: str | None, *, session_id: str) -> int:
    """Render a recorded session as human-readable text (from canonical events)."""
    from arc.bootstrap import paths_for, resolve_home
    p = paths_for(resolve_home(home_override))
    session_dir = p.sessions_dir / session_id
    events_file = session_dir / "events.jsonl"
    if not events_file.exists():
        print(f"no events for session {session_id!r} at {events_file}", file=sys.stderr)
        return 1

    for line in events_file.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = e.get("ts", "")[11:23]  # HH:MM:SS.mmm
        typ = e.get("type", "?")
        stage = e.get("stage", "")
        scope = e.get("scope", "main")
        scope_tag = "" if scope == "main" else f" [{scope}]"
        print(f"{ts}  {typ:30}  {stage}{scope_tag}")
    return 0
