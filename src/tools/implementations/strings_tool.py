import subprocess
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from app_config import config


class StringsTool(BaseTool):
    name = "strings"
    description = "Extract printable strings from a binary file. Useful for reverse engineering and malware analysis."
    weight = ToolWeight.LIGHTWEIGHT

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "path": ToolProperty(type="string", description="Path to the binary file"),
                "min_length": ToolProperty(type="string", description="Minimum string length to display (default: 4)"),
            },
            required=["path"],
        )

    def execute(self, tool_input: dict) -> str:
        path = tool_input["path"]
        min_length = tool_input.get("min_length", config.tools.strings_min_length)
        try:
            result = subprocess.run(
                ["strings", f"-{min_length}", path],
                capture_output=True, text=True, timeout=config.timeouts.default
            )
            output = result.stdout
            if result.stderr:
                output += f"\nSTDERR: {result.stderr}"
            return output if output else "(no strings found)"
        except FileNotFoundError:
            return "Error: 'strings' command not found. Install binutils."
        except Exception as e:
            return f"Error: {e}"
