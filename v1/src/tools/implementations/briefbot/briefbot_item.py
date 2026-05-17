"""briefbot_item — fetch full details on a specific Briefbot item.

Use this after briefbot_search or briefbot_trending to get the full
record for a specific item, including opportunity analysis and tags.
"""
from __future__ import annotations

from app_config import settings
from db.sync import run_async
from logger import get_logger
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight

logger = get_logger(__name__)


class BriefbotItemTool(BaseTool):
    name = "briefbot_item"
    description = (
        "Fetch full details for a specific item from the Briefbot corpus by item_id. "
        "Returns title, URL, source, scores, tags, opportunity analysis, and summary. "
        "Use this after briefbot_search to get full metadata on a specific result."
    )
    weight = ToolWeight.LIGHTWEIGHT

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "item_id": ToolProperty(
                    type="string",
                    description="The Briefbot item_id from a briefbot_search result",
                ),
            },
            required=["item_id"],
        )

    def execute(self, tool_input: dict) -> str:
        if not settings.briefbot_db_path:
            return (
                "Error: BRIEFBOT_DB_PATH is not configured. "
                "Add BRIEFBOT_DB_PATH=/path/to/briefbot.db to your .env file."
            )

        item_id = tool_input["item_id"].strip()

        try:
            item = run_async(_do_get_item(item_id))
        except Exception as e:
            logger.error(f"briefbot_item error: {e}")
            return f"Error: briefbot_item failed: {type(e).__name__}: {e}"

        if item is None:
            return f"No item found with item_id: {item_id}"

        lines = [f"Briefbot Item: {item.item_id}\n"]
        lines.append(f"Title:    {item.title}")
        url = item.canonical_url or item.url or ""
        if url:
            lines.append(f"URL:      {url}")
        lines.append(f"Source:   {item.source_name}  ({item.source_category or 'unknown'})")
        lines.append(f"Score:    {item.score:.3f}")
        if item.score_opportunity is not None:
            lines.append(f"Opp score:{item.score_opportunity:.3f}")
        if item.published_at:
            lines.append(f"Published:{item.published_at}")
        lines.append(f"Fetched:  {item.fetched_at}")
        if item.author:
            lines.append(f"Author:   {item.author}")

        if item.tags_json and item.tags_json != "[]":
            import json
            try:
                tags = json.loads(item.tags_json)
                if tags:
                    lines.append(f"Tags:     {', '.join(str(t) for t in tags)}")
            except Exception:
                pass

        if item.summary:
            lines.append(f"\nSummary:\n{item.summary}")

        if item.opportunity_reason:
            lines.append(f"\nOpportunity:\n{item.opportunity_reason}")

        return "\n".join(lines)


async def _do_get_item(item_id: str):
    from db.session import briefbot_session
    from db.dal.briefbot.items_dal import ItemsDAL
    async with briefbot_session() as session:
        dal = ItemsDAL(session)
        return await dal.get_by_id(item_id)
