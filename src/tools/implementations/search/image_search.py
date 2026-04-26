"""image_search — image search via the Brave Image Search API."""
from app_config import settings
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from logger import get_logger

logger = get_logger(__name__)

_BASE_URL = "https://api.search.brave.com/res/v1/images/search"
_DEFAULT_COUNT = 5
_MAX_COUNT = 20


class ImageSearchTool(BaseTool):
    name = "image_search"
    description = (
        "Search for images via the Brave Image Search API. "
        "Returns image URLs, source pages, titles, and dimensions."
    )
    weight = ToolWeight.LIGHTWEIGHT

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "query": ToolProperty(
                    type="string",
                    description="Image search query",
                ),
                "count": ToolProperty(
                    type="number",
                    description=f"Number of results (1–{_MAX_COUNT}, default {_DEFAULT_COUNT})",
                ),
                "safe_search": ToolProperty(
                    type="string",
                    description="moderate (default), strict, or off",
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
        safe = tool_input.get("safe_search", "moderate")

        params: dict = {"q": query, "count": count}
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
                return "Error: Brave API rate limit hit"
            return f"Error: Brave API HTTP {e.response.status_code}: {e.response.text[:200]}"
        except httpx.RequestError as e:
            return f"Error: request failed: {type(e).__name__}: {e}"

        data = resp.json()
        results = data.get("results", [])
        if not results:
            return f"No image results found for: {query}"

        lines = [f"Images: {query}  ({len(results)} results)\n"]
        for i, r in enumerate(results, 1):
            title = r.get("title", "(no title)")
            src_url = r.get("url", "")
            img_url = r.get("thumbnail", {}).get("src", "") or r.get("image", {}).get("src", "")
            props = r.get("properties", {})
            width = props.get("width", "")
            height = props.get("height", "")
            dims = f"  ({width}×{height})" if width and height else ""
            lines.append(f"[{i}] {title}{dims}")
            if src_url:
                lines.append(f"    Source: {src_url}")
            if img_url and img_url != src_url:
                lines.append(f"    Image:  {img_url}")
            lines.append("")

        return "\n".join(lines).rstrip()
