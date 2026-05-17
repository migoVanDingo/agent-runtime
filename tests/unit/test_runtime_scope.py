"""Unit tests for runtime.scope and the 0090a AFM runtime-budget split."""
from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def test_scope_defaults_to_main():
    from runtime.scope import current_scope, MAIN
    assert current_scope() == MAIN


def test_scoped_context_manager_restores_on_exit():
    from runtime.scope import current_scope, scoped, RUNTIME, MAIN
    assert current_scope() == MAIN
    with scoped(RUNTIME):
        assert current_scope() == RUNTIME
    assert current_scope() == MAIN


def test_scoped_nesting_restores_outer_scope():
    from runtime.scope import current_scope, scoped
    with scoped("a"):
        assert current_scope() == "a"
        with scoped("b"):
            assert current_scope() == "b"
        assert current_scope() == "a"


def test_scoped_restores_even_on_exception():
    from runtime.scope import current_scope, scoped, MAIN, RUNTIME
    try:
        with scoped(RUNTIME):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert current_scope() == MAIN


def test_is_subagent_scope():
    from runtime.scope import is_subagent_scope, scoped
    assert not is_subagent_scope("main")
    assert not is_subagent_scope("runtime")
    assert is_subagent_scope("subagent:ghidra_analyst")
    with scoped("subagent:foo"):
        assert is_subagent_scope()  # uses current scope


# ── AFM scope-aware budget selection ────────────────────────────────────────


def _make_afm(message_budget: int = 65536, runtime_budget: int = 12000):
    from runtime.context.manager import ContextManager
    return ContextManager(params={
        "enabled": True,
        "message_budget_tokens": message_budget,
        "runtime_message_budget_tokens": runtime_budget,
        "half_life_turns": 6,
        "threshold_high": 0.5,
        "threshold_mid": 0.2,
        "compressed_max_chars": 200,
    })


def test_afm_main_scope_uses_full_budget():
    """Main scope (default) gets the larger message_budget_tokens."""
    from runtime.context.manager import ContextManager
    cm = _make_afm(message_budget=50000, runtime_budget=5000)
    # 25k worth of message tokens fits under main budget, doesn't fit under runtime
    msgs = [{"role": "user", "content": "X" * 400} for _ in range(250)]  # ~25k tokens
    out = cm.pack(msgs, "q")
    # under main budget → all messages kept
    assert len(out) == len(msgs)


def test_afm_runtime_scope_uses_smaller_budget():
    """Same messages, runtime scope, smaller budget → packing must engage."""
    from runtime.scope import scoped, RUNTIME
    cm = _make_afm(message_budget=50000, runtime_budget=5000)
    msgs = [{"role": "user", "content": "X" * 400} for _ in range(250)]
    with scoped(RUNTIME):
        out = cm.pack(msgs, "q")
    # 25k > 5k runtime budget → packing engages, tokens reduced
    packed_tokens = sum(len(m["content"]) // 4 for m in out if isinstance(m.get("content"), str))
    # may keep messages but at lower fidelity → total tokens should drop
    assert packed_tokens <= 5000 + 200  # some slack for placeholder overhead


def test_afm_system_prompt_size_reduces_effective_budget():
    """A large system_prompt_size shrinks the effective budget for messages."""
    cm = _make_afm(message_budget=20000, runtime_budget=5000)
    msgs = [{"role": "user", "content": "X" * 400} for _ in range(100)]  # ~10k

    # Without system prompt: under budget, no packing
    out_a = cm.pack(msgs, "q", system_prompt_size=0)
    assert len(out_a) == len(msgs)

    # With 15k system prompt: effective budget = 20000 - 15000 = 5000, must pack
    out_b = cm.pack(msgs, "q", system_prompt_size=15000)
    packed_tokens = sum(len(m["content"]) // 4 for m in out_b if isinstance(m.get("content"), str))
    assert packed_tokens <= 5000 + 200


def test_afm_disabled_returns_messages_unchanged():
    """When enabled=False, pack always returns messages unchanged regardless of scope."""
    from runtime.scope import scoped, RUNTIME
    from runtime.context.manager import ContextManager
    cm = ContextManager(params={
        "enabled": False,
        "message_budget_tokens": 100,
        "runtime_message_budget_tokens": 50,
        "half_life_turns": 6,
        "threshold_high": 0.5,
        "threshold_mid": 0.2,
        "compressed_max_chars": 200,
    })
    msgs = [{"role": "user", "content": "X" * 1000} for _ in range(10)]
    with scoped(RUNTIME):
        assert cm.pack(msgs, "q", system_prompt_size=99) == msgs


def test_all_strategies_accept_system_prompt_size_kwarg():
    """Protocol compliance — every strategy accepts the keyword without raising."""
    from runtime.context.strategies.truncation import TruncationStrategy
    from runtime.context.strategies.sliding import SlidingWindowStrategy
    from runtime.context.strategies.rag_aug import RagAugmentedStrategy
    from runtime.context.manager import ContextManager

    msgs = [{"role": "user", "content": "hi"}]
    for strategy in (
        ContextManager(params={
            "enabled": True, "message_budget_tokens": 100, "runtime_message_budget_tokens": 50,
            "half_life_turns": 6, "threshold_high": 0.5, "threshold_mid": 0.2,
            "compressed_max_chars": 200,
        }),
        TruncationStrategy(params={"budget_tokens": 1000}),
        SlidingWindowStrategy(params={"keep_last_n": 5}),
        RagAugmentedStrategy(params={"keep_last_n": 5}),
    ):
        # Must not raise
        strategy.pack(msgs, "q", system_prompt_size=10)
