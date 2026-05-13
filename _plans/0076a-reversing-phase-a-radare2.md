# 0076a — Phase A: radare2 Tools

## Status: Complete

## What was built

Seven radare2 tools in `src/tools/implementations/reversing/`:

| Tool | r2 command | Analysis needed |
|---|---|---|
| `r2_functions` | `aflj` (JSON) → `afl` fallback | Yes (aaa) |
| `r2_disassemble` | `pdf @ <fn>` | Yes |
| `r2_decompile` | `pdg @ <fn>` → `pdf` fallback if r2ghidra missing | Yes |
| `r2_callgraph` | `agcj` / `agCj` (JSON) → dot fallback | Yes |
| `r2_xrefs` | `axtj @ <addr>` (JSON) → `axt` fallback | Yes |
| `r2_imports` | `iij` (JSON) → `ii` fallback | No |
| `r2_constants` | `izj` (JSON, strings with addresses) | No |

Shared runner in `r2_runner.py`:
- Detects r2 in PATH — returns clear error if not found
- `-q -e scr.color=0` suppresses banner and ANSI codes
- `-A` flag for full analysis (aaa) when needed; skipped for static queries
- r2 WARN/INFO stderr filtered out; only real errors surfaced
- Configurable timeout via `config.tools.radare2.timeout_seconds`

## Config changes

**`config.yml`:**
- Added `tools.radare2.timeout_seconds: 30`
- Added `tools.ghidra.*` and `tools.angr.*` stubs (used by Phases B/C)
- Tightened `routing.toolset_descriptions.analysis` to recon framing
- Added `routing.toolset_descriptions.reversing`
- Added `routing.toolset_descriptions.symbolic`

**`src/config.py`:**
- Added `Radare2Config`, `GhidraConfig`, `AngrConfig` dataclasses
- Extended `ToolsConfig` with optional nested configs (default-instantiated in `__post_init__`)
- Added `_load_tools_config()` helper to handle nested YAML → dataclasses

**`src/settings.py`:**
- Added `ghidra_home: Optional[str]` (reads `GHIDRA_HOME` from `.env`)

**`src/tools/toolsets.py`:**
- Registered `REVERSING` toolset with routing rules and planning note
- Added to `ALL_TOOLSETS`

## Smoke test results

```
r2_functions on _tests/proc → 12 functions (11 imports + main @ size 2197)
r2_imports on _tests/proc   → 13 imports (sscanf, strlen, memset, etc.)
```

Both clean, no STDERR noise in output.

## Notes

- `r2_decompile` degrades gracefully to disassembly since r2ghidra is not installed.
  Output is prefixed with `[r2ghidra not installed — showing disassembly instead]`.
- `r2_imports` fell back to plain `ii` output — JSON parsed fine but the formatter
  produced cleaner plain-text; both paths work.
- Phase B (Ghidra) adds higher-quality decompilation as the primary path.
