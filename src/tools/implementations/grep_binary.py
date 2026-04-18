import subprocess
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from app_config import config


class GrepBinaryTool(BaseTool):
    name = "grep_binary"
    description = "Search for a pattern in a binary file, treating it as text. Useful for finding strings, signatures, or patterns in compiled binaries."
    weight = ToolWeight.MODERATE

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "pattern": ToolProperty(type="string", description="The pattern to search for"),
                "path": ToolProperty(type="string", description="Path to the binary file"),
                "context": ToolProperty(type="string", description="Number of lines of context to show around matches (default: 0)"),
            },
            required=["pattern", "path"],
        )

    def execute(self, tool_input: dict) -> str:
        pattern = tool_input["pattern"]
        path = tool_input["path"]
        context = tool_input.get("context", "0")
        try:
            result = subprocess.run(
                ["grep", "-a", f"-C{context}", pattern, path],
                capture_output=True, text=True, timeout=config.timeouts.default
            )
            output = result.stdout
            if result.stderr:
                output += f"\nSTDERR: {result.stderr}"
            return output if output else "(no matches found)"
        except Exception as e:
            return f"Error: {e}"
