"""Execution state for a plan run.

Plan and Step (in planning/schema.py) are the planner's output — immutable spec.
PlanRun and StepRun carry mutable execution state so spec and runtime are separate.
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
