"""Unit tests for ContextManager._pack_chronological — pair atomicity, fidelity."""
import pytest
from runtime.context_manager import ContextManager
from runtime.schema import FidelityLevel, Importance, ScoredMessage


def _user_text(text, index=0):
    return ScoredMessage(
        index=index, message={"role": "user", "content": text},
        score=0.5, importance=Importance.MEDIUM,
        fidelity=FidelityLevel.FULL,
        token_estimate=len(text) // 4 + 1,
    )


def _assistant_tool_use(tool_id, tool_name, index=0):
    msg = {"role": "assistant", "content": [
        {"type": "tool_use", "id": tool_id, "name": tool_name, "input": {}}
    ]}
    return ScoredMessage(
        index=index, message=msg,
        score=0.5, importance=Importance.MEDIUM,
        fidelity=FidelityLevel.FULL,
        token_estimate=10,
    )


def _tool_result(tool_id, content, index=0):
    msg = {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tool_id, "content": content}
    ]}
    return ScoredMessage(
        index=index, message=msg,
        score=0.5, importance=Importance.MEDIUM,
        fidelity=FidelityLevel.FULL,
        token_estimate=len(content) // 4 + 1,
    )


def _pack(scored):
    cm = ContextManager()
    return cm._pack_chronological(scored)


# ── Basic passthrough ────────────────────────────────────────────────

def test_simple_messages_pass_through():
    msgs = [_user_text("hello", 0), _user_text("world", 1)]
    result = _pack(msgs)
    assert len(result) == 2


# ── Pair atomicity: drop both or keep both ────────────────────────────

def test_tool_use_result_pair_both_included_when_budget_sufficient():
    scored = [
        _assistant_tool_use("id1", "read_file", index=0),
        _tool_result("id1", "file content", index=1),
    ]
    result = _pack(scored)
    assert len(result) == 2


def test_tool_use_result_pair_both_dropped_when_partner_cannot_fit():
    # Make a tiny budget by patching the context manager
    cm = ContextManager()
    cm._budget = 2  # impossibly small
    scored = [
        ScoredMessage(
            index=0,
            message={"role": "assistant", "content": [
                {"type": "tool_use", "id": "id1", "name": "bash_exec", "input": {}}
            ]},
            score=0.5, importance=Importance.MEDIUM,
            fidelity=FidelityLevel.FULL, token_estimate=500,
        ),
        ScoredMessage(
            index=1,
            message={"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "id1", "content": "x" * 100}
            ]},
            score=0.5, importance=Importance.MEDIUM,
            fidelity=FidelityLevel.FULL, token_estimate=500,
        ),
    ]
    result = cm._pack_chronological(scored)
    # Both should be dropped since pair can't fit
    assert len(result) == 0


# ── Plan-window protection ────────────────────────────────────────────

def test_plan_window_tool_result_floored_at_full():
    cm = ContextManager()
    scored = [
        ScoredMessage(
            index=5,
            message={"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "id1", "content": "important data"}
            ]},
            score=0.1,  # low score would normally → placeholder
            importance=Importance.LOW,
            fidelity=FidelityLevel.PLACEHOLDER,
            token_estimate=5,
        )
    ]
    # _assign_fidelity with plan_start_index=5 should floor this tool_result at FULL
    cm._assign_fidelity(scored, plan_start_index=5)
    assert scored[0].fidelity == FidelityLevel.FULL


def test_plan_window_non_tool_result_floored_at_compressed():
    cm = ContextManager()
    scored = [
        ScoredMessage(
            index=5,
            message={"role": "user", "content": "step message"},
            score=0.1,
            importance=Importance.LOW,
            fidelity=FidelityLevel.PLACEHOLDER,
            token_estimate=3,
        )
    ]
    cm._assign_fidelity(scored, plan_start_index=5)
    assert scored[0].fidelity == FidelityLevel.COMPRESSED


# ── Importance classification ─────────────────────────────────────────

def test_first_user_message_is_critical():
    cm = ContextManager()
    msg = {"role": "user", "content": "initial task"}
    importance = cm._classify_importance(msg, index=0, total=5)
    assert importance == Importance.CRITICAL


def test_large_tool_result_is_low():
    cm = ContextManager()
    large_content = "x" * 1000
    msg = {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "id1", "content": large_content}
    ]}
    importance = cm._classify_importance(msg, index=2, total=5)
    assert importance == Importance.LOW


def test_write_file_assistant_message_is_low():
    cm = ContextManager()
    msg = {"role": "assistant", "content": [
        {"type": "tool_use", "id": "x", "name": "write_file",
         "input": {"path": "out.md", "content": "report"}}
    ]}
    importance = cm._classify_importance(msg, index=1, total=5)
    assert importance == Importance.LOW
