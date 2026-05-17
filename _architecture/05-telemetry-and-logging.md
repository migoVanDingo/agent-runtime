# 05 — Telemetry and logging

How arc records what happens in a session — for the user (logs in the
console + `session.log`), for analysts (structured `runtime.jsonl`), and
for the runtime itself (the event bus drives the TUI's event stream).

## Three streams per session

Under `~/.arc/sessions/<session_id>/`:

| File | Format | Purpose |
|---|---|---|
| `logs/session.log` | human-readable plain text | the rolling story of the session, scope-tagged |
| `metrics/council.jsonl` | JSONL | one row per council deliberation (votes, agreement) |
| `events/runtime.jsonl` | JSONL (schema v2) | every structured runtime event |
| `events/blobs/<event_id>.json` | JSON per file | full content for events whose `content` field exceeds 4 KB |
| `session.summary.json` | JSON | one-shot aggregate at session end |

## The event bus

`src/runtime/events/`. `EventBus.emit(event)` is the single fan-out point:

- writes the event to all registered sinks (`JsonlEventSink`, optional
  custom sinks)
- pages content to blob storage if too large
- applies redaction if configured
- stamps `agent_scope` and `model_run_id` if unset
- notifies all `subscribe()`d callbacks (the service layer uses this to
  build the TUI's typed AgentEvent stream)

Sinks and subscribers run synchronously on the emitting thread. They
must be O(1) — enqueue and return. Errors are swallowed so a bad sink
can never crash the agent.

## Schema v2 — flattened, ML-friendly

Each `RuntimeEvent` carries top-level fields (not buried in `payload`):

```
event_type        event_family       ts                  parent_event_id
stage             severity           privacy             payload
content           raw_payload_ref    redacted

# Identity (flattened)
session_id        turn_id            pipeline_run_id     plan_id
plan_run_id       step_run_id        tool_call_id        user_id
project_id

# Metrics
duration_ms       input_tokens       output_tokens
cache_input_tokens  cache_creation_tokens  cost_usd

# Model identity (LLM events)
provider          model              temperature         max_tokens
stop_reason       finish_reason_normalized

# Replay correlation
model_run_id

# Scope tagging (0090c)
agent_scope       # "main" | "runtime" | "subagent:<name>"
```

This lets pandas use the fields directly:

```python
df = pd.read_json("~/.arc/sessions/<id>/events/runtime.jsonl", lines=True)

# Cost by model
df.groupby("model")["cost_usd"].sum()

# Latency by stage and scope
df[df.event_type == "llm.call.completed"].groupby(["stage", "agent_scope"])["duration_ms"].mean()

# Parent vs sub-agent cost split per turn
df.groupby(["turn_id", "agent_scope"])["cost_usd"].sum().unstack()
```

No `json_normalize` needed.

## Blob paging

Events with large `content` (full prompts, tool I/O, plan JSON,
councillor raw responses) page to
`~/.arc/sessions/<id>/events/blobs/<event_id>.json` when serialized
content exceeds `runtime.events.blob_inline_threshold_bytes` (default
4096). The JSONL event keeps a `raw_payload_ref: "blobs/<id>.json"`
pointer. Analysts join blob files back to parent records via this ref.

Blobs land BEFORE redaction so secrets are scrubbed on the way to disk.

## Redaction

Two stages:

- **Stage 1 (emit-time, always on when `redact_on_emit: true`)** — API
  keys, bearer tokens, JWTs, emails, home paths.
- **Stage 2 (export-time, `scripts/export_session.py`)** — stricter for
  sharing; adds IPs, hostnames, absolute paths.

Stage 1 applies to `payload` AND `content` (and blob contents).

## Scope tagging (0090c)

`runtime.scope.current_scope()` returns the active scope. `EventBus.emit`
auto-stamps it onto `event.agent_scope` unless the call site overrode
it. Logging filter (`logger._ScopeTagFilter`) prefixes log records with
`[<scope>]` for non-main scopes:

```
2026-05-17 12:00:00,000 [INFO] runtime.stages.routing: [runtime] mode=plan ...
2026-05-17 12:00:01,000 [INFO] runtime.stages.execution: step 3/12 ...
2026-05-17 12:00:02,000 [INFO] runtime.tool_loop: [subagent:ghidra_analyst] → ghidra_decompile ...
```

Main scope is intentionally tagless (silence = main).

## Session summary

At session end (`finalize_session`), `runtime.events.summary.write_session_summary`
aggregates `runtime.jsonl` into a single `session.summary.json`:

```json
{
  "session_id": "SES...",
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
  "models_seen": ["claude-sonnet-4-6", "gpt-4o"],
  "skills_used": ["deep-disassembly"],
  "outcome": "completed",
  "first_user_message_preview": "...",
  "last_assistant_message_preview": "...",
  "system_prompt_hash": "..."
}
```

One file per session, parseable in milliseconds.

## Replay

`scripts/replay_session.py --source <session_id> --model <name> --provider <name>`
extracts the user messages from a historical session and runs them
through a new session against a different model. The new session gets
a fresh `model_run_id` (set via `runtime.events.set_model_run_id`) so
pandas can join source and replay JSONL files by that key.

Caveats: tools execute for real; state-changing tools (write_file,
bash_exec) re-run; time-dependent tools (web search) produce different
results. Recommended workspace: a sandbox.

## Export

`scripts/export_session.py <session_id>` bundles a session directory
into a tarball with stage-2 redaction applied. Use to share a session
externally (analysts, bug reports) without leaking host info.

## What lives where in the code

| Concern | Location |
|---|---|
| RuntimeEvent dataclass | `runtime/events/schema.py` |
| EventBus + sinks | `runtime/events/bus.py` |
| Process-level bus + identity | `runtime/events/runtime.py` |
| Redactor | `runtime/events/redactor.py` |
| Session summary writer | `runtime/events/summary.py` |
| Logging setup + scope filter | `src/logger.py` |
| Scope contextvar | `runtime/scope.py` |
| Replay script | `scripts/replay_session.py` |
| Export script | `scripts/export_session.py` |

## Related plans

- `_plans/0087-telemetry-overhaul.md` — schema v2 design, blob paging,
  cost telemetry, replay/export.
- `_plans/0090-context-discipline-and-subagents.md` §6 0090c — scope
  tagging.
