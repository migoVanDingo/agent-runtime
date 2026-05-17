"""BriefbotItem — read-only mirror of the Briefbot `items` table.

This model uses a separate MetaData instance (BRIEFBOT_METADATA) so it is
never included in Alembic auto-migrations for the agent-runtime database.
Never write to this model from agent-runtime code.
"""
from __future__ import annotations

from typing import Optional

import sqlalchemy as sa
from sqlmodel import Field, SQLModel

# Separate metadata — excluded from SQLModel.metadata and Alembic migrations
BRIEFBOT_METADATA = sa.MetaData()


class BriefbotItem(SQLModel, table=True, metadata=BRIEFBOT_METADATA):
    __tablename__ = "items"

    item_id: str = Field(primary_key=True)
    dedupe_key: str = Field(nullable=False)
    canonical_url: Optional[str] = Field(default=None)
    url: Optional[str] = Field(default=None)
    source_id: str = Field(nullable=False)
    source_name: str = Field(nullable=False)
    source_category: Optional[str] = Field(default=None)
    source_tier: Optional[int] = Field(default=None)
    source_max_daily: Optional[int] = Field(default=None)
    title: str = Field(nullable=False)
    author: Optional[str] = Field(default=None)
    summary: Optional[str] = Field(default=None)
    # ISO string timestamps (Briefbot stores as TEXT)
    published_at: Optional[str] = Field(default=None)
    fetched_at: str = Field(nullable=False)
    last_seen_at: str = Field(nullable=False)
    # JSON arrays stored as TEXT
    tags_json: str = Field(nullable=False)
    raw_json: str = Field(nullable=False)
    metrics_json: Optional[str] = Field(default=None)
    watch_hits_json: Optional[str] = Field(default=None)
    opportunity_tags_json: Optional[str] = Field(default=None)
    # Scores
    score: float = Field(nullable=False)
    score_opportunity: Optional[float] = Field(default=None)
    opportunity_reason: Optional[str] = Field(default=None)
