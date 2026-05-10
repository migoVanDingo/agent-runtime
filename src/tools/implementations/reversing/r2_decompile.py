from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from tools.implementations.reversing.r2_runner import r2_run


class R2DecompileTool(BaseTool):
    name = "r2_decompile"
    description = (
        "Decompile a function using the r2ghidra plugin (if installed) or fall back to "
        "annotated disassembly. Accepts a function name or hex address. "
        "Install r2ghidra with: r2pm -ci r2ghidra"
    )
    weight = ToolWeight.HEAVY

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "path": ToolProperty(type="string", description="Path to the binary"),
                "function": ToolProperty(
                    type="string",
                    description="Function name or hex address. Use 'main' for entry point.",
                ),
            },
            required=["path", "function"],
        )

    def execute(self, tool_input: dict) -> str:
        path = tool_input["path"]
        fn = tool_input["function"]

        # Try r2ghidra decompiler (pdg = print decompiled ghidra)
        result = r2_run(path, f"pdg @ {fn}", analyze=True)

        if "Cannot find RGhidraDecompiler" in result or "r2ghidra" in result.lower():
            # r2ghidra not installed — fall back to annotated disassembly
            fallback = r2_run(path, f"pdf @ {fn}", analyze=True)
            return f"[r2ghidra not installed — showing disassembly instead]\n\n{fallback}"

        return result
