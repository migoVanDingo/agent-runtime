from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from tools.implementations.reversing.ghidra_cache import ghidra_home, run_ghidra_function


def _decompile(api, target: str | None):
    from ghidra.app.decompiler import DecompInterface, DecompileOptions

    program = api.currentProgram
    fm = program.getFunctionManager()
    decomp = DecompInterface()
    options = DecompileOptions()
    decomp.setOptions(options)
    decomp.openProgram(program)

    try:
        if target:
            fns = [f for f in fm.getFunctions(True) if f.getName() == target]
            if not fns:
                try:
                    addr = program.getAddressFactory().getAddress(target)
                    fn = fm.getFunctionAt(addr)
                    if fn:
                        fns = [fn]
                except Exception:
                    pass
            if not fns:
                fns = [f for f in fm.getFunctions(True) if target in f.getName()]
        else:
            fns = [f for f in fm.getFunctions(True) if not f.isExternal() and not f.isThunk()]

        sections = []
        for fn in fns:
            result = decomp.decompileFunction(fn, 60, api.monitor)
            if result.decompileCompleted():
                code = result.getDecompiledFunction().getC()
            else:
                code = f"/* decompilation failed: {result.getErrorMessage()} */"
            header = f"// {fn.getName()} @ {fn.getEntryPoint()}"
            sections.append(f"{header}\n{code}")

        return "\n\n".join(sections) if sections else f"(no functions found matching '{target}')"
    finally:
        decomp.dispose()


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
        fn = tool_input.get("function") or None
        return run_ghidra_function(tool_input["path"], _decompile, fn)
