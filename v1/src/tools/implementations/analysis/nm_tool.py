import subprocess
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from app_config import config


class NmTool(BaseTool):
    name = "nm"
    description = "List symbols from an object file or binary. Useful for reverse engineering and understanding binary structure."
    weight = ToolWeight.MODERATE

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "path": ToolProperty(type="string", description="Path to the object file or binary"),
                "flags": ToolProperty(type="string", description="nm flags (e.g. '-u' for undefined symbols, '-D' for dynamic symbols). Defaults to empty."),
            },
            required=["path"],
        )

    def execute(self, tool_input: dict) -> str:
        path = tool_input["path"]
        flags = tool_input.get("flags", "")
        try:
            result = subprocess.run(
                f"nm {flags} {path}",
                shell=True, capture_output=True, text=True, timeout=config.timeouts.default
            )
            output = result.stdout
            if result.stderr:
                output += f"\nSTDERR: {result.stderr}"
            return output if output else "(no symbols found)"
        except Exception as e:
            return f"Error: {e}"
