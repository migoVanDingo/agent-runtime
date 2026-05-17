import subprocess
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from app_config import config


class HexdumpTool(BaseTool):
    name = "hexdump"
    description = "Display a hex dump of a file. Useful for inspecting binary files, file headers, and low-level data."
    weight = ToolWeight.HEAVY

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "path": ToolProperty(type="string", description="Path to the file"),
                "bytes": ToolProperty(type="string", description="Number of bytes to dump (default: 256). Use '0' for the entire file."),
            },
            required=["path"],
        )

    def execute(self, tool_input: dict) -> str:
        path = tool_input["path"]
        num_bytes = tool_input.get("bytes", config.tools.hexdump_default_bytes)
        try:
            cmd = ["xxd", path] if num_bytes == "0" else ["xxd", "-l", num_bytes, path]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=config.timeouts.default
            )
            output = result.stdout
            if result.stderr:
                output += f"\nSTDERR: {result.stderr}"
            return output if output else "(no output)"
        except FileNotFoundError:
            return "Error: 'xxd' command not found."
        except Exception as e:
            return f"Error: {e}"
