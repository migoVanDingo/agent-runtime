# Event Schema

All events share these top-level fields (from `RuntimeEvent`):

| Field | Type | Description |
|-------|------|-------------|
| `schema_version` | string | Always `"1.0"` |
| `event_id` | string | Unique event ID (prefixed ULID) |
| `event_type` | string | See table below |
| `ts` | ISO8601 | UTC timestamp |
| `session_id` | string | Session correlation ID |
| `turn_id` | string\|null | Turn correlation ID |
| `pipeline_run_id` | string\|null | Pipeline run correlation ID |
| `plan_id` | string\|null | Plan correlation ID |
| `plan_run_id` | string\|null | Plan execution instance ID |
| `step_run_id` | string\|null | Step execution instance ID |
| `tool_call_id` | string\|null | Tool call correlation ID |
| `stage` | string\|null | Stage or component that emitted this event |
| `privacy` | object | `{classification, redacted, raw_content_stored}` |
| `payload` | object | Event-specific fields (see below) |

---

## Event types

### Session lifecycle
| Event | Privacy | Payload fields |
|-------|---------|---------------|
| `session.started` | internal | `resumed`, `store_enabled` |
| `session.ended` | internal | _(none)_ |

### Turn lifecycle
| Event | Privacy | Payload fields |
|-------|---------|---------------|
| `turn.started` | user-content | `message_preview` (first 300 chars) |
| `turn.completed` | user-content | `response_preview` (first 500 chars) |
| `turn.failed` | internal | `error` |

### Pipeline stages
| Event | Privacy | Payload fields |
|-------|---------|---------------|
| `stage.started` | internal | `stage_name` |
| `stage.finished` | internal | `stage_name`, `status`, `duration_ms` |

### LLM calls
| Event | Privacy | Payload fields |
|-------|---------|---------------|
| `llm.call.started` | internal | `provider`, `model`, `label`, `n_messages`, `n_tools` |
| `llm.call.completed` | internal | `provider`, `model`, `label`, `stop_reason`, `input_tokens`, `output_tokens`, `latency_ms` |
| `llm.call.error` | internal | `provider`, `model`, `label`, `error` |

### Tool calls
| Event | Privacy | Payload fields |
|-------|---------|---------------|
| `tool.call.started` | user-content | `tool_name`, `input_preview` |
| `policy.decision` | internal | `tool_name`, `decision`, `reason` |
| `tool.call.completed` | internal | `tool_name`, `ok`, `error_code`, `result_preview`, `result_bytes` |

### Sandbox
| Event | Privacy | Payload fields |
|-------|---------|---------------|
| `sandbox.run` | internal | `backend`, `isolation`, `exit_code`, `duration_ms`, `timed_out`, `network` |

### Escalation
| Event | Privacy | Payload fields |
|-------|---------|---------------|
| `escalation.requested` | internal | `source`, `reason`, `tool_name` |
| `escalation.resolved` | internal | `source`, `approved` |

### Council
| Event | Privacy | Payload fields |
|-------|---------|---------------|
| `council.deliberation.started` | internal | `mode`, `councillor_labels`, `context` |
| `council.round.completed` | internal | `round_number`, `converged`, `n_decisions` |
| `council.synthesis.completed` | internal | `final_verdict`, `rounds_completed`, `run_id` |

### Planning and execution
| Event | Privacy | Payload fields |
|-------|---------|---------------|
| `plan.created` | internal | `n_steps`, `requires_synthesis`, `action_types` |
| `plan.revised` | internal | `n_challenges`, `surviving_steps` |
| `step.started` | user-content | `step_index`, `action_type`, `tool`, `description_preview` |
| `step.completed` | internal | `step_index`, `status`, `duration_ms`, `importance_score` |
| `step.failed` | internal | `step_index`, `error_class`, `retry_count` |
| `replan.triggered` | internal | `failed_step`, `reason` |

---

## Privacy classes

| Class | Meaning | Redacted on export |
|-------|---------|-------------------|
| `internal` | System metadata, no user content | Credentials only |
| `user-content` | Contains user input or output | Credentials + paths + identifiers |
| `public` | Always safe | Never |

`redact_on_emit` (config) controls local log redaction. `redact_on_export` is
always on for dataset exports regardless of local setting.
