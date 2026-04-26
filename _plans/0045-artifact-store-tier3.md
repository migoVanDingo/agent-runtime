# 0045 — Artifact Store Tier 3: Semantic Recall and Project Memory

## Scope

Tier 3 adds long-term semantic memory on top of Tier 1+2:

- Session-summary semantic recall (cross-session RAG)
- Artifact-summary semantic search
- LLM-facing recall tooling
- Project scoping and pinned memory behavior

Tier 3 remains SQLite-first and local-first. No external vector DB.

---

## Dependencies

- Tier 1 complete (`0043`): artifact CRUD, data/artifacts toolsets, `produces`
- Tier 2 complete (`0044`): resume flow, conversation persistence, decay, request logging
- Shared embedding model available (`all-MiniLM-L6-v2`)

---

## Tier 3 Phases

| Phase | Goal |
|------|------|
| 1 | Tier 3 schema migration + config gates |
| 2 | Embedding/index pipeline for session + artifact summaries |
| 3 | Retrieval engine (`recall`) with sqlite-vec primary + Python fallback |
| 4 | `recall_sessions` toolset integration |
| 5 | Startup memory injection into context manager |
| 6 | Project scoping + pinned memory semantics |

---

## Phase 1 — Schema and Config Activation

### Objectives

1. Add Tier 3 schema objects safely and idempotently.
2. Gate Tier 3 behind config so rollout can be gradual.
3. Add capability detection for `sqlite-vec`.

### Schema changes

```sql
CREATE TABLE IF NOT EXISTS session_summaries (
    session_id TEXT PRIMARY KEY,
    summary    TEXT NOT NULL,
    embedding  BLOB NOT NULL,
    created_at REAL NOT NULL
);
```

Add `summary_embedding` to `artifacts` if missing:

```sql
ALTER TABLE artifacts ADD COLUMN summary_embedding BLOB;
```

Add project tag support (already Tier 2 `artifact_tags`): no new table needed.

### Config additions

```yaml
artifact_store:
  rag:
    enabled: false
    top_k: 3
    similarity_threshold: 0.6
    inject_on_start: true
    max_injected_chars: 3000
  sqlite_vec:
    enabled: true
    extension_path: null
  project:
    enabled: false
    default: null
```

### Files

- `src/runtime/artifact_store.py`
- `src/config.py`
- `config.yml`

---

## Phase 2 — Indexing Pipeline

### Objectives

1. Persist session summaries with embeddings.
2. Persist artifact summary embeddings for large artifacts.
3. Keep indexing incremental and cheap.

### Session summary indexing

At session finalization:
- source summary from synthesizer output when available
- fallback to compact summary from completed steps when synthesizer skipped
- write/update `session_summaries(session_id, summary, embedding, created_at)`

### Artifact summary indexing

When artifact is file-backed and has non-empty `summary`:
- compute embedding for `summary`
- store in `artifacts.summary_embedding`
- skip for tiny inline artifacts unless explicitly requested

### Re-index controls

Add helper APIs:

```python
def index_session_summary(self, session_id: str, summary: str) -> None: ...
def index_artifact_summary(self, key: str) -> None: ...
def reindex_all_missing_embeddings(self, limit: int = 500) -> dict: ...
```

### Files

- `src/runtime/artifact_store.py`
- `src/main.py` (finalization hook for session summary)
- optionally `src/runtime/stages/synthesizer.py` (explicit summary handoff)

---

## Phase 3 — Retrieval Engine

### Objectives

1. Implement robust semantic retrieval API.
2. Prefer `sqlite-vec` when available.
3. Provide deterministic Python cosine fallback if extension unavailable.

### API

```python
def recall_sessions(self, query: str, top_k: int, threshold: float) -> list[SessionRecall]: ...
def recall_artifacts(self, query: str, top_k: int, threshold: float, project: str | None = None) -> list[ArtifactRecall]: ...
```

### Retrieval strategy

1. Encode query once.
2. If sqlite-vec loaded:
   - distance query in SQL against `session_summaries.embedding`
   - distance query in SQL against `artifacts.summary_embedding`
3. Else:
   - load candidate vectors and score via Python cosine
4. Apply threshold and return top-k ranked objects.

### Return shape

```python
@dataclass
class SessionRecall:
    session_id: str
    summary: str
    score: float
    created_at: float

@dataclass
class ArtifactRecall:
    key: str
    kind: str
    summary: str
    source: str
    session_id: str
    score: float
```

### Files

- `src/runtime/artifact_store.py`

---

## Phase 4 — `recall_sessions` Tool Integration

### Objectives

1. Expose Tier 3 recall to the model safely.
2. Keep tool output concise and context-efficient.
3. Route recall intents to artifacts toolset.

### New tool

`src/tools/implementations/artifacts/recall_sessions.py`

Inputs:
- `query` (required)
- `top_k` (optional)
- `include_artifacts` (optional; default true)
- `project` (optional)

Output:
- compact ranked list of relevant sessions and/or artifacts
- includes score + short summary excerpts + IDs/keys for follow-up

### Toolset updates

- Add tool to artifacts toolset in `src/tools/toolsets.py`
- Update planning note to mention historical recall
- Add routing keywords: `recall`, `previous sessions`, `have we done this before`, etc.

### Guard policy

- `ALLOW` by default (read-only)

### Files

- `src/tools/implementations/artifacts/recall_sessions.py`
- `src/tools/implementations/artifacts/__init__.py`
- `src/tools/toolsets.py`
- `src/runtime/guard.py` (no escalation rule expected)

---

## Phase 5 — Startup Memory Injection

### Objectives

1. Inject relevant prior context at session start/new query.
2. Keep injections bounded and non-disruptive.
3. Avoid repeating same recalled memory every turn.

### Behavior

At first user turn of a session (and optionally on resume):
- run `recall_sessions(query=user_message)`
- build compact block:

```text
[Prior related work]
- Session <id> (<age>): <summary excerpt>
- Artifact <key>: <summary excerpt>
[/Prior related work]
```

- insert block as synthetic system/user context message before planning
- enforce `max_injected_chars` budget

### State tracking

- track last injected recall hash in runtime state to prevent duplicate insertion
- only refresh if query drift is high or top results changed materially

### Files

- `src/agent.py` and/or `src/runtime/stages/routing.py`
- `src/runtime/pipeline_context.py` (optional field for recall block/hash)
- `src/runtime/context_manager.py` (injection budget handling)

---

## Phase 6 — Project Scoping and Pinned Memory

### Objectives

1. Add optional project partitioning for recall and artifacts.
2. Define pinned-memory behavior in decay + recall.
3. Keep backward compatibility for unscoped sessions.

### Project model

Use `artifact_tags` and session-level metadata tag:
- artifacts: `tag='project', value='<project_name>'`
- session summary rows infer project via associated artifacts or explicit session tag

### Retrieval filtering

- If `project.enabled` and active project set:
  - restrict recalls to matching project unless `project='*'` override
- Default project can be configured or prompted once per session

### Pinned behavior

- Pinned artifacts (`permanent=1`) are always eligible for recall
- Decay never archives pinned artifacts
- Optionally boost pinned artifacts during ranking (+score prior)

### Files

- `src/runtime/artifact_store.py`
- `src/main.py` (project selection/init hook)
- `src/tools/implementations/artifacts/store_artifact.py` (optional `project` tag input)
- `src/tools/implementations/artifacts/artifact_info.py` (display project tags)

---

## Data Contracts

### Embeddings

- Stored as float32 blob (`BLOB`) for both session and artifact summaries
- Query embedding uses the same model as indexing

### Recall thresholds

- score semantics must be documented (`cosine similarity` vs `distance`)
- tool output must show normalized score (0..1 where possible)

### Summary length policy

- Session summaries: target <= 1,200 chars
- Artifact summaries: existing Tier 1 summary format
- Recall output truncates per item to maintain context budget

---

## Failure Modes and Degradation

- sqlite-vec missing or load failure:
  - log warning, fallback to Python cosine retrieval
- embedding model unavailable:
  - skip indexing and retrieval; return graceful message
- malformed blobs:
  - skip row and continue retrieval
- oversized recall injection:
  - truncate by rank order until budget satisfied

---

## Testing Plan

### Unit-level

1. Schema migration adds `session_summaries` and artifact embedding column idempotently.
2. Session summary indexing writes deterministic row for given summary.
3. Artifact summary indexing updates `summary_embedding` only when summary exists.
4. Retrieval returns ranked results and respects threshold/top-k.
5. sqlite-vec path and Python fallback path produce consistent ordering on fixture data.
6. Project filter excludes cross-project rows unless wildcard override.
7. Pinned artifacts are not archived and remain recallable.

### Integration-level

1. Complete session -> summary indexed -> next session recall returns prior summary.
2. `recall_sessions` tool returns expected items and concise formatting.
3. Startup injection appears once and respects char budget.
4. Disable `artifact_store.rag.enabled` and verify no indexing/retrieval side effects.

---

## Out of Scope

- External hosted vector stores
- Multi-user shared memory ACLs
- Automatic workflow code generation from Tier 3 recall signals

---

## File Impact Summary

| File | Phase(s) | Change |
|------|----------|--------|
| `src/runtime/artifact_store.py` | 1-6 | Tier 3 schema, embedding index, retrieval engine, project filtering |
| `src/config.py` | 1 | `rag`, `sqlite_vec`, and `project` config models |
| `config.yml` | 1 | Tier 3 config knobs |
| `src/main.py` | 2,6 | summary indexing hook, optional project init |
| `src/agent.py` | 5 | startup recall injection orchestration |
| `src/runtime/context_manager.py` | 5 | recall block budget handling |
| `src/tools/implementations/artifacts/recall_sessions.py` | 4 | new tool |
| `src/tools/toolsets.py` | 4 | artifacts toolset update |

---

## Open Questions

Resolved:
1. Startup recall injection runs on both **new and resumed sessions** by default.
2. `recall_sessions` returns **sessions + artifact hits by default**.
3. Project scoping is **enabled by default**, with explicit user-facing visibility.
   Data model impact: no new table; uses existing `artifact_tags` rows (`project` tag).
4. Pinned memory gets a **ranking boost** and **slower decay/archive** than normal artifacts.
5. If `sqlite-vec` is unavailable, proceed without it (degrade gracefully).
