# 0034b — Phase 2: Critic & Planning Refinement

**Date**: 2026-04-17
**Status**: Implemented
**Parent**: 0034

## Changes

### 2a. Tool Weight Categories in Critic Prompt

**Modified**: `src/tools/base.py`
- Added `ToolWeight` enum: `LIGHTWEIGHT`, `MODERATE`, `HEAVY`
- Added `weight` class attribute to `BaseTool` (default: MODERATE)

**Modified**: All 28 tool implementations
- Lightweight (14 tools): file_info, strings, hash_file, base64_encode, base64_decode, list_files, get_working_directory, environment_info, make_directory, copy_file, move_file, delete_file, search_files, xor_decode, write_file
- Moderate (7 tools): nm, read_file, read_file_lines, bash_exec, grep_binary, walk_directory, download_file
- Heavy (6 tools): objdump, hexdump, readelf, strace, ltrace, checksec

**Modified**: `src/tools/registry.py`
- `get_tool_description()` now returns `"[lightweight] description"` format

**Modified**: `src/runtime/prompts.py`
- CRITIC_SYSTEM_PROMPT proportionality criterion updated: "Lightweight tools are cheap — only challenge if clearly irrelevant. Heavy tools must be explicitly justified."

### 2b. Risk-Aware Intent Classification

**Modified**: `src/runtime/classifier.py`
- `classify()` now returns `(mode, risk)` tuple instead of just `mode`
- `_parse()` returns `(mode, risk, reason)` with risk validation

**Modified**: `src/runtime/prompts.py`
- CLASSIFIER_SYSTEM_PROMPT extended with risk guidelines:
  - low: read-only, analysis, conversational
  - moderate: file writes in working directory
  - high: deletion, system-modifying commands, outside working directory
- Added risk field to all examples

**Modified**: `src/planning/schema.py`
- Added `risk: str = "low"` field to Plan dataclass

**Modified**: `src/agent.py`
- Unpacks `(mode, risk)` from classifier
- Sets `plan.risk = risk` before critic review
- Low-risk plans can skip critic (when `config.runtime.plan_critic.skip_low_risk` is true)

**Modified**: `src/config.py`, `config.yml`
- Added `skip_low_risk: bool` to PlanCriticConfig (default: false — conservative)

### 2c. Vestigial Cleanup

**Removed**: `PlanningGateConfig` from `src/config.py`
**Removed**: `gate` field from `PlanningConfig`
**Removed**: `planning.gate` section from `config.yml`

**New file**: `src/embeddings.py`
- `get_embedding_model()` — lazy-loads and caches a single SentenceTransformer instance
- Both router and context manager now use this shared instance

**Modified**: `src/routing/static_router.py`
- No longer imports SentenceTransformer directly
- Embedding model and toolset embeddings are lazy-loaded on first `select()` call
- Router init no longer triggers a 4-second model load

**Modified**: `src/runtime/context_manager.py`
- No longer accepts embedding_model parameter (ignored, deprecated)
- Lazy-loads from shared `embeddings.get_embedding_model()` on first pack()

**Modified**: `src/agent.py`
- `ContextManager()` created without embedding_model argument

### Startup Impact

**Before**: Router loads embedding model in `__init__` (~4 seconds). Context manager receives it by reference.

**After**: Neither loads the model at init. First call to either `router.select()` or `context_manager.pack()` triggers a single shared load. If the user's first message is classified as "direct" and fits in the context budget, the embedding model is never loaded at all.
