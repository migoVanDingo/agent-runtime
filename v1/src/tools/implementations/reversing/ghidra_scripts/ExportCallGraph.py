# Ghidra post-analysis script: export call graph as adjacency list JSON
# Args: <output_path>
import json

args = getScriptArgs()
output_path = args[0]

fm = currentProgram.getFunctionManager()
rm = currentProgram.getReferenceManager()

graph = []
for fn in fm.getFunctions(True):
    if fn.isExternal():
        continue
    callees = set()
    for addr in fn.getBody().getAddresses(True):
        for ref in rm.getReferencesFrom(addr):
            if ref.getReferenceType().isCall():
                callee_fn = fm.getFunctionAt(ref.getToAddress())
                if callee_fn:
                    callees.add(callee_fn.getName())
    graph.append({
        "name": fn.getName(),
        "address": str(fn.getEntryPoint()),
        "calls": sorted(callees),
        "is_thunk": bool(fn.isThunk()),
    })

with open(output_path, "w") as f:
    json.dump(graph, f)

print("ExportCallGraph: wrote {} nodes to {}".format(len(graph), output_path))
