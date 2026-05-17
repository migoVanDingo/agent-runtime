"""extract_html — CSS selector extraction from HTML source or URL.

Useful for scraping structured data: tables, links, specific elements.
Distinct from read_url which returns readable prose — this returns structured
element data matched by a CSS selector.
"""
import httpx
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight

_DEFAULT_TIMEOUT = 30
_MAX_RESULTS = 200


class ExtractHtmlTool(BaseTool):
    name = "extract_html"
    description = (
        "Extract elements from HTML using a CSS selector. "
        "Provide either a URL to fetch or raw HTML as 'source'. "
        "Returns matched element text (or a specific attribute if 'attribute' is set)."
    )
    weight = ToolWeight.MODERATE

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "source": ToolProperty(
                    type="string",
                    description="URL to fetch (https://...) or raw HTML string",
                ),
                "selector": ToolProperty(
                    type="string",
                    description="CSS selector, e.g. 'table', 'a[href]', 'h1', '.classname', '#id'",
                ),
                "attribute": ToolProperty(
                    type="string",
                    description="Optional — extract this attribute instead of text content, e.g. 'href', 'src'",
                ),
                "timeout": ToolProperty(
                    type="number",
                    description=f"Fetch timeout in seconds (default {_DEFAULT_TIMEOUT}), only used when source is a URL",
                ),
            },
            required=["source", "selector"],
        )

    def execute(self, tool_input: dict) -> str:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            return "Error: beautifulsoup4 is not installed. Run: pip install beautifulsoup4"

        source = tool_input["source"]
        selector = tool_input["selector"]
        attribute = tool_input.get("attribute")
        timeout = tool_input.get("timeout", _DEFAULT_TIMEOUT)

        # Fetch if URL, otherwise treat as raw HTML
        if source.startswith(("http://", "https://")):
            try:
                response = httpx.get(
                    source,
                    timeout=timeout,
                    follow_redirects=True,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; agent-runtime/1.0)"},
                )
                response.raise_for_status()
                html = response.text
            except httpx.TimeoutException:
                return f"Error: request timed out after {timeout}s"
            except httpx.HTTPStatusError as e:
                return f"Error: HTTP {e.response.status_code}"
            except httpx.RequestError as e:
                return f"Error: {type(e).__name__}: {e}"
        else:
            html = source

        soup = BeautifulSoup(html, "html.parser")
        elements = soup.select(selector)

        if not elements:
            return f"No elements matched selector '{selector}'"

        results = []
        for el in elements[:_MAX_RESULTS]:
            if attribute:
                val = el.get(attribute, "")
                if val:
                    results.append(str(val).strip())
            else:
                text = el.get_text(separator=" ", strip=True)
                if text:
                    results.append(text)

        if not results:
            return f"Matched {len(elements)} element(s) but none had {'attribute ' + attribute if attribute else 'text content'}"

        truncated = len(elements) > _MAX_RESULTS
        header = f"Matched {len(elements)} element(s)" + (f" (showing first {_MAX_RESULTS})" if truncated else "") + f" for selector '{selector}':\n"
        return header + "\n---\n".join(results)
