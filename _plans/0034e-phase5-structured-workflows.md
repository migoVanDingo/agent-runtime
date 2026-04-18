# 0034e — Phase 5: Structured Workflows (BIN-Inspired)

**Date**: 2026-04-17
**Status**: Implemented
**Parent**: 0034

## Motivation

BIN survey argues that rule-based, graph-based, and behavior-tree (RGB) components remain valuable for predictability alongside LLMs. Our system was almost entirely LLM-driven for planning decisions. This phase adds deterministic fast-paths for common task patterns.

## Changes

### 5a. Workflow Templates

**New module**: `src/workflows/`

**`src/workflows/base.py`** — `Workflow` abstract base class:
- `name`: human-readable name for logging
- `pattern`: compiled regex to match against user messages
- `generate_plan(match, message) -> Plan`: produces a Plan from the regex groups
- `try_match(message) -> Plan | None`: attempts match, returns Plan or None

**`src/workflows/templates.py`** — Three built-in workflows:

1. **AnalyzeAndWrite**: `"analyze <target> ... write/save to <output.md>"`
   - Step 1: file_info on target
   - Step 2: strings on target
   - Step 3: write_file to output
   - Covers the most common pattern from logs (analyze binary → write summary)

2. **ReadModifyWrite**: `"read <source> ... modify/update/change ... write/save to <output>"`
   - Step 1: read_file source
   - Step 2: write_file to output
   - Covers read-transform-write patterns

3. **HashAndReport**: `"hash/checksum/md5/sha256 <target>"`
   - Step 1: hash_file target
   - Simple single-step workflow

**`src/workflows/matcher.py`** — `WorkflowMatcher` class:
- `match(message) -> Plan | None`: tries each template in priority order
- First match wins (templates are ordered from most specific to least)

**Modified**: `src/agent.py`
- `WorkflowMatcher` created in `__init__`
- Before calling the LLM planner, tries `workflow_matcher.match(user_message)`
- If match → skips planner entirely, goes straight to validator → critic → execution
- If no match → falls through to LLM planner (existing behavior)

**Benefit**: Zero LLM calls for planning common tasks. Deterministic plans. Still goes through validator, critic, and monitor for safety — the workflow just replaces the planner, not the entire pipeline.

### 5b. Log Analysis Script

**New file**: `scripts/analyze_logs.py`

Reads all `.log` files in `_logs/`, extracts:
- User messages
- Plan step sequences (tool names in order)
- Direct-mode tool calls

Reports:
- Tool usage frequency (top 20)
- Action type distribution
- Most common plan tool sequences
- Plan length distribution

Use this to identify new workflow patterns to add.

## Execution Flow (After Phase 5)

```
User message (plan mode)
  ↓
Workflow matcher (regex, zero LLM calls)
  ├─ match → Plan (skip planner)
  └─ no match → LLM Planner (existing)
  ↓
Validator → Critic → Execution
```
