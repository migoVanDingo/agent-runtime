"""angr template: dump path constraints at a target address.

Environment:
  ANGR_BINARY  — path to binary
  ANGR_TARGET  — hex address or symbol name
  ANGR_OUTPUT  — path to write JSON result
"""
import angr
import json
import os

binary = os.environ["ANGR_BINARY"]
target = os.environ["ANGR_TARGET"]
output = os.environ["ANGR_OUTPUT"]

proj = angr.Project(binary, auto_load_libs=False)

def _resolve(addr_or_sym: str) -> int:
    s = addr_or_sym.strip()
    if s.startswith("0x") or s.startswith("0X"):
        return int(s, 16)
    sym = proj.loader.find_symbol(s)
    if sym:
        return sym.rebased_addr
    raise ValueError(f"Cannot resolve '{s}'")

find_addr = _resolve(target)
state = proj.factory.entry_state()
simgr = proj.factory.simulation_manager(state)
simgr.explore(find=find_addr)

constraints = []
if simgr.found:
    found_state = simgr.found[0]
    for c in found_state.solver.constraints:
        constraints.append(str(c))

result = {
    "ok": True,
    "result": {
        "target": target,
        "reachable": bool(simgr.found),
        "constraints": constraints,
        "constraint_count": len(constraints),
    },
    "error": None,
}
with open(output, "w") as f:
    json.dump(result, f)
