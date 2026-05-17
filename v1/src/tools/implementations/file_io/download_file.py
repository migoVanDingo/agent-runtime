import httpx
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight

_DEFAULT_TIMEOUT = 30


class DownloadFileTool(BaseTool):
    name = "download_file"
    description = "Download a file from a URL to a local path."
    weight = ToolWeight.MODERATE

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "url": ToolProperty(type="string", description="The URL to download from"),
                "destination": ToolProperty(type="string", description="Local path to save the file to"),
            },
            required=["url", "destination"],
        )

    def execute(self, tool_input: dict) -> str:
        url = tool_input["url"]
        destination = tool_input["destination"]
        try:
            with httpx.stream(
                "GET", url,
                follow_redirects=True,
                timeout=_DEFAULT_TIMEOUT,
                headers={"User-Agent": "Mozilla/5.0 (compatible; agent-runtime/1.0)"},
            ) as response:
                response.raise_for_status()
                with open(destination, "wb") as f:
                    for chunk in response.iter_bytes():
                        f.write(chunk)
            return f"Downloaded {url} to {destination}"
        except httpx.HTTPStatusError as e:
            return f"Network error: HTTP {e.response.status_code} for {url}"
        except httpx.RequestError as e:
            return f"Network error: {type(e).__name__}: {e}"
        except OSError as e:
            return f"Error writing file: {e}"
