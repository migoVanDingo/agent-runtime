"""briefbot_trending — what's trending in the Briefbot corpus right now.

Returns trending storyline clusters (by velocity + trend_score) and
hot topics (by momentum). No query needed — useful for "what's happening
in AI this week" or "what's trending in ML" questions.
"""
from __future__ import annotations

from app_config import settings
from db.sync import run_async
from logger import get_logger
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight

logger = get_logger(__name__)


class BriefbotTrendingTool(BaseTool):
    name = "briefbot_trending"
    description = (
        "Return trending storyline clusters and hot topics from the Briefbot corpus. "
        "Clusters are grouped storylines ranked by velocity (items added per window) "
        "and trend_score. Topics are ranked by momentum. "
        "Use this for 'what's trending', 'what's hot in AI', or 'what's new this week' questions."
    )
    weight = ToolWeight.LIGHTWEIGHT

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "window": ToolProperty(
                    type="string",
                    description="Velocity window: 1d, 3d (default), or 7d",
                ),
                "clusters_limit": ToolProperty(
                    type="number",
                    description="Max trending clusters to return (default 8)",
                ),
                "topics_limit": ToolProperty(
                    type="number",
                    description="Max hot topics to return (default 10)",
                ),
            },
            required=[],
        )

    def execute(self, tool_input: dict) -> str:
        if not settings.briefbot_db_path:
            return (
                "Error: BRIEFBOT_DB_PATH is not configured. "
                "Add BRIEFBOT_DB_PATH=/path/to/briefbot.db to your .env file."
            )

        window = tool_input.get("window", "3d")
        if window not in ("1d", "3d", "7d"):
            window = "3d"
        clusters_limit = min(int(tool_input.get("clusters_limit", 8)), 20)
        topics_limit = min(int(tool_input.get("topics_limit", 10)), 30)

        try:
            clusters, topics = run_async(_do_trending(window, clusters_limit, topics_limit))
        except Exception as e:
            logger.error(f"briefbot_trending error: {e}")
            return f"Error: briefbot_trending failed: {type(e).__name__}: {e}"

        lines = [f"Briefbot Trending  (window: {window})\n"]

        lines.append(f"=== Trending Storylines ({len(clusters)}) ===")
        if not clusters:
            lines.append("  No clusters found.")
        for i, c in enumerate(clusters, 1):
            label = c.label or "(unlabeled)"
            vel = getattr(c, f"velocity_{window}", 0)
            lines.append(f"[{i}] {label}")
            lines.append(f"    trend={c.trend_score:.2f}  velocity_{window}={vel}  items={c.item_count}")
            if c.representative_title:
                lines.append(f"    Representative: {c.representative_title[:80]}")
            if c.representative_url:
                lines.append(f"    {c.representative_url}")
            lines.append("")

        lines.append(f"=== Hot Topics ({len(topics)}) ===")
        if not topics:
            lines.append("  No topics found.")
        for i, t in enumerate(topics, 1):
            lines.append(
                f"[{i}] {t.name}  ({t.kind})  "
                f"momentum={t.momentum:.2f}  "
                f"7d={t.count_7d}  3d={t.count_3d}  1d={t.count_1d}"
            )

        return "\n".join(lines).rstrip()


async def _do_trending(window: str, clusters_limit: int, topics_limit: int):
    from db.session import briefbot_session
    from db.dal.briefbot.clusters_dal import ClustersDAL
    from db.dal.briefbot.topics_dal import TopicsDAL
    async with briefbot_session() as session:
        clusters_dal = ClustersDAL(session)
        topics_dal = TopicsDAL(session)
        clusters = await clusters_dal.get_trending(window=window, limit=clusters_limit)
        topics = await topics_dal.get_top_topics(limit=topics_limit)
        return clusters, topics
