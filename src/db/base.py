"""BaseModel for all agent-runtime owned SQLModel tables.

Provides:
  - id          prefixed ULID primary key (set by subclass default_factory)
  - created_at  UTC timestamp on insert
  - updated_at  UTC timestamp, must be updated manually on writes
  - deleted_at  soft-delete timestamp (None = active)
  - is_active   soft-delete flag (False = deleted)

Briefbot read-only models do NOT inherit from this class — they mirror
an external schema that uses different conventions.
"""
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BaseModel(SQLModel):
    created_at: datetime = Field(default_factory=utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=utcnow, nullable=False)
    deleted_at: Optional[datetime] = Field(default=None, nullable=True)
    is_active: bool = Field(default=True, nullable=False)
