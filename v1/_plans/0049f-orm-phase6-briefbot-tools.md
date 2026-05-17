# 0049f — ORM/DAL Phase 6: Briefbot Toolset

**Status:** Implemented
**Phase:** 6 of 7

## What was built

### New tool files

| File | Tool name | Purpose |
|---|---|---|
| `src/tools/implementations/briefbot/briefbot_search.py` | `briefbot_search` | Text search over items corpus |
| `src/tools/implementations/briefbot/briefbot_trending.py` | `briefbot_trending` | Trending clusters + hot topics |
| `src/tools/implementations/briefbot/briefbot_item.py` | `briefbot_item` | Full detail on one item by item_id |
| `src/db/sync.py` | — | `run_async()` bridge for sync tool → async DAL |

### Modified files

| File | Change |
|---|---|
| `src/tools/toolsets.py` | Added `BRIEFBOT` toolset + added to `ALL_TOOLSETS` |
| `src/planning/schema.py` | Added `BRIEFBOT = "briefbot"` to `ActionType` + plan JSON schema enum |

## Tool API

### briefbot_search
- Required: `query` (string)
- Optional: `days` (default 30), `category` (ai_research/papers/etc.), `limit` (default 15, max 50), `order_by` (score/date)
- Returns: numbered list with title, URL, score, source, summary, opportunity reason

### briefbot_trending
- All optional: `window` (1d/3d/7d, default 3d), `clusters_limit` (default 8), `topics_limit` (default 10)
- Returns: trending clusters (velocity + trend_score) and hot topics (momentum)

### briefbot_item
- Required: `item_id` (from search results)
- Returns: full metadata — title, URL, source, scores, tags, summary, opportunity analysis

## Routing rules

The `BRIEFBOT` toolset routes on:
- Keywords: "research papers", "arxiv", "papers", "trending in ai", "what's hot", "briefbot", etc.
- Regex: "find/search for papers", "what's trending in X", "recent papers on Y", "latest research in Z"
- Planning note: prefer `briefbot_search` over `web_search` for research queries; fall back to web_search if no results

## Sync/async bridge

Tools call `run_async(coroutine)` from `db/sync.py`, which creates a fresh event loop per call. This bridges the sync `BaseTool.execute()` interface with the async DAL. When the runtime moves to async in the future, tools can be updated to `await` directly.

## Live verification

```
briefbot_search('llm agent', days=30):
  [1] "Let's Have a Conversation: Designing and Evaluating LLM Agents..."
      score=8.10  (papers) — arXiv cs.AI

briefbot_trending(window='3d'):
  [1] "deepseek launched flash"  trend=72.88  velocity_3d=7
  [2] "agents domain security"   trend=134.20  velocity_3d=6
  Hot topics: papers (momentum=611), llm (momentum=...), ...
```

## Graceful degradation

All three tools return a clear error message if `BRIEFBOT_DB_PATH` is not configured — they do not crash or raise exceptions to the runtime. The planner can route to `web_search` as fallback.
