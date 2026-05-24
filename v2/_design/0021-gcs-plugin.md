# 0021 — GCS plugin (`arc-plugin-gcs`)

## Motivation

arc agents accumulate state that doesn't fit comfortably in `~/.arc/sessions/`
or the local filesystem:

- **Large binary artifacts** from reverse-engineering work (firmware
  dumps, decompressed sections, packet captures) clutter project dirs
  and become invisible across sessions.
- **Cross-machine continuity** is currently impossible — sessions live
  only on the machine that recorded them.
- **Cross-provider data interchange** has no clean story. Each provider
  has its own Files API (Gemini, Anthropic, OpenAI) and they're
  mutually incompatible; local models need bytes on disk.
- **Sub-agents that generate large intermediate data** (per-frame video
  detections, full disassembly listings) have no clean place to spill
  that data so the parent's context stays small.

Google Cloud Storage solves all four. The bucket is one source of
truth; agents read and write objects by URI; provider integration is
handled per-provider (Gemini accepts `gs://` natively, others download
first).

This phase ships `arc-plugin-gcs` as a **standalone external plugin** —
useful on its own for ad-hoc file management, and the foundation that
later work (the video sub-agent in 0022, session archive, sub-agent
spillover) builds on.

The plugin must not depend on any sub-agent infrastructure. It is a
general-purpose tool pack. Future sub-agents that need GCS access
declare it in their `tools` allowlist; the user installs both packages.

---

## Scope

In:
- New external plugin package `arc-plugin-gcs`, forked from
  `arc-plugin-template`.
- Dependency on `google-cloud-storage` (declared in plugin's
  `pyproject.toml`; not added to arc core).
- **Ten tools** (full list below) covering list, stat, upload,
  download, delete, signed URLs, read-text, bucket overview,
  directory listing, and storage cost estimation.
- Bucket allowlist enforced at every tool call.
- Default bucket so bare paths (`recordings/foo.mp4`) resolve to
  `gs://<default>/recordings/foo.mp4`.
- **Implicit path creation** — `gcs_upload` to a previously-unused
  prefix creates the implied "directory" on the fly. GCS is flat
  key-value; `/` characters in keys are just characters. Documented
  in the tool description so the agent knows this is one operation.
- **Tiered escalation** via `escalation_level: destructive | mutations | all`
  config field. Default `destructive` (current pattern); `all` gates
  every read for paranoid mode.
- **Session-scoped budgets** — per-session caps on total API calls,
  total bytes transferred, and total estimated cost. Mirrors the
  per-spec quota pattern from sub-agents (0020).
- **Per-call cost estimation** — every `gcs.*.completed` event carries
  `cost_estimate_usd` computed from an internal rate table. The TUI
  renders this inline with tool-call output (matching the existing
  tokens/time pattern). Also feeds the session budget.
- Authentication via standard GCP patterns:
  `GOOGLE_APPLICATION_CREDENTIALS` env var pointing to a service-
  account JSON, OR application-default credentials
  (`gcloud auth application-default login`).
- Plugin emits its own observability events (`gcs.*`) for every tool
  call.

Out (deferred):
- **Server-side copy/move** (`gcs_copy`). Useful but not v1-critical;
  the agent can download + upload if needed. Add later if friction
  shows up.
- **Range reads** (`gcs_read_range` for partial-content reads of large
  logs/tarballs). Niche; defer until a real use case appears.
- **Streaming** primitives (chunked downloads of large videos). The
  agent is not a video player — direct streaming has no agent use
  case. Sub-agents that need the bytes use `gcs_download` to local.
- **Lifecycle management** (object lifecycle rules, versioning,
  archival tier transitions). Operator concern, not agent concern.
- **Multi-cloud abstraction** (S3, Azure Blob). Out of scope; another
  plugin per backend if needed. No common interface.
- **Built-in `arc gcs` CLI** subcommand. The plugin contributes tools;
  the agent invokes them. Direct `arc` CLI shortcuts (e.g.,
  `arc gcs list`) are a future quality-of-life nice-to-have, not v1.
- **Session archive plugin** that uses gcs to offload completed
  sessions. Its own follow-up plugin (`arc-plugin-session-archive`)
  that consumes the GCS plugin's tools.
- **Real cost from Billing API** (vs. the estimated cost the plugin
  computes locally). Requires Cloud Billing API access (separate
  scope), is per-project not per-bucket, and lags by ~24h. The local
  estimate is good enough for agent budgeting and UI display.
- **Placeholder-marker `gcs_mkdir`** for "reserving" a prefix without
  uploading content. GCS is flat; this would clutter the bucket with
  `.keep` files and reinforce a false mental model. Implicit creation
  via `gcs_upload` is the correct shape.

---

## The plugin shape — stateful, session-scoped

`arc-plugin-template`'s "Shape A — session-scoped" pattern (see
`docs/PLUGIN_API.md`). The plugin owns:

- One `google.cloud.storage.Client` per session
- The bucket allowlist + default bucket (read once from config at
  build, immutable for the session's lifetime)
- The `UserGate` for destructive-op escalation

All tools are constructed in `on_session_start` and bound to the
plugin's `Client`. `on_session_end` closes the client.

---

## Tool catalog

Ten tools. Names are prefixed `gcs_` so they namespace cleanly in
the tool registry and the agent's tool-use vocabulary.

### `gcs_list(prefix, max_results=100)`
List object names under a prefix. Returns a newline-separated list,
prefixed with the bucket if the prefix didn't include one. Capped at
`max_results` (hard ceiling 1000) so the agent doesn't blow context
on a 50k-object listing.

Input:
- `prefix: str` — `gs://bucket/path/` or just `path/` (resolves to
  default bucket). Empty string lists everything in default bucket.
- `max_results: int = 100` — clamped to 1000.

Output:
```
gs://my-bucket/recordings/2026-05-23/conf-room.mp4    1.4 GB    2026-05-23T14:22:10Z
gs://my-bucket/recordings/2026-05-23/standup.mp4      247 MB    2026-05-23T09:01:33Z
...
(showing 12 of 12 matching)
```

Three columns make the listing useful: URI, size, last-modified.

### `gcs_stat(uri)`
Full metadata for one object. Returns JSON so the agent can parse
fields. Includes `size_bytes`, `content_type`, `updated`, `md5`,
`storage_class`, custom metadata (if any).

Errors:
- Object doesn't exist → `ToolError("no such object: gs://...")`
- Bucket not in allowlist → `ToolError("bucket 'x' not in allowed_buckets")`

### `gcs_upload(local_path, uri, overwrite=False)`
Upload a local file to GCS. Refuses to overwrite by default; setting
`overwrite=true` routes through `UserGate` for confirmation (escalation
required pattern, same shape as guard plugin uses).

**Destination prefixes are created implicitly.** GCS keys are flat;
uploading to `gs://bucket/new/path/file.ext` works even if no other
object under `new/path/` exists. The tool description states this
explicitly so the agent knows it's one operation, not two ("create
directory, then upload").

Input:
- `local_path: str` — absolute or relative to workspace.
- `uri: str` — destination. Default bucket resolution applies. Any
  prefix in the URI is created implicitly if it doesn't exist.
- `overwrite: bool = false` — if false and destination exists, raises
  `ToolError("would overwrite existing object")`. If true and
  destination exists, escalates via UserGate before writing.

Output:
```
uploaded /Users/bubz/recordings/conf.mp4 (1.4 GB) → gs://my-bucket/recordings/conf.mp4
md5: 3b8c4e... etag: CMK4...
```

### `gcs_download(uri, local_path, overwrite=False)`
Pull an object to local disk. Same overwrite semantics as upload —
refuses by default, escalates if `overwrite=true`.

### `gcs_delete(uri)`
Destructive — ALWAYS escalates via UserGate before executing.
NoOpGate (headless) auto-denies, so `arc run` cannot delete via the
agent. To bypass for batch jobs, the user runs with a scratch config
that swaps in a permissive gate (same pattern as safety_gate).

Output on success:
```
deleted gs://my-bucket/recordings/old.mp4 (247 MB)
```

### `gcs_signed_url(uri, expires_in_minutes=60)`
Generate an HTTPS URL valid for `expires_in_minutes` (default 60,
max 1440 = 24h). The cross-provider bridge: any provider that accepts
an image/video URL (most do for vision) can consume a signed URL,
even if it has no native GCS support.

Output is just the URL string (the agent passes it directly to the
next provider call):
```
https://storage.googleapis.com/my-bucket/recordings/conf.mp4?X-Goog-Algorithm=...
```

### `gcs_read_text(uri, max_bytes=1048576)`
Download a text object directly into the tool's output (no local
file). Sensible cap (1 MB default, hard ceiling 10 MB). Refuses
binary content types — checks `content_type` and rejects anything
that isn't `text/*` or `application/json`/`application/xml`/
`application/yaml`. Override unsupported with a clear error message
telling the agent to use `gcs_download` for binary content.

Output is the raw text content.

### `gcs_recent(prefix, n=10)`
The `n` most-recently-modified objects under `prefix`. Cheap context
aid for "what did I just upload?" or "what changed today?". Same
columns as `gcs_list` but sorted by `updated` descending.

### `gcs_summarize_bucket(prefix="", breakdown=True)`
Aggregate view: total objects, total size, optional breakdown by
file extension. Lets the agent get a quick survey without listing
every filename. Useful when the bucket has thousands of objects and
the agent needs to know "is there video content here?" without
paging through everything.

When `breakdown=false`, returns only the top-line totals (cheaper
output, same underlying list — the API cost is identical). Use this
when the agent just needs "how much is in this bucket".

Output with `breakdown=true` (default):
```
gs://my-bucket/ — 1,247 objects, 42.3 GB total

By extension:
  .mp4    127  38.1 GB
  .jpg   1051   2.4 GB
  .pdf     34   1.5 GB
  .json    35   0.3 GB

Oldest: 2025-11-12T03:14:00Z
Newest: 2026-05-24T08:42:31Z
```

Output with `breakdown=false`:
```
gs://my-bucket/ — 1,247 objects, 42.3 GB total
Oldest: 2025-11-12T03:14:00Z
Newest: 2026-05-24T08:42:31Z
```

### `gcs_dirs(prefix="", delimiter="/")`
Return the immediate "subdirectories" under a prefix using GCS's
delimiter convention. Unlike `gcs_list` (which recurses), this only
returns the implied directory names one level deep.

Useful when the agent needs to navigate organization: "what folders
exist in this bucket?" or "what's under /recordings/?".

Input:
- `prefix: str = ""` — anchor. Empty = bucket root (default bucket).
- `delimiter: str = "/"` — the character treated as the path
  separator. Almost always `/`.

Output:
```
gs://my-bucket/photos/
gs://my-bucket/recordings/
gs://my-bucket/research/
gs://my-bucket/scratch/

(4 directories under gs://my-bucket/)
```

Note: GCS doesn't have real directories. These are "prefixes that
appear in objects' keys at the delimiter boundary". The agent
understands this from the tool description; the rendering uses the
intuitive "directory" framing because it matches how humans think.

### `gcs_estimate_storage_cost(prefix="", region="us-multi", storage_class="STANDARD")`
Compute an estimated monthly storage cost for objects under `prefix`,
using public rate-card pricing. Does NOT consult the Cloud Billing
API — this is an estimate, not a billed amount. Useful for "if I
keep this corpus in GCS, what's it costing me?".

Input:
- `prefix: str = ""` — anchor; default bucket if empty.
- `region: str = "us-multi"` — one of `us-multi`, `us-region`,
  `eu-multi`, `eu-region`, `asia-multi`, `asia-region`. Determines
  rate.
- `storage_class: str = "STANDARD"` — one of `STANDARD`, `NEARLINE`,
  `COLDLINE`, `ARCHIVE`. Determines rate.

Output:
```
gs://my-bucket/ — 42.3 GB across 1,247 objects
  storage class:  STANDARD
  region:         us-multi
  rate:           $0.026 / GB-month
  monthly est:    $1.10  (storage only; egress + ops not included)

Note: estimated from public rate card, not Billing API. Actual cost
may differ based on contract pricing, free tier, or volume discounts.
```

Implementation: plugin maintains a small in-memory rate table keyed
by `(region, storage_class)`. Updating rates = a code change in the
plugin (cheap and infrequent).

---

## Config

```yaml
plugins:
  enabled:
    - name: gcs
      enabled: true
      config:
        # REQUIRED — bucket allowlist. Any tool call referencing a bucket
        # not in this list raises ToolError immediately, before any GCS
        # API call. Protects against typos and prompt-injection attempts
        # to touch arbitrary buckets.
        allowed_buckets:
          - my-bucket
          - my-bucket-scratch

        # OPTIONAL — bare paths in tool inputs resolve to this bucket.
        # `gcs_stat("recordings/foo.mp4")` → `gcs_stat("gs://my-bucket/recordings/foo.mp4")`.
        # Must be in allowed_buckets. If unset, every tool call must
        # use full gs:// URIs.
        default_bucket: my-bucket

        # OPTIONAL — path to service-account JSON. If unset, falls back
        # to application-default credentials (gcloud auth application-default login).
        # If both unset and ADC isn't configured, the plugin gracefully
        # disables itself at session start with a clear gcs.disabled event.
        credentials_env: GOOGLE_APPLICATION_CREDENTIALS

        # OPTIONAL — escalation tier. Three levels, increasing strictness:
        #   "destructive" (default) — gcs_delete + overwrite-mode upload/download
        #                             escalate via UserGate.
        #   "mutations"             — above + new uploads + signed URL issuance
        #                             (URLs are credentials; gating them is
        #                             defensible for high-sensitivity buckets).
        #   "all"                   — above + every read (list, stat, read_text,
        #                             dirs, recent, summarize_bucket,
        #                             estimate_storage_cost). Paranoid mode;
        #                             tolerable only for short ad-hoc sessions
        #                             because of prompt fatigue.
        # Headless mode (NoOpGate) auto-denies whatever this tier covers.
        escalation_level: destructive

        # OPTIONAL — session-scoped budgets. When any cap is hit, all further
        # GCS tool calls fail with `ToolError("session GCS budget exceeded")`.
        # Mirrors the per-spec quota pattern from 0020. State is per-session,
        # never persists. Set any field to null to disable that cap.
        #
        # These catch the "runaway agent" cost failure mode WITHOUT requiring
        # per-call user input. Per-call escalation is for "do I consent";
        # budgets are for "have we hit the wallet limit". Separate concerns.
        session_budget:
          max_api_calls: 500           # Class A + Class B combined
          max_bytes_transferred: 1073741824   # 1 GiB
          max_cost_usd: 0.50           # estimated, from internal rate table

        # OPTIONAL — hard ceilings on read sizes (anti-OOM, anti-cost).
        max_text_read_bytes: 1048576       # 1 MB default, ceiling 10 MB
        max_list_results: 1000             # hard ceiling on gcs_list

        # OPTIONAL — signed-URL expiry ceiling (minutes). 24h default.
        max_signed_url_minutes: 1440
```

If `allowed_buckets` is empty or missing, the plugin disables itself
at startup with a `gcs.disabled` event. This is a safety property —
fail closed, not open.

---

## Authentication

Standard GCP patterns. The plugin does not invent its own auth
mechanism.

**Path 1: Service-account JSON**

User exports `GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa.json`. Plugin
reads the env var (named by `config.credentials_env`, defaults to
`GOOGLE_APPLICATION_CREDENTIALS`) and passes it to
`google.cloud.storage.Client.from_service_account_json()`.

**Path 2: Application-default credentials**

User runs `gcloud auth application-default login` once. Plugin
constructs `google.cloud.storage.Client()` with no args; ADC kicks
in automatically.

**Path 3: Workload identity / GKE / Cloud Run**

Same as Path 2 — ADC handles it transparently. No plugin changes
needed.

If neither path works at session start, the plugin emits a
`gcs.disabled` event with a clear actionable message and returns `[]`
from `provides_tools()`. The session continues without GCS access.

---

## Cost estimation and budget enforcement

Every operation has an estimated cost the plugin computes locally
from a small rate table. The estimate feeds three places:

1. **The `gcs.*.completed` event payload** as `cost_estimate_usd`.
2. **The session-budget enforcer** (running total against
   `session_budget.max_cost_usd`).
3. **The TUI render** of the tool call — inline with the existing
   tokens/time display.

### Rate table (in-plugin, hardcoded constants)

```python
# Class A operations: writes, lists, copies, signed-URL issuance.
# Cost: $0.005 / 10,000 = $0.0000005 each.
CLASS_A_USD_PER_CALL = 5e-7

# Class B operations: reads, stat, downloads.
# Cost: $0.0004 / 10,000 = $0.00000004 each.
CLASS_B_USD_PER_CALL = 4e-8

# Egress: $0.12 / GB to internet (US/EU, multi-region).
EGRESS_USD_PER_GB = 0.12

# Storage class monthly rates ($/GB-month), used by
# gcs_estimate_storage_cost. Indexed by (region, storage_class).
# Updated when GCS pricing changes — infrequent.
STORAGE_RATE_USD_PER_GB_MONTH = {
    ("us-multi", "STANDARD"):  0.026,
    ("us-region", "STANDARD"): 0.020,
    ("us-multi", "NEARLINE"):  0.010,
    ("us-multi", "COLDLINE"):  0.004,
    ("us-multi", "ARCHIVE"):   0.0012,
    # ... eu-multi, asia-multi, etc.
}
```

### Per-operation cost rules

- `gcs_list`, `gcs_dirs`, `gcs_recent`, `gcs_summarize_bucket` —
  one Class A op (the list call itself, plus pagination handled by
  the SDK counts as one logical op for budgeting).
- `gcs_stat` — one Class B op.
- `gcs_upload` — one Class A op + (`size_bytes / GB`) egress IF the
  upload itself uses egress (uploading is typically free; only count
  if the SDK reports otherwise). For v1, **upload egress is counted
  as zero** (uploads to GCS are free); rate adjustment can come later
  if costs surprise.
- `gcs_download` — one Class B op + (`size_bytes / GB` × egress
  rate). Egress dominates for non-trivial downloads.
- `gcs_delete` — one Class A op.
- `gcs_signed_url` — one Class A op (URL issuance does count).
- `gcs_read_text` — one Class B op + (`bytes_read / GB` × egress).
- `gcs_estimate_storage_cost` — one Class A op for the list it does
  internally. The COMPUTED result is the estimated *monthly storage*
  cost, separate from the *this-tool-call*'s cost.

### Budget enforcement

Plugin maintains in-memory counters per session:

```python
@dataclass
class _SessionBudget:
    api_calls_used: int = 0
    bytes_transferred: int = 0
    cost_used_usd: float = 0.0

    def would_exceed(self, *, calls: int, bytes_: int, cost: float, caps) -> bool:
        return (
            (caps.max_api_calls is not None and self.api_calls_used + calls > caps.max_api_calls)
            or (caps.max_bytes_transferred is not None and self.bytes_transferred + bytes_ > caps.max_bytes_transferred)
            or (caps.max_cost_usd is not None and self.cost_used_usd + cost > caps.max_cost_usd)
        )
```

Check-then-execute: every tool consults the budget BEFORE making the
API call. If the budget WOULD be exceeded, raises
`ToolError("session GCS budget exceeded: max_cost_usd reached (used $0.49 of $0.50)")`
without making the call. If allowed, executes, then increments
counters AFTER the call returns (so failed calls don't count).

On first denial of each cap, emits a `gcs.budget_exceeded` event.

### TUI rendering

The TUI's existing tool-call renderer (`tui/render.py`) handles
generic `tool.call.*` events. For `gcs_*` tools specifically, the
TUI looks up the corresponding `gcs.*.completed` event from the
session bus and appends the cost estimate inline.

Format rule: cost values smaller than `$0.0001` render as `<$0.0001`
rather than seven zeros, to keep the line readable. Anything `≥
$0.0001` renders to 4 decimal places. Aggregated session cost in the
bottom toolbar can use 2 decimal places.

Examples of post-call render:

```
✓ gcs_list  (gs://my-bucket/recordings/, 0.3s · 12 objects · <$0.0001 est)
✓ gcs_download  (gs://my-bucket/recordings/conf.mp4, 18.2s · 1.4 GB · $0.1681 est)
✗ gcs_upload  → ToolError: session GCS budget exceeded
```

The cost number itself does NOT appear in the tool's text output
(the value the agent's LLM sees) — it would pollute context with
information the model rarely needs for reasoning. Cost rendering
is purely a TUI/log concern.

---

## Failure modes

| Failure | Behavior |
|---|---|
| `allowed_buckets` empty in config | Plugin disables at startup; `gcs.disabled` event; no tools registered. |
| Auth fails (no SA JSON, no ADC) | Same as above. Specific reason in the event. |
| Tool input references a bucket not in `allowed_buckets` | `ToolError("bucket 'X' not in allowed_buckets")` — caught before any API call. |
| Tool input has bare path but no `default_bucket` configured | `ToolError("relative path 'foo' needs a default_bucket or full gs:// URI")`. |
| Object not found (read/stat/delete) | `ToolError("no such object: gs://...")`. |
| Permission denied at GCS layer | `ToolError("permission denied on gs://...; check service account roles")`. |
| Network timeout / 5xx | Retried per the google-cloud-storage SDK's built-in retry (transient errors only). After SDK retries exhaust, surfaces as `ToolError` with the underlying message. |
| `gcs_upload` to existing object, `overwrite=false` | `ToolError("would overwrite gs://...; pass overwrite=true to confirm")`. |
| `gcs_upload`/`gcs_download` with `overwrite=true`, headless mode (NoOpGate) | Auto-denied, `ToolError("destructive op denied by gate")`. |
| `gcs_delete` in headless mode | Same — always auto-denied without explicit user confirmation. |
| `gcs_read_text` on binary content | `ToolError("content_type 'image/png' is not text-shaped; use gcs_download for binary content")`. |
| `local_path` doesn't exist for upload | `ToolError("local file not found: /path/...")`. |
| `local_path` parent dir doesn't exist for download, `overwrite=false` | Same — clear error before any GCS request. |
| Signed URL expiry exceeds `max_signed_url_minutes` | Clamped to ceiling, with a one-line note in the tool output. |
| `session_budget.max_api_calls` reached | Next GCS tool call → `ToolError("session GCS budget exceeded: API call cap reached (used X of Y)")`. `gcs.budget_exceeded` emitted on first hit. |
| `session_budget.max_bytes_transferred` reached | Same shape; counted bytes are upload + download + read_text. |
| `session_budget.max_cost_usd` reached | Same shape. Estimated cost from the rate table, not from Billing API. |
| `escalation_level: all` set, read tool called in interactive mode | UserGate prompts before every list/stat/read. User can `remember` to skip repeats for the session (existing UserGate feature). |
| `escalation_level: all` set, NoOpGate (headless) | Every read auto-denied. Session is effectively GCS-blind. Documented; user opted in. |
| `gcs_estimate_storage_cost` with unknown `(region, storage_class)` pair | `ToolError("no rate for region='X' storage_class='Y'; known: ...")`. The rate table caps what's recognized. |
| `gcs_dirs` with no objects under prefix | Returns "no directories under gs://..." rather than empty string. Empty output confuses the agent more than a sentence does. |

---

## Observability

Plugin emits events for every observable moment, prefixed `gcs.`.
**Every `.completed` event includes `cost_estimate_usd` and
`bytes_transferred`** so the TUI, budget enforcer, and offline cost
analysis can all read from the same source.

- `gcs.disabled` — plugin opted out at startup. Payload: `{reason}`.
- `gcs.client_ready` — successful auth. Payload: `{credential_source}`
  where credential_source is `"service_account_file"` or
  `"application_default"`.
- `gcs.list.completed` — `{prefix, returned, truncated, bucket,
  cost_estimate_usd, bytes_transferred}`.
- `gcs.stat.completed` — `{uri, size_bytes, content_type,
  cost_estimate_usd, bytes_transferred}`.
- `gcs.upload.completed` — `{local_path, uri, size_bytes, md5,
  was_overwrite, cost_estimate_usd, bytes_transferred}`.
- `gcs.download.completed` — `{uri, local_path, size_bytes,
  was_overwrite, cost_estimate_usd, bytes_transferred}`.
- `gcs.delete.completed` — `{uri, size_bytes, cost_estimate_usd,
  bytes_transferred}`. Emitted ONLY after the gate allowed and the
  delete succeeded.
- `gcs.signed_url.issued` — `{uri, expires_in_minutes,
  cost_estimate_usd, bytes_transferred}`. URL itself is NOT in the
  payload (signed URLs are credentials).
- `gcs.read_text.completed` — `{uri, bytes_read, truncated,
  cost_estimate_usd, bytes_transferred}`.
- `gcs.dirs.completed` — `{prefix, returned, delimiter,
  cost_estimate_usd, bytes_transferred}`.
- `gcs.summarize_bucket.completed` — `{prefix, n_objects,
  total_bytes, breakdown_included, cost_estimate_usd,
  bytes_transferred}`.
- `gcs.estimate_storage_cost.completed` — `{prefix, n_objects,
  total_bytes, region, storage_class, monthly_estimate_usd,
  cost_estimate_usd, bytes_transferred}`. Note: `monthly_estimate_usd`
  is the COMPUTED estimate the tool returns; `cost_estimate_usd` is
  the cost of THIS tool call (the list it did internally).
- `gcs.escalation.requested` — `{operation, uri}` — destructive or
  gated op prompted the gate.
- `gcs.escalation.denied` — `{operation, uri}` — gate denied.
- `gcs.budget_exceeded` — `{cap, used, ceiling}` where cap is one of
  `"api_calls"`, `"bytes_transferred"`, `"cost_usd"`. Emitted on
  first hit; not re-emitted on subsequent denials of the same cap.

Events without a "completed" pair (e.g., failed tool calls) are
covered by the existing `tool.call.failed` event emitted by the
runtime; the GCS plugin doesn't duplicate.

One-line formatters added to a `gcs_formatter.py` module within the
plugin so `arc log` renders them readably. The plugin registers them
into `log_writer`'s extensible dispatch (already supported per 0008).

---

## File layout (in the `arc-plugin-gcs` repo)

```
arc-plugin-gcs/
├── pyproject.toml                       # deps: google-cloud-storage
├── README.md                            # install + auth + config + tool reference
├── LICENSE                              # MIT
├── .gitignore
├── src/arc_plugin_gcs/
│   ├── __init__.py
│   ├── plugin.py                        # GCSPlugin class + build()
│   ├── client.py                        # wraps storage.Client; allowlist check; URI parsing
│   ├── auth.py                          # SA-JSON-or-ADC resolution
│   ├── rates.py                         # rate table + per-op cost calculation
│   ├── budget.py                        # session budget tracker
│   ├── escalation.py                    # tiered escalation policy
│   ├── formatters.py                    # log_writer entries for gcs.* events
│   └── tools/
│       ├── __init__.py
│       ├── file_ops.py                  # list, stat, upload, download, delete
│       ├── sharing.py                   # signed_url, read_text
│       └── overview.py                  # recent, summarize_bucket, dirs,
│                                        #   estimate_storage_cost
└── tests/
    ├── __init__.py
    ├── conftest.py                      # FakeStorageClient fixture (no network)
    ├── test_plugin.py                   # build/start/disabled paths
    ├── test_client_allowlist.py         # URI parsing + allowlist enforcement
    ├── test_tools_file_ops.py
    ├── test_tools_sharing.py
    ├── test_tools_overview.py           # +dirs, +estimate_storage_cost
    ├── test_rates.py                    # per-op cost calculation correctness
    ├── test_budget.py                   # session budget enforcement
    ├── test_escalation_tiers.py         # destructive vs mutations vs all
    ├── test_auth_resolution.py
    └── test_integration_real.py         # opt-in via ARC_GCS_TEST_BUCKET env
```

Single pyproject entry-point:
```toml
[project.entry-points."arc.plugins"]
gcs = "arc_plugin_gcs.plugin:build"
```

No new arc core changes. The plugin is discovered via existing
entry-point machinery at session start; tools are merged via the
existing `provides_tools()` contract.

---

## Test plan

All unit tests use a `FakeStorageClient` that mimics the relevant
subset of `google.cloud.storage.Client` (no network, no auth, no
real bucket). The integration test exercises a real bucket only
when the user explicitly opts in.

**`test_plugin.py`:**
1. `build()` constructs plugin with allowlist + default_bucket
2. `on_session_start` with valid auth → plugin ready, tools provided
3. Empty `allowed_buckets` → plugin disabled, `gcs.disabled` event,
   `provides_tools()` returns `[]`
4. Missing auth (no SA file, no ADC) → plugin disabled cleanly
5. `on_session_end` closes the client

**`test_client_allowlist.py`:**
1. `gs://my-bucket/path` → parsed bucket=my-bucket, key=path
2. Bare `path/to/file` with default_bucket → resolved to
   `gs://default/path/to/file`
3. Bare path with no default_bucket → clear error
4. `gs://disallowed-bucket/...` → ToolError before any API call
5. Malformed URI (e.g., `gs:/missing-slash`) → clear parse error

**`test_tools_file_ops.py`:**
1. `gcs_list` returns formatted text with size + updated columns
2. `gcs_list` truncates at `max_results` and notes truncation
3. `gcs_stat` returns valid JSON with expected fields
4. `gcs_upload` happy path emits `gcs.upload.completed`
5. `gcs_upload` to existing without `overwrite=true` → ToolError
6. `gcs_upload` with `overwrite=true` calls UserGate before writing
7. `gcs_upload` with `overwrite=true` and NoOpGate → denied
8. `gcs_download` mirrors upload semantics
9. `gcs_delete` always escalates regardless of `overwrite` flag
10. `gcs_delete` with NoOpGate → denied, no `gcs.delete.completed`
    event emitted (gate denial is the terminal state)

**`test_tools_sharing.py`:**
1. `gcs_signed_url` returns a URL string of the expected shape
2. URL is not present in any emitted event payload (security)
3. `expires_in_minutes` capped at `max_signed_url_minutes`
4. `gcs_read_text` reads text content, emits `gcs.read_text.completed`
5. `gcs_read_text` on `image/png` content → ToolError naming the type
6. `gcs_read_text` truncates at `max_text_read_bytes` and notes truncation

**`test_tools_overview.py`:**
1. `gcs_recent` returns objects sorted by `updated` descending
2. `gcs_summarize_bucket(breakdown=True)` aggregates correctly:
   counts, sizes, extension breakdown, oldest/newest timestamps
3. `gcs_summarize_bucket(breakdown=False)` returns just totals (no
   extension table); same underlying list, smaller output
4. `gcs_summarize_bucket` on empty prefix returns sensible "no
   objects" output without error
5. `gcs_dirs` returns immediate "subdirectories" using the delimiter
   convention; nested keys past the delimiter are NOT included
6. `gcs_dirs` on a prefix with no objects → "no directories under ..."
   sentinel string, not empty output
7. `gcs_estimate_storage_cost` computes the right monthly figure for
   STANDARD/us-multi (verifies the constant): 1.0 GB → $0.026/month
8. `gcs_estimate_storage_cost` on unknown (region, storage_class)
   pair → ToolError listing the known combinations

**`test_rates.py`:**
1. Class A op cost is `5e-7` per call
2. Class B op cost is `4e-8` per call
3. Egress cost: 1 GB download = $0.12 (us-multi default)
4. `gcs_download` of a 1.4 GB object = ~$0.168 total
5. `gcs_list` cost is `5e-7` regardless of how many objects returned
6. `gcs_upload` egress is zero (uploads are free per current rate)
7. Storage rate table lookup returns the right monthly rate per
   (region, storage_class) combo

**`test_budget.py`:**
1. No `session_budget` set → budgets are no-ops; every call allowed
2. `max_api_calls: 3` → first 3 calls succeed, 4th raises
   `ToolError("session GCS budget exceeded")`
3. `max_bytes_transferred: 1024` → upload of 2048 bytes pre-flight
   rejects with budget error; no actual API call made
4. `max_cost_usd: 0.10` → download that would push cost to $0.15 is
   pre-flight rejected
5. `gcs.budget_exceeded` event emitted on first denial of each cap;
   not re-emitted on subsequent denials
6. Failed tool calls (e.g., object-not-found) do NOT consume budget
   slots (counter only increments AFTER successful API completion)
7. Per-cap isolation: hitting api_calls cap doesn't affect bytes or
   cost counters

**`test_escalation_tiers.py`:**
1. `escalation_level: destructive` (default) — gcs_list/stat/read_text
   don't call UserGate; gcs_delete + overwrite-mode upload do
2. `escalation_level: mutations` — gcs_list/stat don't call UserGate;
   new uploads DO call it; gcs_signed_url DOES call it
3. `escalation_level: all` — every tool calls UserGate
4. UserGate `remember` cache (existing feature) — repeated calls in
   same session don't re-prompt
5. NoOpGate (headless) auto-denies whatever the tier covers; the
   non-covered reads still work
6. Invalid `escalation_level` value (typo) → plugin disables at
   startup with a clear error

**`test_auth_resolution.py`:**
1. SA JSON env var present + valid → service_account credential source
2. SA JSON env var unset → falls back to ADC
3. SA JSON env var present but file missing → clear error in
   `gcs.disabled` event

**`test_integration_real.py`:**
1. Skip unless `ARC_GCS_TEST_BUCKET` env var set
2. Round-trip: upload a tiny test file, stat it, list it, download
   it, delete it. Verify each step's tool output is well-formed.
3. Signed URL generation: issue URL, fetch via `httpx`, assert
   matching bytes
4. Allowlist enforcement: configure plugin with a bucket OTHER than
   the test bucket, verify tool call refuses

---

## Open questions

1. **Should the plugin support multiple GCP projects in one
   session?**
   Resolution: no. Service account JSON pins the project; switching
   projects mid-session is not a real use case and adds complexity.
   Users who need multiple projects run separate sessions with
   separate `ARC_HOME` directories.

2. **Should `gcs_read_text` follow gzip-encoded objects (content-
   encoding: gzip) automatically?**
   Resolution: yes — the SDK does this transparently when
   `Blob.download_as_text()` is used. Tested implicitly.

3. **Should the plugin expose a "dry-run" mode where destructive ops
   log what they would do but don't execute?**
   Resolution: no — defer. The escalation gate already forces user
   confirmation; dry-run is a separate concern. If real-world use
   shows this is needed, add a `dry_run: true` config field later.

4. **Should `gcs_list` recurse into subdirectories or respect the
   `delimiter='/'` convention?**
   Resolution: recurse by default (no delimiter). The agent typically
   wants "what's under this prefix", not "what's at this 'directory'
   level only". If a use case for delimited listing appears, add an
   optional `delimiter: str = ""` input field — additive change.

5. **Should signed URLs be loggable in events?**
   Resolution: no. Signed URLs are credentials. Emitting them in
   events would leak them into events.jsonl which may be archived
   or shared. The event records that a URL was issued (and for which
   URI) but not the URL itself.

6. **Should the plugin handle Customer-Managed Encryption Keys (CMEK),
   requester-pays buckets, or other GCS power-features?**
   Resolution: no for v1. The plugin uses defaults. If users have
   specialized bucket configurations the SDK still works — the
   plugin doesn't get in the way — but explicit support is
   deferred.

7. **What happens if the same tool name is contributed by multiple
   plugins (e.g., a hypothetical `arc-plugin-s3` also defines
   `gcs_list`)?**
   Resolution: existing arc core behavior — collisions raise at tool
   merge time per `_merge_plugin_tools`. Choose unique names. (A
   future `arc-plugin-s3` would namespace as `s3_list`.)

8. **Should `gcs_upload` ever auto-create a placeholder for empty
   prefixes the agent "reserves" without uploading content yet?**
   Resolution: no. GCS is flat; placeholders are an anti-pattern.
   Implicit creation via `gcs_upload` is the correct shape. Tool
   description states this so the agent doesn't try to "mkdir first".

9. **Should the rate table be user-overridable via config?**
   Resolution: no for v1. The plugin's rates are tracked in the
   source for two reasons: (a) public rates change rarely, and (b)
   if a user has contract pricing, this plugin's *estimate* will be
   wrong but the *Billing API* (deferred) is the right source for
   that user's actual cost. Don't fork the estimate to handle
   contract pricing; defer to Billing API when that lands.

10. **Should `cost_estimate_usd` appear in the tool's text output
    (visible to the agent's LLM)?**
    Resolution: no. The agent rarely needs per-call cost for
    reasoning, and showing it pollutes context. Cost is for the
    TUI / logs / budget enforcer only. Exception: the COMPUTED
    monthly estimate from `gcs_estimate_storage_cost` IS in the
    output because that's literally what the tool is for.

11. **Should `escalation_level: all` honor UserGate's session-scoped
    `remember` decisions?**
    Resolution: yes — defer to existing UserGate behavior. The user
    confirms a list call once, gate remembers "allow list calls for
    this session"; future list calls don't re-prompt. This is what
    makes `all` mode actually usable for ad-hoc sessions.

12. **Should the session budget reset on `arc resume`?**
    Resolution: yes — budgets are per-session-instance, not
    persisted. Resume starts a fresh session (new session_id, new
    counters). If the user wants persistent budget tracking, that's
    a separate concern handled by the Billing API integration when
    it lands.

---

## State

Designed. Not yet implemented.

---

## Implementation notes

(Filled in after the plugin lands, per repo convention.)
