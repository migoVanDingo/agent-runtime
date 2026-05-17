"""ArtifactDAL — CRUD for artifact table."""
from __future__ import annotations

from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from db.dal.base_dal import BaseDAL
from db.models.artifact import Artifact


class ArtifactDAL(BaseDAL[Artifact]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Artifact, session)

    async def create(
        self,
        *,
        session_id: str,
        key: str,
        tier: str,
        mime_type: Optional[str] = None,
        size_bytes: Optional[int] = None,
        content_preview: Optional[str] = None,
        storage_path: Optional[str] = None,
    ) -> Artifact:
        obj = Artifact(
            session_id=session_id,
            key=key,
            tier=tier,
            mime_type=mime_type,
            size_bytes=size_bytes,
            content_preview=content_preview[:500] if content_preview else None,
            storage_path=storage_path,
        )
        return await self.save(obj)

    async def list_by_session(self, session_id: str) -> List[Artifact]:
        stmt = (
            select(Artifact)
            .where(Artifact.session_id == session_id, Artifact.is_active == True)
            .order_by(Artifact.created_at.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_session_and_key(
        self, session_id: str, key: str
    ) -> Optional[Artifact]:
        stmt = select(Artifact).where(
            Artifact.session_id == session_id,
            Artifact.key == key,
            Artifact.is_active == True,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
