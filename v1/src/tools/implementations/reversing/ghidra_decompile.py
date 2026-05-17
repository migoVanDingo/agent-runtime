from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from tools.implementations.reversing.ghidra_cache import ghidra_home, run_ghidra_op


class GhidraDecompileTool(BaseTool):
    name = "ghidra_decompile"
    description = (
        "Decompile a binary function (or all functions) to C pseudocode using Ghidra/PyGhidra. "
        "Produces significantly higher quality output than raw disassembly. "
        "Omit 'function' to decompile all non-thunk functions. "
        "Requires GHIDRA_HOME in .env."
    )
    weight = ToolWeight.HEAVY

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "path": ToolProperty(type="string", description="Path to the binary"),
                "function": ToolProperty(
                    type="string",
                    description="Function name or address to decompile. Omit for all functions.",
                ),
            },
            required=["path"],
        )

    def execute(self, tool_input: dict) -> str:
        if not ghidra_home():
            return "Error: GHIDRA_HOME not set. Add GHIDRA_HOME=/path/to/ghidra to .env"
        return run_ghidra_op(
            tool_input["path"],
            "decompile",
            {"function": tool_input.get("function") or None},
        )
