"""Artifact — one row per item registered in the artifact store during a session."""
from __future__ import annotations

from typing import Optional

from sqlmodel import Field

from db.base import BaseModel
from db.utils.generate_id import generate_id
from db.utils.id_prefix import IdPrefix


class Artifact(BaseModel, table=True):
    __tablename__ = "artifact"

    id: str = Field(
        primary_key=True,
        default_factory=lambda: generate_id(IdPrefix.ARTIFACT),
    )
    session_id: str = Field(foreign_key="agent_session.id", nullable=False, index=True)
    # The artifact store key (e.g. "search_results", "extracted_paper")
    key: str = Field(nullable=False)
    mime_type: Optional[str] = Field(default=None)
    size_bytes: Optional[int] = Field(default=None)
    # hot | warm | cold
    tier: str = Field(nullable=False)
    # First 500 chars for quick inspection without fetching full content
    content_preview: Optional[str] = Field(default=None)
    # Set for cold-tier artifacts stored on disk
    storage_path: Optional[str] = Field(default=None)
