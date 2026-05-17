"""Unit tests for PlanCriticAdapter.synthesize — all branches."""
import pytest
from runtime.critic import PlanCriticAdapter
from runtime.council import CouncillorDecision
from runtime.schema import CriticResult, CriticVerdict, CriticChallenge


def _decision(label, verdict, challenges=None):
    parsed = CriticResult(verdict=CriticVerdict(verdict), challenges=challenges)
    return CouncillorDecision(
        label=label, provider="test", model=None,
        raw_response="", parsed=parsed, round_number=1,
    )


def _challenge(step, suggestion):
    return CriticChallenge(step=step, tool="some_tool", challenge="test", suggestion=suggestion)


def _synth(decisions, threshold=0.6):
    from tools.registry import ToolRegistry
    from planning.schema import Plan, Step, ActionType, StepFlags
    plan = Plan(original_query="test", steps=[
        Step(step=i, description=f"s{i}", action_type=ActionType.FILE_IO,
             tool="read_file", flags=StepFlags())
        for i in range(1, 5)
    ])
    adapter = PlanCriticAdapter(ToolRegistry(), plan)
    result, agreement, trace = adapter.synthesize(decisions, threshold)
    return result, agreement, trace


# ── All approved ─────────────────────────────────────────────────────

def test_all_approved_returns_approved():
    decisions = [_decision("A", "approved"), _decision("B", "approved")]
    result, _, _ = _synth(decisions)
    assert result.verdict == CriticVerdict.APPROVED
    assert "all councillors approved" in (result.reasoning or "")


# ── Unanimous challenge at/above threshold ───────────────────────────

def test_unanimous_drop_above_threshold_keeps_drop():
    decisions = [
        _decision("A", "challenged", [_challenge(1, "drop")]),
        _decision("B", "challenged", [_challenge(1, "drop")]),
    ]
    result, _, _ = _synth(decisions, threshold=0.6)
    assert result.verdict == CriticVerdict.CHALLENGED
    assert result.challenges[0].suggestion == "drop"


# ── Below threshold: downgrade ────────────────────────────────────────

def test_minority_drop_downgrades_to_replace():
    # 1/3 challenged with drop — below threshold, majority not challenging
    decisions = [
        _decision("A", "challenged", [_challenge(2, "drop")]),
        _decision("B", "approved"),
        _decision("C", "approved"),
    ]
    result, agreement, _ = _synth(decisions, threshold=0.6)
    assert result.verdict == CriticVerdict.CHALLENGED
    # lone wolf → floors at justify
    assert result.challenges[0].suggestion == "justify"


def test_2_of_3_drop_below_threshold_downgrades():
    # 2/3 = 0.67 if threshold is 0.8 → below threshold → downgrade drop→replace
    decisions = [
        _decision("A", "challenged", [_challenge(1, "drop")]),
        _decision("B", "challenged", [_challenge(1, "drop")]),
        _decision("C", "approved"),
    ]
    result, _, trace = _synth(decisions, threshold=0.8)
    assert result.verdict == CriticVerdict.CHALLENGED
    assert result.challenges[0].suggestion == "replace"
    assert any("downgrade" in t for t in trace)


# ── Lone wolf ────────────────────────────────────────────────────────

def test_lone_wolf_justify_with_N_gt_2_discards():
    decisions = [
        _decision("A", "challenged", [_challenge(3, "justify")]),
        _decision("B", "approved"),
        _decision("C", "approved"),
    ]
    result, _, _ = _synth(decisions, threshold=0.6)
    # lone wolf justify with N>2 → discard → no challenges survive
    assert result.verdict == CriticVerdict.APPROVED


def test_lone_wolf_drop_floors_at_justify():
    decisions = [
        _decision("A", "challenged", [_challenge(1, "drop")]),
        _decision("B", "approved"),
        _decision("C", "approved"),
    ]
    result, _, _ = _synth(decisions, threshold=0.6)
    assert result.verdict == CriticVerdict.CHALLENGED
    assert result.challenges[0].suggestion == "justify"


# ── N=2 tie ──────────────────────────────────────────────────────────

def test_n2_split_keeps_justify():
    decisions = [
        _decision("A", "challenged", [_challenge(2, "justify")]),
        _decision("B", "approved"),
    ]
    result, _, _ = _synth(decisions, threshold=0.6)
    # 1/2 = 0.5 < 0.6 → but N==2 lone wolf → keep justify
    assert result.verdict == CriticVerdict.CHALLENGED
    assert result.challenges[0].suggestion == "justify"


# ── No challenges survive → approved ────────────────────────────────

def test_no_challenges_survive_returns_approved():
    # 1 councillor, threshold=1.0 (exact match required)
    # 1/1 = 1.0 → keep suggestion
    decisions = [_decision("A", "approved")]
    result, _, _ = _synth(decisions, threshold=1.0)
    assert result.verdict == CriticVerdict.APPROVED


# ── Multiple steps ───────────────────────────────────────────────────

def test_multiple_steps_each_assessed_independently():
    decisions = [
        _decision("A", "challenged", [_challenge(1, "drop"), _challenge(2, "replace")]),
        _decision("B", "challenged", [_challenge(1, "drop"), _challenge(2, "replace")]),
    ]
    result, agreement, _ = _synth(decisions, threshold=0.5)
    assert len(result.challenges) == 2
    steps = {c.step for c in result.challenges}
    assert steps == {1, 2}
