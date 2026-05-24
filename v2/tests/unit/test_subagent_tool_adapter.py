"""SubAgentTool — adapter that exposes a spec as a parent-callable Tool."""
from __future__ import annotations

import json

import pytest

from arc.runtime.subagents.result import SubAgentResult
from arc.runtime.subagents.tool_adapter import SubAgentTool
from arc.subagent_api import SubAgentSpec
from arc.tools.base import ToolError


class _FakeRunner:
    """Captures dispatch calls and returns a scripted SubAgentResult."""
    def __init__(self, result: SubAgentResult) -> None:
        self._result = result
        self.calls = []

    def dispatch(self, spec_name, task, *, context_bundle=None,
                 parent_session_id, parent_turn_id=None, count_against_quota=True):
        self.calls.append({
            "spec_name": spec_name,
            "task": task,
            "context_bundle": context_bundle,
            "parent_session_id": parent_session_id,
            "parent_turn_id": parent_turn_id,
        })
        return self._result


def _spec(name="echo") -> SubAgentSpec:
    return SubAgentSpec(
        name=name, description="echo the task", provider="anthropic",
        model="claude-haiku-4-5", system_prompt="p",
    )


def test_tool_name_format():
    runner = _FakeRunner(SubAgentResult(
        status="ok", output="x", error_message=None, child_session_id="c",
        cost_usd=0.0, turns=1, tool_calls=0, wallclock_s=0.1,
    ))
    tool = SubAgentTool(_spec("video_analyst"), runner)
    assert tool.name == "subagent_video_analyst"


def test_schema_has_task_required():
    runner = _FakeRunner(SubAgentResult(
        status="ok", output="x", error_message=None, child_session_id="c",
        cost_usd=0.0, turns=1, tool_calls=0, wallclock_s=0.1,
    ))
    tool = SubAgentTool(_spec(), runner)
    schema = tool.input_schema.to_json_schema()
    assert "task" in schema["properties"]
    assert "context_bundle" in schema["properties"]
    assert schema["required"] == ["task"]


def test_ok_returns_json_string():
    result = SubAgentResult(
        status="ok", output='{"a":1}', error_message=None,
        child_session_id="child_1", cost_usd=0.01,
        turns=3, tool_calls=2, wallclock_s=4.2, retries_attempted=0,
    )
    runner = _FakeRunner(result)
    tool = SubAgentTool(_spec(), runner)
    out = tool.execute({"task": "do it"})
    parsed = json.loads(out)
    assert parsed["status"] == "ok"
    assert parsed["output"] == '{"a":1}'
    assert parsed["child_session_id"] == "child_1"
    assert parsed["metrics"]["turns"] == 3


def test_error_status_raises_tool_error():
    result = SubAgentResult(
        status="error", output="", error_message="something broke",
        child_session_id="child_1", cost_usd=0.0,
        turns=0, tool_calls=0, wallclock_s=0.05,
    )
    runner = _FakeRunner(result)
    tool = SubAgentTool(_spec(), runner)
    with pytest.raises(ToolError, match="something broke"):
        tool.execute({"task": "do it"})


def test_timeout_raises_tool_error():
    result = SubAgentResult(
        status="timeout", output="", error_message="timed out after 30s",
        child_session_id="child_1", cost_usd=0.0,
        turns=5, tool_calls=4, wallclock_s=30.0,
    )
    runner = _FakeRunner(result)
    tool = SubAgentTool(_spec(), runner)
    with pytest.raises(ToolError, match="timed out"):
        tool.execute({"task": "do it"})


def test_empty_task_raises():
    runner = _FakeRunner(SubAgentResult(
        status="ok", output="x", error_message=None, child_session_id="c",
        cost_usd=0.0, turns=1, tool_calls=0, wallclock_s=0.1,
    ))
    tool = SubAgentTool(_spec(), runner)
    with pytest.raises(ToolError, match="non-empty"):
        tool.execute({"task": ""})


def test_context_bundle_passed_through():
    runner = _FakeRunner(SubAgentResult(
        status="ok", output="x", error_message=None, child_session_id="c",
        cost_usd=0.0, turns=1, tool_calls=0, wallclock_s=0.1,
    ))
    tool = SubAgentTool(_spec(), runner)
    tool.execute({"task": "analyze", "context_bundle": "extra context"})
    assert runner.calls[0]["context_bundle"] == "extra context"


def test_context_bundle_non_string_rejected():
    runner = _FakeRunner(SubAgentResult(
        status="ok", output="x", error_message=None, child_session_id="c",
        cost_usd=0.0, turns=1, tool_calls=0, wallclock_s=0.1,
    ))
    tool = SubAgentTool(_spec(), runner)
    with pytest.raises(ToolError, match="must be a string"):
        tool.execute({"task": "go", "context_bundle": 42})  # type: ignore[arg-type]
