"""Unit tests for PlanValidator.validate."""
import pytest
from runtime.validator import PlanValidator
from runtime.schema import ValidationStatus
from planning.schema import Plan, Step, ActionType, StepFlags


def _step(n, action_type=ActionType.FILE_IO, tool="read_file", description=None):
    return Step(
        step=n,
        description=description or f"Step {n} description",
        action_type=action_type,
        tool=tool,
        flags=StepFlags(),
    )


def _plan(*steps, query="do the thing"):
    return Plan(original_query=query, steps=list(steps))


def _validator(toolsets=None, tools=None):
    ts = toolsets or {"file_io", "shell", "web", "analysis", "crypto", "data",
                      "artifacts", "search", "git", "document", "briefbot"}
    t = tools or {"read_file", "write_file", "bash_exec", "read_url", "web_search",
                  "strings", "hash_file", "make_directory"}
    return PlanValidator(ts, t)


v = _validator()


def test_valid_plan_passes():
    plan = _plan(_step(1), _step(2, tool="write_file"))
    result = v.validate(plan)
    assert result.status == ValidationStatus.VALID


def test_empty_steps_fails():
    plan = _plan()
    result = v.validate(plan)
    assert result.status == ValidationStatus.INVALID
    assert "no steps" in (result.feedback or "").lower()


def test_non_sequential_steps_fails():
    plan = _plan(_step(1), _step(3))
    result = v.validate(plan)
    assert result.status == ValidationStatus.INVALID
    assert "sequential" in (result.feedback or "").lower()


def test_empty_description_fails():
    s = _step(1, description="   ")
    plan = _plan(s)
    result = v.validate(plan)
    assert result.status == ValidationStatus.INVALID


def test_duplicate_consecutive_descriptions_fails():
    s1 = _step(1, description="read the file")
    s2 = _step(2, description="read the file")
    plan = _plan(s1, s2)
    result = v.validate(plan)
    assert result.status == ValidationStatus.INVALID


def test_unregistered_tool_fails():
    s = Step(step=1, description="do it", action_type=ActionType.FILE_IO,
             tool="nonexistent_tool", flags=StepFlags())
    plan = _plan(s)
    result = v.validate(plan)
    assert result.status == ValidationStatus.INVALID
    assert "nonexistent_tool" in (result.feedback or "")


def test_conversation_step_without_tool_passes():
    s = Step(step=1, description="explain the output", action_type=ActionType.CONVERSATION,
             tool=None, flags=StepFlags())
    plan = _plan(s)
    result = v.validate(plan)
    assert result.status == ValidationStatus.VALID


def test_non_conversation_step_without_tool_fails():
    s = Step(step=1, description="read it", action_type=ActionType.FILE_IO,
             tool=None, flags=StepFlags())
    plan = _plan(s)
    result = v.validate(plan)
    assert result.status == ValidationStatus.INVALID


def test_query_expecting_write_output_fails_without_write_file():
    plan = _plan(_step(1), query="write a report to results.md")
    result = v.validate(plan)
    assert result.status == ValidationStatus.INVALID
    assert "write_file" in (result.feedback or "")


def test_query_expecting_write_output_passes_with_write_file():
    plan = _plan(_step(1), _step(2, tool="write_file"), query="write a report to results.md")
    result = v.validate(plan)
    assert result.status == ValidationStatus.VALID
