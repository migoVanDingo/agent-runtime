# 0076c — Phase C: angr Template Tools

## Status: Complete

## What was built

### angr runner (`angr_runner.py`)

- `angr_available()` — cached subprocess check for angr installation
- `_function_count(binary)` — quick `nm` call to count symbols for timeout scaling
- `scaled_timeout(base, binary)` — applies complexity multiplier:
  - `< 50 functions` → 1.0× (base)
  - `≥ 50 functions` → 1.5×
  - `≥ 200 functions` → 2.5×
- `run_angr_script(script, timeout, env_vars)` — runs a Python script with angr,
  reads JSON from `ANGR_OUTPUT` temp file, returns `{ok, result, error}` dict

### Template scripts (`templates/`)

Scripts read all inputs from environment variables, write JSON to `ANGR_OUTPUT`.

| Script | Inputs | Output |
|---|---|---|
| `reachable.py` | `ANGR_BINARY`, `ANGR_TARGET`, `ANGR_AVOID` | `{reachable, path_count}` |
| `solve_input.py` | `ANGR_BINARY`, `ANGR_FIND`, `ANGR_AVOID`, `ANGR_INPUT_TYPE`, `ANGR_INPUT_LEN` | `{solved, input, paths_found}` |
| `constraints.py` | `ANGR_BINARY`, `ANGR_TARGET` | `{reachable, constraints, constraint_count}` |

All templates accept both hex addresses (`0x401234`) and exported symbol names.

### Tool wrappers

| Tool | Base timeout | Purpose |
|---|---|---|
| `angr_reachable` | 60s | Can execution reach target? |
| `angr_solve` | 120s | Find stdin/argv that reaches find, avoids avoid |
| `angr_constraints` | 120s | What conditions must hold to reach target? |

### Guard integration

All `angr_*` tools escalate: `"host symbolic execution: angr_solve on '<path>'"`.
Approval is cached per-session once the user approves.

# 0076d — Phase D: angr_explore

## Status: Complete

`angr_explore` generates a custom angr script from a natural-language goal via the
runtime LLM, then runs it via `run_angr_script`.

- Script generation uses `get_runtime_provider()` with a concise system prompt
  constraining the model to produce only valid Python < 60 lines
- Strips markdown fences from model output
- On script failure, returns both the error AND the generated script so the user
  can see what was attempted
- Timeout: `config.tools.angr.timeout_explore` × binary complexity multiplier
