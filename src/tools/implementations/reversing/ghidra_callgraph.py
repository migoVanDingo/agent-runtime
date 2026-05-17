from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from tools.implementations.reversing.ghidra_cache import ghidra_home, run_ghidra_op


class GhidraCallgraphTool(BaseTool):
    name = "ghidra_callgraph"
    description = (
        "Generate a complete call graph for a binary using Ghidra/PyGhidra. "
        "Returns an adjacency list of which functions call which. "
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
        return run_ghidra_op(tool_input["path"], "callgraph")
