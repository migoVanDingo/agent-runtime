"""BaseDAL[T] — generic async CRUD base for all agent-runtime owned tables.

Briefbot DALs do NOT inherit from this class — they are read-only and use
their own base with no save/delete methods.
"""
from __future__ import annotations

from typing import Generic, Optional, Type, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import SQLModel, select

from db.base import utcnow

T = TypeVar("T", bound=SQLModel)


class BaseDAL(Generic[T]):
    def __init__(self, model: Type[T], session: AsyncSession) -> None:
        self.model = model
        self.session = session

    async def get_by_id(self, obj_id: str) -> Optional[T]:
        stmt = select(self.model).where(
            self.model.id == obj_id,  # type: ignore[attr-defined]
            self.model.is_active == True,  # type: ignore[attr-defined]
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def save(self, obj: T) -> T:
        # Keep updated_at current on every save
        if hasattr(obj, "updated_at"):
            obj.updated_at = utcnow()  # type: ignore[attr-defined]
        self.session.add(obj)
        await self.session.commit()
        await self.session.refresh(obj)
        return obj

    async def soft_delete(self, obj: T) -> None:
        obj.deleted_at = utcnow()  # type: ignore[attr-defined]
        obj.is_active = False  # type: ignore[attr-defined]
        await self.save(obj)
