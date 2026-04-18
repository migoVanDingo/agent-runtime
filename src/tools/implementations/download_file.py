import urllib.request
import urllib.error
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight


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
            urllib.request.urlretrieve(url, destination)
            return f"Downloaded {url} to {destination}"
        except urllib.error.URLError as e:
            return f"Network error: {e}"
        except Exception as e:
            return f"Error: {e}"
