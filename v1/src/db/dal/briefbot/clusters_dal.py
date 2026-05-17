"""ClustersDAL — read-only queries against Briefbot cluster tables."""
from __future__ import annotations

from typing import List

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from db.models.briefbot.cluster import BriefbotCluster, BriefbotClusterMembership
from db.models.briefbot.item import BriefbotItem


class ClustersDAL:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_trending(
        self,
        *,
        window: str = "3d",  # "1d" | "3d" | "7d"
        limit: int = 10,
    ) -> List[BriefbotCluster]:
        """Return clusters sorted by trend_score + velocity for the given window.

        Args:
            window: Which velocity window to factor in: '1d', '3d', or '7d'.
            limit:  Maximum clusters to return.
        """
        stmt = select(BriefbotCluster)

        if window == "1d":
            stmt = stmt.order_by(
                BriefbotCluster.velocity_1d.desc(),
                BriefbotCluster.trend_score.desc(),
            )
        elif window == "7d":
            stmt = stmt.order_by(
                BriefbotCluster.velocity_7d.desc(),
                BriefbotCluster.trend_score.desc(),
            )
        else:  # default 3d
            stmt = stmt.order_by(
                BriefbotCluster.velocity_3d.desc(),
                BriefbotCluster.trend_score.desc(),
            )

        stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_items_for_cluster(
        self,
        cluster_id: str,
        *,
        limit: int = 10,
    ) -> List[BriefbotItem]:
        """Return items belonging to a cluster, sorted by similarity descending."""
        # Join cluster_memberships → items
        stmt = (
            select(BriefbotItem)
            .join(
                BriefbotClusterMembership,
                BriefbotClusterMembership.item_id == BriefbotItem.item_id,
            )
            .where(BriefbotClusterMembership.cluster_id == cluster_id)
            .order_by(BriefbotClusterMembership.similarity.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, cluster_id: str) -> BriefbotCluster | None:
        stmt = select(BriefbotCluster).where(
            BriefbotCluster.cluster_id == cluster_id
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
