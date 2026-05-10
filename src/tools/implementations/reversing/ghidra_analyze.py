from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from tools.implementations.reversing.ghidra_cache import ghidra_home, run_ghidra_function


def _probe(api, path):
    return f"Ghidra ready for '{path}' — {api.currentProgram.getName()}"


class GhidraAnalyzeTool(BaseTool):
    name = "ghidra_analyze"
    description = (
        "Initialize Ghidra analysis on a binary using PyGhidra. "
        "Call this first to verify Ghidra is working before using other ghidra_* tools. "
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
        return run_ghidra_function(tool_input["path"], _probe, tool_input["path"])
