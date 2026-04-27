"""briefbot_search — search the local Briefbot research corpus.

Queries the nightly-indexed SQLite database of research papers, blog posts,
Hacker News items, and other tech content collected by Briefbot. Prefer this
over web_search for research and paper queries — the corpus is pre-scored,
deduplicated, and covers dozens of sources.
"""
from __future__ import annotations

from app_config import settings
from db.sync import run_async
from logger import get_logger
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight

logger = get_logger(__name__)

_CATEGORIES = "ai_research, papers, ai_industry, devtools, mlops_infra, security, tech_news, aggregator"
_ORDER_OPTIONS = "score (default), date"


class BriefbotSearchTool(BaseTool):
    name = "briefbot_search"
    description = (
        "Search the local Briefbot research corpus — nightly-indexed papers, "
        "blog posts, Hacker News items, and tech news from dozens of curated sources. "
        "Returns scored, deduplicated results with title, URL, source, and summary. "
        "Prefer this over web_search for research, paper, and tech topic queries."
    )
    weight = ToolWeight.LIGHTWEIGHT

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "query": ToolProperty(
                    type="string",
                    description="Search terms matched against title and summary",
                ),
                "days": ToolProperty(
                    type="number",
                    description="Recency window in days (default 30)",
                ),
                "category": ToolProperty(
                    type="string",
                    description=f"Filter by source category: {_CATEGORIES}",
                ),
                "limit": ToolProperty(
                    type="number",
                    description="Max results to return (default 15, max 50)",
                ),
                "order_by": ToolProperty(
                    type="string",
                    description=f"Sort order: {_ORDER_OPTIONS}",
                ),
            },
            required=["query"],
        )

    def execute(self, tool_input: dict) -> str:
        if not settings.briefbot_db_path:
            return (
                "Error: BRIEFBOT_DB_PATH is not configured. "
                "Add BRIEFBOT_DB_PATH=/path/to/briefbot.db to your .env file."
            )

        query = tool_input["query"]
        days = int(tool_input.get("days", 30))
        category = tool_input.get("category") or None
        limit = min(int(tool_input.get("limit", 15)), 50)
        order_by = tool_input.get("order_by", "score")
        if order_by not in ("score", "date"):
            order_by = "score"

        try:
            results = run_async(_do_search(query, days=days, category=category, limit=limit, order_by=order_by))
        except Exception as e:
            logger.error(f"briefbot_search error: {e}")
            return f"Error: briefbot_search failed: {type(e).__name__}: {e}"

        if not results:
            return f"No results found in Briefbot corpus for: {query}"

        lines = [f"Briefbot Search: {query!r}  ({len(results)} results, last {days}d)\n"]
        for i, item in enumerate(results, 1):
            score_str = f"  [score={item.score:.2f}]" if item.score else ""
            category_str = f"  ({item.source_category})" if item.source_category else ""
            source_str = f"  — {item.source_name}" if item.source_name else ""
            lines.append(f"[{i}] {item.title}{score_str}{category_str}{source_str}")
            url = item.canonical_url or item.url or ""
            if url:
                lines.append(f"    {url}")
            if item.summary:
                summary = item.summary[:200].replace("\n", " ").strip()
                lines.append(f"    {summary}")
            if item.opportunity_reason:
                lines.append(f"    Opportunity: {item.opportunity_reason[:120]}")
            lines.append("")

        return "\n".join(lines).rstrip()


async def _do_search(query: str, *, days: int, category, limit: int, order_by: str):
    from db.session import briefbot_session
    from db.dal.briefbot.items_dal import ItemsDAL
    async with briefbot_session() as session:
        dal = ItemsDAL(session)
        return await dal.search(query, days=days, category=category, limit=limit, order_by=order_by)
