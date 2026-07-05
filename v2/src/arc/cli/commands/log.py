"""`arc log` — print the human-readable session.log for a session."""
from __future__ import annotations

import sys


def _cmd_log(home_override: str | None, *, session_id: str, tail: int | None) -> int:
    """Print the v1-style session.log written by the log-writer plugin."""
    from arc.bootstrap import paths_for, resolve_home
    p = paths_for(resolve_home(home_override))
    log_path = p.sessions_dir / session_id / "session.log"
    if not log_path.is_file():
        print(f"no session.log for session {session_id!r} at {log_path}",
              file=sys.stderr)
        return 1
    lines = log_path.read_text(encoding="utf-8").splitlines()
    if tail is not None:
        lines = lines[-tail:]
    for line in lines:
        print(line)
    return 0
