import subprocess
from tools.base import BaseTool, InputSchema, ToolProperty
from app_config import config


class LtraceTool(BaseTool):
    name = "ltrace"
    description = "Trace library calls made by a program. Useful for dynamic analysis. Note: not available on macOS — use Linux."

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "command": ToolProperty(type="string", description="The command/binary to trace, with any arguments"),
                "flags": ToolProperty(type="string", description="ltrace flags (e.g. '-e malloc' to filter calls). Defaults to empty."),
            },
            required=["command"],
        )

    def execute(self, tool_input: dict) -> str:
        command = tool_input["command"]
        flags = tool_input.get("flags", "")
        try:
            result = subprocess.run(
                f"ltrace {flags} {command}",
                shell=True, capture_output=True, text=True, timeout=config.timeouts.default
            )
            output = result.stdout
            if result.stderr:
                output += f"\nSTDERR: {result.stderr}"
            return output if output else "(no output)"
        except FileNotFoundError:
            return "Error: 'ltrace' not found. ltrace is not available on macOS — use a Linux environment."
        except Exception as e:
            return f"Error: {e}"
