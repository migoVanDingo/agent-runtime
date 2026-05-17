# 0087 — Telemetry overhaul: full-fidelity capture for model evaluation and ML training

> **Audience:** Implementer with full codebase access, no prior context.
> Read this document end-to-end. Each phase doc (`0087a` … `0087h`) will be
> written separately and is self-contained when executed.
>
> **Reading order:** `0079-runtime-as-god.md` (so you understand the
> infrastructure boundary) → this document → the relevant phase doc.

---

## 0. North star

The user wants to:

1. Run the same conversation/task against many different LLMs.
2. Capture *everything* — message text, tool I/O, token usage, latency, plan
   structure, council votes, RAG hits, context-manager decisions.
3. Analyze the resulting dataset with CNNs and other algorithms (e.g.,
   classifier on conversation features → "is this conversation going to
   succeed?"; regression on latency vs prompt size; clustering on failure
   modes by model).

This means telemetry must be:

- **Comprehensive** — no decision is invisible.
- **Lossless** — full content captured, not previews.
- **Joinable** — every event carries enough IDs to reconstruct the tree.
- **ML-friendly** — typed numeric fields at the top level, not buried in
  `payload`.
- **Versioned** — schema can evolve without breaking old logs.
- **Sanitizable** — PII / secrets scrubbed before disk persist or export.

The existing event bus is the right substrate. This plan extends it; it does
not replace it.

---

## 1. Current state

### 1.1 Event bus (`src/runtime/events/`)

- `bus.py:EventBus` — `emit(event)` → sinks + subscribers. Subscribe API
  added in 0083b for the service layer.
- `schema.py:RuntimeEvent` — `event_type: str`, `identity: RuntimeIdentity`,
  `payload: dict[str, Any]`, `stage: str | None`, `event_id`, `ts`,
  `schema_version: str = "1.0"`.
- `redactor.py:RegexRedactor` — pattern-based redaction of API keys in
  payload. Triggered when `redact_on_emit=True`.
- `runtime.py` — process-level singleton bus + `RuntimeIdentity`.
- Identity (`identity.py`): `session_id`, `turn_id`, `pipeline_run_id`,
  `plan_id`, `plan_run_id`, `step_run_id`, `tool_call_id`, `user_id`,
  `project_id`. All ULID-prefixed.

### 1.2 Sink layout

- `JsonlEventSink(path)` — appends to `~/.arc/sessions/<id>/events/runtime.jsonl`.
- Enabled via `config.runtime.events.enabled` + `jsonl_enabled`.

### 1.3 What is emitted today (verified — 26 `bus.emit` call sites)

Inventory:

| Source | Events | Payload |
|---|---|---|
| `main.py:213, 461, 523, 547, 558` | `session.started`, `session.resumed`, `session.ended`, `turn.started`, `turn.completed`, `turn.failed` | message preview, response preview, error |
| `providers/base.py:57, 77, 91` | `llm.call.started`, `llm.call.completed`, `llm.call.error` | provider, model, label, n_messages, n_tools, stop_reason, input_tokens, output_tokens, latency_ms, error |
| `runtime/council.py:185, 269, 363` | `council.deliberation.started/completed`, `council.round.completed` | mode, councillor labels, agreement, round number |
| `runtime/pipeline.py:94, 114` | `stage.started`, `stage.finished` | stage_name, status, retry_count |
| `runtime/escalation.py:42, 68, 78` | `escalation.requested`, `escalation.approved`, `escalation.denied` | tool_name, reason, source |
| `runtime/tool_executor.py:120, 132, 206` | `tool.call.started`, `policy.decision`, `tool.call.completed` | tool_name, input_preview, decision, reason, ok, error_code, result_preview, result_bytes |
| `runtime/stages/council.py:208` | `council.synthesized` | (varies) |
| `runtime/stages/execution.py:197, 234, 342, 365, 425` | `plan.created`, `step.started`, `step.completed`, `replan.triggered`, `goal.achieved` | n_steps, action_types, step_index, tool, duration_ms, importance_score, failed_step, reason |
| `runtime/sandbox/manager.py:58` | `sandbox.started` | image, limits |

### 1.4 Gaps identified

Each gap is a category the user-stated requirements explicitly call out:

1. **Per-message LLM call telemetry**: `llm.call.completed` has tokens and
   latency but NOT: the full prompt sent (messages array, system prompt
   text), the full response text, cache hit/miss, temperature, stop_reason
   detail (anthropic distinguishes `end_turn` vs `tool_use` vs `max_tokens`
   — currently captured, but for OpenAI-compat the mapping isn't normalized).

2. **Conversation telemetry**: never emitted. The full message history exists
   in `agent.messenger` but is only persisted via the artifact store
   `save_conversation` at session end. Per-message timestamps are absent.

3. **Tool execution telemetry**: only previews (500 chars) of input/output.
   Full I/O is never emitted. Container ID, resource-limit hits, exit code
   (when applicable) are absent.

4. **Plan telemetry**: `plan.created` has `n_steps` and `action_types` but
   not the full plan JSON. Council votes per councillor not surfaced at
   step granularity — only aggregate `council.synthesized`.

5. **RAG telemetry**: not emitted. `rag/local.py` has its own logger but
   does not emit events.

6. **Artifact store telemetry**: not emitted. `set`/`get`/`expel`/`apply_decay`
   all silent from the event-bus perspective.

7. **Skill telemetry**: skill match decisions emit nothing. `SkillHintStage`
   calls `WorkflowSelector.match()` but no `skill.match` event fires.
   Completion-criteria evaluation in `ContinuationStage` emits nothing.

8. **Error telemetry**: `turn.failed` carries a 500-char string, no
   traceback, no error category, no "what was attempted".

9. **Context manager telemetry**: `ContextManager.pack()` is called 4+ times
   per turn, makes substantial decisions (importance, fidelity, drop), and
   emits zero events.

10. **Top-level vs nested fields**: `tokens_in`, `tokens_out`, `latency_ms`
    are inside `payload`, not at top level. To compute "mean latency by
    model" with pandas, the analyst must `pd.json_normalize` first.

---

## 2. Proposed schema — v2.0

### 2.1 Frame

Bump `SCHEMA_VERSION = "2.0"`. v1.0 events remain readable; the v2.0 reader
falls back to v1 parsers when it sees `schema_version == "1.0"`.

### 2.2 Event record (top-level layout)

```python
@dataclass(frozen=True)
class RuntimeEventV2:
    # ── Versioning ──────────────────────────────────────────────────
    schema_version: str = "2.0"
    event_id: str = field(default_factory=lambda: new_id("EVT"))
    parent_event_id: str | None = None  # for tool.call.completed → tool.call.started
    ts: str = field(default_factory=utc_now_iso)  # ISO 8601 with microseconds, UTC

    # ── Classification ──────────────────────────────────────────────
    event_type: str             # e.g. "llm.call.completed"
    event_family: str           # e.g. "llm" (== event_type.split(".")[0])
    stage: str | None = None
    severity: str = "info"      # info | warn | error

    # ── Identity (flattened — every ID is a top-level field) ─────────
    session_id: str
    turn_id: str | None = None
    pipeline_run_id: str | None = None
    plan_id: str | None = None
    plan_run_id: str | None = None
    step_run_id: str | None = None
    tool_call_id: str | None = None
    user_id: str | None = None
    project_id: str | None = None

    # ── Metrics (flattened — typed, top-level for direct pandas access) ──
    # All optional; only set for events where they apply.
    duration_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_input_tokens: int | None = None
    cache_creation_tokens: int | None = None
    cost_usd: float | None = None

    # ── Model identification (flattened — LLM events only) ──────────
    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    stop_reason: str | None = None
    finish_reason_normalized: str | None = None  # "end_turn" | "tool_use" | "max_tokens" | "stop_sequence" | "error"

    # ── Privacy ─────────────────────────────────────────────────────
    privacy: EventPrivacy = field(default_factory=EventPrivacy)
    redacted: bool = False
    raw_payload_ref: str | None = None  # path/key to large content stored separately

    # ── Free-form payload (small, ML-uninteresting fields) ───────────
    payload: dict[str, Any] = field(default_factory=dict)

    # ── Inline large content (small enough — under threshold) ────────
    # Above threshold, raw_payload_ref points to a paged location.
    content: dict[str, Any] = field(default_factory=dict)
```

### 2.3 Why the flattening matters

A pandas analyst can run:

```python
import pandas as pd
df = pd.read_json("~/.arc/sessions/.../events/runtime.jsonl", lines=True)
df.groupby("model")["duration_ms"].agg(["mean", "p95", "p99"])
df[df.event_type == "llm.call.completed"].plot.scatter(
    x="input_tokens", y="duration_ms", c="model")
```

Without `pd.json_normalize`. This is the single biggest analyst-experience win.

### 2.4 New event types to introduce

| Family | Type | When emitted |
|---|---|---|
| `conversation` | `conversation.message.added` | Every `messenger.add_user_message` / `add_assistant_message` |
| `llm` | `llm.call.started` | Already exists — augment payload (full system + messages by ref) |
| `llm` | `llm.call.completed` | Already exists — augment with full response (by ref) |
| `llm` | `llm.cache.hit` / `llm.cache.miss` | Provider sets cache flag |
| `context` | `context.pack.started` / `context.pack.completed` | Every `ContextManager.pack()` |
| `context` | `context.message.compressed` | When a message gets COMPRESSED or PLACEHOLDER |
| `tool` | `tool.call.started` | Exists — augment input |
| `tool` | `tool.call.completed` | Exists — augment full output (by ref if large) |
| `tool` | `tool.call.resource_limit` | Container kill / timeout / OOM |
| `plan` | `plan.created` | Exists — add full plan JSON |
| `plan` | `plan.revised` | Council-revised plan |
| `plan` | `plan.replanned` | `Planner.replan` produced new steps |
| `council` | `council.councillor.responded` | Per councillor, per round |
| `council` | `council.synthesis.completed` | Final agreement map + verdict |
| `rag` | `rag.query.issued` | Every retrieval call |
| `rag` | `rag.query.returned` | Hits + scores |
| `rag` | `rag.index.updated` | Chunks added |
| `artifact` | `artifact.stored` | `ArtifactStore.set` |
| `artifact` | `artifact.read` | `ArtifactStore.get` |
| `artifact` | `artifact.expelled` | `ArtifactStore.expel` |
| `artifact` | `artifact.decay.applied` | `apply_decay` archived items |
| `artifact` | `recall.queried` / `recall.returned` | Semantic-recall path |
| `skill` | `skill.match.evaluated` | `WorkflowSelector.match` |
| `skill` | `skill.expanded` | `SkillExpansionStage` replaced `skill:<name>` step |
| `skill` | `skill.completion.evaluated` | `ContinuationStage._evaluate_criteria` |
| `continuation` | `continuation.decided` | LOOP / SYNTHESIZE / DONE |
| `continuation` | `continuation.iteration.started` | Each new iteration |
| `error` | `error.raised` | Any caught exception that bubbles to user-visible failure |

---

## 3. Content paging

### 3.1 Problem

Full prompts and tool outputs can exceed 100k chars. Embedding them inline
in every event:

- Bloats JSONL line size (slow read for analysts)
- Makes redaction harder (more places to scrub)
- Wastes disk (the same prompt appears in `llm.call.started` and persists
  forever)

### 3.2 Design

Two-tier:

- **Inline** if `payload_serialized_size <= 4096 bytes`. Goes in `content` field.
- **Referenced** if larger. Write to `~/.arc/sessions/<id>/events/blobs/<event_id>.json`
  and set `raw_payload_ref = "blobs/<event_id>.json"` (relative to session dir).

Blob structure:

```json
{
  "event_id": "EVT01HFXY...",
  "ts": "2026-05-10T20:00:00.123456Z",
  "kind": "llm.prompt" | "llm.response" | "tool.input" | "tool.output" | "rag.chunks" | "plan.full",
  "data": <the full content — shape depends on kind>
}
```

Blob writer:

```python
class BlobSink:
    def __init__(self, blob_dir: Path) -> None: ...
    def write(self, event_id: str, kind: str, data: Any) -> str:
        """Returns the ref path, relative to session_dir."""
```

The `EventBus.emit()` checks size; if large, `BlobSink.write()` happens
synchronously on the emit thread (acceptable — it's a single file write,
~10ms even for 100k content).

### 3.3 Schema for `content` (when inline) vs blob (when referenced)

Define one canonical schema *per content kind*. Same shape inline or on disk.

**`llm.prompt`** (`llm.call.started` event):
```json
{
  "system": "<full system prompt>",
  "messages": [{"role": "...", "content": "..."}, ...],
  "tools": [{"name": "...", "input_schema": {...}}, ...],
  "json_schema": {...} | null
}
```

**`llm.response`** (`llm.call.completed`):
```json
{
  "content_blocks": [{"type": "text", "text": "..."}, {"type": "tool_use", "name": "...", "input": {...}}, ...]
}
```

**`tool.input`** / **`tool.output`** (`tool.call.started/completed`):
```json
{"input": {...}} or {"output": "<raw string>", "output_bytes": 123456}
```

**`plan.full`** (`plan.created`):
```json
{"plan": <full Plan.to_dict() including all steps>}
```

**`rag.chunks`** (`rag.query.returned`):
```json
{
  "query": "...",
  "hits": [
    {"chunk_id": "...", "score": 0.87, "source_file": "...", "text": "..."},
    ...
  ]
}
```

---

## 4. Redaction layer

### 4.1 Existing

`runtime/events/redactor.py:RegexRedactor` scrubs API keys from `payload`.
Used only when `redact_on_emit=True`. Output destination: `event.payload`.

### 4.2 Required for v2

Two-stage redaction:

- **Stage 1 (always-on, per emit)**: scrub API keys, JWT-like tokens,
  email-bearing strings (configurable). Affects `payload` AND `content` AND
  the blob data before write.
- **Stage 2 (export-time, optional)**: stricter scrub for sharing — removes
  filenames, IP addresses, hostnames. Run by `scripts/export_session.py`,
  not the live agent.

### 4.3 Module: `src/runtime/events/redactor.py` (extend the existing one)

```python
class Redactor(Protocol):
    def redact_payload(self, payload: dict) -> dict: ...
    def redact_content(self, kind: str, content: Any) -> Any: ...
    def redact_event(self, event: RuntimeEventV2) -> RuntimeEventV2: ...


class RegexRedactor:
    """Default redactor: regex patterns over strings, recursive over dicts/lists."""
    def __init__(self, patterns: list[tuple[Pattern, str]] | None = None,
                 redact_content_kinds: set[str] | None = None) -> None: ...
```

The `redact_content` API is the new bit — content shapes are kind-specific
(e.g., for `tool.input`, recurse into the input dict; for `llm.prompt`,
recurse into messages and system separately).

### 4.4 Config

```yaml
runtime:
  events:
    enabled: true
    jsonl_enabled: true
    redact_on_emit: true            # stage 1 (recommended on)
    redact_blobs: true              # apply stage 1 to blob writes too
    blob_inline_threshold_bytes: 4096
    blobs_enabled: true             # turn off to skip large content entirely
```

---

## 5. Cross-session correlation

Every event already carries the full identity chain via `RuntimeIdentity`.
v2 promotes each ID to a top-level field for ML access. No new ID needed.

Add one new ID: `model_run_id`. Used by the replay tool (§7) — the ID of a
"run of session X against model Y". Set process-wide at the start of replay
and added to every event. Default `None` for non-replay sessions.

Index file per session: `~/.arc/sessions/<id>/events/session.summary.json`
written at session end:

```json
{
  "session_id": "SESS...",
  "model_run_id": null,
  "started_at": "...",
  "ended_at": "...",
  "n_turns": 5,
  "n_llm_calls": 47,
  "n_tool_calls": 21,
  "n_replans": 2,
  "n_errors": 0,
  "total_input_tokens": 124000,
  "total_output_tokens": 8500,
  "total_cost_usd": 2.13,
  "p95_llm_latency_ms": 4120,
  "models_seen": ["claude-sonnet-4-5", "gpt-4o"],
  "skills_used": ["dynamic-analysis"],
  "outcome": "completed" | "failed" | "cancelled",
  "first_user_message_preview": "...",
  "last_assistant_message_preview": "..."
}
```

This is the "index file" the user requested. One file per session, parseable
in ms.

Optional: a Parquet `turns.parquet` per session — one row per turn with the
fields above aggregated to turn granularity. Skip in v1; add when the user
starts running pandas at scale.

---

## 6. Emission call sites — additions required

This section is the work map. Each item: file, approximate line, the
existing emission (if any), and what to add.

### 6.1 Conversation message events

**Add** in `src/messenger.py`:

```python
def _emit_message_added(role: str, content: str | list) -> None:
    from runtime.events import RuntimeEvent, get_event_bus, get_runtime_identity
    bus = get_event_bus()
    bus.emit(RuntimeEvent(
        event_type="conversation.message.added",
        identity=get_runtime_identity(),
        content={"role": role, "content": content},  # paged if large
        payload={
            "role": role,
            "content_size_bytes": len(_serialize(content)),
            "content_text_length": _text_length(content),
        },
    ))
```

Call from `add_user_message`, `add_assistant_message`, and the bulk
`get_messages().extend(...)` paths in `agent.py:140` and
`service/inprocess.py:425`.

### 6.2 LLM call augmentation

**Modify** `src/providers/base.py:57, 91`:

```python
bus.emit(RuntimeEvent(
    event_type="llm.call.started",
    identity=identity,
    provider=provider_name,
    model=model,
    temperature=config.llm.temperature,  # add when config has it
    max_tokens=config.llm.max_tokens,
    content={  # paged if large
        "system": system,
        "messages": messages,
        "tools": tools,
        "json_schema": json_schema,
    },
    payload={
        "label": label,
        "n_messages": len(messages),
        "n_tools": len(tools),
    },
    stage=label or provider_name,
))
```

For `llm.call.completed`, add:

- `cache_input_tokens` — from `response.usage.cache_read_input_tokens`
  (anthropic) or equivalent for OpenAI.
- `cache_creation_tokens` — `response.usage.cache_creation_input_tokens`.
- `cost_usd` — computed from a `model_pricing` lookup table
  (new file: `src/runtime/cost.py` with `compute_cost(model, input_tokens,
  output_tokens, cache_input_tokens)`).
- `finish_reason_normalized` — mapped through a small dict
  `{anthropic.end_turn: "end_turn", openai.stop: "end_turn", ...}`.
- `content.content_blocks` — the full response.

### 6.3 Tool call augmentation

**Modify** `src/runtime/tool_executor.py:120, 206`:

- Store full `tool_input` in `content` (paged if large).
- Store full `result.content` in `content` (paged — tool outputs frequently
  exceed 50k).
- Drop the `_preview` payload fields (the previews are inline-content's
  first 500 chars anyway).

Add new emission: `tool.call.resource_limit` from `container/tools.py` and
`container/runtime.py` when container hits OOM/timeout/PID limit. Emit
before raising / returning the error string.

### 6.4 Context manager events

**Modify** `src/runtime/context_manager.py` (or `runtime/context/manager.py`
post-0085-§6 split):

```python
def pack(self, messages, current_query, plan_start_index=None) -> list[dict]:
    t0 = time.monotonic()
    input_size = sum(_estimate_tokens(_message_text(m)) for m in messages)

    bus.emit(RuntimeEvent(
        event_type="context.pack.started",
        identity=get_runtime_identity(),
        payload={
            "n_messages_in": len(messages),
            "input_token_estimate": input_size,
            "budget": self._budget,
            "plan_start_index": plan_start_index,
        },
    ))

    # ... existing pack logic ...

    bus.emit(RuntimeEvent(
        event_type="context.pack.completed",
        identity=get_runtime_identity(),
        duration_ms=int((time.monotonic() - t0) * 1000),
        payload={
            "n_messages_out": len(packed),
            "output_token_estimate": packed_total,
            "fidelity_counts": fidelity_counts,
            "n_dropped": len(scored) - len(packed),
        },
    ))
    return [s.message for s in packed]
```

Per-message compression event optional in v1; defer to a follow-up if
fidelity-level decisions need finer-grain visibility.

### 6.5 Plan augmentation

**Modify** `src/runtime/stages/execution.py:197` (and ContinuationStage,
PlanningStage replan paths):

```python
bus.emit(RuntimeEvent(
    event_type="plan.created",
    identity=identity,
    content={"plan": plan.to_dict()},  # paged if many steps
    payload={"n_steps": len(plan.steps),
             "action_types": list({s.action_type.value for s in plan.steps}),
             "risk": plan.risk},
))
```

Same content shape for `plan.replanned` and `plan.revised`.

### 6.6 Council per-councillor events

**Modify** `src/runtime/council.py:269` (round completion):

```python
for d in round.decisions:
    bus.emit(RuntimeEvent(
        event_type="council.councillor.responded",
        identity=self._identity,
        provider=d.provider,
        model=d.model,
        content={"raw_response": d.raw_response, "parsed": adapter.summarize_decision(d.parsed)},
        payload={"label": d.label, "round_number": d.round_number},
    ))
```

Plus the existing aggregate `council.synthesized` event.

### 6.7 RAG events

**New** in `src/rag/local.py` (and `rag/http.py` if it emits, though it lives
out-of-process — that one only emits client-side `rag.query.issued` /
`rag.query.returned`).

```python
def query(self, session_id, query_text, top_k=5):
    bus.emit(RuntimeEvent(
        event_type="rag.query.issued",
        identity=get_runtime_identity(),
        payload={"top_k": top_k, "session_scope": session_id},
        content={"query": query_text},
    ))
    hits = self._do_query(session_id, query_text, top_k=top_k)
    bus.emit(RuntimeEvent(
        event_type="rag.query.returned",
        identity=get_runtime_identity(),
        content={
            "query": query_text,
            "hits": [{"chunk_id": h.id, "score": h.score, "source_file": h.source,
                      "text": h.text} for h in hits],
        },
        payload={"n_hits": len(hits)},
    ))
    return hits
```

Same pattern for `index_chunks` → `rag.index.updated`.

### 6.8 Artifact store events

**New** in `src/runtime/artifact_store/crud.py`:

```python
def set(self, key: str, value, **meta) -> str:
    artifact_id = self._do_set(...)
    bus.emit(RuntimeEvent(
        event_type="artifact.stored",
        identity=get_runtime_identity(),
        payload={
            "key": key,
            "artifact_id": artifact_id,
            "size_bytes": _size_of(value),
            "stored_inline": _stored_inline(value),
            "tags": meta.get("tags", []),
        },
    ))
    return artifact_id

def get(self, key: str): ...   # emit "artifact.read"
def expel(self, key: str): ...  # emit "artifact.expelled"
def apply_decay(self, ...):     # emit "artifact.decay.applied" with n_archived
```

For `recall_sessions` / semantic search (`artifact_store/discovery.py`):

```python
bus.emit(RuntimeEvent(
    event_type="recall.queried",
    payload={"query": q, "top_k": top_k},
))
# ... do search ...
bus.emit(RuntimeEvent(
    event_type="recall.returned",
    content={"hits": [{"session_id": h.sid, "score": h.score, "summary": h.summary}
                       for h in hits]},
    payload={"n_hits": len(hits)},
))
```

### 6.9 Skill events

**New** in `src/runtime/stages/skill_hint.py`:

```python
# After WorkflowSelector.match() returns
bus.emit(RuntimeEvent(
    event_type="skill.match.evaluated",
    payload={
        "candidates": [{"name": s, "score": sc} for s, sc in scored],
        "chosen": chosen_name,
        "threshold": threshold,
    },
))
```

**New** in `src/runtime/stages/skill_expansion.py`:

```python
# After expanding skill:<name> step into concrete steps
bus.emit(RuntimeEvent(
    event_type="skill.expanded",
    payload={
        "skill_name": name,
        "original_step_index": orig_idx,
        "n_expanded_steps": len(expanded),
    },
    content={"expanded_steps": [s.to_dict() for s in expanded]},
))
```

**New** in `src/runtime/stages/continuation.py:_evaluate_criteria`:

```python
bus.emit(RuntimeEvent(
    event_type="skill.completion.evaluated",
    payload={
        "skill_name": context.active_skill_name,
        "criteria_type": type(criteria).__name__,
        "outcome": outcome.value,
    },
))
```

### 6.10 Continuation events

**New** in `src/runtime/stages/continuation.py:run` decision branch:

```python
bus.emit(RuntimeEvent(
    event_type="continuation.decided",
    payload={
        "iteration": context.continuation_state.iteration_count,
        "max_iterations": cfg.max_iterations,
        "decision": decision.value,
        "decided_by": "criteria" if criteria_matched else "llm_judge",
    },
))
```

And on loop entry: `continuation.iteration.started`.

### 6.11 Error events

**New** in `src/agent.py:call` exception handler (and `main.py`):

```python
except Exception as exc:
    bus.emit(RuntimeEvent(
        event_type="error.raised",
        severity="error",
        payload={
            "error_type": type(exc).__name__,
            "error_message": str(exc)[:1000],
            "where": "agent.call",
        },
        content={"traceback": traceback.format_exc()},
    ))
    raise
```

Also wrap key stage runners and tool execute paths.

---

## 7. Replay harness — `scripts/replay_session.py`

### 7.1 What it does

Given a historical session and a target model:

1. Read `~/.arc/sessions/<id>/events/runtime.jsonl`.
2. Extract the sequence of *user inputs* (filter `event_type ==
   "conversation.message.added"` with `payload.role == "user"`).
3. Configure the agent to use the target model + provider.
4. Start a new session with `model_run_id = new_id("MRUN")`.
5. Replay each user message in order through `agent.call(...)`.
6. The new session's event log is the *parallel stream* for comparison.

### 7.2 Why this fits the user's goal

The user wants "test many LLMs against the same workload." After replay, the
analyst has two JSONL files for the same workload, different models. Pandas
joins by `model_run_id`.

### 7.3 Skeleton

```python
# scripts/replay_session.py
import argparse, json
from pathlib import Path
from session_paths import session_dir

def load_user_messages(session_id: str) -> list[str]:
    log = session_dir(session_id) / "events" / "runtime.jsonl"
    msgs = []
    for line in log.read_text().splitlines():
        ev = json.loads(line)
        if ev["event_type"] == "conversation.message.added" \
           and ev.get("payload", {}).get("role") == "user":
            content = ev["content"].get("content")
            if isinstance(content, str):
                msgs.append(content)
    return msgs

def replay(source_session: str, target_model: str, target_provider: str) -> str:
    from agent import Agent
    from app_config import config

    config.llm.model = target_model
    config.llm.provider = target_provider

    new_sid = ...  # bootstrap fresh session
    agent = Agent()
    for msg in load_user_messages(source_session):
        agent.call(msg)
    return new_sid

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--provider", required=True)
    args = ap.parse_args()
    new_sid = replay(args.source, args.model, args.provider)
    print(f"Replayed → session {new_sid}")
```

### 7.4 Caveats — declare in the doc

- **Tools are real, not recorded.** If the source session ran a tool that
  hit a live API, the replay will hit it again (with potentially different
  results). For deterministic benchmarking, an offline-replay mode would
  re-use the *recorded tool outputs* from the source event stream and skip
  re-execution. Out of scope for v1; flag as a known limitation. Listed in
  open questions.
- **Time-dependent results** (e.g., news search) will differ.
- **State-changing tools** (file_io write, bash exec) re-execute. The
  replay should warn loudly. Recommend a sandboxed workspace as the
  workspace_root.

---

## 8. Phase breakdown

Phases are ordered. Each phase is independently deployable; lower-numbered
phases produce useful telemetry on their own.

| Phase | Title | Touches |
|---|---|---|
| **0087a** | Schema v2 + flattened identity/metrics + version bump | `runtime/events/schema.py`, `runtime/events/bus.py` |
| **0087b** | Blob sink + paging policy | `runtime/events/blob_sink.py` (new), `runtime/events/bus.py` |
| **0087c** | Redactor v2 — content + blob redaction | `runtime/events/redactor.py` |
| **0087d** | LLM call augmentation — full prompt/response + cache + cost | `providers/base.py`, `providers/anthropic.py`, `providers/openai_compat.py`, `runtime/cost.py` (new) |
| **0087e** | Tool call augmentation — full I/O + resource events | `runtime/tool_executor.py`, `tools/implementations/container/{tools,runtime}.py` |
| **0087f** | Context manager + plan + skill + continuation + council per-councillor events | `runtime/context_manager.py`, `runtime/stages/*.py`, `runtime/council.py` |
| **0087g** | Conversation + artifact + RAG + recall + error events | `messenger.py`, `runtime/artifact_store/crud.py`, `runtime/artifact_store/discovery.py`, `rag/local.py`, `agent.py`, `main.py` |
| **0087h** | Session summary index + replay tool | `runtime/events/runtime.py` (write summary on session end), `scripts/replay_session.py` (new), `scripts/export_session.py` (new — sanitize for sharing) |

### Phase scopes

#### 0087a — Schema v2

- Add v2 dataclass alongside v1 (`RuntimeEventV2`).
- Update `EventBus.emit` to accept either v1 or v2 events; v1 emits unchanged.
- Update `JsonlEventSink.emit` to serialize the new flat fields.
- Bump `SCHEMA_VERSION` constant. Add a sentinel field
  `_schema_version_minor: 0` for forward minor-revisions.
- Add a `legacy_v1_to_v2_view()` reader helper for analysts so historical
  logs work seamlessly.

**Verification**: `pytest`; load a sample v1 log via the reader; emit one v2
event and round-trip.

#### 0087b — Blob sink + paging

- New `BlobSink` writing `~/.arc/sessions/<id>/events/blobs/<event_id>.json`.
- `EventBus.emit` checks `event.content` serialized size; if >
  `blob_inline_threshold_bytes`, hands to BlobSink, sets `raw_payload_ref`,
  removes the inline `content`.
- Config: `runtime.events.blobs_enabled: bool`,
  `runtime.events.blob_inline_threshold_bytes: int = 4096`.

**Verification**: emit a small event (inline) and a large event (referenced);
both readable; blob path resolves.

#### 0087c — Redactor v2

- `Redactor.redact_content(kind: str, content: Any) -> Any`.
- Update `RegexRedactor` to handle the canonical content shapes.
- Apply redaction *before* blob write (so secrets are never on disk).

**Verification**: emit an event whose content contains
`"ANTHROPIC_API_KEY=sk-..."`; verify the on-disk blob is redacted.

#### 0087d — LLM augmentation

- Add `temperature`, `max_tokens`, `cache_*`, `cost_usd`,
  `finish_reason_normalized` to `llm.call.completed`.
- Move full prompt to `content.system` / `content.messages` /
  `content.tools` / `content.json_schema`.
- Same for response: `content.content_blocks`.
- New `src/runtime/cost.py` with a pricing table (sourced from each
  provider's published rates; one constant per known model).

**Verification**: run a known prompt against Claude; verify
`llm.call.completed` has all fields populated; verify cost matches manual
calculation from the rate card.

#### 0087e — Tool augmentation + resource events

- Move full `tool_input` to `content.input`; full `result.content` to
  `content.output`.
- New event `tool.call.resource_limit` with `payload.resource: str`
  (`"memory" | "timeout" | "pids" | "network"`), `payload.limit: int | str`,
  `payload.observed: int | str`.
- Emit in `container/tools.py` when adapter reports OOM / timeout / etc.
- Emit in `container/runtime.py:ContainerSession.run` when exit code
  indicates resource kill (137 = OOM, 124 = timeout under coreutils).

**Verification**: run a tool that exceeds limits; verify resource event;
verify `tool.call.completed` still fires with the error result.

#### 0087f — Stage telemetry

- Context: `pack.started/completed` events.
- Plan: full `plan.full` content on `plan.created`/`plan.replanned`/
  `plan.revised`.
- Skill: `skill.match.evaluated`, `skill.expanded`, `skill.completion.evaluated`.
- Continuation: `continuation.decided`, `continuation.iteration.started`.
- Council: `council.councillor.responded` per councillor per round.

**Verification**: run a fix-loop session; verify the iteration events fire in
order with monotonic `iteration` field; verify skill match shows the chosen
skill.

#### 0087g — Conversation / artifact / RAG / errors

- Wire emission in the listed files.
- Test that `replay_session.py` (0087h) can extract user inputs from the
  log.

**Verification**: a 5-turn conversation produces 5 `conversation.message.added`
events with role=user; agent's responses each produce a role=assistant
event; RAG hits visible.

#### 0087h — Session summary + replay

- Write `session.summary.json` from event aggregation at session end
  (`_finalize_session` in `main.py` and the equivalent in
  `service/builder.py:finalize_session`).
- New `scripts/replay_session.py`.
- New `scripts/export_session.py` — copies a session dir to a tarball with
  stage-2 redaction applied.

**Verification**: complete a session; check `session.summary.json`
contents; replay through a second model; check `model_run_id` differs.

---

## 9. ML-friendliness checklist (verify after 0087a–h ship)

Run on a session log:

```python
import pandas as pd
df = pd.read_json("~/.arc/sessions/<id>/events/runtime.jsonl", lines=True)

# Direct access to numeric fields — no json_normalize required:
assert df.duration_ms.dtype.kind in ("i", "f")
assert df.input_tokens.dtype.kind in ("i", "f")
assert df.model.dtype == object  # string

# Filterable by event family:
df[df.event_family == "llm"]

# Joinable across sessions via model_run_id:
df.groupby("model_run_id").size()

# Cost breakdown:
df.groupby("model")["cost_usd"].sum()
```

All of the above must "just work." If any field requires unpacking, the
flattening in §2.2 is wrong.

---

## 10. Risks

| Risk | Mitigation |
|---|---|
| Blob writes blow up disk usage | Config `blobs_enabled: false` skips; per-session size cap in 0087b doc |
| Redactor misses a secret pattern | Easy to extend; v2 includes a `custom_patterns` config field |
| v2 schema breaks downstream consumers | Old `RuntimeEvent` (v1) still emittable; bus writes both formats during a transition period |
| Replay non-determinism (live tools) | Documented; offline-replay mode is a follow-up plan |
| Sensitive content in conversation events | Stage-2 redaction in export script; conversation events are blob-only by default with strict redaction |
| Event volume hurts agent latency | All emissions are synchronous but fast (<1ms in-process); blob writes are 1 fs.write per large event |

---

## 11. Open questions

**Q1**. Should `cost_usd` be computed per provider or in a central pricing
table? Recommend central table at `src/runtime/cost.py` with provider-aware
lookup — pricing changes shouldn't require code changes in providers.

**Q2**. Should `temperature` be in `RuntimeEventV2` top-level or in
`content.params`? Recommend top-level — it's a comparison dimension for
ML analyses.

**Q3**. Should we store the full agent system prompt (large, mostly
constant) inline on every `llm.call.started`? Recommend: store on disk once
per session (in `session.summary.json` or a sibling file) and reference by
hash. Add when prompt size becomes a measurable disk drag.

**Q4**. Should replay support "offline mode" (no live tool execution; replay
recorded tool outputs)? Yes, eventually. Out of scope here; file as
`0091-offline-replay.md`.

**Q5**. Should each councillor's full response be a blob? In long debates
this can be 4 councillors × 3 rounds × 5k chars = 60k. Yes — emit
`council.councillor.responded` with raw_response in content (paged).

**Q6**. Backwards-compat with v1 logs: should the replay tool support v1?
Recommend yes — v1 events have the user messages we need. Adapter is small.

---

## 12. Verification — end-to-end

After all phases land:

1. Run a known conversation through the agent.
2. `wc -l ~/.arc/sessions/<id>/events/runtime.jsonl` — non-trivial line count.
3. `cat ~/.arc/sessions/<id>/events/session.summary.json | jq .` — populated.
4. `python scripts/replay_session.py --source <sid> --model gpt-4o --provider openai`
   — produces a new session id; the new log has the same user messages and a
   matching `model_run_id` chain.
5. Pandas analysis (§9) — works without `json_normalize`.
6. `grep -i "ANTHROPIC_API_KEY=sk-" ~/.arc/sessions/<id>/events/**/*.json*`
   — returns nothing (redaction works).
