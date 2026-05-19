"""ULID-based identifier generation.

ULIDs are sortable, compact (26 chars), and well-supported. Per design §6.5
they form the spine of event correlation: session_id, turn_id, event_id all
share this format.

Prefixed for human-readability when scanning logs:
  SES01HXYZ...   session
  TRN01HXYZ...   turn
  EVT01HXYZ...   event
  TCL01HXYZ...   tool call

The prefix is informational only — strip it for sorting / comparison if needed.
"""
from __future__ import annotations

import secrets
import time

# ── Self-contained ULID generator ───────────────────────────────────────────
# Two PyPI packages (`python-ulid`, `ulid-py`) share the `ulid` import name
# but have incompatible APIs (ULID() vs ulid.new()). Depending on either is
# fragile — whichever happens to be installed in a venv wins. So we ship our
# own 25-line ULID instead. Pure stdlib, no dep.
#
# Format: 26 chars total = 10 chars timestamp (48 bits, ms since epoch) +
# 16 chars random (80 bits), all in Crockford Base32. Sortable by creation
# time at millisecond resolution. Compliant with the ULID spec.

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode_b32(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(out))


def _make_ulid() -> str:
    ts_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rnd = secrets.randbits(80)
    return _encode_b32(ts_ms, 10) + _encode_b32(rnd, 16)


def _gen(prefix: str) -> str:
    return f"{prefix.upper()}{_make_ulid()}"


def new_session_id() -> str:
    return _gen("ses")


def new_turn_id() -> str:
    return _gen("trn")


def new_event_id() -> str:
    return _gen("evt")


def new_tool_call_id() -> str:
    return _gen("tcl")
