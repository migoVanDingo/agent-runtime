"""Active sub-agent dispatch cancellation — lets Ctrl+C reach a running child.

A dispatch runs the child on the main thread, so the parent loop is blocked
inside it and the parent's pause mechanism can't be observed until it returns.
The runner registers its `cancel_flag` here for the duration of a dispatch, so a
signal handler can trip it; the child checks the flag at its next iteration
boundary and raises `Cancelled`. Depth-1 recursion prohibition means at most one
flag is ever registered.
"""
from __future__ import annotations

import threading

_lock = threading.Lock()
_active: set[threading.Event] = set()


def register(flag: threading.Event) -> None:
    with _lock:
        _active.add(flag)


def unregister(flag: threading.Event) -> None:
    with _lock:
        _active.discard(flag)


def cancel_active() -> bool:
    """Trip any active, not-yet-set dispatch flag.

    Returns True if one was tripped — i.e. there was a running sub-agent to
    cancel. Returns False if there's no active dispatch OR the active one was
    already cancelled (the second Ctrl+C), so the caller can escalate.
    """
    with _lock:
        to_set = [f for f in _active if not f.is_set()]
    for f in to_set:
        f.set()
    return bool(to_set)
