"""SlidingWindowContextPlugin — drop middle fragments from long conversations.

Phase A of context management (see _design/0009-context-manager-sliding-window.md).
Simplest strategy: split the message list into user-turn fragments, always
keep the first N and last M fragments, drop everything in between.

This plugin never rewrites messages — it only drops fragments. If a tool
output is too big for the budget, the answer is "drop the whole turn it
belonged to," not "page out the contents." Paging was v1's worst bug;
we don't repeat it.
"""
from __future__ import annotations

from typing import Iterable

from arc.runtime.events import EventType, RuntimeEvent
from arc.runtime.hooks import Message


class SlidingWindowContextPlugin:
    """`pack_context` plugin. Returns a filtered message list or None for no-op."""

    name = "sliding-window-context"
    version = "1.0.0"

    def __init__(
        self,
        *,
        keep_first_turns: int,
        keep_last_turns: int,
        max_tokens: int | None,
        token_estimate_chars_per: int,
        bus=None,
    ) -> None:
        # Defensive: never collapse to zero — leaves model with no context
        self._keep_first = max(0, int(keep_first_turns))
        self._keep_last = max(1, int(keep_last_turns))
        self._max_tokens = max_tokens
        self._chars_per_token = max(1, int(token_estimate_chars_per))
        # Bus is wired by the factory so we can emit context_packed events.
        # Optional — works without one (tests construct without a bus).
        self._bus = bus

    def bind_bus(self, bus) -> None:
        """Called by the plugin factory after construction so the plugin can emit."""
        self._bus = bus

    # ── Hook ───────────────────────────────────────────────────────────

    def pack_context(self, ctx, messages: list[Message], query: str) -> list[Message] | None:
        fragments = split_into_fragments(messages)
        n_fragments = len(fragments)

        # Short conversation: nothing to do
        if n_fragments <= self._keep_first + self._keep_last:
            return None

        # Build the kept set: first N + last M
        kept = list(fragments[: self._keep_first]) + list(fragments[-self._keep_last :])

        # If a token budget is set, drop additional fragments from the
        # middle (the OLDEST of the last-M set) until under budget.
        # Hard floor: never drop below keep_first + 1.
        if self._max_tokens is not None:
            kept = self._enforce_budget(kept)

        flat = [m for frag in kept for m in frag]

        n_before = len(messages)
        n_after = len(flat)
        if n_after == n_before:
            # No actual change — silently pass through
            return None

        bytes_before = _approx_bytes(messages)
        bytes_after = _approx_bytes(flat)
        self._emit_packed(
            n_before=n_before, n_after=n_after,
            frag_before=n_fragments, frag_after=len(kept),
            bytes_before=bytes_before, bytes_after=bytes_after,
        )
        return flat

    # ── Internals ──────────────────────────────────────────────────────

    def _enforce_budget(self, kept: list[list[Message]]) -> list[list[Message]]:
        """Drop fragments from the middle until under budget or at the floor."""
        floor = self._keep_first + 1
        while len(kept) > floor and self._estimate_tokens(kept) > self._max_tokens:
            # Drop the OLDEST of the keep_last set — i.e., fragment at index keep_first.
            drop_at = self._keep_first
            if drop_at >= len(kept):
                break  # shouldn't happen given the floor check, but safe
            kept.pop(drop_at)
        return kept

    def _estimate_tokens(self, fragments: Iterable[list[Message]]) -> int:
        total_chars = 0
        for frag in fragments:
            for msg in frag:
                total_chars += _approx_message_bytes(msg)
        return total_chars // self._chars_per_token

    def _emit_packed(self, **stats) -> None:
        if self._bus is None:
            return
        payload = {
            "n_messages_before": stats["n_before"],
            "n_messages_after": stats["n_after"],
            "n_fragments_before": stats["frag_before"],
            "n_fragments_after": stats["frag_after"],
            "bytes_before": stats["bytes_before"],
            "bytes_after": stats["bytes_after"],
            "bytes_dropped": stats["bytes_before"] - stats["bytes_after"],
        }
        if self._max_tokens is not None:
            payload["budget_max_tokens"] = self._max_tokens
        try:
            self._bus.emit(RuntimeEvent(
                type=EventType.RUNTIME_CONTEXT_PACKED,
                stage="SlidingWindowContextPlugin",
                payload=payload,
            ))
        except Exception:
            # Emit must never break a turn
            pass


# ── Pure helpers (testable) ────────────────────────────────────────────────


def split_into_fragments(messages: list[Message]) -> list[list[Message]]:
    """Group messages into fragments by user turn.

    A fragment starts at a `user` message and continues until the next `user`
    message (or end). Messages before the first user are their own fragment
    (rare, but handled — e.g., a prior system pre-fill).
    """
    fragments: list[list[Message]] = []
    current: list[Message] = []
    for msg in messages:
        if msg.role == "user" and current:
            fragments.append(current)
            current = [msg]
        else:
            current.append(msg)
    if current:
        fragments.append(current)
    return fragments


def _approx_message_bytes(msg: Message) -> int:
    """Cheap char-count estimate of one message's payload."""
    content = msg.content
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        n = 0
        for block in content:
            # Could be a ContentBlock or a raw dict (tool result)
            if hasattr(block, "text") and block.text:
                n += len(block.text)
            if hasattr(block, "tool_input") and block.tool_input:
                n += len(str(block.tool_input))
            if isinstance(block, dict):
                n += len(str(block))
        return n
    return 0


def _approx_bytes(messages: Iterable[Message]) -> int:
    return sum(_approx_message_bytes(m) for m in messages)
