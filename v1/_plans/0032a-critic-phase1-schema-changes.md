# 0032a — Plan Critic Phase 1: Schema Changes

**Date**: 2026-04-15
**Status**: Implemented
**Parent**: 0032

## Changes

### 1. `planning/schema.py` — Added `tool` field to `Step`
- New field: `tool: str | None = None`
- Updated `to_dict()` and `from_dict()` to serialize/deserialize the field
- Conversation steps use `tool: null`, all other steps declare the specific tool name

### 2. `planning/prompts.py` — Reworked planner prompt
- Added information-needs-first reasoning guidance: "BEFORE selecting tools, think about what information you actually need"
- Added `tool` field to the JSON schema template
- Listed all available tools grouped by action type
- Replaced single example with two examples:
  - Summary task (file_info, strings, write_file — 3 steps, no heavy tools)
  - Vulnerability analysis task (file_info, checksec, strings, objdump, nm — 5 steps, heavy tools justified)
- Teaches the model that tool selection should scale with task depth

### 3. `runtime/validator.py` — Tool field validation
- Constructor now takes `registered_tools: set[str]` in addition to toolsets
- Removed regex-based multi-tool detection (no longer needed — the `tool` field is explicit)
- Added check #6: non-conversation steps must declare a `tool` field
- Added check: declared tool must exist in the registry

### 4. `tools/registry.py` — New methods
- `tool_names() -> set[str]`: returns all registered tool names
- `get_tool_schema(tool_name) -> list[dict]`: returns API schema for a single tool
- `get_tool_description(tool_name) -> str`: returns a tool's description

### 5. `agent.py` — Wiring
- Passes `self.registry.tool_names()` to `PlanValidator` constructor
