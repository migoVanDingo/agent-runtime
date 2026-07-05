"""`arc sessions` — list known sessions."""
from __future__ import annotations

import json
import sys


def _cmd_sessions(home_override: str | None) -> int:
    """List recorded sessions from sessions/index.jsonl."""
    from arc.bootstrap import paths_for, resolve_home
    p = paths_for(resolve_home(home_override))
    if not p.sessions_index.exists():
        print("no sessions recorded yet", file=sys.stderr)
        return 0

    rows = []
    for line in p.sessions_index.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if not rows:
        print("no sessions recorded yet", file=sys.stderr)
        return 0

    # Simple aligned columns
    print(f"{'session_id':32}  {'started_at':28}  {'provider':10}  {'model':40}")
    for r in rows:
        print(f"{r.get('session_id', '?'):32}  "
              f"{r.get('started_at', '?')[:26]:28}  "
              f"{r.get('provider', '?'):10}  "
              f"{r.get('model', '?'):40}")
    return 0
