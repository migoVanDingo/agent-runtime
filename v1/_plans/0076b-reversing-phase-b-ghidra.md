# 0076b — Phase B: Ghidra Tools

## Status: Complete (revised — PyGhidra)

## Root cause of original failure

Ghidra 12.x dropped Jython (Python 2.7 via JVM). The original `analyzeHeadless + .py postscript` approach silently failed with:

```
GhidraScriptLoadException: Ghidra was not started with PyGhidra. Python is not available
```

This only appeared in stdout (not stderr), so `run_ghidra_script` was logging only the 3-line Java version header from stderr and reporting "no output".

## Fix: switched to PyGhidra direct API

PyGhidra embeds the Ghidra JVM in the current Python process via JPype. Instead of spawning a subprocess and hoping post-scripts work, the tools now call the Ghidra Java API directly from Python.

**Install**: `pip install pyghidra` (added to project deps)

**Pattern**:
```python
import pyghidra
pyghidra.start(install_dir=GHIDRA_HOME, verbose=False)   # once per process

with pyghidra.open_program(binary_path) as api:
    program = api.currentProgram
    fm = program.getFunctionManager()
    # use full Ghidra Java API from Python
```

The JVM is started once (`_ghidra_started` flag) and shared across all tool calls in the session. Subsequent `open_program()` calls reuse the running JVM — no cold-start overhead after the first.

## ghidra_cache.py (rewritten)

- `_ensure_started()` — calls `pyghidra.start()` once; cached in module-level flag
- `run_ghidra_function(binary, fn, *args)` — opens binary and calls `fn(api, *args)`
- No temp files, no subprocesses, no Jython scripts

## Tools (rewritten to use direct API)

| Tool | What it calls |
|---|---|
| `ghidra_analyze` | `api.currentProgram.getName()` — probe to confirm Ghidra is working |
| `ghidra_functions` | `fm.getFunctions(True)` — full function list |
| `ghidra_decompile` | `DecompInterface.decompileFunction()` — C pseudocode |
| `ghidra_callgraph` | `rm.getReferencesFrom(addr)` + isCall() filter |
| `ghidra_find_constants` | `listing.getDefinedData(True)` + magic constant annotation |

## Ghidra script files (obsolete)

`ghidra_scripts/` directory and `.py` post-scripts are no longer used.
They can be deleted but are kept for reference.

## Notes

- On macOS Mach-O binaries, Ghidra labels the entry point `entry` not `main`.
  Use `ghidra_functions` first to get the actual function names, then pass
  the correct name to `ghidra_decompile`.
- `ghidra_decompile` with no `function` argument decompiles all non-thunk functions.
- The JVM startup takes ~10s on first call in a session; all subsequent calls are fast.

## Also fixed: approval cache for ghidra_*/angr_* tools

`_approval_key` now returns `f"{tool_name}:{binary_path}"` for ghidra_* and angr_* tools.
Previously it returned `None`, which meant the cache was never checked and the user
was prompted on every call (including retries). Now approval of `ghidra_decompile` on
`_tests/proc` caches under `ghidra_decompile:_tests/proc` and all subsequent calls
in the same session are auto-approved.
