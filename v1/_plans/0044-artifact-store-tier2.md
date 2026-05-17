# 0044 — Artifact Store Tier 2: Cross-Session Persistence

## Scope

Tier 2 extends the Tier 1 artifact store from in-session state to cross-session
state and recovery.

This tier covers:
- Session resumption (`--resume`) with persisted conversation state
- Cross-session artifact loading and decay scoring
- Request logging and workflow discovery candidates

This tier does **not** include Tier 3 semantic recall/RAG (`session_summaries`,
`sqlite-vec`, `recall_sessions` tool).

---

## Assumptions

- Tier 1 (`0043`) is already merged and stable:
  - `ArtifactStore` singleton exists and is initialized in `main.py`
  - `read_url` stores fetched content as artifacts
  - data/artifact toolsets exist
  - plan schema already supports `produces`
- Existing runtime stack (pipeline stages, messenger, context manager,
  workflow matcher, embedding model) remains in use.

---

## Tier 2 Phases

| Phase | Goal |
|------|------|
| 1 | Add Tier 2 schema/config + migration/indexes |
| 2 | Persist/restore conversation + session lifecycle semantics |
| 3 | Add CLI resume flow and agent startup hydration |
| 4 | Cross-session artifact loading + decay pass |
| 5 | Request logging + workflow candidate discovery |
| 6 | Candidate surfacing + approval/rejection state flow |

---

## Phase 1 — Schema, Config, and Migration Layer

### Objectives

1. Add Tier 2 tables:
   - `conversation_history`
   - `requests`
   - `workflow_candidates`
   - `artifact_tags`
2. Keep all migrations idempotent and backward-compatible.
3. Add indexes required for lookup and startup performance.
4. Extend config model for Tier 2 knobs.

### Schema additions

```sql
CREATE TABLE IF NOT EXISTS conversation_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT    NOT NULL,
    role       TEXT    NOT NULL,
    content    TEXT    NOT NULL,
    turn       INTEGER NOT NULL,
    created_at REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS requests (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT    NOT NULL,
    message    TEXT    NOT NULL,
    embedding  BLOB,
    workflow   TEXT,
    created_at REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS workflow_candidates (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT,
    description   TEXT    NOT NULL,
    example_ids   TEXT    NOT NULL,
    frequency     INTEGER NOT NULL,
    last_seen     REAL    NOT NULL,
    recency_score REAL    NOT NULL,
    status        TEXT    NOT NULL DEFAULT 'candidate',
    approved_at   REAL
);

CREATE TABLE IF NOT EXISTS artifact_tags (
    key   TEXT NOT NULL,
    tag   TEXT NOT NULL,
    value TEXT,
    PRIMARY KEY (key, tag)
);
```

### Indexes

```sql
CREATE INDEX IF NOT EXISTS idx_artifacts_session_id ON artifacts(session_id);
CREATE INDEX IF NOT EXISTS idx_artifact_sessions_session_id ON artifact_sessions(session_id);
CREATE INDEX IF NOT EXISTS idx_conversation_history_session_turn ON conversation_history(session_id, turn);
CREATE INDEX IF NOT EXISTS idx_requests_created_at ON requests(created_at);
CREATE INDEX IF NOT EXISTS idx_requests_session_id ON requests(session_id);
CREATE INDEX IF NOT EXISTS idx_workflow_candidates_status_last_seen ON workflow_candidates(status, last_seen);
CREATE INDEX IF NOT EXISTS idx_sessions_resumable_ended ON sessions(resumable, ended_at, started_at);
```

### Config additions

Add to `config.yml`:

```yaml
artifact_store:
  enabled: true
  inline_threshold_bytes: 4096
  decay:
    enabled: true
    factor: 0.85
    archive_threshold: 0.1
  workflow_discovery:
    enabled: true
    lookback_days: 30
    similarity_threshold: 0.82
    frequency_threshold: 5
    recency_decay: 0.95
```

Add typed config in `src/config.py`:
- `ArtifactStoreDecayConfig`
- `ArtifactStoreWorkflowDiscoveryConfig`
- expand `ArtifactStoreConfig`

### Files

- `src/runtime/artifact_store.py`
- `src/config.py`
- `config.yml`

---

## Phase 2 — Session Persistence and Conversation History

### Objectives

1. Persist compressed messenger history on shutdown.
2. Restore messenger history on resume.
3. Clarify detached vs closed session semantics in DB updates.

### ArtifactStore API additions

```python
def load_session(self, resume_id: str | None = None) -> str: ...
def save_conversation(self, messages: list[dict]) -> int: ...
def load_conversation(self, session_id: str) -> list[dict]: ...
def mark_detached(self, session_id: str) -> None: ...
def mark_closed(self, session_id: str) -> None: ...
```

### Lifecycle semantics

- Normal exit (`quit`/`exit`/Ctrl-C):
  - flush dirty artifacts
  - persist conversation history
  - mark session as detached (`ended_at=NULL`, `resumable=1`)
- Explicit close (future command; stubbed API now):
  - mark session closed (`ended_at=now`, `resumable=0`)

### Conversation persistence contract

- Store post-compression messenger messages (same serialized structure already
  used by provider calls).
- Preserve order with `turn` index.
- On save, replace prior rows for that session to avoid duplicate turn growth
  across repeated flushes.

### Files

- `src/runtime/artifact_store.py`
- `src/agent.py` (hook save/load calls)

---

## Phase 3 — Resume CLI and Startup Hydration

### Objectives

1. Add `--resume` flag support in `main.py`.
2. Resolve target session via interactive picker (or explicit ID override).
3. Hydrate agent messenger and active session identity before first turn.

### CLI behavior

```bash
python src/main.py                # new session
python src/main.py --resume       # interactive picker of resumable sessions
python src/main.py --resume <id>  # resume explicit session ID
```

### Resolution rules

- `--resume` (no ID): query resumable sessions (`resumable=1`, `ended_at IS NULL`)
  ordered by `started_at DESC`, then present an interactive numbered list.
- each list row must include human-readable context, not only ID:
  - first user message preview from `conversation_history` (preferred)
  - fallback: stored session summary excerpt (if available)
  - fallback: `(no preview available)` + timestamp/artifact count
- user selects by list index (e.g. `1`, `2`, `3`), not by typing session ID.
- explicit ID is still supported as advanced/manual override.
- if stdin is non-interactive (non-TTY), fallback to latest resumable session
  and print which one was selected.
- explicit ID: validate session exists and resumable.
- if not found: fail fast with clear message and exit code 1.

### Resume picker UX

Example:

```text
Resumable sessions:
1) Apr 25 19:42  |  “summarize this arxiv paper on inference-time compute...”  | artifacts: 4
2) Apr 24 11:03  |  “analyze /tmp/proc-synth and write a markdown report...”    | artifacts: 9
3) Apr 21 08:15  |  “(no preview available)”                                      | artifacts: 2

Select session to resume [1-3] (Enter=1, q=cancel):
```

Selection rules:
- empty input defaults to `1` (most recent)
- `q` aborts resume and exits cleanly
- invalid input reprompts up to a small max retry count, then exits with error

### Hydration flow

1. Build/init `ArtifactStore`
2. `load_session(resume_id=...)`
3. Load persisted conversation via `load_conversation()`
4. Seed `Messenger` before pipeline starts
5. Print resume banner (session ID, artifact count, started time)

### Files

- `src/main.py`
- `src/agent.py`
- `src/runtime/artifact_store.py`

---

## Phase 4 — Cross-Session Artifact Loading and Decay

### Objectives

1. Load artifact metadata for resumed session.
2. Apply decay scoring for non-permanent artifacts not accessed in current
   session.
3. Mark archived artifacts using tags (non-destructive), not hard delete.

### Behavior

- Resume path loads artifact metadata for `session_id=<active>` into in-memory
  metadata cache; values remain lazy-loaded.
- Decay pass runs once at session startup if enabled.
- Skip decay for `permanent=1` artifacts.
- If `decay_score < archive_threshold`, set tag:
  - `tag='archived'`, `value='1'`

### Why tag-based archive

`artifacts` schema currently has no dedicated `archived` column. Using
`artifact_tags` keeps migration small and reversible while preserving data.

### API additions

```python
def load_artifact_meta_for_session(self, session_id: str) -> int: ...
def apply_decay(self, factor: float, threshold: float) -> list[str]: ...
def set_tag(self, key: str, tag: str, value: str = "1") -> None: ...
def get_tag(self, key: str, tag: str) -> str | None: ...
```

### Files

- `src/runtime/artifact_store.py`
- `src/config.py`
- `config.yml`

---

## Phase 5 — Request Logging and Workflow Candidate Discovery

### Objectives

1. Record every user request into `requests` with embedding and matched workflow.
2. Discover recurring clusters from recent requests.
3. Persist candidate rows in `workflow_candidates` with recency-weighted score.

### Request logging

- Log on each user turn after routing/workflow selection is known.
- Store fields:
  - `session_id`
  - raw user message
  - embedding from existing sentence-transformer model
  - selected workflow name (or `NULL`)
  - timestamp

### Discovery algorithm (startup pass)

Inputs from config:
- `lookback_days`
- `similarity_threshold`
- `frequency_threshold`
- `recency_decay`

Flow:
1. Load requests within lookback window.
2. Build similarity graph (`cosine >= threshold`).
3. Extract connected clusters.
4. For each cluster:
   - `frequency = len(cluster)`
   - `recency_score = sum(recency_decay ** days_ago(msg))`
   - require `frequency >= frequency_threshold`
5. Skip if semantically equivalent approved candidate already exists.
6. Insert/update candidate with `status='candidate'`.

### Candidate dedupe

Use normalized fingerprint based on nearest-neighbor exemplar IDs and
cluster centroid hash to avoid repeated inserts on every startup.

### Files

- `src/runtime/artifact_store.py`
- `src/agent.py` and/or `src/runtime/stages/routing.py` (where request logging hook is best placed)
- possibly `src/workflows/matcher.py` (equivalence helper)

---

## Phase 6 — Candidate Surfacing and Approval Lifecycle

### Objectives

1. Surface pending candidates to user non-blockingly at startup.
2. Persist user decision (approved/rejected).
3. Keep Tier 2 scope: persist status and metadata; workflow code generation is
   optional and can be deferred behind a feature flag.

### UX behavior

At startup (after resume/new-session init):
- if pending candidates exist, print compact notice:
  - candidate description
  - frequency and recency score
  - example request snippets
- prompt yes/no per candidate in CLI

### Decision handling

- approve:
  - `status='approved'`, `approved_at=now`
- reject:
  - `status='rejected'`
- never re-surface rejected candidates

### Optional flag for auto workflow materialization

Add config gate:

```yaml
artifact_store:
  workflow_discovery:
    materialize_on_approve: false
```

If enabled later, approval path can call a scaffold writer to create
`src/workflows/implementations/<generated>.py` and register it.

### Files

- `src/runtime/artifact_store.py`
- `src/main.py` or `src/agent.py` (startup candidate prompt)
- `src/config.py`
- `config.yml`

---

## Data Contracts

### `requests.workflow`

- store exact matched workflow name from current turn if available
- `NULL` when no workflow matched

### `conversation_history.content`

- JSON string containing the existing message block format
- no schema transformation in Tier 2

### `workflow_candidates.example_ids`

- JSON array of request IDs, max 5 exemplars
- all IDs must reference rows in `requests`

---

## Failure Modes and Safety

- Resume target not found: explicit error and do not silently start new session.
- Corrupt conversation row JSON: skip row, log warning, continue loading.
- Missing embedding model or embedding failure: still write request row with
  `embedding=NULL`; candidate discovery skips null embeddings.
- Decay pass errors: log and continue startup; never block user turn.
- Candidate prompt interruption (Ctrl-C): leave candidates untouched and continue
  next startup.

---

## Testing Plan

### Unit-level

1. Schema init is idempotent across repeated starts.
2. `save_conversation` + `load_conversation` round-trip fidelity.
3. resume picker builds ordered rows with preview text and maps selection index
   to session ID correctly.
4. Decay updates score and archives via tag when below threshold.
5. Request logging stores workflow label and timestamp.
6. Discovery inserts only candidate rows meeting frequency/recency thresholds.

### Integration-level

1. Start session, execute turns, exit, resume, verify conversation and artifacts
   are restored.
2. `--resume` interactive path shows list with previews and resumes selected row.
3. Multiple sessions with old artifacts: verify decay progression across starts.
4. Repeated similar requests across sessions: candidate appears once and is
   not duplicated.
5. Reject candidate, restart, confirm it is not re-surfaced.

---

## Out of Scope (Tier 3)

- `session_summaries` table and summary embedding index
- `artifacts.summary_embedding`
- `sqlite-vec` extension loading and vector queries
- `recall_sessions` tool
- automatic context injection from semantic retrieval

---

## File Impact Summary

| File | Phase(s) | Change |
|------|----------|--------|
| `src/runtime/artifact_store.py` | 1-6 | Tier 2 schema, resume, history persistence, decay, request logging, discovery, candidate state |
| `src/config.py` | 1,4,6 | Tier 2 artifact-store config models |
| `config.yml` | 1,4,6 | decay + workflow_discovery settings |
| `src/main.py` | 3,6 | `--resume` flow + candidate prompt |
| `src/agent.py` | 2,3,5 | messenger hydration, lifecycle hooks, request logging hook |
| `src/runtime/stages/routing.py` (optional hook point) | 5 | capture selected workflow label for request logging |
| `src/workflows/matcher.py` (optional) | 5 | workflow-equivalence helper for candidate dedupe |

---

## Rollout Notes

- Implement phases sequentially; each phase should land green and runnable.
- Phase 3 should be gated behind robust resume validation before enabling by
  default in docs.
- Keep Tier 2 strictly additive and backward-compatible with existing Tier 1 DB.
