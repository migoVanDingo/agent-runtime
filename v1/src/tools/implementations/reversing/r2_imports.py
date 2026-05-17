import json
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from tools.implementations.reversing.r2_runner import r2_run


class R2ImportsTool(BaseTool):
    name = "r2_imports"
    description = (
        "List all imported symbols (library functions) in a binary. "
        "Reveals what external libraries and functions the binary depends on. "
        "Does not require full analysis — fast on any binary."
    )
    weight = ToolWeight.LIGHTWEIGHT

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
        # iij = imports as JSON; no full analysis needed
        raw = r2_run(path, "iij", analyze=False)
        if raw.startswith("Error:"):
            return raw

        try:
            imports = json.loads(raw)
        except json.JSONDecodeError:
            return r2_run(path, "ii", analyze=False)

        if not imports:
            return "(no imports found — may be statically linked)"

        lines = [f"{'#':>4}  {'Type':<8}  {'Library':<20}  Name"]
        lines.append("-" * 60)
        for imp in imports:
            ordinal = imp.get("ordinal", 0)
            itype = imp.get("type", "?")
            lib = imp.get("libname", "") or "-"
            name = imp.get("name", "?")
            lines.append(f"{ordinal:>4}  {itype:<8}  {lib:<20}  {name}")
        lines.append(f"\n{len(imports)} import(s)")
        return "\n".join(lines)
