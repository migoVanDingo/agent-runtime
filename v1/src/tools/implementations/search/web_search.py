"""web_search — full web search via the Brave Search API."""
from app_config import settings
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from logger import get_logger

logger = get_logger(__name__)

_BASE_URL = "https://api.search.brave.com/res/v1/web/search"
_DEFAULT_COUNT = 10
_MAX_COUNT = 20


class WebSearchTool(BaseTool):
    name = "web_search"
    description = (
        "Search the web using the Brave Search API. "
        "Returns a list of results with titles, URLs, and descriptions. "
        "Use read_url to fetch and read the full content of any result."
    )
    weight = ToolWeight.LIGHTWEIGHT

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "query": ToolProperty(
                    type="string",
                    description="Search query",
                ),
                "count": ToolProperty(
                    type="number",
                    description=f"Number of results (1–{_MAX_COUNT}, default {_DEFAULT_COUNT})",
                ),
                "country": ToolProperty(
                    type="string",
                    description="2-letter country code for localized results (e.g. 'US', 'GB')",
                ),
                "freshness": ToolProperty(
                    type="string",
                    description="Recency filter: pd (past day), pw (past week), pm (past month), py (past year)",
                ),
                "safe_search": ToolProperty(
                    type="string",
                    description="safe_search level: moderate (default), strict, or off",
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
        params: dict = {"q": query, "count": count}

        if tool_input.get("country"):
            params["country"] = tool_input["country"]
        if tool_input.get("freshness"):
            params["freshness"] = tool_input["freshness"]
        safe = tool_input.get("safe_search", "moderate")
        if safe in ("moderate", "strict", "off"):
            params["safesearch"] = safe

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
                return "Error: Brave API rate limit hit — wait before retrying"
            return f"Error: Brave API HTTP {e.response.status_code}: {e.response.text[:200]}"
        except httpx.RequestError as e:
            return f"Error: request failed: {type(e).__name__}: {e}"

        data = resp.json()
        results = data.get("web", {}).get("results", [])
        if not results:
            return f"No results found for: {query}"

        lines = [f"Search: {query}  ({len(results)} results)\n"]
        for i, r in enumerate(results, 1):
            title = r.get("title", "(no title)")
            url = r.get("url", "")
            desc = r.get("description", "").replace("\n", " ").strip()
            age = r.get("age", "")
            age_str = f"  [{age}]" if age else ""
            lines.append(f"[{i}] {title}{age_str}")
            lines.append(f"    {url}")
            if desc:
                lines.append(f"    {desc}")
            lines.append("")

        return "\n".join(lines).rstrip()
