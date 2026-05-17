# 0010 — Dynamic Tool Loading: Phase 1 — Toolset Model

## Goal

Define the `Toolset` dataclass — the core data model that groups tools into named, described sets.
No registry changes yet, no routing logic. Just the model.

---

## Files

### New: `src/tools/toolset.py`

A `Toolset` is a plain Python dataclass with three fields:
- `name: str` — identifier used throughout the system (e.g. `"analysis"`)
- `description: str` — human/model-readable description of what tasks this toolset handles
- `tools: list[BaseTool]` — the tool instances belonging to this set

---

## Notes

- Plain dataclass, no Pydantic, no external dependencies
- `tools` holds instances, not classes — consistent with how `ToolRegistry` works today
- No behavior here — this is purely a data container
