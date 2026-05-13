"""Execution state for a plan run.

Plan and Step (in planning/schema.py) are the planner's output — immutable spec.
PlanRun and StepRun carry mutable execution state so spec and runtime are separate.

Also owns StepRuntimeState / StepFlags — the per-step runtime flags (retry_count,
deferred, skipped).  These belong here because the runtime, not the planner, owns
their lifecycle.  planning/schema.py re-exports both names for backwards compat.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from planning.schema import Plan, Step


class StepStatus(str, Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    COMPLETED = "completed"
    ERROR     = "error"


@dataclass
class StepRuntimeState:
    """Runtime-managed state for a step. Never set by the planner or skills.

    The execution stage and monitor mutate these as the step runs.
    Defined here (runtime/run_state.py) because the runtime owns the lifecycle.
    planning/schema.py re-exports this class for backwards-compatible imports.
    """
    retry_count: int = 0
    deferred: bool = False
    skipped: bool = False

    def to_dict(self) -> dict:
        return {
            "retry_count": self.retry_count,
            "deferred": self.deferred,
            "skipped": self.skipped,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "StepRuntimeState":
        return cls(
            retry_count=data.get("retry_count", 0),
            deferred=data.get("deferred", False),
            skipped=data.get("skipped", False),
        )


# Alias kept for compatibility with any remaining callers.
StepFlags = StepRuntimeState


@dataclass
class StepRun:
    """Mutable execution state for one plan step."""
    spec: "Step"
    status: StepStatus = StepStatus.PENDING
    result: str | None = None
    error: str | None = None
    retry_count: int = 0
    deferred: bool = False
    skipped: bool = False

    @property
    def step(self) -> int:
        return self.spec.step

    @property
    def description(self) -> str:
        return self.spec.description

    @property
    def action_type(self):
        return self.spec.action_type

    @property
    def tool(self):
        return self.spec.tool

    @property
    def produces(self):
        return self.spec.produces


@dataclass
class PlanRun:
    """Mutable execution state for an entire plan."""
    spec: "Plan"
    steps: list[StepRun] = field(default_factory=list)
    replan_count: int = 0

    @classmethod
    def from_plan(cls, plan: "Plan") -> "PlanRun":
        """Create a PlanRun from a Plan spec with all steps in PENDING state."""
        steps = [StepRun(spec=s) for s in plan.steps]
        return cls(spec=plan, steps=steps)

    @property
    def original_query(self) -> str:
        return self.spec.original_query

    @property
    def risk(self) -> str:
        return self.spec.risk
