"""AgentSessionDAL — CRUD for agent_session table."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from db.base import utcnow
from db.dal.base_dal import BaseDAL
from db.models.agent_session import AgentSession


class AgentSessionDAL(BaseDAL[AgentSession]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(AgentSession, session)

    async def create(
        self,
        *,
        original_query: str,
        model: str,
        provider: str,
    ) -> AgentSession:
        obj = AgentSession(
            original_query=original_query,
            model=model,
            provider=provider,
        )
        return await self.save(obj)

    async def mark_completed(
        self,
        session_id: str,
        *,
        total_steps: int,
        total_tokens: Optional[int] = None,
    ) -> Optional[AgentSession]:
        obj = await self.get_by_id(session_id)
        if obj is None:
            return None
        obj.status = "completed"
        obj.total_steps = total_steps
        obj.total_tokens = total_tokens
        obj.completed_at = utcnow()
        return await self.save(obj)

    async def mark_error(
        self,
        session_id: str,
        *,
        error: str,
        total_steps: int = 0,
    ) -> Optional[AgentSession]:
        obj = await self.get_by_id(session_id)
        if obj is None:
            return None
        obj.status = "error"
        obj.error = error[:2000]  # cap error message length
        obj.total_steps = total_steps
        obj.completed_at = utcnow()
        return await self.save(obj)

    async def list_recent(self, *, limit: int = 20) -> List[AgentSession]:
        stmt = (
            select(AgentSession)
            .where(AgentSession.is_active == True)
            .order_by(AgentSession.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
