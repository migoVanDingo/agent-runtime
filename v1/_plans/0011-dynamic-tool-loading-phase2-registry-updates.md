# 0011 — Dynamic Tool Loading: Phase 2 — Registry Updates

## Goal

Extend `ToolRegistry` with toolset-aware methods while keeping all existing
individual tool registration and lookup unchanged.

---

## Files

### Updated: `src/tools/registry.py`

**New internal state:**
- `_toolsets: dict[str, Toolset]` — toolsets indexed by name

**New methods:**
- `register_toolset(toolset: Toolset)` — registers the toolset, indexes all its tools individually
  so `get(name)` still works for tool dispatch during the agent loop
- `get_toolset_tools(name: str) -> list[BaseTool]` — returns tools for a named toolset
- `get_toolset_schema(names: list[str]) -> list[dict]` — returns the union of API schemas
  for a list of toolset names, deduplicating if a tool appears in multiple sets
- `toolset_names() -> list[str]` — returns registered toolset names, for inspection and logging

**Unchanged:**
- `register(tool)` — individual tool registration still works
- `get(name)` — tool dispatch by name still works
- `to_api_schema()` — returns all registered tool schemas (used as fallback)

---

## Notes

- `register_toolset` calls the existing `register()` internally for each tool —
  no duplication of indexing logic
- `get_toolset_schema` deduplicates by tool name in case toolsets share tools in future
