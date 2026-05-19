"""Resume engine — see _design/0006-foundation-phase2.1.5-pause-and-resume.md.

Also powers branch (mode 4) via the `max_turns` parameter on
messages_from_session — see _design/0007-foundation-phase2.2-branch-and-rerun.md.
"""
from arc.resume.reconstruct import (
    count_completed_turns,
    messages_from_events,
    messages_from_session,
)

__all__ = [
    "count_completed_turns",
    "messages_from_events",
    "messages_from_session",
]
