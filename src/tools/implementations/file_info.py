import subprocess
from tools.base import BaseTool, InputSchema, ToolProperty
from app_config import config


class FileInfoTool(BaseTool):
    name = "file_info"
    description = "Determine the type of a file using the 'file' command. Works on binaries, scripts, archives, and more."

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "path": ToolProperty(type="string", description="Path to the file to inspect"),
            },
            required=["path"],
        )

    def execute(self, tool_input: dict) -> str:
        path = tool_input["path"]
        try:
            result = subprocess.run(
                ["file", path],
                capture_output=True, text=True, timeout=config.timeouts.fast
            )
            return result.stdout.strip() if result.stdout else result.stderr.strip()
        except FileNotFoundError:
            return "Error: 'file' command not found."
        except Exception as e:
            return f"Error: {e}"
