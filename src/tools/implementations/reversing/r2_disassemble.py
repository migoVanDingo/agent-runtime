from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from tools.implementations.reversing.r2_runner import r2_run


class R2DisassembleTool(BaseTool):
    name = "r2_disassemble"
    description = (
        "Disassemble a specific function in a binary using radare2. "
        "Accepts a function name (e.g. 'main', 'sym.encrypt') or hex address (e.g. '0x1000'). "
        "Produces annotated disassembly with cross-references."
    )
    weight = ToolWeight.MODERATE

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "path": ToolProperty(type="string", description="Path to the binary"),
                "function": ToolProperty(
                    type="string",
                    description="Function name or hex address to disassemble. Use 'main' for the entry point.",
                ),
            },
            required=["path", "function"],
        )

    def execute(self, tool_input: dict) -> str:
        path = tool_input["path"]
        fn = tool_input["function"]
        # pdf = print disassembly of function
        return r2_run(path, f"pdf @ {fn}", analyze=True)
