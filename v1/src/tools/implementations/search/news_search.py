"""news_search — recent news articles via the Brave News Search API."""
from app_config import settings
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from logger import get_logger

logger = get_logger(__name__)

_BASE_URL = "https://api.search.brave.com/res/v1/news/search"
_DEFAULT_COUNT = 10
_MAX_COUNT = 20


class NewsSearchTool(BaseTool):
    name = "news_search"
    description = (
        "Search for recent news articles via the Brave News API. "
        "Returns articles with title, source, publication date, and summary. "
        "Defaults to results from the past week."
    )
    weight = ToolWeight.LIGHTWEIGHT

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "query": ToolProperty(
                    type="string",
                    description="News search query",
                ),
                "count": ToolProperty(
                    type="number",
                    description=f"Number of results (1–{_MAX_COUNT}, default {_DEFAULT_COUNT})",
                ),
                "freshness": ToolProperty(
                    type="string",
                    description=(
                        "Recency filter: pd (past day), pw (past week, default), "
                        "pm (past month), py (past year)"
                    ),
                ),
                "country": ToolProperty(
                    type="string",
                    description="2-letter country code for regional news (e.g. 'US')",
                ),
            },
            required=["query"],
        )

    def execute(self, tool_input: dict) -> str:
        api_key = (settings.brave_api_key or "").strip()
        if not api_key:
            return (
                "Error: BRAVE_API_KEY is not set. "
                "Add BRAVE_API_KEY=<your_key> to your .env file."
            )

        import httpx

        query = tool_input["query"]
        count = min(int(tool_input.get("count", _DEFAULT_COUNT)), _MAX_COUNT)
        freshness = tool_input.get("freshness", "pw")

        params: dict = {"q": query, "count": count, "freshness": freshness}
        if tool_input.get("country"):
            params["country"] = tool_input["country"]

        headers = {
            "X-Subscription-Token": api_key,
            "Accept": "application/json",
        }

        try:
            resp = httpx.get(_BASE_URL, params=params, headers=headers, timeout=15)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                return "Error: Brave API returned 401 Unauthorized — check BRAVE_API_KEY"
            if e.response.status_code == 429:
                return "Error: Brave API rate limit hit"
            return f"Error: Brave API HTTP {e.response.status_code}: {e.response.text[:200]}"
        except httpx.RequestError as e:
            return f"Error: request failed: {type(e).__name__}: {e}"

        data = resp.json()
        results = data.get("results", [])
        if not results:
            return f"No news results found for: {query}"

        freshness_labels = {
            "pd": "past day", "pw": "past week", "pm": "past month", "py": "past year",
        }
        freshness_label = freshness_labels.get(freshness, freshness)
        lines = [f"News: {query}  ({len(results)} results, {freshness_label})\n"]

        for i, r in enumerate(results, 1):
            title = r.get("title", "(no title)")
            url = r.get("url", "")
            age = r.get("age", "")
            desc = r.get("description", "").replace("\n", " ").strip()
            source = r.get("meta_url", {}).get("hostname", "") or r.get("source", {}).get("name", "")
            source_str = f"  ({source})" if source else ""
            age_str = f"  [{age}]" if age else ""
            lines.append(f"[{i}] {title}{source_str}{age_str}")
            lines.append(f"    {url}")
            if desc:
                lines.append(f"    {desc}")
            lines.append("")

        return "\n".join(lines).rstrip()
