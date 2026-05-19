"""Replay-specific exceptions.

These exist as their own types so the diff/CLI layers can distinguish
"the recording itself is bad" from "the replay diverged from the recording."
"""
from __future__ import annotations


class ReplayError(Exception):
    """Base for all replay errors."""


class MissingRecordingError(ReplayError):
    """The source session dir is missing, incomplete, or unparseable."""


class ReplayDivergenceError(ReplayError):
    """The runtime under replay asked for something the recording can't supply.

    Raised by replay tool stubs when:
      - mode 2: a tool was called but the recorded queue for that name is empty
      - mode 3: a tool was called with inputs the recording never saw

    Either case means the replayed agent took a different path than the
    recorded one. The diff layer turns this into a useful report.
    """
