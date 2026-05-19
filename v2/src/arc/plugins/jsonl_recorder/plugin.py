"""JSONL recorder plugin implementation.

Subscribes to `on_event` and appends every event to `events.jsonl` inside the
session's directory. Also writes `meta.json` at session start, updates it at
session end, and adds a line to `sessions/index.jsonl`.

Lifecycle:
  1. Runtime constructs JSONLRecorder(sessions_dir=, session_id=, config_snapshot_yaml=)
  2. Runtime registers it against `on_event`, `on_session_start`, `on_session_end`
  3. on_session_start: create session dir, write meta.json + config snapshot
  4. on_event: append serialized event to events.jsonl
  5. on_session_end: update meta.json with ended_at, append to index.jsonl

If a flush fails (disk full, permissions), the hook raises and the runtime
treats it as a hook failure. After 3 such failures (configurable via
plugins.failure_threshold) the recorder is disabled for the rest of the session.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from arc.runtime.events import RuntimeEvent


class JSONLRecorder:
    """Plugin that persists events to JSONL."""

    name = "jsonl-recorder"
    version = "1.0.0"

    def __init__(
        self,
        *,
        sessions_dir: Path,
        session_id: str,
        config_snapshot_yaml: str | None = None,
    ) -> None:
        self._session_id = session_id
        self._session_dir = sessions_dir / session_id
        self._events_file = self._session_dir / "events.jsonl"
        self._meta_file = self._session_dir / "meta.json"
        self._config_snapshot_file = self._session_dir / "config.snapshot.yml"
        self._index_file = sessions_dir / "index.jsonl"
        self._config_snapshot_yaml = config_snapshot_yaml
        self._meta: dict | None = None

    # ── on_session_start ───────────────────────────────────────────────

    def on_session_start(self, ctx) -> None:
        """Create the session dir, write meta.json + config snapshot."""
        self._session_dir.mkdir(parents=True, exist_ok=True)

        self._meta = {
            "session_id": self._session_id,
            "started_at": ctx.started_at,
            "ended_at": None,
            "provider": ctx.provider_name,
            "model": ctx.provider_model,
            "workspace": ctx.workspace,
        }
        self._write_meta()

        if self._config_snapshot_yaml is not None:
            self._config_snapshot_file.write_text(
                self._config_snapshot_yaml, encoding="utf-8",
            )

        # Touch events.jsonl so it exists even if no events ever fire
        self._events_file.touch()

    # ── on_event ───────────────────────────────────────────────────────

    def on_event(self, ctx, event: RuntimeEvent) -> None:
        """Append the event to events.jsonl as one canonical JSON line.

        Critical for replay (per design §6.3): content is serialized verbatim
        with compact separators and no key reordering. The byte output is
        deterministic given identical inputs.
        """
        line = json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":"))
        with self._events_file.open("a", encoding="utf-8") as f:
            f.write(line)
            f.write("\n")

    # ── on_session_end ─────────────────────────────────────────────────

    def on_session_end(self, ctx, outcome) -> None:
        """Stamp meta.json with ended_at, append a line to index.jsonl."""
        if self._meta is None:
            return  # session_start never fired (shouldn't happen)

        ended_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        self._meta["ended_at"] = ended_at
        if outcome is not None:
            self._meta["last_outcome"] = {
                "success": outcome.success,
                "n_tool_calls": outcome.n_tool_calls,
                "n_llm_calls": outcome.n_llm_calls,
                "error": outcome.error,
            }
        self._write_meta()

        # Append to global index.jsonl
        index_entry = {
            "session_id": self._session_id,
            "started_at": self._meta["started_at"],
            "ended_at": ended_at,
            "provider": self._meta["provider"],
            "model": self._meta["model"],
        }
        self._index_file.parent.mkdir(parents=True, exist_ok=True)
        with self._index_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(index_entry, ensure_ascii=False, separators=(",", ":")))
            f.write("\n")

    # ── Helpers ────────────────────────────────────────────────────────

    def _write_meta(self) -> None:
        self._meta_file.write_text(
            json.dumps(self._meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
