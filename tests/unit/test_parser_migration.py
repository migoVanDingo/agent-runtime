"""Tests confirming each migrated parser handles fenced, bare, and malformed JSON."""
import pytest


def _monitor_parse(raw):
    from runtime.monitor import ExecutionMonitor
    from providers.base import BaseProvider
    class _FakeProv(BaseProvider):
        capabilities = None
        def _chat_impl(self, **kwargs): ...
    m = ExecutionMonitor.__new__(ExecutionMonitor)
    return m._parse(raw)


def _importance_parse(raw):
    from runtime.importance import ImportanceScorer
    s = ImportanceScorer.__new__(ImportanceScorer)
    s._cache = {}
    return s._parse(raw)


def _planner_parse(raw):
    from planning.planner import Planner
    p = Planner.__new__(Planner)
    return p._parse(raw)


# ── Monitor ───────────────────────────────────────────────────────────────────

def test_monitor_bare_json():
    from runtime.schema import StepDecision
    result = _monitor_parse('{"decision": "retry", "reason": "fail", "confidence": 0.9}')
    assert result.decision == StepDecision.RETRY
    assert result.confidence == pytest.approx(0.9)


def test_monitor_fenced_json():
    from runtime.schema import StepDecision
    result = _monitor_parse('```json\n{"decision": "continue", "reason": "ok", "confidence": 1.0}\n```')
    assert result.decision == StepDecision.CONTINUE


def test_monitor_malformed_returns_continue():
    from runtime.schema import StepDecision
    result = _monitor_parse("not json at all")
    assert result.decision == StepDecision.CONTINUE


def test_monitor_invalid_decision_defaults():
    from runtime.schema import StepDecision
    result = _monitor_parse('{"decision": "banana", "reason": "?"}')
    assert result.decision == StepDecision.CONTINUE


# ── ImportanceScorer ──────────────────────────────────────────────────────────

def test_importance_bare_json():
    from runtime.schema import Importance
    result = _importance_parse('{"importance": "high", "reason": "key fact"}')
    assert result == Importance.HIGH


def test_importance_fenced_json():
    from runtime.schema import Importance
    result = _importance_parse('```\n{"importance": "critical"}\n```')
    assert result == Importance.CRITICAL


def test_importance_malformed_returns_medium():
    from runtime.schema import Importance
    result = _importance_parse("garbage")
    assert result == Importance.MEDIUM


# ── Planner._parse ────────────────────────────────────────────────────────────

_VALID_PLAN = '''{
  "original_query": "test",
  "requires_synthesis": false,
  "steps": [
    {"step": 1, "description": "do it", "action_type": "file_io",
     "tool": "read_file", "produces": null,
     "flags": {"retry": false, "escalate": false, "defer": false}}
  ]
}'''


def test_planner_bare_json():
    plan = _planner_parse(_VALID_PLAN)
    assert plan is not None
    assert len(plan.steps) == 1


def test_planner_fenced_json():
    plan = _planner_parse(f"```json\n{_VALID_PLAN}\n```")
    assert plan is not None


def test_planner_malformed_returns_none():
    plan = _planner_parse("not json")
    assert plan is None


def test_planner_empty_steps_returns_none():
    plan = _planner_parse('{"original_query": "x", "requires_synthesis": false, "steps": []}')
    assert plan is None
