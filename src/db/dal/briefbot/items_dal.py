"""ItemsDAL — read-only queries against the Briefbot `items` table."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import or_, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from db.models.briefbot.item import BriefbotItem


class ItemsDAL:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def search(
        self,
        query: str,
        *,
        days: int = 30,
        category: Optional[str] = None,
        limit: int = 20,
        order_by: str = "score",  # "score" | "date"
    ) -> List[BriefbotItem]:
        """Full-text search over title and summary with optional filters.

        Args:
            query:     Search terms (matched against title and summary via LIKE).
            days:      Only return items fetched within this many days.
            category:  Filter by source_category (e.g. 'ai_research', 'papers').
            limit:     Maximum number of results.
            order_by:  'score' (default) or 'date' (most recent first).
        """
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).strftime("%Y-%m-%d")

        stmt = select(BriefbotItem).where(
            BriefbotItem.fetched_at >= cutoff,
            or_(
                BriefbotItem.title.ilike(f"%{query}%"),
                BriefbotItem.summary.ilike(f"%{query}%"),
            ),
        )

        if category:
            stmt = stmt.where(BriefbotItem.source_category == category)

        if order_by == "date":
            stmt = stmt.order_by(BriefbotItem.fetched_at.desc())
        else:
            stmt = stmt.order_by(BriefbotItem.score.desc())

        stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, item_id: str) -> Optional[BriefbotItem]:
        stmt = select(BriefbotItem).where(BriefbotItem.item_id == item_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_top_scored(
        self,
        *,
        days: int = 7,
        category: Optional[str] = None,
        limit: int = 20,
    ) -> List[BriefbotItem]:
        """Return highest-scored items without a text query."""
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).strftime("%Y-%m-%d")

        stmt = select(BriefbotItem).where(BriefbotItem.fetched_at >= cutoff)

        if category:
            stmt = stmt.where(BriefbotItem.source_category == category)

        stmt = stmt.order_by(BriefbotItem.score.desc()).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_opportunities(self, *, days: int = 7, limit: int = 10) -> List[BriefbotItem]:
        """Return highest opportunity-scored items."""
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=days)
        ).strftime("%Y-%m-%d")

        stmt = (
            select(BriefbotItem)
            .where(
                BriefbotItem.fetched_at >= cutoff,
                BriefbotItem.score_opportunity.isnot(None),
            )
            .order_by(BriefbotItem.score_opportunity.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
