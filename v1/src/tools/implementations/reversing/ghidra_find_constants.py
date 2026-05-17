from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from tools.implementations.reversing.ghidra_cache import ghidra_home, run_ghidra_op


class GhidraFindConstantsTool(BaseTool):
    name = "ghidra_find_constants"
    description = (
        "Find defined data, strings, and magic constants in a binary using Ghidra/PyGhidra. "
        "Annotates known crypto constants (TEA, SHA-256, MD5, etc.) automatically. "
        "Requires GHIDRA_HOME in .env."
    )
    weight = ToolWeight.HEAVY

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "path": ToolProperty(type="string", description="Path to the binary"),
            },
            required=["path"],
        )

    def execute(self, tool_input: dict) -> str:
        if not ghidra_home():
            return "Error: GHIDRA_HOME not set. Add GHIDRA_HOME=/path/to/ghidra to .env"
        return run_ghidra_op(tool_input["path"], "find_constants")
