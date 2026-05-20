"""LogWriterPlugin — writes a v1-style human session.log per session.

Lifecycle mirrors the JSONL recorder:
  on_session_start  → create session dir if needed, attach FileHandler
  on_event          → format event into log records, emit
  on_session_end    → flush + detach FileHandler

The plugin uses a plugin-local logger named by session_id so concurrent
sessions (if we ever support them) don't share file handlers, and so
records never leak into the root logger or pytest output.

Display names (`arc.runtime`, `arc.tool`, `arc.llm`, `arc.plugin`) are
carried via `extra={"display_name": ...}` and substituted in the
formatter. That lets a single per-session logger carry records that
display under different category names — categorized for grep, isolated
for safety.
"""
from __future__ import annotations

import logging
from pathlib import Path

from arc.plugins.log_writer.formatter import format_event
from arc.runtime.events import RuntimeEvent


_FORMAT = "%(asctime)s.%(msecs)03d [%(levelname)s] %(display_name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


_LEVEL_NAMES = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warn": logging.WARNING,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


class _DisplayNameFormatter(logging.Formatter):
    """Substitutes record.display_name in for the format string's name slot.

    Records without `display_name` (shouldn't happen from this plugin) fall
    back to the logger name so we never raise from inside the logger.
    """

    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "display_name"):
            record.display_name = record.name
        return super().format(record)


class LogWriterPlugin:
    """Subscribes to on_event, writes formatted lines to session.log."""

    name = "log-writer"
    version = "1.0.0"

    def __init__(
        self,
        *,
        sessions_dir: Path,
        session_id: str,
        level: str = "info",
        preview_chars: int = 200,
        include_events: list[str] | None = None,
        exclude_events: list[str] | None = None,
    ) -> None:
        self._session_dir = sessions_dir / session_id
        self._log_path = self._session_dir / "session.log"
        self._preview_chars = preview_chars
        self._include = set(include_events or [])
        self._exclude = set(exclude_events or [])
        self._level = _LEVEL_NAMES.get(level.lower(), logging.INFO)

        # Plugin-local logger named by session_id so handlers don't cross.
        self._logger = logging.getLogger(f"arc._sess.{session_id}")
        self._logger.setLevel(self._level)
        self._logger.propagate = False  # keep our records out of root/pytest

        self._handler: logging.FileHandler | None = None

    # ── on_session_start ───────────────────────────────────────────────

    def on_session_start(self, ctx) -> None:
        self._session_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(self._log_path, mode="a", encoding="utf-8")
        handler.setFormatter(_DisplayNameFormatter(_FORMAT, datefmt=_DATEFMT))
        handler.setLevel(self._level)
        self._logger.addHandler(handler)
        self._handler = handler

    # ── on_event ───────────────────────────────────────────────────────

    def on_event(self, ctx, event: RuntimeEvent) -> None:
        if not self._should_log(event):
            return
        records = format_event(event, preview_chars=self._preview_chars)
        for display_name, level, message in records:
            if level < self._level:
                continue
            self._logger.log(level, message, extra={"display_name": display_name})

    # ── on_session_end ─────────────────────────────────────────────────

    def on_session_end(self, ctx, outcome) -> None:
        if self._handler is None:
            return
        try:
            self._handler.flush()
        finally:
            self._logger.removeHandler(self._handler)
            self._handler.close()
            self._handler = None

    # ── Helpers ────────────────────────────────────────────────────────

    def _should_log(self, event: RuntimeEvent) -> bool:
        t = event.type
        if t in self._exclude:
            return False
        if self._include and t not in self._include:
            return False
        return True
