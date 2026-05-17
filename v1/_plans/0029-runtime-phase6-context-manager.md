# 0028 — Runtime Infrastructure Phase 6: Context Manager

## What

AFM-inspired non-destructive context manager. The Messenger stores full
history unchanged. Before each `provider.chat()` call, the ContextManager
produces a budget-constrained version of the messages by scoring each
message and assigning fidelity levels.

This directly addresses the context bloat problem — large tool outputs
accumulated in history causing max_tokens at step 3 of a 6-step plan.

## Design (adapted from Cruz AFM)

### Non-destructive packing

```
Messenger.get_messages()  (full history, unchanged)
       │
       ▼
ContextManager.pack(messages, current_query, budget)
       │
       ├── Score each message (similarity × recency × importance)
       ├── Assign fidelity: FULL / COMPRESSED / PLACEHOLDER
       ├── Pack chronologically under budget (downgrade if over)
       └── Return packed messages
              │
              ▼
       provider.chat(packed_messages, ...)
```

If history is under budget, `pack()` returns messages unchanged — zero
overhead for short conversations.

### Three scoring signals

**1. Semantic similarity** — cosine similarity between message embedding
and current query. Reuses the `all-MiniLM-L6-v2` model already loaded
in `StaticRouter`. No new model load.

**2. Recency decay** — `0.5 ^ (age / half_life)`. Default half_life = 10
turns. Recent messages score higher.

**3. Rule-based importance** — no LLM call:

| Message type | Importance | Score behavior |
|---|---|---|
| First user message (original query) | CRITICAL | Force score = 1.0, always FULL |
| User text messages | HIGH | Similarity weighted, generous recency |
| Small tool results (< 500 chars) | MEDIUM | Standard scoring |
| Large tool results (>= 500 chars) | LOW | Aggressive decay — primary bloat source |
| Assistant with write_file | LOW | File on disk, content redundant |
| Other assistant messages | MEDIUM | Standard scoring |

Key AFM insight: CRITICAL messages are force-elevated to score 1.0
regardless of age or similarity. This is what drives AFM's 83% success
rate vs 0% when importance classification is disabled.

### Fidelity levels

Thresholds (configurable):
- score >= 0.45 → FULL (verbatim)
- score >= 0.25 → COMPRESSED
- score < 0.25 → PLACEHOLDER

### Compression (heuristic, no LLM)

| Content | COMPRESSED form | PLACEHOLDER form |
|---|---|---|
| Tool result (large) | First 5 lines + `[... N omitted]` + last 3 lines | `[tool result: tool_name — N chars]` |
| write_file in tool_use | `[wrote N chars to path]` | `[tool_use: write_file]` |
| Assistant text | First + last sentence | `[assistant response — preview...]` |
| User text | Truncated to max_chars | `[user message, turn N — preview...]` |

### Budget

Default: 16384 tokens (~65K chars). Formula basis:
- 200K model context - 4096 output - ~5000 tools schema - ~300 system - margin
- Conservative to keep attention focused and costs down
- When history < budget, pack() is a no-op

Token estimation: `len(text) / 4`. Good enough for budget enforcement —
we're staying well under limits, not trying to fill exactly.

### API compatibility

Placeholder messages must maintain valid API format:
- tool_result blocks keep their `tool_use_id` (API requires matching IDs)
- assistant messages with tool_use keep the tool_use blocks (with empty input)
- This ensures the packed history is still a valid conversation

## Changes

### New files

- **`src/runtime/compressor.py`** — stateless compression functions:
  - `compress_tool_result(content, max_chars)`
  - `compress_assistant_text(content, max_chars)`
  - `placeholder_tool_result(tool_name, content)`
  - `placeholder_assistant(content)`
  - `placeholder_user(content, turn_index)`
  - `summarize_write_file(tool_input)`

- **`src/runtime/context_manager.py`** — `ContextManager` class:
  - `__init__(embedding_model)` — accepts shared SentenceTransformer
  - `pack(messages, current_query) -> list[dict]`
  - `_score_messages()` — three-signal scoring
  - `_classify_importance()` — rule-based, returns Importance enum
  - `_assign_fidelity()` — threshold-based
  - `_pack_chronological()` — greedy packing with downgrade cascade
  - `_compress_message()` — per-role compression
  - `_placeholder_message()` — per-role stubbing

### Modified files

- **`src/agent.py`**:
  - `__init__`: creates `self.context_mgr` with the router's embedding
    model instance (`self.router._model`) — no second model load
  - `_run_step()`: gains `query` parameter. Calls `context_mgr.pack()`
    on messenger history before `provider.chat()`
  - `_run_loop()`: same — packs before chat
  - `_execute_plan()`: passes `plan.original_query` as the query to
    `_run_step()` for similarity scoring

## What does not change

- Messenger — stores full history, completely unchanged
- Provider interface — unchanged (receives packed messages)
- Planner, Synthesizer — use their own ephemeral Messengers, not affected
- All tool implementations — unchanged
