"""Named Ghidra operations dispatched by the subprocess worker.

Each operation takes a ``FlatProgramAPI`` (``api``) plus a ``params`` dict
and returns a string. Operations live here, separated from the tool wrappers,
so the subprocess worker can import them by name without pulling in
``BaseTool`` and the rest of the agent runtime.

Adding a new ghidra_X tool:
  1. Write the operation function below.
  2. Register it in ``OPERATIONS``.
  3. Create a thin ``ghidra_X.py`` tool wrapper that calls
     ``run_ghidra_op('X', params)``.

The operations never import anything from ``tools.base``, ``runtime/``, or
``agent.py`` — they run inside the subprocess and need to stay isolated.
Pyghidra / JPype imports happen lazily inside each function so this module
can be loaded for introspection (e.g., listing supported operations) without
spinning up the JVM.
"""
from __future__ import annotations

from typing import Any, Callable


# ── probe ────────────────────────────────────────────────────────────────────

def _probe(api, params: dict[str, Any]) -> str:
    """Verify the JVM + project loaded and report the program name."""
    path = params.get("path", "")
    return f"Ghidra ready for '{path}' — {api.currentProgram.getName()}"


# ── list_functions ───────────────────────────────────────────────────────────

def _list_functions(api, params: dict[str, Any]) -> str:
    """Enumerate every function in the program with address + size."""
    fm = api.currentProgram.getFunctionManager()
    user_fns: list[dict] = []
    ext_fns: list[dict] = []
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
    lines.append(
        f"\n{len(user_fns) + len(ext_fns)} total "
        f"({len(user_fns)} user-defined, {len(ext_fns)} external)"
    )
    return "\n".join(lines)


# ── decompile ────────────────────────────────────────────────────────────────

def _decompile(api, params: dict[str, Any]) -> str:
    """Decompile one named function, one address, or all non-thunk functions."""
    from ghidra.app.decompiler import DecompInterface, DecompileOptions  # type: ignore

    target = params.get("function") or None
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


# ── find_constants ───────────────────────────────────────────────────────────

_MAGIC = {
    2654435769: "TEA DELTA (0x9e3779b9)",
    2654435761: "TEA negative DELTA (0x61c88647)",
    1779033703: "SHA-256 H0 (0x6a09e667)",
    1732584193: "MD5 A (0x67452301)",
    4023233417: "MD5 B (0xefcdab89)",
    2562383102: "SHA-1 K1 (0x5a827999)",
    1518500249: "SHA-1 K2 (0x6ed9eba1)",
}


def _find_constants(api, params: dict[str, Any]) -> str:
    """Enumerate defined data, annotating well-known crypto magic constants."""
    listing = api.currentProgram.getListing()
    items: list[str] = []
    annotations: list[str] = []

    for d in listing.getDefinedData(True):
        dt_name = d.getDataType().getName()
        try:
            val = d.getValue()
            val_str = str(val) if val is not None else None
        except Exception:
            val_str = None
        addr = str(d.getAddress())
        label = str(d.getLabel()) if d.getLabel() else ""

        try:
            val_int = int(val_str) if val_str else None
            note = _MAGIC.get(val_int, "") if val_int is not None else ""
            if note:
                annotations.append(f"  *** {addr}: {val_str} → {note}")
        except (ValueError, TypeError):
            pass

        items.append(f"  {addr:<20} {dt_name:<15} {repr(val_str):<30}  {label}")

    out = f"{'Address':<20} {'Type':<15} {'Value':<30}  Label\n" + "-" * 75 + "\n"
    out += "\n".join(items[:200])
    if len(items) > 200:
        out += f"\n... ({len(items) - 200} more items truncated)"
    if annotations:
        out += "\n\n=== KNOWN CRYPTO CONSTANTS ===\n" + "\n".join(annotations)
    return out


# ── callgraph ────────────────────────────────────────────────────────────────

def _callgraph(api, params: dict[str, Any]) -> str:
    """Adjacency-list call graph (caller → callee) for non-external functions."""
    program = api.currentProgram
    fm = program.getFunctionManager()
    rm = program.getReferenceManager()

    lines: list[str] = []
    for fn in fm.getFunctions(True):
        if fn.isExternal():
            continue
        callees: set[str] = set()
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


# ── Registry ─────────────────────────────────────────────────────────────────

OPERATIONS: dict[str, Callable[..., str]] = {
    "probe": _probe,
    "list_functions": _list_functions,
    "decompile": _decompile,
    "find_constants": _find_constants,
    "callgraph": _callgraph,
}


def known_operations() -> list[str]:
    return sorted(OPERATIONS)
