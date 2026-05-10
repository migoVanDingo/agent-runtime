"""angr template: can execution reach target_addr from program entry?

Environment:
  ANGR_BINARY  — path to binary
  ANGR_TARGET  — hex address or symbol name to find
  ANGR_AVOID   — comma-separated hex addresses to avoid (optional)
  ANGR_OUTPUT  — path to write JSON result
"""
import angr
import json
import os

binary  = os.environ["ANGR_BINARY"]
target  = os.environ["ANGR_TARGET"]
avoid_s = os.environ.get("ANGR_AVOID", "")
output  = os.environ["ANGR_OUTPUT"]

proj = angr.Project(binary, auto_load_libs=False)
state = proj.factory.entry_state()
simgr = proj.factory.simulation_manager(state)

def _resolve(addr_or_sym: str) -> int:
    s = addr_or_sym.strip()
    if s.startswith("0x") or s.startswith("0X"):
        return int(s, 16)
    sym = proj.loader.find_symbol(s)
    if sym:
        return sym.rebased_addr
    raise ValueError(f"Cannot resolve '{s}' — provide a hex address or an exported symbol name")

find_addr = _resolve(target)
avoid_addrs = [_resolve(a) for a in avoid_s.split(",") if a.strip()]

simgr.explore(find=find_addr, avoid=avoid_addrs or None)

reachable = bool(simgr.found)
path_count = len(simgr.found)

result = {
    "ok": True,
    "result": {
        "reachable": reachable,
        "path_count": path_count,
        "target": target,
        "avoided": avoid_s or None,
    },
    "error": None,
}
with open(output, "w") as f:
    json.dump(result, f)
