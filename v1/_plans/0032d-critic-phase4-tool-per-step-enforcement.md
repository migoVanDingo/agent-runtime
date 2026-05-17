# 0032d — Plan Critic Phase 4: Tool-Per-Step Enforcement

**Date**: 2026-04-15
**Status**: Implemented
**Parent**: 0032

## Changes

### 1. `agent.py` — Tool selection rewritten in `_execute_plan`
Old behavior: router selects toolsets based on step description, provides all tools in those toolsets.
New behavior:
- If `step.tool` is set: provide ONLY that tool's schema (via `registry.get_tool_schema()`)
- Plus utility tools from `_step_utility_tools()` (e.g., `make_directory` for write steps)
- If `step.tool` is None (conversation): no tools
- Fallback: if step has no tool field, fall back to router-based selection (shouldn't happen after critic)

This is a hard constraint. If the plan says `tool: "file_info"`, the model physically cannot call strings, objdump, or write_file — those tools aren't in the schema.

### 2. `agent.py` — `_step_utility_tools()` method
Returns small allowlist of utility tools needed alongside the declared tool:
- `write_file` → also gets `make_directory` (to create parent dirs)
- `bash_exec` → also gets `read_file` (may need to read before executing)

### 3. `agent.py` — Enhanced step system prompt
When a step has a declared tool, the system prompt now includes:
"You have been given ONLY the '{tool}' tool for this step. Use it and stop."

This reinforces the hard constraint at the prompt level too.

### 4. Log format updated
Plan steps now log with tool field: `Step 1 [analysis] tool=file_info: description`
