"""Tests for the post-SES01KRTZG0R4BN105HB2M8J17XTE hardening:

- Fix 2: get_toolset_schema skip-and-warn on unknown toolsets (no KeyError).
- Fix 3B: ExecutionMonitor detects 'Error: sub-agent ... failed' → REPLAN.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


# ── Fix 2 — get_toolset_schema doesn't raise on unknown names ──────────────


def test_get_toolset_schema_skips_unknown_toolsets():
    """The router can return names that aren't in a narrowed registry — skip, don't raise."""
    from tools.base import BaseTool, InputSchema, ToolProperty
    from tools.registry import ToolRegistry
    from tools.toolset import Toolset

    class _T(BaseTool):
        name = "t"
        description = "ok"
        @property
        def input_schema(self):
            return InputSchema(properties={"x": ToolProperty(type="string", description="")}, required=["x"])
        def execute(self, tool_input):
            return ""

    reg = ToolRegistry()
    reg.register_toolset(Toolset(name="known", description="", tools=[_T()]))

    # Mix known + unknown names — should return known tool schemas, skip unknown.
    schemas = reg.get_toolset_schema(["known", "missing_one", "another_missing"])
    assert len(schemas) == 1
    assert schemas[0]["name"] == "t"


def test_get_toolset_schema_all_unknown_returns_empty():
    from tools.registry import ToolRegistry
    reg = ToolRegistry()
    schemas = reg.get_toolset_schema(["nope", "also_nope"])
    assert schemas == []


def test_get_toolset_tools_still_raises_for_strict_callers():
    """Preserve the existing strict-by-name lookup for callers that need it."""
    from tools.registry import ToolRegistry
    reg = ToolRegistry()
    with pytest.raises(KeyError, match="Toolset not found"):
        reg.get_toolset_tools("does_not_exist")


# ── Fix 3B — monitor detects sub-agent failure → REPLAN ─────────────────────


def _make_monitor(tmp_path_factory=None):
    """Build a real ExecutionMonitor with a stub provider (LLM won't be reached)."""
    from runtime.monitor import ExecutionMonitor

    class _StubProvider:
        def chat(self, **kwargs):  # pragma: no cover — short-circuit prevents call
            raise AssertionError("monitor should short-circuit before reaching LLM")

    return ExecutionMonitor(_StubProvider(), skill_registry=None)


def _make_step_and_plan():
    from planning.schema import Plan, Step, ActionType
    step = Step(
        step=2,
        description="Delegate to ghidra_analyst",
        action_type=ActionType.SUBAGENT,
        tool="subagent_ghidra_analyst",
    )
    plan = Plan(
        original_query="analyse proc",
        steps=[
            Step(step=1, description="file_info", action_type=ActionType.ANALYSIS, tool="file_info"),
            step,
        ],
        risk="low",
    )
    return step, plan


def test_monitor_detects_subagent_failure_string_and_replans():
    from runtime.schema import StepDecision

    mon = _make_monitor()
    step, plan = _make_step_and_plan()

    # The exact format SubAgentTool.execute returns on failure.
    failure_result = (
        "Error: sub-agent 'ghidra_analyst' failed: "
        "KeyError: 'Toolset not found: analysis'"
    )
    assessment = mon.assess(step, plan, failure_result)

    assert assessment.decision == StepDecision.REPLAN
    assert assessment.confidence == 1.0
    assert "ghidra_analyst" in assessment.reason
    assert "failed" in assessment.reason.lower()
    # Must instruct the planner NOT to retry the same sub-agent
    assert "WITHOUT this sub-agent" in assessment.reason or "without" in assessment.reason.lower()


def test_monitor_normal_error_still_routes_to_llm():
    """Don't false-positive: a generic 'Error: …' must still go through LLM assessment.

    We can't easily exercise _llm_assess here without a real provider, so we
    verify by asserting that the failure regex doesn't match plain errors.
    """
    from runtime.monitor import _SUBAGENT_FAILURE_RE

    # Generic tool errors should NOT match (no "sub-agent" prefix)
    assert _SUBAGENT_FAILURE_RE.match("Error: bash_exec failed: permission denied") is None
    assert _SUBAGENT_FAILURE_RE.match("Error: file not found") is None
    assert _SUBAGENT_FAILURE_RE.match("Error: connection timeout") is None

    # Only the specific sub-agent shape matches
    assert _SUBAGENT_FAILURE_RE.match("Error: sub-agent 'X' failed: boom") is not None


def test_subagent_failure_regex_captures_name_and_reason():
    from runtime.monitor import _SUBAGENT_FAILURE_RE
    m = _SUBAGENT_FAILURE_RE.match(
        "Error: sub-agent 'ghidra_analyst' failed: timeout after 600s"
    )
    assert m is not None
    assert m.group(1) == "ghidra_analyst"
    assert "timeout" in m.group(2)


def test_monitor_detects_artifact_store_unavailable_and_replans():
    """Bug #4 from SES01KRV1XJ7WK4177X1KHDYEWQ4B — the planner kept re-emitting
    store_artifact across 6 replans because the LLM monitor's reasoning didn't
    convey 'this tool is dead for the whole session'. A regex short-circuit
    forces REPLAN with a hard message.
    """
    from runtime.schema import StepDecision

    mon = _make_monitor()
    step, plan = _make_step_and_plan()

    failure_result = "Error: artifact store is not initialized."
    assessment = mon.assess(step, plan, failure_result)

    assert assessment.decision == StepDecision.REPLAN
    assert assessment.confidence == 1.0
    assert "artifact" in assessment.reason.lower()
    # Must tell planner to restructure without artifact tools, not just retry
    assert "without" in assessment.reason.lower()


def test_step_runner_surfaces_raw_subagent_error_over_wrapped_response():
    """Bug #19 from SES01KRV1XJ7WK4177X1KHDYEWQ4B — when a sub-agent tool returned
    an error, the model wrapped it with prose. The wrapped text became step.result
    and never matched the subagent-failure regex, so the monitor went heuristics
    PASS instead of REPLAN. The fix: step_runner returns last_tool_output_raw
    when it matches a non-recoverable pattern.
    """
    import re
    from runtime.stages.execution.step_runner import _NON_RECOVERABLE_TOOL_ERROR_RE

    # The exact shape SubAgentTool emits on failure
    assert _NON_RECOVERABLE_TOOL_ERROR_RE.match(
        "Error: sub-agent 'ghidra_analyst' failed: timeout after 600s"
    )
    # And artifact store unavailable
    assert _NON_RECOVERABLE_TOOL_ERROR_RE.match(
        "Error: artifact store is not initialized."
    )
    # Generic errors should NOT match (would over-fire and break legitimate flows)
    assert not _NON_RECOVERABLE_TOOL_ERROR_RE.match("Error: file not found")
    assert not _NON_RECOVERABLE_TOOL_ERROR_RE.match("Error: connection timeout")
    # Wrapped prose should NOT match — this is the actual production failure
    assert not _NON_RECOVERABLE_TOOL_ERROR_RE.match(
        "The sub-agent failed. I'll need to take a different approach."
    )


def test_subagent_failure_regex_handles_multiline_traceback():
    """Real failures can carry multi-line tracebacks; the regex must still match."""
    from runtime.monitor import _SUBAGENT_FAILURE_RE
    multiline = (
        "Error: sub-agent 'ghidra_analyst' failed: KeyError: 'Toolset not found: analysis'\n"
        "  File \"foo.py\", line 10, in bar\n"
        "  …"
    )
    m = _SUBAGENT_FAILURE_RE.match(multiline)
    assert m is not None
    assert m.group(1) == "ghidra_analyst"
    assert "KeyError" in m.group(2)
