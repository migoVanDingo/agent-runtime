"""Tests for PlanRun / StepRun — spec / execution state separation."""
import pytest
from runtime.run_state import PlanRun, StepRun, StepStatus
from planning.schema import Plan, Step, ActionType, StepFlags


def _plan(*steps):
    return Plan(original_query="test", steps=list(steps))


def _step(n, tool="read_file"):
    return Step(
        step=n, description=f"step {n}",
        action_type=ActionType.FILE_IO, tool=tool, flags=StepFlags()
    )


def test_plan_run_from_plan_creates_pending_steps():
    plan = _plan(_step(1), _step(2))
    run = PlanRun.from_plan(plan)
    assert len(run.steps) == 2
    assert all(sr.status == StepStatus.PENDING for sr in run.steps)


def test_plan_run_delegates_spec_fields():
    plan = _plan(_step(1))
    run = PlanRun.from_plan(plan)
    assert run.original_query == "test"
    assert run.requires_synthesis == plan.requires_synthesis


def test_step_run_wraps_spec():
    s = _step(3, tool="write_file")
    sr = StepRun(spec=s)
    assert sr.step == 3
    assert sr.tool == "write_file"
    assert sr.description == "step 3"
    assert sr.status == StepStatus.PENDING


def test_step_run_status_mutation():
    sr = StepRun(spec=_step(1))
    sr.status = StepStatus.RUNNING
    assert sr.status == StepStatus.RUNNING
    sr.result = "some output"
    assert sr.result == "some output"


def test_plan_spec_not_mutated_by_step_run():
    s = _step(1)
    plan = _plan(s)
    run = PlanRun.from_plan(plan)
    run.steps[0].status = StepStatus.COMPLETED
    run.steps[0].result = "done"
    # Original Step spec unchanged
    assert plan.steps[0].status.value == "pending"
    assert plan.steps[0].result is None


def test_plan_run_replan_count_starts_at_zero():
    run = PlanRun.from_plan(_plan(_step(1)))
    assert run.replan_count == 0
    run.replan_count += 1
    assert run.replan_count == 1
