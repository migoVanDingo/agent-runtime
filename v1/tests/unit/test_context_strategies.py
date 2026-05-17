"""Unit tests for the pluggable context-strategy system (0089)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


# ── Factory ─────────────────────────────────────────────────────────────────


def test_factory_returns_known_strategies():
    from runtime.context import ContextStrategy, build_strategy, known_strategies

    for name in ("afm", "default", "truncate", "sliding", "rag"):
        s = build_strategy(name)
        assert isinstance(s, ContextStrategy)
        # name is "afm" for both afm and default (alias)
        assert s.name in ("afm", "truncate", "sliding", "rag")

    assert "afm" in known_strategies()


def test_factory_unknown_strategy_raises():
    from runtime.context import build_strategy

    with pytest.raises(ValueError, match="unknown context strategy"):
        build_strategy("does-not-exist")


def test_register_strategy_extends_registry():
    from runtime.context import build_strategy, register_strategy

    class _Dummy:
        name = "dummy"

        def __init__(self, params=None):
            self.params = params or {}

        def pack(self, messages, current_query, plan_start_index=None):
            return messages

        def set_summarizer(self, provider): pass
        def set_importance(self, message_index, importance): pass
        def get_importance(self, message_index): return None

    register_strategy("dummy", _Dummy)
    s = build_strategy("dummy")
    assert isinstance(s, _Dummy)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _build_conversation(n: int):
    """Mix of user-text, assistant-tool_use, user-tool_result, assistant-text."""
    msgs = []
    for i in range(n):
        if i == 0:
            msgs.append({"role": "user", "content": "Original task: solve this crackme"})
        elif i % 3 == 1:
            msgs.append({"role": "assistant", "content": [
                {"type": "tool_use", "id": f"tu{i}", "name": "read_file", "input": {"path": f"f{i}.txt"}}
            ]})
        elif i % 3 == 2:
            msgs.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"tu{i-1}", "content": "result " * 50}
            ]})
        else:
            msgs.append({"role": "assistant", "content": [
                {"type": "text", "text": f"reasoning step {i}"}
            ]})
    return msgs


def _has_orphan_tool_result(messages: list[dict]) -> bool:
    for i, m in enumerate(messages):
        if m.get("role") != "user" or not isinstance(m.get("content"), list):
            continue
        for block in m["content"]:
            if block.get("type") != "tool_result":
                continue
            if i == 0:
                return True
            prev = messages[i - 1]
            if prev.get("role") != "assistant" or not isinstance(prev.get("content"), list):
                return True
            tool_use_id = block.get("tool_use_id")
            if not any(b.get("type") == "tool_use" and b.get("id") == tool_use_id
                       for b in prev["content"]):
                return True
    return False


# ── TruncationStrategy ──────────────────────────────────────────────────────


def test_truncate_under_budget_returns_unchanged():
    from runtime.context.strategies.truncation import TruncationStrategy

    s = TruncationStrategy({"budget_tokens": 1_000_000})
    msgs = _build_conversation(10)
    out = s.pack(msgs, "any query")
    assert out == msgs


def test_truncate_drops_when_over_budget():
    from runtime.context.strategies.truncation import TruncationStrategy

    s = TruncationStrategy({"budget_tokens": 200, "keep_first_user": True})
    msgs = _build_conversation(30)
    out = s.pack(msgs, "any query")
    assert len(out) < len(msgs)
    # First user message preserved.
    assert msgs[0] in out


def test_truncate_preserves_pair_atomicity():
    from runtime.context.strategies.truncation import TruncationStrategy

    s = TruncationStrategy({"budget_tokens": 500})
    msgs = _build_conversation(30)
    out = s.pack(msgs, "any query")
    assert not _has_orphan_tool_result(out)


# ── SlidingWindowStrategy ───────────────────────────────────────────────────


def test_sliding_under_window_returns_unchanged():
    from runtime.context.strategies.sliding import SlidingWindowStrategy

    s = SlidingWindowStrategy({"keep_last_n": 50})
    msgs = _build_conversation(10)
    assert s.pack(msgs, "q") == msgs


def test_sliding_collapses_older_into_summary():
    from runtime.context.strategies.sliding import SlidingWindowStrategy

    s = SlidingWindowStrategy({"keep_last_n": 5, "summarize_older": True})
    msgs = _build_conversation(20)
    out = s.pack(msgs, "q")
    # 1 summary message + at least keep_last_n recent messages.
    assert len(out) >= 6
    # The summary is the very first message.
    assert out[0]["role"] == "user"
    assert "summary" in out[0]["content"].lower()


def test_sliding_no_summary_just_drops():
    from runtime.context.strategies.sliding import SlidingWindowStrategy

    s = SlidingWindowStrategy({"keep_last_n": 5, "summarize_older": False})
    msgs = _build_conversation(20)
    out = s.pack(msgs, "q")
    # No prepended summary; just the trimmed tail.
    assert len(out) <= 7  # may expand split slightly to keep pairs intact


# ── RagAugmentedStrategy ────────────────────────────────────────────────────


def test_rag_falls_back_when_no_embedding_model(monkeypatch):
    """Without a working embedding model, the strategy keeps the tail + first user."""
    from runtime.context.strategies import rag_aug
    from runtime.context.strategies.rag_aug import RagAugmentedStrategy

    # Force the embedder to be unavailable.
    monkeypatch.setattr(
        rag_aug,
        "_pack_event",
        lambda *a, **kw: rag_aug.RuntimeEvent("test", None) if False else None,
        raising=False,
    )

    s = RagAugmentedStrategy({"keep_last_n": 4})
    s._model = False  # sentinel: don't try to load
    msgs = _build_conversation(15)
    out = s.pack(msgs, "")  # empty query → no scoring
    # First user + last 4 (or expanded for pair atomicity).
    assert msgs[0] in out
    assert out[-1] is msgs[-1]


def test_rag_returns_messages_unchanged_for_short_history():
    from runtime.context.strategies.rag_aug import RagAugmentedStrategy

    s = RagAugmentedStrategy({"keep_last_n": 50})
    msgs = _build_conversation(5)
    out = s.pack(msgs, "anything")
    # All recent → returned unchanged (older slice is empty).
    assert len(out) == len(msgs)


# ── ContextManager (afm) — round-trip behavior unchanged ────────────────────


def test_afm_strategy_constructed_with_params_block():
    from runtime.context.manager import ContextManager

    s = ContextManager(params={
        "enabled": True,
        "message_budget_tokens": 100,
        "half_life_turns": 4,
        "threshold_high": 0.5,
        "threshold_mid": 0.2,
        "compressed_max_chars": 200,
    })
    assert s.name == "afm"
    assert s._budget == 100


def test_afm_strategy_under_budget_returns_unchanged():
    from runtime.context.manager import ContextManager

    s = ContextManager(params={
        "enabled": True,
        "message_budget_tokens": 1_000_000,
        "half_life_turns": 6,
        "threshold_high": 0.5,
        "threshold_mid": 0.2,
        "compressed_max_chars": 400,
    })
    msgs = _build_conversation(10)
    assert s.pack(msgs, "q") == msgs


def test_afm_strategy_disabled_returns_unchanged():
    from runtime.context.manager import ContextManager

    s = ContextManager(params={
        "enabled": False,
        "message_budget_tokens": 10,
        "half_life_turns": 1,
        "threshold_high": 0.5,
        "threshold_mid": 0.2,
        "compressed_max_chars": 100,
    })
    msgs = _build_conversation(20)
    assert s.pack(msgs, "q") == msgs


# ── Config compat shim ──────────────────────────────────────────────────────


def test_loader_compat_shim_synthesises_afm_from_legacy(tmp_path):
    """When only runtime.context_manager is present, build a ContextConfig with afm params."""
    from config.loader import _load_context_config_with_compat

    rt = {
        "context_manager": {
            "enabled": True,
            "message_budget_tokens": 12345,
            "half_life_turns": 7,
            "threshold_high": 0.4,
            "threshold_mid": 0.2,
            "compressed_max_chars": 500,
        }
    }
    cfg = _load_context_config_with_compat(rt)
    assert cfg.strategy == "afm"
    assert cfg.params["afm"]["message_budget_tokens"] == 12345


def test_loader_new_block_takes_precedence_over_legacy():
    from config.loader import _load_context_config_with_compat

    rt = {
        "context_manager": {"message_budget_tokens": 10},
        "context": {"strategy": "truncate", "params": {"truncate": {"budget_tokens": 999}}},
    }
    cfg = _load_context_config_with_compat(rt)
    assert cfg.strategy == "truncate"
    assert cfg.params["truncate"]["budget_tokens"] == 999


# ── Tool-pair detection ─────────────────────────────────────────────────────


def test_detect_tool_pairs_round_trip():
    from runtime.context.packing import detect_tool_pairs

    msgs = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "x", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
    ]
    pairs = detect_tool_pairs(msgs)
    assert pairs == {1: 2, 2: 1}
