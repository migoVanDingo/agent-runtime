"""TopicsDAL — read-only queries against the Briefbot `topic_profiles` table."""
from __future__ import annotations

from typing import List

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from db.models.briefbot.topic import BriefbotTopic


class TopicsDAL:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_top_topics(
        self,
        *,
        limit: int = 20,
        min_momentum: float = 0.0,
    ) -> List[BriefbotTopic]:
        """Return topics sorted by momentum descending.

        Args:
            limit:        Maximum topics to return.
            min_momentum: Only include topics above this momentum threshold.
        """
        stmt = (
            select(BriefbotTopic)
            .where(BriefbotTopic.momentum >= min_momentum)
            .order_by(BriefbotTopic.momentum.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_name(self, name: str) -> BriefbotTopic | None:
        stmt = select(BriefbotTopic).where(BriefbotTopic.name == name)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def search_by_name(self, query: str, *, limit: int = 10) -> List[BriefbotTopic]:
        stmt = (
            select(BriefbotTopic)
            .where(BriefbotTopic.name.ilike(f"%{query}%"))
            .order_by(BriefbotTopic.momentum.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
