# 0049e — ORM/DAL Phase 5: DAL Layer

**Status:** Implemented
**Phase:** 5 of 7

## What was built

### Agent-runtime owned DALs

| File | Class | Table |
|---|---|---|
| `src/db/dal/base_dal.py` | `BaseDAL[T]` | generic |
| `src/db/dal/agent_session_dal.py` | `AgentSessionDAL` | `agent_session` |
| `src/db/dal/plan_dal.py` | `PlanDAL`, `StepDAL` | `plan`, `step` |
| `src/db/dal/artifact_dal.py` | `ArtifactDAL` | `artifact` |

### Briefbot read-only DALs

| File | Class | Table(s) |
|---|---|---|
| `src/db/dal/briefbot/items_dal.py` | `ItemsDAL` | `items` |
| `src/db/dal/briefbot/clusters_dal.py` | `ClustersDAL` | `clusters`, `cluster_memberships` |
| `src/db/dal/briefbot/topics_dal.py` | `TopicsDAL` | `topic_profiles` |

## API summary

### BaseDAL[T]
- `get_by_id(id)` — fetches active row by PK
- `save(obj)` — add/commit/refresh, updates `updated_at`
- `soft_delete(obj)` — sets `deleted_at`, `is_active=False`

### AgentSessionDAL
- `create(original_query, model, provider)` → AgentSession
- `mark_completed(id, total_steps, total_tokens)` → AgentSession
- `mark_error(id, error, total_steps)` → AgentSession
- `list_recent(limit)` → List[AgentSession]

### PlanDAL
- `create(session_id, plan_index, original_query, steps, replan_reason)` → Plan
- `list_by_session(session_id)` → List[Plan]

### StepDAL
- `create(plan_id, session_id, step_index, action_type, description, ...)` → Step
- `update_result(step_id, status, result, error, retry_count, ...)` → Step
- `list_by_plan(plan_id)` → List[Step]
- `list_by_session(session_id)` → List[Step]

### ArtifactDAL
- `create(session_id, key, tier, mime_type, size_bytes, ...)` → Artifact
- `list_by_session(session_id)` → List[Artifact]
- `get_by_session_and_key(session_id, key)` → Optional[Artifact]

### ItemsDAL (read-only)
- `search(query, days, category, limit, order_by)` → List[BriefbotItem]
- `get_by_id(item_id)` → Optional[BriefbotItem]
- `get_top_scored(days, category, limit)` → List[BriefbotItem]
- `get_opportunities(days, limit)` → List[BriefbotItem]

### ClustersDAL (read-only)
- `get_trending(window, limit)` → List[BriefbotCluster]  (window: 1d/3d/7d)
- `get_items_for_cluster(cluster_id, limit)` → List[BriefbotItem]
- `get_by_id(cluster_id)` → Optional[BriefbotCluster]

### TopicsDAL (read-only)
- `get_top_topics(limit, min_momentum)` → List[BriefbotTopic]
- `get_by_name(name)` → Optional[BriefbotTopic]
- `search_by_name(query, limit)` → List[BriefbotTopic]

## Live query verification

```
items_dal.search('agent', days=30): 3 results
  first: "Show HN: A Karpathy-style LLM wiki your agents maintain"  score=9.555

clusters_dal.get_trending(window='3d'): 3 results
  first: "deepseek launched flash"  trend_score=72.875

topics_dal.get_top_topics(): 3 results
  first: "papers"  momentum=611.550
```

## Usage pattern

```python
from db.session import briefbot_session
from db.dal.briefbot.items_dal import ItemsDAL

async with briefbot_session() as session:
    dal = ItemsDAL(session)
    results = await dal.search("transformer attention", days=14, limit=10)
```
