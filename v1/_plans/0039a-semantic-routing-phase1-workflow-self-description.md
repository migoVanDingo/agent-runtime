# Phase 1: Workflow Self-Description

## What Was Built

Each `Workflow` subclass now declares its own `intent` — a 1-2 sentence description of what user requests it handles, written for an LLM audience. `WorkflowMatcher` exposes two new methods that downstream phases use to inject descriptions into prompts at runtime.

## Changes

### `workflows/base.py`
- Added abstract `intent: str` property to `Workflow` base class
- All subclasses must implement it (enforced at instantiation)

### `workflows/templates.py`
- Implemented `intent` on all four workflows:
  - `AnalyzeAndWrite`: file analysis + write findings to a doc
  - `ReadModifyWrite`: read, transform, write to output file
  - `HashAndReport`: compute cryptographic hash/checksum of a file
  - `DeepDisassembly`: disassemble/decompile/reverse-engineer a binary, reconstruct source, generate call graph, security audit

### `workflows/matcher.py`
- Added `get_by_name(name) -> Workflow | None`: looks up a workflow by name
- Added `get_descriptions() -> list[tuple[str, str]]`: returns `(name, intent)` pairs for all registered workflows — called at prompt-build time by classifier and fallback router

## No Behavior Change
This phase is purely additive. No routing logic was touched. The `intent` strings are declared but not yet used — Phases 3 and 4 consume them.

## Auto-sync Contract
Adding a new workflow to `ALL_WORKFLOWS` with an `intent` property automatically makes it visible in all classifier and fallback prompts. No manual prompt maintenance required.
