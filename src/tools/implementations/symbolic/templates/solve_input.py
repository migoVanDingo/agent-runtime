"""angr template: find stdin/argv input that reaches find_addr and avoids avoid_addrs.

Environment:
  ANGR_BINARY      — path to binary
  ANGR_FIND        — hex address or symbol name of the success state
  ANGR_AVOID       — comma-separated hex addresses to avoid (optional)
  ANGR_INPUT_TYPE  — 'stdin' | 'argv' (default: stdin)
  ANGR_INPUT_LEN   — max symbolic input length in bytes (default: 64)
  ANGR_OUTPUT      — path to write JSON result
"""
import angr
import claripy
import json
import os

binary     = os.environ["ANGR_BINARY"]
find_s     = os.environ["ANGR_FIND"]
avoid_s    = os.environ.get("ANGR_AVOID", "")
input_type = os.environ.get("ANGR_INPUT_TYPE", "stdin")
input_len  = int(os.environ.get("ANGR_INPUT_LEN", "64"))
output     = os.environ["ANGR_OUTPUT"]

proj = angr.Project(binary, auto_load_libs=False)

def _resolve(addr_or_sym: str) -> int:
    s = addr_or_sym.strip()
    if s.startswith("0x") or s.startswith("0X"):
        return int(s, 16)
    sym = proj.loader.find_symbol(s)
    if sym:
        return sym.rebased_addr
    raise ValueError(f"Cannot resolve '{s}'")

find_addr   = _resolve(find_s)
avoid_addrs = [_resolve(a) for a in avoid_s.split(",") if a.strip()]

sym_input = claripy.BVS("input", input_len * 8)

if input_type == "argv":
    state = proj.factory.full_init_state(
        args=[binary, sym_input],
        add_options=angr.options.unicorn,
    )
else:
    state = proj.factory.full_init_state(
        stdin=angr.SimFile(name="stdin", content=sym_input, size=input_len),
        add_options=angr.options.unicorn,
    )

simgr = proj.factory.simulation_manager(state)
simgr.explore(find=find_addr, avoid=avoid_addrs or None)

solved = None
if simgr.found:
    found_state = simgr.found[0]
    try:
        raw = found_state.solver.eval(sym_input, cast_to=bytes)
        # Strip null bytes for display
        solved = raw.split(b"\x00")[0].decode("latin-1")
    except Exception as e:
        solved = f"(eval failed: {e})"

result = {
    "ok": True,
    "result": {
        "solved": solved is not None,
        "input": solved,
        "find": find_s,
        "avoided": avoid_s or None,
        "paths_found": len(simgr.found),
    },
    "error": None,
}
with open(output, "w") as f:
    json.dump(result, f)
