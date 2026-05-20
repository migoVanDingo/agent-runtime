"""Tests for the sliding-window context manager plugin."""
from __future__ import annotations

import pytest

from arc.plugins.sliding_window_context import (
    SlidingWindowContextPlugin,
    split_into_fragments,
)
from arc.runtime.events import EventType, RuntimeEvent
from arc.runtime.hooks import ContentBlock, Message


# ── Fragment splitter ──────────────────────────────────────────────────────


def test_split_empty_messages():
    assert split_into_fragments([]) == []


def test_split_single_user_message_is_one_fragment():
    msgs = [Message(role="user", content="hi")]
    frags = split_into_fragments(msgs)
    assert len(frags) == 1
    assert frags[0] == msgs


def test_split_user_then_assistant_is_one_fragment():
    msgs = [
        Message(role="user", content="hi"),
        Message(role="assistant", content="hello back"),
    ]
    assert len(split_into_fragments(msgs)) == 1


def test_split_two_user_turns_makes_two_fragments():
    msgs = [
        Message(role="user", content="hi"),
        Message(role="assistant", content="hello"),
        Message(role="user", content="how are you"),
        Message(role="assistant", content="great"),
    ]
    frags = split_into_fragments(msgs)
    assert len(frags) == 2
    assert frags[0][0].content == "hi"
    assert frags[1][0].content == "how are you"


def test_split_keeps_tool_call_with_its_owning_user_turn():
    msgs = [
        Message(role="user", content="list files"),
        Message(role="assistant", content=[
            ContentBlock(type="tool_use", tool_use_id="t1", tool_name="ls", tool_input={"path": "."})
        ]),
        Message(role="tool", content=[
            {"function_response": {"name": "ls", "response": {"result": "a\nb"}}}
        ], name="ls"),
        Message(role="assistant", content="here they are"),
        Message(role="user", content="thanks"),
    ]
    frags = split_into_fragments(msgs)
    # First fragment: user + assistant(tool_use) + tool + assistant(text)
    # Second fragment: just the new user message
    assert len(frags) == 2
    assert len(frags[0]) == 4
    assert frags[1][0].content == "thanks"


def test_split_pre_user_messages_form_their_own_fragment():
    """Edge case: if conversation starts with a non-user message (rare,
    e.g., a system pre-fill), it's its own fragment."""
    msgs = [
        Message(role="assistant", content="pre-fill"),
        Message(role="user", content="actual user input"),
    ]
    frags = split_into_fragments(msgs)
    assert len(frags) == 2
    assert frags[0][0].role == "assistant"


# ── Plugin behavior (pure pack_context calls, no bus needed) ──────────────


def _multi_turn_messages(n_turns: int) -> list[Message]:
    """Build n_turns of (user + assistant) pairs."""
    msgs = []
    for i in range(n_turns):
        msgs.append(Message(role="user", content=f"turn {i} user"))
        msgs.append(Message(role="assistant", content=f"turn {i} reply"))
    return msgs


def _plugin(**overrides) -> SlidingWindowContextPlugin:
    base = dict(
        keep_first_turns=1,
        keep_last_turns=2,
        max_tokens=None,
        token_estimate_chars_per=4,
    )
    base.update(overrides)
    return SlidingWindowContextPlugin(**base)


def test_short_conversation_passes_through():
    """Conversation under (keep_first + keep_last) → return None (no change)."""
    msgs = _multi_turn_messages(3)  # 3 turns, threshold is 1+2=3
    result = _plugin().pack_context(None, msgs, query="x")
    assert result is None


def test_long_conversation_drops_middle_fragments():
    msgs = _multi_turn_messages(10)  # 10 turns; keep first 1 + last 2 = 3
    result = _plugin().pack_context(None, msgs, query="x")
    assert result is not None
    assert len(result) == 6  # 3 fragments × 2 messages each
    # First fragment preserved (turn 0)
    assert result[0].content == "turn 0 user"
    # Last 2 fragments preserved (turns 8 and 9)
    assert result[-4].content == "turn 8 user"
    assert result[-1].content == "turn 9 reply"


def test_exactly_at_threshold_passes_through():
    msgs = _multi_turn_messages(3)  # exactly keep_first + keep_last
    result = _plugin(keep_first_turns=1, keep_last_turns=2).pack_context(None, msgs, query="x")
    assert result is None


def test_keep_first_zero_only_preserves_tail():
    msgs = _multi_turn_messages(10)
    result = _plugin(keep_first_turns=0, keep_last_turns=3).pack_context(None, msgs, query="x")
    assert result is not None
    # keep_last_turns=3 fragments × 2 msgs each = 6
    assert len(result) == 6
    assert result[0].content == "turn 7 user"
    assert result[-1].content == "turn 9 reply"


def test_keep_last_clamped_to_minimum_one():
    """If user passes keep_last_turns=0, plugin defensively forces to 1."""
    p = _plugin(keep_first_turns=1, keep_last_turns=0)
    msgs = _multi_turn_messages(5)
    result = p.pack_context(None, msgs, query="x")
    assert result is not None
    # Should keep at least the first and one last
    assert len(result) >= 4


# ── Token budget enforcement ──────────────────────────────────────────────


def test_no_budget_skips_token_enforcement():
    """With max_tokens=None, fragment count rules — no further drops."""
    msgs = _multi_turn_messages(20)
    p = _plugin(keep_first_turns=1, keep_last_turns=5, max_tokens=None)
    result = p.pack_context(None, msgs, query="x")
    assert result is not None
    # 6 fragments × 2 msgs = 12
    assert len(result) == 12


def test_budget_drops_additional_fragments():
    """With a tiny budget, additional fragments get dropped from the middle."""
    # Build messages with substantial content so estimates have something to chew
    msgs = []
    for i in range(20):
        msgs.append(Message(role="user", content=f"turn {i} " + "x" * 400))
        msgs.append(Message(role="assistant", content=f"reply {i} " + "y" * 400))
    # Budget that allows ~200 tokens (= 800 chars at 4 chars/token)
    p = SlidingWindowContextPlugin(
        keep_first_turns=1, keep_last_turns=10,
        max_tokens=200, token_estimate_chars_per=4,
    )
    result = p.pack_context(None, msgs, query="x")
    assert result is not None
    # Should be much fewer than the 11 fragments the turn-count window would keep
    # Floor is keep_first + 1 = 2 fragments
    assert len(result) <= 6  # 3 fragments × 2 msgs max with such a tight budget


def test_budget_respects_floor():
    """Even with an absurdly small budget, plugin never collapses below floor."""
    msgs = []
    for i in range(10):
        msgs.append(Message(role="user", content="x" * 10000))
        msgs.append(Message(role="assistant", content="y" * 10000))
    p = SlidingWindowContextPlugin(
        keep_first_turns=1, keep_last_turns=5,
        max_tokens=1, token_estimate_chars_per=4,  # absurdly small
    )
    result = p.pack_context(None, msgs, query="x")
    # Floor is keep_first + 1 = 2 fragments
    assert result is not None
    # Floor means at LEAST 2 fragments × 2 msgs = 4 messages survive
    assert len(result) >= 4


# ── Bus event emission ───────────────────────────────────────────────────


class _CapturingBus:
    def __init__(self):
        self.events = []
    def emit(self, event):
        self.events.append(event)


def test_emits_context_packed_when_filtering_happens():
    bus = _CapturingBus()
    p = _plugin(keep_first_turns=1, keep_last_turns=2)
    p.bind_bus(bus)
    msgs = _multi_turn_messages(10)
    p.pack_context(None, msgs, query="x")

    packed = [e for e in bus.events if e.type == EventType.RUNTIME_CONTEXT_PACKED]
    assert len(packed) == 1
    payload = packed[0].payload
    assert payload["n_messages_before"] == 20
    assert payload["n_messages_after"] == 6
    assert payload["n_fragments_before"] == 10
    assert payload["n_fragments_after"] == 3
    assert payload["bytes_dropped"] > 0


def test_no_event_when_short_conversation():
    """Pass-through case must not emit context_packed — silent no-op."""
    bus = _CapturingBus()
    p = _plugin(keep_first_turns=1, keep_last_turns=2)
    p.bind_bus(bus)
    p.pack_context(None, _multi_turn_messages(3), query="x")
    assert bus.events == []


def test_emit_failure_does_not_break_pack():
    """If the bus is broken (raises on emit), pack_context still returns."""
    class _BrokenBus:
        def emit(self, e):
            raise RuntimeError("disk full")

    p = _plugin()
    p.bind_bus(_BrokenBus())
    msgs = _multi_turn_messages(10)
    result = p.pack_context(None, msgs, query="x")
    # Pack still works
    assert result is not None
    assert len(result) == 6


# ── Tool-content content estimation ──────────────────────────────────────


def test_byte_estimate_handles_tool_content_lists():
    """Tool messages have content=[dict]; estimator shouldn't crash on them."""
    bus = _CapturingBus()
    msgs = [
        Message(role="user", content="x" * 200),
        Message(role="assistant", content=[
            ContentBlock(type="tool_use", tool_use_id="t",
                         tool_name="ls", tool_input={"path": "/very/long/path/" + "a" * 100})
        ]),
        Message(role="tool", content=[
            {"function_response": {"name": "ls", "response": {"result": "x" * 500}}}
        ], name="ls"),
        Message(role="assistant", content="ok"),
        Message(role="user", content="next"),
        Message(role="assistant", content="reply"),
        Message(role="user", content="third"),
        Message(role="assistant", content="r"),
        Message(role="user", content="fourth"),
        Message(role="assistant", content="r"),
    ]
    p = _plugin(keep_first_turns=1, keep_last_turns=2)
    p.bind_bus(bus)
    result = p.pack_context(None, msgs, query="x")
    assert result is not None
    # Check the bus event has bytes_before > 0 (tool content counted)
    packed = next(e for e in bus.events if e.type == EventType.RUNTIME_CONTEXT_PACKED)
    assert packed.payload["bytes_before"] > 500
