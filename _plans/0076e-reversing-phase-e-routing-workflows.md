# 0076e — Phase E: Routing & Workflow Integration

## Status: Complete

## Routing changes

**`config.yml`** — toolset descriptions updated:

- `analysis` — reframed as fast recon/triage to avoid overlap with `reversing`
- `reversing` — added: decompile, call graph, xref, structural analysis, r2/ghidra
- `symbolic` — added: symbolic execution, angr, solve, prove, path conditions

**`planning/schema.py`** — added `ActionType.REVERSING` and `ActionType.SYMBOLIC`

**`planning/prompts.py`** — updated action_type field to include `reversing|symbolic`

## Five new workflows

| Workflow | Trigger | Tools |
|---|---|---|
| `quick-recon` | "quick look", "what is this binary" | file_info, checksec, strings, nm |
| `function-map` | "list functions", "call graph", "what calls what" | r2_functions, r2_callgraph, r2_imports |
| `decompile-target` | "decompile main", "show pseudocode" | ghidra_analyze+decompile (Ghidra available) or r2_disassemble (fallback) |
| `solve-crackme` | "find the password", "crack the binary", "solve crackme" | file_info, strings, r2_functions, angr_solve |
| `audit-binary` | "security audit", "find vulnerabilities", "attack surface" | checksec, r2_imports, r2_xrefs, angr_reachable, write_file |

Priority order in registry: `solve-crackme` > `audit-binary` > `deep-disassembly` >
`decompile-target` > `function-map` > `quick-recon` > existing workflows.

## deep_disassembly update

Phase 2 now branches on `GHIDRA_HOME`:

- **Ghidra available**: steps 5–8 use `ghidra_analyze → ghidra_functions → ghidra_decompile → ghidra_find_constants`. Full decompiled C pseudocode with crypto constant annotation.
- **Ghidra not available**: steps 5–8 use chunked objdump (original behavior, 500-line slices). Unchanged fallback.

Step numbering for output/test steps computed dynamically from `synthesis_step_num + 1`
so it stays correct regardless of which Phase 2 path is taken.

## decompile-target Ghidra fallback

`DecompileTarget.generate_plan()` checks `settings.ghidra_home` at plan-generation time:
- If Ghidra configured: `ghidra_analyze → ghidra_decompile → conversation`
- If not: `file_info → r2_disassemble → conversation`

This means the workflow is useful even without Ghidra installed.
