# 0034f — Phase 6: Advanced Context Management

**Date**: 2026-04-17
**Status**: Implemented
**Parent**: 0034

## Motivation

AFM's key finding: importance classification is the dominant factor (83.3% pass rate with it, 0% without it). Our importance classification was rule-based and coarse. This phase adds LLM-assisted importance scoring and intelligent compression — the two areas where the AFM paper shows the most improvement potential.

## Changes

### 6a. LLM-Assisted Importance Classification

**New file**: `src/runtime/importance.py`

- `ImportanceScorer` class with `score(original_query, step_description, result) -> Importance`
- Uses runtime provider (cheap model) to classify tool results as CRITICAL/HIGH/MEDIUM/LOW
- Caches results (same step description + result prefix → same importance)
- Falls back to MEDIUM on parse failure or error

**Modified**: `src/runtime/context_manager.py`

- Added `_importance_overrides: dict[int, Importance]` — parallel index mapping message indices to LLM-assigned importance
- Added `set_importance(message_index, importance)` method
- `_classify_importance()` checks overrides before falling through to rule-based classification

**Modified**: `src/agent.py`

- `ImportanceScorer` created in `__init__` (uses runtime provider)
- After each plan step completes (after `step.result` capture, before monitor assessment):
  - Calls `importance_scorer.score()` on the step result
  - Calls `context_mgr.set_importance()` to store the override
- Only scores in plan mode (direct mode uses rule-based classification — keeping it fast)

**Cost**: +1 LLM call per completed plan step (runtime provider = cheap). The cache prevents re-scoring identical results.

### 6b. Compression Quality Improvement

**Modified**: `src/runtime/context_manager.py`

- Added `_summarizer: BaseProvider | None` and `set_summarizer(provider)` method
- Added `_summary_cache: dict[str, str]` for caching LLM summaries
- New `_compress_tool_result(content, max_chars)` method:
  - If content ≤ 2x max_chars or no summarizer → mechanical compression (existing behavior)
  - If content > 2x max_chars and summarizer available → LLM summarization:
    - Sends first 2000 chars to summarizer with instruction to summarize in ≤ max_chars
    - Caches summary for reuse
    - Falls back to mechanical compression on LLM failure
- Replaces direct `compressor.compress_tool_result()` calls in `_compress_message()`

**Modified**: `src/agent.py`

- Calls `context_mgr.set_summarizer(get_runtime_provider())` at init

**Cost**: +1 LLM call per compressed tool result (only when packing is triggered and result is large enough). Cached — same content never summarized twice.

## Context Management Pipeline (After Phase 6)

```
Step completes in plan execution
  ↓
Importance scorer (LLM, runtime provider)
  → classifies result as CRITICAL/HIGH/MEDIUM/LOW
  → stored as override in context manager
  ↓
Next step needs context packing (budget exceeded)
  ↓
Score messages:
  1. Semantic similarity (embedding cosine) — shared model
  2. Recency decay (exponential half-life)
  3. Importance: LLM override → plan-awareness boost → rule-based
  ↓
Assign fidelity (FULL/COMPRESSED/PLACEHOLDER)
  ↓
Compress (COMPRESSED fidelity):
  - Small results → mechanical truncation
  - Large results → LLM summarization (cached)
  ↓
Pack chronologically under budget (pair atomicity preserved)
```

## Comparison with AFM Paper

| Feature | AFM Paper | Our Implementation |
|---------|-----------|-------------------|
| Fidelity levels | 3 (FULL/COMPRESSED/PLACEHOLDER) | 3 (same) |
| Importance classification | Dominant factor (83.3%) | Rule-based + LLM override |
| Scoring signals | Embedding similarity + recency + importance | Same three signals |
| Plan awareness | Not discussed | Boost messages from current plan |
| LLM importance | Not explicitly described | Per-step classification |
| Compression | Not detailed | Mechanical + LLM summarization |
| Pair atomicity | Not discussed | Enforced for tool_use/tool_result |
| Non-destructive | Yes | Yes (Messenger holds originals) |
