import subprocess
from tools.base import BaseTool, InputSchema, ToolProperty
from app_config import config


class StraceTool(BaseTool):
    name = "strace"
    description = "Trace system calls made by a program. Linux only — not available on macOS."

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "command": ToolProperty(type="string", description="The command/binary to trace, with any arguments"),
                "flags": ToolProperty(type="string", description="strace flags (e.g. '-e trace=open,read' to filter). Defaults to empty."),
            },
            required=["command"],
        )

    def execute(self, tool_input: dict) -> str:
        command = tool_input["command"]
        flags = tool_input.get("flags", "")
        try:
            result = subprocess.run(
                f"strace {flags} {command}",
                shell=True, capture_output=True, text=True, timeout=config.timeouts.default
            )
            output = result.stdout
            if result.stderr:
                output += f"\nSTDERR: {result.stderr}"
            return output if output else "(no output)"
        except FileNotFoundError:
            return "Error: 'strace' not found. strace is Linux only — not available on macOS."
        except Exception as e:
            return f"Error: {e}"
