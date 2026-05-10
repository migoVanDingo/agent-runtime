from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from tools.implementations.reversing.ghidra_cache import ghidra_home, run_ghidra_function


def _list_functions(api):
    fm = api.currentProgram.getFunctionManager()
    user_fns, ext_fns = [], []
    for fn in fm.getFunctions(True):
        entry = {
            "name": fn.getName(),
            "address": str(fn.getEntryPoint()),
            "size": int(fn.getBody().getNumAddresses()),
            "is_thunk": bool(fn.isThunk()),
            "is_external": bool(fn.isExternal()),
        }
        (ext_fns if fn.isExternal() else user_fns).append(entry)

    lines = [f"{'Address':<20} {'Size':>6}  {'Thunk':<6}  Name", "-" * 55]
    for f in sorted(user_fns, key=lambda x: x["address"]):
        thunk = "yes" if f["is_thunk"] else ""
        lines.append(f"{f['address']:<20} {f['size']:>6}  {thunk:<6}  {f['name']}")
    if ext_fns:
        lines.append(f"\nExternal ({len(ext_fns)}):")
        for f in sorted(ext_fns, key=lambda x: x["name"]):
            lines.append(f"  {f['name']}")
    lines.append(f"\n{len(user_fns) + len(ext_fns)} total ({len(user_fns)} user-defined, {len(ext_fns)} external)")
    return "\n".join(lines)


class GhidraFunctionsTool(BaseTool):
    name = "ghidra_functions"
    description = (
        "List all functions in a binary via Ghidra/PyGhidra. "
        "Returns address, size, name, and thunk/external flags. "
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
        return run_ghidra_function(tool_input["path"], _list_functions)
