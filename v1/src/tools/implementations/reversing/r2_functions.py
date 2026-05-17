import json
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from tools.implementations.reversing.r2_runner import r2_run


class R2FunctionsTool(BaseTool):
    name = "r2_functions"
    description = (
        "List all functions in a binary with their addresses, sizes, and names. "
        "Requires radare2. Returns a structured function inventory."
    )
    weight = ToolWeight.MODERATE

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "path": ToolProperty(type="string", description="Path to the binary"),
            },
            required=["path"],
        )

    def execute(self, tool_input: dict) -> str:
        path = tool_input["path"]
        raw = r2_run(path, "aflj", analyze=True)
        if raw.startswith("Error:"):
            return raw

        try:
            funcs = json.loads(raw)
        except json.JSONDecodeError:
            # aflj failed — fall back to plain afl
            return r2_run(path, "afl", analyze=True)

        if not funcs:
            return "(no functions found)"

        lines = [f"{'Address':<18} {'Size':>6}  Name"]
        lines.append("-" * 50)
        for f in sorted(funcs, key=lambda x: x.get("offset", 0)):
            addr = f"0x{f.get('offset', 0):016x}"
            size = f.get("size", 0)
            name = f.get("name", "?")
            lines.append(f"{addr}  {size:>6}  {name}")
        lines.append(f"\n{len(funcs)} function(s) total")
        return "\n".join(lines)
