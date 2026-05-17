"""http_request — structured HTTP client tool.

Supports any method with headers, query params, and request body.
All requests are ESCALATE in the guard — the agent must get user approval
before any outbound HTTP call.

Response bodies over 50k chars are truncated with a note.
JSON responses are pretty-printed for LLM readability.
"""
import json
import httpx
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight

_MAX_RESPONSE_CHARS = 50_000
_DEFAULT_TIMEOUT = 30


class HttpRequestTool(BaseTool):
    name = "http_request"
    description = (
        "Make an HTTP request (GET, POST, PUT, PATCH, DELETE, HEAD). "
        "Returns status code, response headers, and response body. "
        "Use for API calls and structured HTTP interactions."
    )
    weight = ToolWeight.MODERATE

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "method": ToolProperty(
                    type="string",
                    description="HTTP method: GET, POST, PUT, PATCH, DELETE, HEAD",
                ),
                "url": ToolProperty(
                    type="string",
                    description="Full URL including scheme (https://...)",
                ),
                "headers": ToolProperty(
                    type="object",
                    description="Optional request headers as key-value pairs",
                ),
                "params": ToolProperty(
                    type="object",
                    description="Optional URL query parameters as key-value pairs",
                ),
                "body": ToolProperty(
                    type="string",
                    description="Optional request body. For JSON APIs, pass a JSON string.",
                ),
                "timeout": ToolProperty(
                    type="number",
                    description=f"Request timeout in seconds (default {_DEFAULT_TIMEOUT})",
                ),
            },
            required=["method", "url"],
        )

    def execute(self, tool_input: dict) -> str:
        method = tool_input["method"].upper()
        url = tool_input["url"]
        headers = tool_input.get("headers") or {}
        params = tool_input.get("params") or {}
        body = tool_input.get("body")
        timeout = tool_input.get("timeout", _DEFAULT_TIMEOUT)

        # Auto-set Content-Type if body looks like JSON and header not set
        if body and "content-type" not in {k.lower() for k in headers}:
            try:
                json.loads(body)
                headers["Content-Type"] = "application/json"
            except (json.JSONDecodeError, TypeError):
                pass

        try:
            response = httpx.request(
                method=method,
                url=url,
                headers=headers,
                params=params or None,
                content=body.encode() if body else None,
                timeout=timeout,
                follow_redirects=True,
            )
        except httpx.TimeoutException:
            return f"Error: request timed out after {timeout}s"
        except httpx.RequestError as e:
            return f"Error: {type(e).__name__}: {e}"

        # Build response string
        lines = [f"Status: {response.status_code} {response.reason_phrase}"]

        # Include a few key response headers
        interesting = {"content-type", "content-length", "location", "x-request-id", "x-ratelimit-remaining"}
        for k, v in response.headers.items():
            if k.lower() in interesting:
                lines.append(f"{k}: {v}")

        lines.append("")

        # Format body
        body_text = response.text
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                body_text = json.dumps(json.loads(body_text), indent=2)
            except json.JSONDecodeError:
                pass

        if len(body_text) > _MAX_RESPONSE_CHARS:
            body_text = (
                body_text[:_MAX_RESPONSE_CHARS]
                + f"\n[truncated — response was {len(response.text)} chars, "
                f"showing first {_MAX_RESPONSE_CHARS}]"
            )

        lines.append(body_text)
        return "\n".join(lines)
