"""Sliding-window context manager plugin — see plugin.py."""
from arc.plugins.sliding_window_context.plugin import (
    SlidingWindowContextPlugin,
    split_into_fragments,
)

__all__ = ["SlidingWindowContextPlugin", "split_into_fragments"]
