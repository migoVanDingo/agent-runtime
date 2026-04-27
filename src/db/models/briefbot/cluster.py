"""BriefbotCluster / BriefbotClusterMembership — read-only mirrors.

Uses BRIEFBOT_METADATA so these are never included in agent-runtime migrations.
"""
from __future__ import annotations

from typing import Optional

import sqlalchemy as sa
from sqlmodel import Field, SQLModel

from db.models.briefbot.item import BRIEFBOT_METADATA


class BriefbotCluster(SQLModel, table=True, metadata=BRIEFBOT_METADATA):
    __tablename__ = "clusters"

    cluster_id: str = Field(primary_key=True)
    label: Optional[str] = Field(default=None)
    # ISO string timestamps
    created_at: str = Field(nullable=False)
    first_seen_at: Optional[str] = Field(default=None)
    last_seen_at: Optional[str] = Field(default=None)
    item_count: int = Field(default=0, nullable=False)
    sources_count: int = Field(default=0, nullable=False)
    # JSON TEXT columns
    categories: Optional[str] = Field(default=None)
    top_tokens: Optional[str] = Field(default=None)
    # Velocity (items added per window)
    velocity_1d: int = Field(default=0, nullable=False)
    velocity_3d: int = Field(default=0, nullable=False)
    velocity_7d: int = Field(default=0, nullable=False)
    # Ranking signals
    diversity_score: float = Field(default=0.0, nullable=False)
    trend_score: float = Field(default=0.0, nullable=False)
    # Representative item for display
    representative_url: Optional[str] = Field(default=None)
    representative_title: Optional[str] = Field(default=None)


class BriefbotClusterMembership(SQLModel, table=True, metadata=BRIEFBOT_METADATA):
    __tablename__ = "cluster_memberships"

    item_id: str = Field(primary_key=True, nullable=False)
    cluster_id: str = Field(nullable=False)
    assigned_at: str = Field(nullable=False)
    similarity: float = Field(default=0.0, nullable=False)
