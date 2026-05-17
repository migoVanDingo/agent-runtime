# Ghidra post-analysis script: export function list as JSON
# Args: <output_path>
import json

args = getScriptArgs()
output_path = args[0]

funcs = []
fm = currentProgram.getFunctionManager()
for fn in fm.getFunctions(True):
    funcs.append({
        "name": fn.getName(),
        "address": str(fn.getEntryPoint()),
        "size": int(fn.getBody().getNumAddresses()),
        "is_thunk": bool(fn.isThunk()),
        "is_external": bool(fn.isExternal()),
        "calling_convention": str(fn.getCallingConventionName()) if fn.getCallingConventionName() else None,
    })

with open(output_path, "w") as f:
    json.dump(funcs, f)

print("ExportFunctions: wrote {} functions to {}".format(len(funcs), output_path))
