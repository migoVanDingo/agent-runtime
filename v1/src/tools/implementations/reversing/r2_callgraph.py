import json
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from tools.implementations.reversing.r2_runner import r2_run


class R2CallgraphTool(BaseTool):
    name = "r2_callgraph"
    description = (
        "Generate a function call graph for a binary using radare2. "
        "Returns an adjacency list showing which functions call which. "
        "Use to understand program structure and call relationships."
    )
    weight = ToolWeight.HEAVY

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "path": ToolProperty(type="string", description="Path to the binary"),
                "function": ToolProperty(
                    type="string",
                    description="(Optional) Root function name to show call graph from. Omit for full program graph.",
                ),
            },
            required=["path"],
        )

    def execute(self, tool_input: dict) -> str:
        path = tool_input["path"]
        fn = tool_input.get("function")

        if fn:
            # agcj = call graph JSON for a specific function
            raw = r2_run(path, f"agcj @ {fn}", analyze=True)
        else:
            # agCj = full program call graph JSON
            raw = r2_run(path, "agCj", analyze=True)

        if raw.startswith("Error:"):
            return raw

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Fall back to dot format if JSON parse fails
            cmd = f"agc @ {fn}" if fn else "agC"
            return r2_run(path, cmd, analyze=True)

        if not data:
            return "(empty call graph)"

        lines = []
        for node in data:
            name = node.get("name", "?")
            imports = node.get("imports", [])
            if imports:
                for callee in imports:
                    lines.append(f"  {name}  →  {callee}")
            else:
                lines.append(f"  {name}  (no outgoing calls)")

        return "\n".join(lines) if lines else "(no call relationships found)"
