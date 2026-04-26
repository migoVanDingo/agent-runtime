# 0042 — Artifact Store & Long-Term Memory Design

## Overview

The artifact store is a session-scoped, persistable registry of named values
produced by tools during agent execution. It replaces ad-hoc path-passing
through step descriptions and provides the foundation for a three-tier memory
architecture: in-session state, cross-session persistence with decay, and
long-term semantic recall via RAG.

This document covers the full design across all three tiers: data model,
SQLite schema, session resumption, workflow discovery, cross-session RAG,
tool integration, and extension path.

---

## Motivation

### Problems today

1. **Paths embedded in plan text** — the entity critic tries to correct
   hallucinated paths in step descriptions, sometimes badly. The real fix
   is for step descriptions to reference artifact names, not raw paths.

2. **`read_url` quarantine path** — returned as a string in the tool result;
   the next step has to parse it out. Fragile and unnecessary.

3. **Dataframe state** — would be a one-off module-level dict without the
   store. With the store it's just another artifact kind.

4. **Injection expel** — "purge this fetched content" needs to know exactly
   what to clean up (file on disk, derived artifacts, any references in the
   conversation). The store is the authoritative manifest.

5. **Cross-step continuity** — step 3 produces `analysis_result`, step 7
   references it. Currently depends on the string content of step results
   surviving context compression. Artifact names are stable references.

6. **No session resumption** — sessions are fire-and-forget. Prior work is
   inaccessible in a new session unless the user restates it.

7. **No learned workflows** — recurring task patterns have to be
   hand-authored. The system never notices that it's done something 10 times
   and offers to formalize it.

8. **No cross-session recall** — the agent has no memory of prior sessions.
   Every session starts cold even if it's the fifth time touching the same
   binary or the same dataset.

---

## Three-Tier Memory Architecture

```
┌─────────────────────────────────────────────────────┐
│  Tier 1 — Session (in-memory, this process)         │
│  Artifact cache, active dataframes, tool results    │
│  Cleared on exit unless flushed                     │
├─────────────────────────────────────────────────────┤
│  Tier 2 — Cross-session (SQLite, on disk)           │
│  Artifact metadata + values, session history,       │
│  conversation resume state, workflow candidates,    │
│  decay scoring                                      │
├─────────────────────────────────────────────────────┤
│  Tier 3 — Long-term semantic (SQLite + sqlite-vec)  │
│  Session summaries indexed by embedding,            │
│  artifact summaries indexed by embedding,           │
│  RAG retrieval at session start or on demand,       │
│  pinned permanent artifacts                         │
└─────────────────────────────────────────────────────┘
```

All three tiers live in a single SQLite file. No external services.
The embedding model already loaded (`all-MiniLM-L6-v2`) handles Tier 3.
`sqlite-vec` adds vector similarity search as a SQLite extension.

---

## Directory Layout

```
_store/
  artifacts.db        ← single SQLite file (all three tiers)
  data/
    <key>.parquet     ← persisted dataframes
    <key>.txt         ← persisted text content (fetched pages, results)
    <key>.json        ← persisted JSON artifacts
```

Lives in the project root alongside `_logs/`, `_plans/`, `_metrics/`.

---

## SQLite Schema

### `artifacts` table — Tier 1/2

Primary record for each named artifact.

```sql
CREATE TABLE IF NOT EXISTS artifacts (
    key           TEXT    PRIMARY KEY,
    kind          TEXT    NOT NULL,
    value         TEXT,           -- inline for small values (< 4KB)
    summary       TEXT,           -- compressed description for large values
    source        TEXT,           -- origin: file path, url, tool name
    data_path     TEXT,           -- path to large value file in _store/data/
    session_id    TEXT    NOT NULL,
    created_at    REAL    NOT NULL,
    last_accessed REAL    NOT NULL,
    access_count  INTEGER NOT NULL DEFAULT 0,
    decay_score   REAL    NOT NULL DEFAULT 1.0,
    permanent     INTEGER NOT NULL DEFAULT 0  -- 1 = survives decay (pinned)
);
```

**kind values:** `file`, `dataframe`, `path`, `string`, `result`, `url_content`

**value vs data_path:** Exactly one is set.
- `value` — small artifacts under `inline_threshold_bytes` (~4KB). Inline in SQLite.
- `data_path` — large artifacts. File lives in `_store/data/`. Parquet for
  dataframes, plain text for fetched content, JSON for everything else.

**summary:** Always set for large artifacts. Human-readable compressed description
the LLM can read without loading the value — dataframes get shape + columns +
dtypes + 3 sample rows; text gets char count + first 300 chars.

**decay_score:** Starts at 1.0. Multiplied by a configurable factor each session
it isn't accessed. Below threshold → archived. `permanent=1` bypasses decay.

### `artifact_sessions` table — Tier 2

Audit log of all artifact interactions across sessions.

```sql
CREATE TABLE IF NOT EXISTS artifact_sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT    NOT NULL,
    key         TEXT    NOT NULL,
    action      TEXT    NOT NULL,  -- 'create', 'read', 'update', 'expel'
    accessed_at REAL    NOT NULL
);
```

### `artifact_tags` table — Tier 2

Key-value tags for filtering and grouping. Supports future project scoping.

```sql
CREATE TABLE IF NOT EXISTS artifact_tags (
    key   TEXT NOT NULL,
    tag   TEXT NOT NULL,
    value TEXT,
    PRIMARY KEY (key, tag)
);
```

### `sessions` table — Tier 2

One row per agent session. Supports resumption via `ended_at IS NULL`.

```sql
CREATE TABLE IF NOT EXISTS sessions (
    session_id        TEXT    PRIMARY KEY,
    started_at        REAL    NOT NULL,
    ended_at          REAL,               -- NULL means resumable
    artifact_count    INTEGER DEFAULT 0,
    summary           TEXT,               -- synthesizer output for this session
    summary_embedding BLOB,               -- embedding of summary (Tier 3)
    resumable         INTEGER DEFAULT 1   -- 0 = explicitly closed, not resumable
);
```

`ended_at IS NULL AND resumable = 1` → session is in progress or detached and
can be resumed. `ended_at IS NOT NULL` → cleanly closed.

### `conversation_history` table — Tier 2

Persisted messenger state for session resumption.

```sql
CREATE TABLE IF NOT EXISTS conversation_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT    NOT NULL,
    role       TEXT    NOT NULL,   -- 'user', 'assistant', 'tool_result'
    content    TEXT    NOT NULL,   -- JSON-serialized message content
    turn       INTEGER NOT NULL,
    created_at REAL    NOT NULL
);
```

On resume: load rows for the target session ordered by `turn`, re-hydrate the
messenger. The context manager's compression has already been applied — we
store the compressed form, not the raw history.

### `requests` table — Tier 2/3 (workflow discovery input)

Every user message stored with its embedding for clustering.

```sql
CREATE TABLE IF NOT EXISTS requests (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT    NOT NULL,
    message    TEXT    NOT NULL,
    embedding  BLOB,               -- float32 array, for workflow discovery clustering
    workflow   TEXT,               -- matched workflow name if any, else NULL
    created_at REAL    NOT NULL
);
```

### `workflow_candidates` table — Tier 2

Discovered workflow patterns that crossed the frequency+recency threshold.

```sql
CREATE TABLE IF NOT EXISTS workflow_candidates (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT,               -- proposed workflow name (null until approved)
    description   TEXT    NOT NULL,   -- generated description
    example_ids   TEXT    NOT NULL,   -- JSON array of request IDs that formed the cluster
    frequency     INTEGER NOT NULL,
    last_seen     REAL    NOT NULL,
    recency_score REAL    NOT NULL,   -- frequency weighted by recency
    status        TEXT    NOT NULL DEFAULT 'candidate',  -- candidate | approved | rejected
    approved_at   REAL
);
```

Approved candidates get written to `src/workflows/` as proper workflow
definitions and picked up by `WorkflowMatcher` on next session start.

### `session_summaries` table — Tier 3 (RAG corpus)

One row per session that produced a synthesizer summary. The primary input
for cross-session semantic recall.

```sql
CREATE TABLE IF NOT EXISTS session_summaries (
    session_id TEXT    PRIMARY KEY,
    summary    TEXT    NOT NULL,
    embedding  BLOB    NOT NULL,   -- float32 array from all-MiniLM-L6-v2
    created_at REAL    NOT NULL
);
```

### `artifacts` embedding column — Tier 3

The `artifacts` table gains a `summary_embedding` column for semantic search
over artifact summaries. Added when Tier 3 is activated — schema migration is
a single `ALTER TABLE`.

```sql
ALTER TABLE artifacts ADD COLUMN summary_embedding BLOB;
```

---

## Session Resumption

### How it works

A session is a named context, not just a process lifetime. Sessions can be:
- **Active** — process is running
- **Detached** — process ended cleanly, `ended_at IS NULL` (in-progress work
  preserved, resumable)
- **Closed** — `ended_at IS NOT NULL, resumable = 0` (explicitly finished)

The CLI gains a `--resume` flag:

```
python main.py                  # new session
python main.py --resume         # resume most recent detached session
python main.py --resume <id>    # resume specific session by ID
```

### Resume flow

1. Look up the target session in `sessions` table
2. Load conversation history from `conversation_history` ordered by `turn`
3. Re-hydrate the `Messenger` with the loaded messages
4. Load artifact metadata for that session (artifacts with that `session_id`)
5. Set the active session ID to the resumed ID (so new work appends to it)
6. Print a resume banner showing session ID, original start time, artifact count

### Detach vs close

Normal `exit`/`quit` → `flush()` → session is detached (resumable).
Explicit close (future `/close` command) → sets `resumable = 0`, `ended_at`.
Ctrl+C (KeyboardInterrupt) → same as normal exit, session is detached.

This matches the Claude Code model: you can always come back to where you were.

---

## Workflow Discovery

### The signal

Every user message is stored in `requests` with its embedding. At session
start (after loading), a clustering pass runs over recent requests:

1. Load embeddings from `requests` for the past N days (configurable, default 30)
2. Cluster by cosine similarity above a threshold (default 0.82)
3. For each cluster with frequency ≥ threshold AND recency_score ≥ threshold:
   - Check if an equivalent workflow already exists in `WorkflowMatcher`
   - If not, insert a row into `workflow_candidates`
4. Surface pending candidates to the user as a notification (not blocking):
   `"I've noticed a recurring pattern. Would you like to formalize it as a workflow?"`

### Recency-weighted scoring

Raw frequency alone is weak signal — 20 occurrences over 2 years is weaker
than 5 occurrences this week.

```python
recency_score = sum(
    1.0 * (decay_factor ** days_ago(r.created_at))
    for r in cluster_requests
)
```

Default `decay_factor = 0.95` (per day). A cluster that appeared 5 times in
the last week scores ~4.8. The same 5 occurrences spread over a month score ~3.2.

### Approval flow

Candidates require approval before becoming real workflows. The agent surfaces
them interactively: shows the description + example requests, user says yes/no.

Approved → `status = 'approved'`, written to `src/workflows/<name>.py` as a
workflow definition. `WorkflowMatcher` picks it up on next session start.
Rejected → `status = 'rejected'`, never surfaced again.

---

## Cross-Session RAG (Tier 3)

### What gets indexed

- **Session summaries** — the synthesizer's final response for each session.
  Best signal: human-readable, already compressed, captures what happened.
- **Artifact summaries** — the `summary` field for large artifacts. Lets the
  LLM find prior dataframes, fetched pages, analysis results by description.

### Retrieval

At session start, if there's a current user message (e.g. `--resume` context
or first message), run a semantic search over `session_summaries` and return
the top-K most similar prior sessions. These get injected into the context
manager's initial state as compressed prior context — not full conversation
history, just the summary.

The LLM sees something like:
```
[Prior session context — 3 related sessions found]
Session SES01KQ2N (3 days ago): Analyzed proc-synth binary, identified main
  function structure, wrote disassembly summary to _tests/run_4/proc-summary.md
Session SES01KQ2R (5 days ago): Deep disassembly of proc-synth, reconstructed
  C source, wrote proc_analysis_new.c
...
[End prior context]
```

On-demand retrieval is also available via a `recall` tool — "what do I know
about proc-synth from previous sessions?" triggers a semantic search and
returns the relevant session summaries.

### sqlite-vec

`sqlite-vec` adds vector similarity search as a loadable SQLite extension.
Vectors are stored as BLOB columns, queried with:

```sql
SELECT key, summary,
       vec_distance_cosine(summary_embedding, ?) AS distance
FROM artifacts
WHERE summary_embedding IS NOT NULL
ORDER BY distance
LIMIT 10;
```

No external vector database. One file. The same embedding model already
running for the static router handles encoding.

---

## ArtifactStore Class

**File:** `src/runtime/artifact_store.py`

```python
class ArtifactStore:
    def __init__(self, db_path: Path, data_dir: Path, session_id: str): ...

    # Core CRUD
    def set(self, key: str, value, kind: str, source: str = "") -> ArtifactMeta: ...
    def get(self, key: str)                                        -> Any: ...
    def meta(self, key: str)                                       -> ArtifactMeta | None: ...
    def list(self, kind: str | None = None)                        -> list[ArtifactMeta]: ...
    def expel(self, key: str)                                      -> bool: ...
    def expel_pattern(self, pattern: str)                          -> list[str]: ...
    def pin(self, key: str)                                        -> None: ...

    # Session lifecycle
    def load_session(self, resume_id: str | None = None) -> None: ...
    def flush(self, summary: str | None = None)          -> None: ...

    # Decay
    def apply_decay(self, factor: float = 0.85, threshold: float = 0.1) -> list[str]: ...

    # Workflow discovery
    def record_request(self, message: str, workflow: str | None) -> None: ...
    def discover_workflows(self) -> list[WorkflowCandidate]: ...

    # Tier 3 — RAG (activated when sqlite-vec available)
    def recall(self, query: str, top_k: int = 3) -> list[SessionRecall]: ...
    def index_summary(self, summary: str) -> None: ...
```

**ArtifactMeta dataclass:**
```python
@dataclass
class ArtifactMeta:
    key:           str
    kind:          str
    summary:       str
    source:        str
    session_id:    str
    created_at:    float
    last_accessed: float
    access_count:  int
    decay_score:   float
    permanent:     bool
    has_value:     bool
    has_data_path: bool
```

### Storage routing

```python
INLINE_THRESHOLD = 4096  # bytes

def _store_value(self, key, value, kind):
    if kind == "dataframe":
        path = self._data_dir / f"{key}.parquet"
        value.to_parquet(path)
        return None, str(path), self._df_summary(value)

    serialized = self._serialize(value)
    if len(serialized) <= INLINE_THRESHOLD:
        return serialized, None, ""

    ext = {"url_content": "txt", "result": "txt"}.get(kind, "json")
    path = self._data_dir / f"{key}.{ext}"
    path.write_text(serialized)
    return None, str(path), self._text_summary(serialized)
```

### Session lifecycle

**At session start (`load_session`):**
1. Register or resume session in `sessions` table
2. Load artifact metadata into in-memory cache (no large values yet)
3. Apply decay to artifacts not accessed in this session
4. Run workflow discovery clustering (async, non-blocking)
5. If Tier 3 active: run RAG retrieval for initial context injection

**At session end (`flush`):**
1. Write dirty artifacts to SQLite
2. Write `artifact_sessions` rows
3. Write `conversation_history` rows (compressed messenger state)
4. If summary provided: write to `session_summaries`, compute + store embedding
5. Update `sessions.ended_at = NULL` (detached/resumable) or set timestamp

---

## Integration Points

### `main.py` — session ID threading

The session ID is generated in `main.py` and must flow to the artifact store.
`--resume` flag changes the flow: look up the prior session ID, pass it to
`Agent.__init__`.

```python
session_id = generate_id("session") if not args.resume else resolve_resume(args.resume)
agent = Agent(verbose=args.verbose, session_id=session_id)
```

### `agent.py` — store construction

```python
self.artifact_store = ArtifactStore(
    db_path=PROJECT_ROOT / "_store" / "artifacts.db",
    data_dir=PROJECT_ROOT / "_store" / "data",
    session_id=session_id,
)
self.artifact_store.load_session(resume_id=resume_id)
```

On exit:
```python
summary = context.response  # last synthesizer output if available
agent.artifact_store.flush(summary=summary)
```

### `read_url` tool

```python
key = f"fetched_{url_hash}"
store.set(key, content, kind="url_content", source=url)
return f"Fetched content stored as artifact '{key}'\nSize: {char_count} chars\nPreview: {preview}..."
```

Injection expel becomes: `store.expel(key)` — removes SQLite row, deletes
`_store/data/<key>.txt`. One call, complete cleanup.

### Dataframe tools

```python
# dataframe_load
store.set("sales_data", df, kind="dataframe", source="sales_q1.csv")

# dataframe_query
df = store.get("sales_data")
```

### Stages that need the store

The store is passed into `ExecutionStage` and `DirectExecutionStage` via the
`Agent._build_pipeline()` constructor. Stages that execute tools pass it to
tool implementations that register artifacts (or tools call
`get_artifact_store()` via a module-level accessor — same pattern as
`get_tracker()`).

---

## Artifact Tools (exposed to LLM)

New `artifacts` toolset:

| Tool | Purpose |
|------|---------|
| `list_artifacts` | List all artifacts with kind + summary |
| `get_artifact` | Retrieve artifact value by key |
| `store_artifact` | Manually store a string/path as a named artifact |
| `expel_artifact` | Delete artifact and associated files |
| `artifact_info` | Get metadata without loading value |
| `recall_sessions` | Semantic search over prior session summaries (Tier 3) |

`planning_note`: "Use these to manage named values across steps. Store results
you'll need in later steps by name. Use recall_sessions to find relevant prior
work on a topic."

---

## Plan Schema Integration

`Step` gains an optional `produces` field — the artifact key this step is
expected to register. Non-breaking: existing plans without it work unchanged.

```json
{
  "step": 1,
  "description": "Fetch the paper at https://arxiv.org/abs/2604.21928",
  "action_type": "web",
  "tool": "read_url",
  "produces": "paper_content",
  "flags": {"retry": false, "escalate": false, "defer": false}
}
```

After step completion, the executor checks if `produces` was registered.
If not, logs a warning. The monitor can use this as a signal for replan.

---

## Configuration

```yaml
artifact_store:
  enabled: true
  db_path: "_store/artifacts.db"
  data_dir: "_store/data"
  inline_threshold_bytes: 4096
  decay:
    enabled: true
    factor: 0.85            # multiplied per session not accessed
    archive_threshold: 0.1  # below this → archived
  workflow_discovery:
    enabled: true
    lookback_days: 30
    similarity_threshold: 0.82
    frequency_threshold: 5
    recency_decay: 0.95     # per-day decay for recency scoring
  rag:
    enabled: false          # enabled when sqlite-vec available
    top_k: 3
    similarity_threshold: 0.6
```

---

## Extensibility

### New artifact kinds
Add a `kind` string. Storage routing handles it via JSON fallback. No schema
changes.

### Project scoping (Tier 3)
Add a `project` tag via `artifact_tags`. Filter all queries by project tag.
Lets the agent maintain separate artifact namespaces for different codebases
or long-running initiatives — similar to ChatGPT Projects, but automatic.

### Shared artifact stores (multi-machine)
Replace the SQLite file with a remote SQLite-compatible backend (Turso, Litestream
replication). The `ArtifactStore` interface doesn't change — only the db_path
configuration. The `_store/data/` files would need syncing separately (rsync,
object storage).

### Permanent artifacts (Tier 3)
`store.pin("target_binary")` sets `permanent = 1`. Pinned artifacts survive
decay and are always loaded at session start regardless of last_accessed.
Useful for known reference points in a long-running project.

---

## Build Phases

| Phase | Scope | Tier |
|-------|-------|------|
| 1 | `ArtifactStore` class — SQLite schema, in-memory cache, core CRUD, `load_session/flush` | 1 |
| 2 | Wire into `agent.py` + `main.py` — session ID threading, store construction | 1 |
| 3 | Wire `read_url` — store quarantine as artifact, expel on rejection | 1 |
| 4 | `data` toolset — `dataframe_load`, `dataframe_query` on top of store | 1 |
| 5 | Artifact tools — `list_artifacts`, `get_artifact`, `store_artifact`, `expel_artifact` | 1 |
| 6 | Plan schema `produces` field — optional, non-breaking | 1 |
| 7 | Session persistence — `conversation_history` table, `--resume` CLI flag | 2 |
| 8 | Decay + cross-session artifact loading | 2 |
| 9 | Workflow discovery — `requests` table, clustering, candidate surfacing | 2 |
| 10 | Session summaries + RAG — `session_summaries` table, `sqlite-vec`, `recall_sessions` tool | 3 |
| 11 | Project scoping + pinned artifacts | 3 |

Phases 1–6 are the immediate build (artifact store + data toolset).
Phases 7–9 are Tier 2 (cross-session persistence, session resumption, workflow learning).
Phases 10–11 are Tier 3 (semantic recall, project memory).
