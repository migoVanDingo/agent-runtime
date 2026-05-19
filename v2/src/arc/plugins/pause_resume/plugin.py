"""Pause-resume plugin.

Two trigger paths:
  1. External signal file at <session_dir>/pause — touch it from any process.
  2. In-process flag via plugin.request_pause() — called by SIGINT handler,
     TUI keybindings, or any code with a plugin reference.

`pause_check` clears whichever trigger fired and raises PauseRequested.
The loop catches it, ends the turn with error="paused", and the
recorder closes the session.

Resume is not handled here — it's a CLI subcommand that constructs a
new session with conversation restored from the recorded events.jsonl.
"""
from __future__ import annotations

from pathlib import Path

from arc.runtime.hooks import PauseRequested


class PauseResumePlugin:
    """`pause_check` plugin that watches a signal file and an in-process flag."""

    name = "pause-resume"
    version = "1.0.0"

    def __init__(self, *, sessions_dir: Path, session_id: str) -> None:
        self._signal_path = sessions_dir / session_id / "pause"
        self._flag = False

    # ── Public API for in-process triggers ─────────────────────────────

    def request_pause(self) -> None:
        """Set the in-memory flag. Next pause_check raises PauseRequested.

        Safe to call from a signal handler — sets a single bool, no locking.
        """
        self._flag = True

    @property
    def pause_signal_path(self) -> Path:
        """Where to `touch` to trigger pause from outside the process."""
        return self._signal_path

    # ── Hook ───────────────────────────────────────────────────────────

    def pause_check(self, ctx) -> None:
        """Called between loop iterations. Raises if pause was requested."""
        # Cheap fast path
        if not self._flag and not self._signal_path.exists():
            return

        # Trigger fired — clean up state so the next turn isn't auto-paused
        if self._signal_path.exists():
            try:
                self._signal_path.unlink()
            except OSError:
                pass
        self._flag = False

        raise PauseRequested("pause requested")
