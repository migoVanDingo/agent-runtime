# 0081 — Dynamic Two-Tier RAG with LanceDB

## Context

The existing RAG is a single SQLite store that brute-force scans all embeddings at session
startup. It indexes only coarse session summaries and cannot retrieve specific content from
prior analysis work. It is being replaced entirely — the SQLite embedding tables are dev
data and will be dropped.

The replacement is a two-tier LanceDB system with three design constraints that shape every
decision in this plan:

1. **Start fresh locally, migrate to GCS with a config change.** LanceDB supports both
   local paths and `gs://` URIs natively. The storage URI is the only thing that changes
   between environments.

2. **The RAG layer is a service from day one.** It runs in-process locally, but the
   interface is defined as a service contract so that when the RAG gets its own container,
   the agent swaps `LocalRagService` for `HttpRagService` with a config flag. No other
   code changes.

3. **Embeddings are provider-swappable.** `sentence_transformers` is the local default.
   OpenAI, Gemini, or any other embedding API slots in via config. The RAG layer never
   calls an embedding model directly.

---

## Architecture

### Two tiers, two purposes

```
Tier 1 — Global Warehouse  (_rag/global/)
  One document per session. Coarse. Fast.
  Purpose: "have we seen something like this before?"
  Schema: session_id, summary, vector, binary_name, project, timestamp, tags

Tier 2 — Session Chunk Stores  (_rag/sessions/<session_id>/)
  One LanceDB dataset per session. Fine-grained. Grows during the session.
  Purpose: "what exactly did we find in session X about key derivation?"
  Schema: chunk_id, text, vector, source_file, offset, binary_name, session_id, timestamp
```

### Query flow — runs on every turn

```
User sends message
        │
        ▼
1. Embed query text
        │
        ├─► Query Tier 2 (current session, always) → top-k relevant chunks
        │
        └─► Query Tier 1 (global warehouse) → top-k past sessions above threshold
                    │
                    └─► For each hit: query that session's Tier 2 → precise excerpts
                                │
                                ▼
        Merge → score = similarity × recency_decay → fill budget greedily
                                │
                                ▼
        Inject as [Historical Context] block in system prompt
```

### Service boundary — the core structural decision

```
Agent code
    │
    │  calls only this interface
    ▼
RagService (abstract base)
    ├── LocalRagService     ← LanceDB in-process, used now
    └── HttpRagService      ← HTTP client to a containerized RAG service, used later

init_rag_service(config) → LocalRagService | HttpRagService
```

`get_rag_service()` is the only import the rest of the codebase uses. The concrete
implementation is an internal detail. Switching to the containerized version is:
```yaml
rag:
  mode: http
  http_base_url: http://rag-service:8080
```

### Storage abstraction — the only thing that changes for GCS

```
Environment      storage.base_uri        Tier 1 URI
─────────────────────────────────────────────────────────
Local (now)      ""                      _rag/global/
GCS (later)      gs://bucket             gs://bucket/rag/global/
```

`session_paths.py` resolves all URIs. LanceDB accepts both forms identically.

### Embedding abstraction — provider-swappable

```
RagService
    │
    └── Embedder (abstract)
            ├── SentenceTransformerEmbedder   ← default, local
            ├── OpenAIEmbedder                ← api call
            └── GeminiEmbedder                ← api call

Config: rag.embedding_provider + rag.embedding_model
```

`Embedder.embed(text: str) -> list[float]` is the only method. The RAG layer never
touches a model or API client directly.

---

## Directory Layout

```
_rag/
  global/                       ← Tier 1 LanceDB dataset
  sessions/
    <session_id>/               ← Tier 2 LanceDB dataset, one per session
    <session_id>/
    ...

_sessions/                      ← operational data: logs, metrics, events (plan 0080)
_analysis/                      ← raw tool output artifacts (plan 0080)
```

---

## Phases

---

### Phase A — Foundation: RagService interface + LocalRagService + Embedder

This phase builds the skeleton that every subsequent phase plugs into.

**New package**: `src/rag/`

```
src/rag/
  __init__.py      exports: RagService, get_rag_service, init_rag_service
  service.py       RagService abstract base class
  local.py         LocalRagService — LanceDB in-process implementation
  http.py          HttpRagService — HTTP client stub (interface only, not yet functional)
  embedder.py      Embedder base + SentenceTransformerEmbedder + factory
  schema.py        Chunk, SessionHit, ChunkHit dataclasses
  chunker.py       chunk_text(text, size=1500, overlap=300) -> list[Chunk]
```

**`src/rag/service.py`** — abstract interface:
```python
from abc import ABC, abstractmethod
from rag.schema import Chunk, SessionHit, ChunkHit

class RagService(ABC):
    @abstractmethod
    def index_session(self, session_id: str, summary: str, metadata: dict) -> None: ...
    @abstractmethod
    def index_chunks(self, session_id: str, chunks: list[Chunk]) -> None: ...
    @abstractmethod
    def query_global(self, query: str, top_k: int, threshold: float) -> list[SessionHit]: ...
    @abstractmethod
    def query_session(self, session_id: str, query: str, top_k: int, threshold: float) -> list[ChunkHit]: ...
    @abstractmethod
    def build_context_block(self, query: str, current_session_id: str, budget_chars: int) -> str: ...
```

**`src/rag/http.py`** — stub that raises `NotImplementedError` on every method with a
clear message: "HttpRagService is not yet implemented. Set rag.mode=local in config."
The stub exists so the import path and config plumbing are wired from day one — no
surprises when it's time to implement it.

**`src/rag/embedder.py`** — factory function:
```python
def get_embedder(provider: str, model: str) -> Embedder:
    if provider == "sentence_transformers":
        return SentenceTransformerEmbedder(model)
    elif provider == "openai":
        return OpenAIEmbedder(model)
    elif provider == "gemini":
        return GeminiEmbedder(model)
    raise ValueError(f"unknown embedding provider: {provider}")
```

**`src/rag/__init__.py`** — singleton pattern:
```python
_service: RagService | None = None

def init_rag_service(session_id: str) -> RagService:
    # reads config, instantiates LocalRagService or HttpRagService
    ...

def get_rag_service() -> RagService | None:
    return _service
```

**`session_paths.py`** additions:
```python
def rag_global_uri() -> str
def rag_session_uri(session_id: str) -> str
```
Both respect `config.storage.base_uri` for GCS.

**Config additions** (`config.yml`):
```yaml
storage:
  base_uri: ""           # "" = local filesystem, "gs://bucket" = GCS

rag:
  enabled: true
  mode: local            # local | http
  http_base_url: ""      # used when mode=http
  embedding_provider: sentence_transformers
  embedding_model: all-MiniLM-L6-v2
  top_k: 5
  threshold: 0.65
  injection_budget_chars: 2000
```

**`src/main.py`** — add `init_rag_service(session_id)` call alongside
`configure_logging` and `init_runtime_events`.

**Dependency**: `lancedb>=0.6` added to `pyproject.toml`. If not installed and
`rag.mode=local`, init logs a warning and `get_rag_service()` returns `None`.
All callers guard with `if rag := get_rag_service()`.

---

### Phase B — SQLite RAG removal

Drop the old system cleanly before building on top of it.

**Remove from `src/runtime/artifact_store/recall.py`**:
- `_RecallMixin` class entirely
- `_embed_text`, `_cosine_similarity` helpers
- `index_session_summary`, `recall_sessions`, `recall_artifacts`,
  `index_artifact_summary`, `reindex_all_missing_embeddings`

**Remove from `src/runtime/artifact_store/`**:
- All imports of `_RecallMixin`
- The `session_summaries` table from schema SQL (drop the `CREATE TABLE` — the table
  remains in existing DBs but is no longer referenced)

**Remove from `src/config.py`**:
- `ArtifactStoreRagConfig` dataclass
- `rag` field on `ArtifactStoreConfig`
- All config parsing for the old `rag.*` keys

**Remove from `src/agent.py`**:
- `_build_startup_recall_block`
- `_recall_injected` flag
- The startup recall injection block in `call()`

**Remove from `src/embeddings.py`**:
- Module can be deleted if `SentenceTransformerEmbedder` in `src/rag/embedder.py`
  fully replaces it. Check for other callers first (StaticRouter uses it for routing
  embeddings — keep the module if so, just remove the RAG-specific callers).

**Update `src/tools/implementations/artifacts/recall_sessions.py`**:
- Rewrite to call `get_rag_service().query_global(...)` instead of the old mixin.
  This is a thin wrapper — the tool still exists, it just queries LanceDB now.

---

### Phase C — Tier 1: Global Warehouse

Build `LocalRagService.index_session` and `LocalRagService.query_global`.

**LanceDB schema** (Lance table `sessions` in `_rag/global/`):
```
session_id   : pa.string()       primary identifier
summary      : pa.string()       up to 1200 chars
vector       : pa.list_(pa.float32(), embedding_dim)
binary_name  : pa.string()       nullable
project      : pa.string()       nullable
timestamp    : pa.float64()      unix epoch
tags         : pa.string()       json-encoded list
```

**`index_session`**: embed the summary, upsert by `session_id`.

**`query_global`**: ANN search on the `vector` column, filter by `project` if set,
return top-k above threshold as `list[SessionHit]`.

**Wire into session end** (`src/main.py`):
Replace the removed `store.index_session_summary()` call with:
```python
if rag := get_rag_service():
    rag.index_session(session_id, summary, metadata)
```

---

### Phase D — Tier 2: Session Chunk Stores

Build `LocalRagService.index_chunks` and `LocalRagService.query_session`.

**LanceDB schema** (Lance table `chunks` in `_rag/sessions/<id>/`):
```
chunk_id    : pa.string()       uuid
text        : pa.string()       chunk content, up to 1500 chars
vector      : pa.list_(pa.float32(), embedding_dim)
source_file : pa.string()       relative path to originating file
offset      : pa.int64()        byte offset in source file
binary_name : pa.string()       nullable
session_id  : pa.string()
timestamp   : pa.float64()
```

**`index_chunks`**: embed each chunk, batch upsert into the session's Lance table.
Create the table on first write.

**`query_session`**: ANN search on `vector`, return top-k above threshold as
`list[ChunkHit]`. When `threshold=0.0` (current session), return top-k regardless
of score.

**Two write triggers**:

1. `src/runtime/tool_executor.py — _maybe_page()`:
   After writing the artifact file, before returning the summary string:
   ```python
   if rag := get_rag_service():
       session_id = get_runtime_identity().session_id
       rag.index_chunks(session_id, chunk_text(raw, source_file=str(artifact)))
   ```

2. `src/tools/implementations/file_io/write_file.py`:
   After a successful write to any path under `_analysis/`:
   ```python
   if rag := get_rag_service():
       session_id = get_runtime_identity().session_id
       rag.index_chunks(session_id, chunk_text(content, source_file=path))
   ```

**Chunker** (`src/rag/chunker.py`):
- Fixed windows: 1500 chars, 300-char overlap
- Each `Chunk` carries: `text`, `source_file`, `offset`, `binary_name` (extracted
  from source path)
- No parser — content-agnostic

---

### Phase E — Two-Phase Query Engine + Prompt Injection

Build `LocalRagService.build_context_block` and wire it into the system prompt.

**Query algorithm**:
```
Step 1: chunks = query_session(current_session_id, query, top_k=5, threshold=0.0)

Step 2: past_sessions = query_global(query, top_k=3, threshold=config.rag.threshold)

Step 3: for session in past_sessions:
            past_chunks = query_session(session.session_id, query, top_k=2,
                                        threshold=config.rag.threshold + 0.05)
            chunks.extend(past_chunks)

Step 4: for each chunk:
            days_ago = (now - chunk.timestamp) / 86400
            chunk.score *= 0.9 ** (days_ago / 30)   # recency decay, ~10% per month
            # current session chunks: days_ago ≈ 0, decay ≈ 1.0

Step 5: sort by score desc, fill budget greedily
```

**Output format**:
```
--- Historical context ---
[This session · _analysis/proc/ghidra_decompile.txt]
  ...delta = 0x9e3779b9 (TEA constant), 32 rounds, 8-byte block...

[Session SES01KR7... · 3 days ago · proc]
  ...XORs each block with the prior ciphertext block — CBC mode confirmed...
---
```

**Injection point** (`src/agent.py — call()`):
```python
context_block = ""
if rag := get_rag_service():
    context_block = rag.build_context_block(
        user_message, session_id, config.rag.injection_budget_chars
    )
# append to system prompt, not to user message
effective_system = agent_system + context_block
```

This replaces the removed startup recall block. The 0080 manifest injection
(file listing) remains separate and is still injected per-step in the execution
stages — the two are additive and complementary.

---

### Phase F — HttpRagService (containerization seam)

Implement `HttpRagService` so the switch to a containerized RAG service is a
config change, not a coding task.

**`src/rag/http.py`** — full implementation:
```python
class HttpRagService(RagService):
    def __init__(self, base_url: str): ...

    def index_session(self, ...):
        httpx.post(f"{self.base_url}/index/session", json={...})

    def index_chunks(self, ...):
        httpx.post(f"{self.base_url}/index/chunks", json={...})

    def query_global(self, ...):
        r = httpx.post(f"{self.base_url}/query/global", json={...})
        return [SessionHit(**h) for h in r.json()]

    def query_session(self, ...):
        r = httpx.post(f"{self.base_url}/query/session", json={...})
        return [ChunkHit(**c) for c in r.json()]

    def build_context_block(self, ...):
        r = httpx.post(f"{self.base_url}/context", json={...})
        return r.json()["block"]
```

The RAG service's FastAPI app (in the future fork) would expose exactly these
endpoints, delegating to `LocalRagService` internally. The agent codebase does
not change.

**GCS migration checklist** (documented here for the fork):
1. `storage.base_uri: gs://your-bucket` in config
2. `pip install lancedb[gcs]`
3. Service account: `Storage Object Admin` on the bucket
4. Done — `LocalRagService` now writes to and reads from GCS

---

## Implementation order

```
A → B → C → D → E → F
│    │    │    │    │    │
│    │    │    │    │    └─ HttpRagService (containerization seam)
│    │    │    │    └─ Query engine + prompt injection (makes RAG visible)
│    │    │    └─ Tier 2 session chunks (the new capability)
│    │    └─ Tier 2 write triggers (_maybe_page, write_file)
│    └─ SQLite removal (clean break before building on top)
└─ Foundation: interface, LocalRagService shell, Embedder, config
```

B (SQLite removal) is deliberately second — clean the slate before writing new code
on top of it. C and D are the same phase in terms of what gets built; split here for
clarity. F is independent of E and can be done any time after A.

---

## What does NOT change

- `_analysis/` artifact files and `_maybe_page` (plan 0080) — Phase D adds a second
  action after the file write, not a replacement
- `_sessions/` operational data (logs, metrics, events)
- Artifact store SQLite — conversation history, decay, workflow discovery are unaffected;
  only the `session_summaries` table and `_RecallMixin` are removed
- Tool interfaces, the pipeline, skills system
- The 0080 Phase B manifest injection — it stays, complementing the RAG context block
