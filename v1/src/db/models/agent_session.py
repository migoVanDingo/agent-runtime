"""AgentSession — one row per top-level user query handled by the agent."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlmodel import Field

from db.base import BaseModel
from db.utils.generate_id import generate_id
from db.utils.id_prefix import IdPrefix


class AgentSession(BaseModel, table=True):
    __tablename__ = "agent_session"
    __table_args__ = (
        sa.Index("ix_agent_session_created_at", "created_at"),
    )

    id: str = Field(
        primary_key=True,
        default_factory=lambda: generate_id(IdPrefix.SESSION),
    )
    original_query: str = Field(nullable=False)
    model: str = Field(nullable=False)
    provider: str = Field(nullable=False)
    # active | completed | error
    status: str = Field(default="active", nullable=False, index=True)
    total_steps: int = Field(default=0, nullable=False)
    total_tokens: Optional[int] = Field(default=None)
    error: Optional[str] = Field(default=None)
    completed_at: Optional[datetime] = Field(default=None)
