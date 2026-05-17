"""BriefbotTopic — read-only mirror of the Briefbot `topic_profiles` table.

Uses BRIEFBOT_METADATA so it is never included in agent-runtime migrations.
"""
from __future__ import annotations

from typing import Optional

from sqlmodel import Field, SQLModel

from db.models.briefbot.item import BRIEFBOT_METADATA


class BriefbotTopic(SQLModel, table=True, metadata=BRIEFBOT_METADATA):
    __tablename__ = "topic_profiles"

    topic_id: str = Field(primary_key=True)
    name: str = Field(nullable=False)
    kind: str = Field(nullable=False)
    # ISO string timestamps
    first_seen_at: Optional[str] = Field(default=None)
    last_seen_at: Optional[str] = Field(default=None)
    # Rolling window counts
    count_1d: int = Field(default=0)
    count_3d: int = Field(default=0)
    count_7d: int = Field(default=0)
    count_30d: int = Field(default=0)
    # Trend signal
    momentum: float = Field(default=0.0)
    # Timestamps (TEXT in Briefbot)
    created_at: str = Field(nullable=False)
    updated_at: str = Field(nullable=False)
