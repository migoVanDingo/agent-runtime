"""Plan and Step — one Plan per planner call, one Step per execution step."""
from __future__ import annotations

from typing import Optional

import sqlalchemy as sa
from sqlmodel import Field

from db.base import BaseModel
from db.utils.generate_id import generate_id
from db.utils.id_prefix import IdPrefix


class Plan(BaseModel, table=True):
    __tablename__ = "plan"

    id: str = Field(
        primary_key=True,
        default_factory=lambda: generate_id(IdPrefix.PLAN),
    )
    session_id: str = Field(foreign_key="agent_session.id", nullable=False, index=True)
    # 0 = original plan, 1+ = replan iteration
    plan_index: int = Field(default=0, nullable=False)
    original_query: str = Field(nullable=False)
    # Full JSON-serialized step list from planner output
    steps_json: str = Field(nullable=False)
    replan_reason: Optional[str] = Field(default=None)


class Step(BaseModel, table=True):
    __tablename__ = "step"

    id: str = Field(
        primary_key=True,
        default_factory=lambda: generate_id(IdPrefix.STEP),
    )
    plan_id: str = Field(foreign_key="plan.id", nullable=False, index=True)
    session_id: str = Field(foreign_key="agent_session.id", nullable=False, index=True)
    step_index: int = Field(nullable=False)
    action_type: str = Field(nullable=False)
    tool: Optional[str] = Field(default=None)
    description: str = Field(nullable=False)
    # pending | success | error | skipped
    status: str = Field(nullable=False)
    # Truncated to first 1000 chars of tool output
    result: Optional[str] = Field(default=None)
    error: Optional[str] = Field(default=None)
    retry_count: int = Field(default=0, nullable=False)
    importance_score: Optional[float] = Field(
        default=None,
        sa_column=sa.Column(sa.REAL, nullable=True),
    )
    duration_ms: Optional[int] = Field(default=None)
