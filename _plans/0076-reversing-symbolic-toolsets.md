# 0076 — Reversing & Symbolic Execution Toolsets

## Overview

Add two new toolsets that lift binary analysis from raw bytes to structured
understanding and constraint solving:

- **`reversing`** — Ghidra (headless) and radare2 for deep structural analysis:
  function listing, decompiled pseudocode, call graphs, cross-references.
- **`symbolic`** — angr for constraint-based questions: path reachability, input
  synthesis, password/key solving, vulnerability proof.

These complement the existing `analysis` toolset rather than replacing it.
`analysis` handles fast recon (file_info, strings, nm, checksec, objdump).
`reversing` and `symbolic` handle deeper work that requires full program
understanding or constraint solving.

---

## Motivation

The current `analysis` toolset tops out at raw disassembly bytes (objdump).
To understand a binary at the level needed to clone it or find vulnerabilities,
the agent needs:

1. **Decompiled pseudocode** — humans and models read C, not assembly
2. **Function-level structure** — names, sizes, addresses, call relationships
3. **Cross-references** — what calls what, where is this constant used
4. **Constraint solving** — given a binary, find inputs that satisfy conditions

Without these, the agent must guess at algorithm structure from assembly
fragments, leading to the failures seen in proc binary analysis sessions:
wrong rounds count, missed CBC mode, incomplete key derivation.

---

## Current State

```
analysis toolset (10 tools):
  strings, objdump, file_info, hexdump, nm,
  ltrace, strace, readelf, checksec, grep_binary
```

`objdump` produces raw x86 bytes. `nm` gives symbol names. `strings` finds
printable chars. No decompilation, no call graph, no symbolic execution.

---

## New Toolsets

### `reversing` toolset

Two backends, selectable by capability/availability:

**radare2 tools** (fast, ~100ms, requires `r2` in PATH):

| Tool | r2 command | Purpose |
|---|---|---|
| `r2_functions` | `afl` | List all functions with address, size, name |
| `r2_disassemble` | `pdf @ <fn>` | Disassemble one function |
| `r2_decompile` | `pdg @ <fn>` | Decompile one function (r2ghidra plugin) |
| `r2_callgraph` | `agCd` | Full call graph (dot format) |
| `r2_xrefs` | `axt <addr>` | Cross-references to an address |
| `r2_imports` | `iij` | Imported symbols (JSON) |
| `r2_constants` | `iz` + `aav` | Strings with addresses + value analysis |

**Ghidra tools** (thorough, 30–60s first run, cached after):

| Tool | Purpose |
|---|---|
| `ghidra_analyze` | Run headless analysis, cache project by binary SHA256 |
| `ghidra_decompile` | Decompiled C pseudocode for one function or all |
| `ghidra_functions` | Function list with addresses, sizes, namespaces |
| `ghidra_callgraph` | Call graph as adjacency list |
| `ghidra_find_constants` | Magic constants, data references, cross-refs |

Ghidra caches projects in `_store/ghidra_projects/<sha256>/` so the 30–60s
analysis cost is paid once per binary. Subsequent tool calls reuse the project.

### `symbolic` toolset

angr-based symbolic execution. Two tiers:

**Template tools** (pre-built patterns, deterministic):

| Tool | Purpose |
|---|---|
| `angr_reachable` | Can execution reach target address/function? |
| `angr_solve` | Find stdin/argv that reaches `find_addr`, avoids `avoid_addrs` |
| `angr_constraints` | What conditions must hold for execution to reach a point? |

**Open-ended tool** (generates + runs angr script):

| Tool | Purpose |
|---|---|
| `angr_explore` | Natural-language symbolic goal → generated script → result |

`angr_explore` takes a description like "find input that makes the success
string print" and generates a Python angr script, runs it via bash_exec,
and returns the result. This handles cases the templates don't cover.

---

## Configuration

### config.yml additions

```yaml
tools:
  ghidra:
    project_dir: "_store/ghidra_projects"
    timeout_seconds: 120
    scripts_dir: "src/tools/implementations/reversing/ghidra_scripts"
  radare2:
    timeout_seconds: 30
  angr:
    timeout_seconds: 300
    base_addr: null        # null = auto-detect

routing:
  toolset_descriptions:
    reversing: >-
      decompile function pseudocode call graph cross-reference xref what functions
      exist what calls what structural analysis radare2 ghidra full function listing
      understand program structure decompiled C
    symbolic: >-
      symbolic execution angr reachable reach path find input solve password key
      checksum constraint prove buffer overflow vulnerable sink test case generate
      input trigger behavior what conditions
```

### .env / settings additions

```
GHIDRA_HOME=/path/to/ghidra_installation
```

`GHIDRA_HOME` is the only secret-class config — it's a local path that varies
per machine, so it stays in `.env`. Everything else goes in `config.yml`.

---

## Routing Logic

The `analysis` toolset description is tightened to emphasize fast recon so it
doesn't compete with `reversing` for structural-understanding queries:

```yaml
analysis: >-
  binary recon file type identify architecture strings extract security features
  checksec nx aslr stack canary raw disassembly bytes hexdump symbol table
  initial triage lightweight quick scan
```

| Query type | Router picks |
|---|---|
| "what is this file" / "check security" | `analysis` |
| "find strings" / "quick recon" | `analysis` |
| "what functions exist" / "decompile main" | `reversing` |
| "call graph" / "what calls X" | `reversing` |
| "full analysis and clone" | `analysis` + `reversing` |
| "solve the password" / "reach this branch" | `symbolic` |
| "prove buffer overflow" / "find input" | `symbolic` |

Planning notes on each toolset guide the planner when the router selects
multiple toolsets and the planner must pick the right tool:

- `analysis` note: *"Use for initial recon. Do NOT use objdump when r2_disassemble or ghidra_decompile is available — they produce better output."*
- `reversing` note: *"Prefer ghidra_decompile over r2_decompile for full decompilation (higher quality). Use r2_* for fast function listing or cross-ref queries."*
- `symbolic` note: *"Only use when the question requires constraint solving or path proof. Always run analysis/reversing recon first to get addresses."*

---

## deep_disassembly Workflow Updates

The existing `deep_disassembly` workflow is updated to use reversing tools in
Phase 2 (structural analysis) instead of raw objdump chunks:

```
Phase 1 (recon)     — file_info, checksec, strings, nm         [analysis]
Phase 2 (structure) — ghidra_analyze, ghidra_functions,         [reversing]
                      ghidra_decompile (all), r2_callgraph
Phase 3 (synthesis) — identify algorithm, constants, dataflow   [conversation]
Phase 4 (write+test)— write_file, bash_exec                     [file_io, shell]
```

This replaces the current chunked-objdump approach that hit the 150-call
fan-out bug and still produced incomplete disassembly.

---

## Implementation Phases

### Phase A — radare2 tools
**Files:** `src/tools/implementations/reversing/__init__.py`,
`r2_functions.py`, `r2_disassemble.py`, `r2_decompile.py`,
`r2_callgraph.py`, `r2_xrefs.py`, `r2_imports.py`, `r2_constants.py`

- Each tool runs `r2 -q -c "<commands>" <binary>` via subprocess
- JSON output where available (`-j` flag), plain text fallback
- `r2_decompile` checks for `r2ghidra` plugin availability, degrades
  gracefully to `r2_disassemble` if not installed
- Toolset registered in `src/tools/toolsets.py`

### Phase B — Ghidra tools + project cache
**Files:** `src/tools/implementations/reversing/ghidra_cache.py`,
`ghidra_analyze.py`, `ghidra_decompile.py`, `ghidra_functions.py`,
`ghidra_callgraph.py`, `ghidra_find_constants.py`

**Ghidra scripts:** `src/tools/implementations/reversing/ghidra_scripts/`
- `ExportFunctions.py` — function list as JSON
- `DecompileFunction.py` — decompile one or all functions
- `ExportCallGraph.py` — adjacency list
- `FindConstants.py` — magic values, data refs

**Cache logic (`ghidra_cache.py`):**
```python
def get_or_create_project(binary_path: str) -> str:
    sha = sha256_of(binary_path)
    project_dir = config.tools.ghidra.project_dir / sha
    if not project_dir.exists():
        run_analyze_headless(binary_path, project_dir)
    return str(project_dir)
```

- `GHIDRA_HOME` read from settings, validated at tool call time
- Missing `GHIDRA_HOME` → tool returns clear error, not stack trace
- Timeout: `config.tools.ghidra.timeout_seconds` (default 120)

### Phase C — angr template tools
**Files:** `src/tools/implementations/symbolic/__init__.py`,
`angr_reachable.py`, `angr_solve.py`, `angr_constraints.py`

Each template tool:
1. Validates angr is installed (`import angr` check, clear error if not)
2. Runs angr script in a subprocess with timeout (angr can hang)
3. Returns structured result: reachable (bool), solution (bytes/str), or constraints

Template scripts live in `src/tools/implementations/symbolic/templates/`:
- `reachable.py` — `simgr.explore(find=addr)`, returns bool + path count
- `solve_input.py` — symbolic stdin/argv, `find`/`avoid`, claripy eval
- `constraints.py` — dump path constraints at address as human-readable

### Phase D — angr_explore (script generation)
**File:** `src/tools/implementations/symbolic/angr_explore.py`

- Takes `binary`, `goal` (natural-language description)
- Calls the provider (runtime model) to generate an angr script from
  a template prompt + the goal description
- Writes script to `/tmp/angr_explore_<hash>.py`
- Runs via bash_exec with timeout
- Returns stdout/stderr

This is the only tool that does an LLM call internally. The generated script
is logged (and optionally stored as an artifact) so it can be inspected.

### Phase E — routing + workflow integration
**Files:** `config.yml`, `src/workflows/implementations/deep_disassembly.py`,
`src/tools/toolsets.py`

- Tighten `analysis` description (recon framing)
- Add `reversing` and `symbolic` descriptions
- Add planning notes to both new toolsets
- Update `deep_disassembly` workflow Phase 2 to use ghidra/r2
- Update routing test cases if any exist

---

## File Layout

```
src/tools/implementations/
  reversing/
    __init__.py              # toolset definition, planning_note
    r2_functions.py
    r2_disassemble.py
    r2_decompile.py
    r2_callgraph.py
    r2_xrefs.py
    r2_imports.py
    r2_constants.py
    ghidra_cache.py          # project cache, analyzeHeadless runner
    ghidra_analyze.py
    ghidra_decompile.py
    ghidra_functions.py
    ghidra_callgraph.py
    ghidra_find_constants.py
    ghidra_scripts/
      ExportFunctions.py
      DecompileFunction.py
      ExportCallGraph.py
      FindConstants.py
  symbolic/
    __init__.py              # toolset definition, planning_note
    angr_reachable.py
    angr_solve.py
    angr_constraints.py
    angr_explore.py
    templates/
      reachable.py
      solve_input.py
      constraints.py
```

---

## Open Questions

1. **r2ghidra plugin** — do you have it installed? It gives r2 a Ghidra-quality
   decompiler. If not, `r2_decompile` degrades to disassembly. Ghidra tools
   cover the decompile case either way.

2. **angr timeout** — angr can run indefinitely on hard problems. Default 300s.
   Should `angr_explore` have a user-configurable timeout?

3. **Ghidra script language** — Ghidra supports both Jython (Python 2 via Java)
   and Java post-scripts. Jython is simpler but limited. Fine for our use case
   (JSON export scripts), but noting it.

4. **Sandbox** — Ghidra and angr both run on the host (not in the Docker
   sandbox). They need real filesystem access to the binary and project dirs.
   The sandbox `allowed_read_roots` may need updating for project cache paths.
