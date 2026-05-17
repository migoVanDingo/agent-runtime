# Ghidra post-analysis script: decompile one or all user-defined functions
# Args: <output_path> [function_name_or_addr]
import json
from ghidra.app.decompiler import DecompInterface, DecompileOptions

args = getScriptArgs()
output_path = args[0]
target = args[1] if len(args) > 1 else None

decomp = DecompInterface()
options = DecompileOptions()
decomp.setOptions(options)
decomp.openProgram(currentProgram)

fm = currentProgram.getFunctionManager()
results = []

if target:
    # Try by name first, then by address
    fns = [f for f in fm.getFunctions(True) if f.getName() == target]
    if not fns:
        try:
            addr = currentProgram.getAddressFactory().getAddress(target)
            fn = fm.getFunctionAt(addr)
            if fn:
                fns = [fn]
        except Exception:
            pass
    if not fns:
        fns = [f for f in fm.getFunctions(True) if target in f.getName()]
else:
    # All non-external, non-thunk functions
    fns = [f for f in fm.getFunctions(True) if not f.isExternal() and not f.isThunk()]

for fn in fns:
    try:
        result = decomp.decompileFunction(fn, 60, monitor)
        if result.decompileCompleted():
            code = result.getDecompiledFunction().getC()
        else:
            code = "/* decompilation failed: {} */".format(result.getErrorMessage())
    except Exception as e:
        code = "/* error: {} */".format(str(e))

    results.append({
        "name": fn.getName(),
        "address": str(fn.getEntryPoint()),
        "is_thunk": bool(fn.isThunk()),
        "code": code,
    })

decomp.dispose()

with open(output_path, "w") as f:
    json.dump(results, f)

print("DecompileFunction: wrote {} function(s) to {}".format(len(results), output_path))
