# 0013 — Dynamic Tool Loading: Phase 4 — Toolset Definitions

## Goal

Define the four default toolsets with their tools and routing rules in a single
file (`src/tools/toolsets.py`). Rules are owned by the toolsets themselves —
the router has no hardcoded knowledge of any toolset.

---

## Files

### New: `src/tools/toolsets.py`

Defines and exports `ALL_TOOLSETS: list[Toolset]`. Each toolset carries its
own `RoutingRule` list using condition builders from `routing/conditions.py`.

| Toolset | Tools | Rule signals |
|---|---|---|
| `file_io` | read_file, write_file, list_files, walk_directory, copy_file, move_file, delete_file, make_directory, read_file_lines, get_working_directory, environment_info, download_file | `has_file_path()`, common file extensions, file/directory keywords |
| `shell` | bash_exec, search_files | execution/command keywords |
| `analysis` | strings, objdump, file_info, hexdump, nm, ltrace, strace, readelf, checksec, grep_binary | binary extensions (`.elf`, `.so`, `.bin`, ...), RE keywords, `last_tools_were` continuation |
| `crypto` | hash_file, base64_encode, base64_decode, xor_decode | hash/encode/decode keywords, `last_tools_were` continuation |

### Updated: `src/agent.py`

Replace 28 individual `registry.register()` calls with a loop:

```python
for toolset in ALL_TOOLSETS:
    self.registry.register_toolset(toolset)
```

All tool imports removed from `agent.py` — tool membership is now the toolset's
responsibility.

---

## Rule Design

Rules are intentionally explicit — routing logic should be readable and auditable,
not inferred. Each toolset defines:

- **Extension rules** (where applicable) — file extensions strongly indicate the
  toolset needed without requiring keyword matching
- **Keyword rules** — broad vocabulary coverage using `any_keyword()`; token-based
  so word boundaries are respected
- **Continuation rules** — `last_tools_were()` keeps the same toolset active across
  multi-turn tool chains without re-evaluating the original message

---

## Notes

- `search_files` placed in `shell` (grep-like behavior) rather than `file_io`
- `file_info` placed in `analysis` (binary inspection) rather than `file_io`
- Toolset descriptions for embedding-based routing remain in `config.yml` —
  they serve the router's semantic matching, not the toolset definition itself
- Adding a new toolset in the future: create a `Toolset(...)` instance in this file
  and add it to `ALL_TOOLSETS` — zero other files need to change
