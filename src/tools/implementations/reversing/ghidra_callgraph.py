from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from tools.implementations.reversing.ghidra_cache import ghidra_home, run_ghidra_function


def _callgraph(api):
    program = api.currentProgram
    fm = program.getFunctionManager()
    rm = program.getReferenceManager()

    lines = []
    for fn in fm.getFunctions(True):
        if fn.isExternal():
            continue
        callees = set()
        for addr in fn.getBody().getAddresses(True):
            for ref in rm.getReferencesFrom(addr):
                if ref.getReferenceType().isCall():
                    callee = fm.getFunctionAt(ref.getToAddress())
                    if callee:
                        callees.add(callee.getName())
        if callees:
            for callee in sorted(callees):
                lines.append(f"  {fn.getName()}  →  {callee}")
        else:
            lines.append(f"  {fn.getName()}  (leaf)")
    return "\n".join(lines) if lines else "(empty call graph)"


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
        return run_ghidra_function(tool_input["path"], _callgraph)
