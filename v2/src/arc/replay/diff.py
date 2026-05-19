"""Normalize + diff two event-log paths.

Replay re-generates timestamps, event IDs, parent_event_ids, tool_use_ids
on every run — they're unstable by design. The normalizer strips them so
the comparison focuses on semantic content (types, payloads, content fields,
ordering).

If after normalization the sequences differ, that's a real divergence:
the recorder dropped a field, the runtime changed behavior, or replay
mis-handled something. The diff output points at the first divergence so
debugging starts from a concrete place.
"""
from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class DiffResult:
    """Outcome of a normalize-then-diff comparison."""
    matched: bool
    n_events_a: int
    n_events_b: int
    unified_diff: str        # human-readable
    first_divergence_index: int | None  # event index of first mismatch, or None


def diff_event_logs(
    original: Path,
    replayed: Path,
) -> DiffResult:
    """Load + normalize both logs, return a DiffResult.

    Both paths must exist. If either is empty, that's a divergence by
    definition (one had events, the other didn't).
    """
    a = _load_events(original)
    b = _load_events(replayed)
    a_norm = [normalize_event(e, i) for i, e in enumerate(a)]
    b_norm = [normalize_event(e, i) for i, e in enumerate(b)]

    a_lines = [_canonical_line(e) for e in a_norm]
    b_lines = [_canonical_line(e) for e in b_norm]

    matched = a_lines == b_lines

    # Find first divergence index (only meaningful when lengths overlap)
    first_div = None
    for i, (la, lb) in enumerate(zip(a_lines, b_lines)):
        if la != lb:
            first_div = i
            break
    if first_div is None and len(a_lines) != len(b_lines):
        first_div = min(len(a_lines), len(b_lines))

    unified = "".join(difflib.unified_diff(
        [l + "\n" for l in a_lines],
        [l + "\n" for l in b_lines],
        fromfile=str(original),
        tofile=str(replayed),
        n=2,  # 2 lines of context — enough to orient, not so much it's noisy
    ))

    return DiffResult(
        matched=matched,
        n_events_a=len(a_lines),
        n_events_b=len(b_lines),
        unified_diff=unified,
        first_divergence_index=first_div,
    )


# ── Normalization ──────────────────────────────────────────────────────────


# Top-level envelope fields that vary between runs (regenerated each time)
_VOLATILE_TOP_LEVEL = {
    "event_id",
    "session_id",
    "turn_id",
    "parent_event_id",
    "ts",
    "ts_monotonic_ns",
    "duration_ms",
}

# Stable replacement markers (so cross-event refs by index stay traceable)
_PLACEHOLDER_EVENT = "EVT_REPLAY_PLACEHOLDER"
_PLACEHOLDER_SESSION = "SES_REPLAY_PLACEHOLDER"
_PLACEHOLDER_TURN = "TRN_REPLAY_PLACEHOLDER"
_PLACEHOLDER_TOOL_CALL = "TCL_REPLAY_PLACEHOLDER"


def normalize_event(event: dict, index: int) -> dict:
    """Return a copy of `event` with volatile fields replaced by stable markers.

    The normalized form is purely for comparison — we never write it back
    to disk. `index` is the event's position in the log; not currently used
    in the placeholders but kept so we can include it later for debug.
    """
    out: dict[str, Any] = {}
    for k, v in event.items():
        if k == "event_id":
            out[k] = _PLACEHOLDER_EVENT
        elif k == "session_id":
            out[k] = _PLACEHOLDER_SESSION if v is not None else None
        elif k == "turn_id":
            out[k] = _PLACEHOLDER_TURN if v is not None else None
        elif k == "parent_event_id":
            out[k] = _PLACEHOLDER_EVENT if v is not None else None
        elif k in ("ts", "ts_monotonic_ns", "duration_ms"):
            out[k] = ""  # collapse to empty so type/precision drift doesn't matter
        elif k == "payload":
            out[k] = _normalize_payload(v) if isinstance(v, dict) else v
        elif k == "content":
            out[k] = _normalize_content(v) if isinstance(v, dict) else v
        else:
            out[k] = v
    return out


def _normalize_payload(payload: dict) -> dict:
    """Strip volatile fields from a payload dict."""
    out: dict[str, Any] = {}
    for k, v in payload.items():
        if k == "tool_call_id":
            out[k] = _PLACEHOLDER_TOOL_CALL
        elif k == "turn_id":
            out[k] = _PLACEHOLDER_TURN
        else:
            out[k] = v
    return out


def _normalize_content(content: dict) -> dict:
    """Walk content fields and strip tool_use_id from any nested blocks."""
    out: dict[str, Any] = {}
    for k, v in content.items():
        if k == "response_content" and isinstance(v, list):
            out[k] = [_normalize_block(b) for b in v]
        elif k == "messages" and isinstance(v, list):
            out[k] = [_normalize_message(m) for m in v]
        elif k == "raw_provider_response":
            # The raw provider response is opaque — we don't try to normalize
            # inside it. Provider IDs/timestamps in there would falsely diff.
            # Replace with a stable marker so we still verify "something was
            # captured" without asserting on its contents.
            out[k] = {"__opaque_provider_response__": True}
        else:
            out[k] = v
    return out


def _normalize_block(block: dict) -> dict:
    out: dict[str, Any] = {}
    for k, v in block.items():
        if k == "tool_use_id":
            out[k] = _PLACEHOLDER_TOOL_CALL
        else:
            out[k] = v
    return out


def _normalize_message(msg: dict) -> dict:
    """A Message dict has role/content; content may be a list of blocks."""
    out: dict[str, Any] = {}
    for k, v in msg.items():
        if k == "content" and isinstance(v, list):
            out[k] = [_normalize_block(b) if isinstance(b, dict) else b for b in v]
        else:
            out[k] = v
    return out


# ── I/O helpers ────────────────────────────────────────────────────────────


def _load_events(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(json.loads(line))
    return out


def _canonical_line(event: dict) -> str:
    """Stable JSON serialization of one event for comparison.

    Uses sort_keys=True so dict-ordering differences don't show as diffs.
    (We DO preserve insertion order in the on-disk recording for human
    readability, but for the comparison sort_keys makes the diff stable.)
    """
    return json.dumps(event, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)
