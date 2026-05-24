# 0022 — Video sub-agent (`arc-sub-agent-video`)

## Motivation

Reverse engineering, research, and personal-archive workflows produce
video that arc's main agent can't reason about: long recordings of
malware behavior, conference talks, conf-room sessions, family
captures, training footage. We want one well-shaped tool the agent
can call to extract structured, queryable data from any video — and
keep the raw video out of the agent's context entirely.

Three problems make this hard from the parent's perspective:

1. **Provider lock.** Only Gemini (among arc's installed providers)
   has serious native video understanding. If the parent session is
   running Claude or a local model, video analysis is impossible
   without a provider-pinning mechanism.
2. **Context explosion.** A 10-minute video is hundreds of thousands
   of input tokens. Pumping that through the parent's loop would
   blow context and cost on every other turn even after the video
   is done.
3. **Workflow friction.** The user often has a local `.mp4` and
   wants to ask "what's in this?" — not "first upload to GCS, then
   craft a multimodal Gemini call, then…"

The video sub-agent solves all three: pinned to **Vertex AI
Gemini** (which accepts `gs://` URIs natively in `fileData`, unlike
the public Gemini API), runs as a scoped child (own context, own
bus), accepts either `gs://` URIs OR local paths (auto-uploads via
`arc-plugin-gcs`), returns a single structured JSON the parent can
query for any of the things the user typically asks about a video —
summary, transcript, speaker timing, object/people counts, action
timestamps, subject tracking with bounding boxes, scene boundaries,
scene attributes (setting, lighting, time-of-day).

The sub-agent ships as the standalone external package
**`arc-sub-agent-video`**, depends on `arc-plugin-gcs` for its
2-tool allowlist, and is discovered via the `arc.subagents`
entry-point group (per 0020).

### Why Vertex AI, not the public Gemini API?

arc already has a `gemini` provider that uses the public Gemini
API (`google-genai` SDK). It works great for text/tool turns. For
video specifically, it has one fatal limitation: the public API
does NOT accept `gs://` URIs in `fileData` parts (that's a Vertex
AI-only feature). Workarounds with signed URLs + bridge tools are
possible but always feel like working around the API.

Vertex AI accepts `gs://` URIs directly using IAM-authenticated
service-account credentials — exactly the credentials we already
configured for the GCS plugin. The video sub-agent's flow
collapses from "stat → signed_url → bridge → analyze" to
"stat → analyze."

Cost is the same on both: Vertex Gemini and Gemini API charge
identical per-token rates for the same model. The added cost is
the one-time work of writing a `vertex_gemini` provider for arc.

---

## Scope

In:
- New external package `arc-sub-agent-video`, forked from
  `arc-sub-agent-template`.
- **New arc-core provider `vertex_gemini`** (`arc.providers.vertex_gemini`)
  using the same `google-genai` SDK as the existing `gemini`
  provider, but constructed with `vertexai=True` and a project /
  region (instead of an API key). Parallel to the
  existing `gemini` / `anthropic` / `ollama` / `llama_cpp`
  providers. Authenticates via the same SA already used by
  `arc-plugin-gcs`.
- Sub-agent pinned to `vertex_gemini`, default model
  `gemini-2.5-pro` (best current video understanding + spatial
  grounding).
- Input: either a `gs://` URI OR a local filesystem path.
  Local paths trigger an auto-upload via `gcs_upload` to a
  date-namespaced prefix, then the dispatch continues with the
  resulting URI.
- Tool allowlist (2 tools, both from `arc-plugin-gcs`):
  `gcs_stat`, `gcs_upload`. Vertex ingests `gs://` URIs natively
  via IAM — no signed-URL or bridge-tool step.
- Single rich output schema (the "fat schema") with optional
  fields populated based on the task string.
- **Subject registry pattern** for bounding boxes — one bbox per
  subject in a top-level table, all timeline entries reference
  by `subject_id`. Bounds output size O(num_subjects), not
  O(num_subjects × num_frames).
- Per-spec config: `video_fps` (Gemini sampling rate),
  `auto_upload_prefix`, `model`, `max_duration_s`.
- Per-provider config (for `vertex_gemini`): `project_id`,
  `region`. Read from arc's `provider:` config block (when
  parent session uses `vertex_gemini`) OR from sub-agent's
  `api_key_env` field (repurposed to point at a JSON config —
  see Config below).
- Hard cap on video duration enforced at the Vertex side; clear
  error surfaced if exceeded. Documented as 60 minutes for
  `gemini-2.5-pro`.
- Cost transparency: per-call cost estimates embedded in the
  spec's `description` so the parent agent treats calls
  deliberately.
- Auto-uploaded video URIs surfaced in the result so the
  parent agent (which has the `user_gate`) can offer cleanup
  to the user.

Out (deferred):
- **Long-video handling** (chunking, sampling, hierarchical
  analysis). Hard cap for v1; v2 design captures the option
  space.
- **Per-frame bounding boxes.** Subject registry returns ONE
  representative bbox per subject. Per-frame tracking is
  available only via a narrow time-window second call.
- **Live / streaming analysis.** Not Gemini-supported anyway.
- **Multi-provider video** (Claude, OpenAI). Different sub-agent
  specs when needed; not v1 scope.
- **In-sub-agent cleanup.** Sub-agents don't have `user_gate`
  (`gcs_delete` would auto-deny). Cleanup decision surfaces to
  parent.
- **Auto-translate, auto-OCR, auto-face-identify.** Gemini does
  these natively when the task asks; we don't wire dedicated
  capabilities for v1.
- **Real-cost surfacing from Vertex/Gemini API** (versus the
  estimate we display). Add when arc gains a billing-API plugin.

---

## Architecture

The sub-agent is pure declarative `SubAgentSpec` data + a long
system prompt. The non-trivial new code is the **`vertex_gemini`
provider** in arc core; the sub-agent itself stays clean because
Vertex accepts `gs://` URIs natively.

```
parent session
   ↓ tool call: subagent_video_analyst
SubAgentTool.execute(task, context_bundle)
   ↓
SubAgentRunner.dispatch(...)
   ↓ spawns child AgentSession with:
       - provider: vertex_gemini (pinned)
       - model: gemini-2.5-pro
       - tools: gcs_stat, gcs_upload
       - system_prompt: <full video-analysis methodology + schema>
       - tripwire / scope / budget guards (per 0020)
   ↓ child runs ReAct loop:
       1. parse task — extract URI or local path
       2. if local path → gcs_upload to <default_bucket>/<auto_upload_prefix>/<basename>
       3. gcs_stat the uri → verify exists, get size + content_type
       4. construct Vertex multimodal request:
            content: [Part.from_uri(gs://..., mime_type=ct), Part.from_text(task)]
       5. Vertex processes → returns fat-schema JSON
       6. validate JSON shape (model self-checks against expected_output)
       7. emit final assistant message = JSON
   ↓
SubAgentResult(output=<json>, status=ok)
   ↓
parent receives the JSON via the tool result
```

Two non-obvious details:

1. **The sub-agent code is trivial.** No bridge tool, no contextvar
   plumbing in providers. The Spec + system prompt + tool allowlist
   are the entire sub-agent package. All the heavy lifting lives in
   the new arc-core provider.
2. **Vertex SDK constructs `Part.from_uri(gs://...)` directly.**
   arc's `vertex_gemini` provider translates each `ContentBlock` in
   the request into a `Part`. For a regular text block,
   `Part.from_text()`. For tool calls / tool results, the existing
   shapes. For a video reference, the provider supports a new
   `ContentBlock` shape — but instead of inventing a new type, we
   use existing `ContentBlock(type="text", text=...)` with the
   convention that text matching `gs://...` is treated as a URI ref
   when the child's last tool call was `gcs_stat`. (Implementation
   detail in the provider — see below.)

   Actually cleaner: the child's task message constructed by the
   runner can include the gs:// URI in a structured way the
   provider recognizes. We add a `tool_input` field on the
   provider's translation: when constructing the multimodal
   request, if the chain includes a `gcs_stat` tool call result
   for a `video/*` content type, append `Part.from_uri(uri,
   mime_type)` to the request automatically.

   See **Provider design** below.

### Provider design — `arc.providers.vertex_gemini`

New file: `v2/src/arc/providers/vertex_gemini.py` (~250 lines).
Implements the `LLMProvider` protocol (same as `gemini.py` does).
Key differences from `gemini.py`:

- **SDK**: same `google-genai` as the existing `gemini` provider,
  but constructed with `Client(vertexai=True, project=...,
  location=...)` instead of an API key. Authenticates via standard
  GCP credentials (`google.auth.default()`) — same SA already used
  by `arc-plugin-gcs`. The two providers share request-translation
  helpers; only the client construction differs.
- **Initialization**: requires `project_id` and `region` (vs.
  the public API's `api_key`). Read from `ProviderConfig.params`
  (new convention) or from `ProviderConfig.base_url` (repurposed
  to `<project>/<region>` for Vertex — ugly; better is `params`).
- **Multimodal request construction**: arc's `LLMRequest` carries
  `messages: list[Message]`. The provider translates each Message
  to Vertex `Content`. For video, the convention is:
  - Child uses `gcs_stat` to verify the video; the tool result
    JSON includes the `uri` and `content_type`.
  - The next user-turn injection (the synthesized turn that
    feeds the tool result back into the LLM) is constructed by
    arc's loop. The provider checks: if a tool result has a
    `content_type` starting with `video/` or `image/` and a
    `uri` starting with `gs://`, the provider appends a
    `Part.from_uri(uri, mime_type=content_type)` to the next
    request alongside the textual tool result.
  - This is provider-side magic. The child agent doesn't need
    to construct anything special; it just calls `gcs_stat` on
    the video and Vertex sees the file. The provider's
    translation layer does the work.

This convention is local to `vertex_gemini` — no change to other
providers, no protocol change. Out-of-tree plugins that produce
tool results with `{uri, content_type}` shape automatically work
with Vertex multimodal.

### Why no `gcs_signed_url` in the tool allowlist

Vertex AI authenticates via IAM. The SA running arc has
`roles/storage.objectAdmin` on the bucket (per 0021 setup); that
same SA's credential is what Vertex uses to fetch the `gs://` URI.
Signed URLs are for cross-API bridges (giving a non-GCP recipient
time-limited access). Vertex IS GCP — no bridge needed.

The sub-agent uses `gcs_stat` purely for pre-flight validation
(does the file exist? is it a video? how big is it?) before
shipping the URI to Vertex. `gcs_upload` is for the local-path
auto-upload case. That's it.

---

## Output schema — the fat schema

Single JSON object. **Only `source_uri`, `duration_s`, `summary` are
always populated.** Other fields are populated only when the parent's
task pulls for them; otherwise `null` or omitted.

```json
{
  "source_uri": "gs://my-bucket/recordings/conf.mp4",
  "duration_s": 1847.5,
  "summary": "30-minute conference room recording with 5 people…",

  "scene_attributes": {
    "setting": "indoor",
    "lighting": "artificial",
    "time_of_day_estimate": "evening",
    "weather": null,
    "location_type": "office",
    "scene_description": "Modern conference room, whiteboard, large window"
  },

  "object_counts": [
    {"object_class": "person", "max_at_any_frame": 5, "estimated_unique": 7},
    {"object_class": "laptop", "max_at_any_frame": 3, "estimated_unique": 4},
    {"object_class": "window", "max_at_any_frame": 2, "estimated_unique": 2}
  ],

  "subjects": [
    {
      "id": "person_A",
      "class": "person",
      "description": "Tall, blue shirt, beard",
      "representative_bbox": [220, 110, 480, 380],
      "appearances": [
        {"start_s": 0.0, "end_s": 145.2},
        {"start_s": 320.0, "end_s": 1847.5}
      ]
    }
  ],

  "transcript": [
    {"start_s": 0.0, "end_s": 12.3, "speaker_id": "person_A", "text": "Let's get started…"}
  ],

  "actions": [
    {"action": "laughing", "subject_id": "person_B", "start_s": 145.2, "end_s": 147.1},
    {"action": "typing",   "subject_id": "person_A", "start_s": 0.0,   "end_s": 30.0, "object": "laptop"}
  ],

  "scenes": [
    {"start_s": 0.0, "end_s": 245.0, "summary": "Introduction phase"}
  ],

  "auto_upload_uri": null,
  "notes": "Camera shifted at ~12min; occluded faces 800-820s"
}
```

### Field semantics

| Field | Always populated? | Notes |
|---|---|---|
| `source_uri` | yes | The `gs://` URI the model analyzed. Same as input unless auto-uploaded. |
| `duration_s` | yes | Total video length in seconds (float). Model reads from Gemini's video metadata. |
| `summary` | yes | 1-3 sentence overview. Always present even if the task is narrowly scoped. |
| `scene_attributes` | when task asks about setting / lighting / time-of-day / weather / location | Single object, fixed enum-ish fields. `time_of_day_estimate` is `dawn\|morning\|midday\|afternoon\|evening\|night\|unknown`. |
| `object_counts` | when task asks about objects / counts | List of `{class, max_at_any_frame, estimated_unique}`. Top 10 most-prominent classes plus anything the task explicitly mentioned. |
| `subjects` | when task asks about subjects / people / tracking / bboxes | List of `{id, class, description, representative_bbox, appearances}`. Subject IDs use a `<class>_<letter>` pattern: `person_A`, `dog_A`, `car_B`. |
| `transcript` | when task asks about speech / dialogue / who said what | Speaker IDs reference `subjects[].id`. Includes start/end timestamps. |
| `actions` | when task asks about who-did-what-when | Action labels are free-form short verbs (`laughing`, `typing`, `running`, `interacting`). Optional `object` field for actions involving objects. |
| `scenes` | when task asks for structure / sections | Coarse boundaries with per-scene summaries. |
| `auto_upload_uri` | when sub-agent uploaded a local file | Parent agent reads this to offer cleanup to the user via `gcs_delete`. |
| `notes` | when there's something unusual to flag | Free-form. Camera glitches, ambiguity, low confidence. |

### Bounding box convention

`[ymin, xmin, ymax, xmax]` normalized to 0-1000 per Gemini's spatial
grounding convention. ONE `representative_bbox` per subject — the
typical position/extent where they appear longest. NOT per-frame.
If the task needs per-frame tracking of a specific subject, the
parent re-dispatches with a narrow time window in the task string.

### Subject ID convention

`<class>_<letter>` for distinct subjects of the same class:
`person_A`, `person_B`, `person_C` for three different people;
`dog_A`, `dog_B` for two dogs. Letters cycle through alphabet;
beyond Z, use `AA`, `AB`, etc. (rare in practice). IDs are stable
within one dispatch — `person_A` is the same person across all
`transcript`, `actions`, `appearances` entries.

---

## The system prompt (sketch)

Lives in `arc-sub-agent-video/src/arc_sub_agent_video/prompts/system.md`.
~600-800 lines of markdown. Structured as:

1. **Role + general posture** — "You are a video analyst. Your job is
   to extract structured data from videos and return strictly-formed
   JSON. Be precise about what you saw vs. inferred."

2. **Tool playbook** — the deterministic flow:
   1. Parse the task string. Extract the input (URI or local path).
   2. If local path: `gcs_upload` to default bucket + auto-upload prefix.
   3. `gcs_stat` the URI to confirm + get size + content_type.
      The provider sees this tool result and auto-attaches the
      `gs://` URI to the next request as a Vertex `Part.from_uri`.
   4. Produce the final assistant message: the fat-schema JSON.

   (No signed-URL step, no bridge tool. Vertex pulls the file
   directly via IAM after `gcs_stat` validates it.)

3. **Task interpretation rules** — how to map free-form task strings
   to schema sections:
   - "summarize" / "what's in" → just `summary`
   - "who spoke when" → `subjects` (people) + `transcript`
   - "count X" → `object_counts` with X explicitly included
   - "track person" → `subjects` filtered to people + their `appearances`
   - "when does X happen" → `actions` filtered to action=X
   - "is it indoors / nighttime / etc." → `scene_attributes`
   - Multi-question task → populate all relevant sections

4. **Output format rules — STRICT**:
   - Final assistant message MUST be a single JSON object — no
     prose before/after, no markdown fences, no comments.
   - Optional fields are `null` or omitted; do NOT include them with
     empty placeholders.
   - All timestamps are seconds (float) from video start.
   - All bboxes are `[ymin, xmin, ymax, xmax]` normalized 0-1000.
   - Subject IDs follow `<class>_<letter>`.

5. **Bounded output rules** — anti-bloat:
   - `object_counts` ≤ 10 entries (top-N + task-specified).
   - `subjects` ≤ 30 entries (cap on tracked subjects).
   - `transcript` natural length; if > 200 entries, summarize tail.
   - `actions` ≤ 50 entries; if more, prioritize task-relevant.

6. **Hard limits / refusals**:
   - If video duration > 60 minutes (per Gemini stat): return
     `{"error": "video exceeds 60-minute limit for gemini-2.5-pro",
       "duration_s": <actual>}`.
   - If content_type isn't video/*: return error noting it.
   - If the URI is not in the parent's `gcs_*` allowed bucket: the
     `gcs_stat` call will fail with a clear error — surface it.

7. **Uncertainty handling**:
   - Confidence-low observations go in `notes`, not in structured
     fields.
   - If unsure of speaker identity: use `speaker_id: "unknown"`,
     don't invent.
   - If unable to determine `time_of_day_estimate`: use `"unknown"`.

---

## Tool allowlist + dependencies

```python
tools=(
    "gcs_stat",     # verify URI + content_type pre-flight; provider auto-attaches
    "gcs_upload",   # local-path auto-upload to default bucket
)
```

**Dependencies (user must install all three):**
- arc v2 **with the new `vertex_gemini` provider** (this work).
  Reuses the existing `google-genai` dep (no new package needed).
- `arc-plugin-gcs` v0.1+ (provides `gcs_stat` and `gcs_upload`).
- This package — `arc-sub-agent-video`.

**GCP setup (one-time, outside arc):**

```bash
# Enable the Vertex AI API on the project
gcloud services enable aiplatform.googleapis.com

# Grant the existing service account Vertex AI user role
gcloud projects add-iam-policy-binding migodamus \
  --member="serviceAccount:local-projects@migodamus.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"
```

The SA already has `roles/storage.objectAdmin` on the video
bucket from the 0021 setup. Vertex AI uses the same SA for both
auth and bucket reads — no separate credentials.

If `arc-plugin-gcs` isn't installed when this sub-agent is loaded,
the Runner's tool-allowlist intersection (per 0020) raises
`SubAgentError("tool 'gcs_stat' not available")` at dispatch time.
Clear error; user installs the plugin.

---

## Config surface

Two config blocks matter — the new provider's, and the sub-agent's.

**Provider config** (in arc's main `provider:` block when the
parent session uses `vertex_gemini`; otherwise unused at the parent
level — the sub-agent spec still pulls in `vertex_gemini` for its
child session):

```yaml
provider:
  name: vertex_gemini
  model: gemini-2.5-pro
  api_key_env: ""                              # Vertex uses GCP auth, not API key
  params:
    project_id: migodamus                      # REQUIRED
    region: us-central1                        # REQUIRED — Vertex regional endpoint
```

When the video sub-agent dispatches, its child session uses these
same `project_id` and `region` values (inherited via the runner's
child-config construction per 0020). If the parent session doesn't
use `vertex_gemini`, the sub-agent reads `project_id` and `region`
from its own spec config (below).

**Sub-agent config** (per `~/.arc/config.yml`'s `subagents:` block,
processed by the Registry per 0020):

```yaml
subagents:
  video_analyst:
    model: gemini-2.5-pro                # default
    timeout_s: 600                       # 10min — videos take a while
    max_turns: 6                         # tight; the prompt is deterministic
    max_dispatches_per_session: 3        # video is expensive

    # Sub-agent-specific knobs (read at spec construction time
    # and injected into the system prompt — see Config injection).
    video_fps: 1.0                       # Vertex video sampling rate (0.5 cheaper)
    auto_upload_prefix: "video-uploads/{date}/"   # {date} = YYYY-MM-DD
    max_duration_s: 3600                 # 60min refusal threshold

    # Vertex provider config — overridable per-spec so the
    # sub-agent can run on a different project/region than the
    # parent session if needed.
    vertex_project_id: migodamus
    vertex_region: us-central1
```

### Config injection

`SubAgentSpec` doesn't have a "custom config" field — fields are
fixed (provider, model, system_prompt, tools, etc., per 0020). To
get `video_fps`, `auto_upload_prefix`, `max_duration_s` to the
running sub-agent, we inject them into the `system_prompt` at
spec construction time:

```python
def build(config, build_ctx):
    video_fps = config.get("video_fps", 1.0)
    prefix    = config.get("auto_upload_prefix", "video-uploads/{date}/")
    max_dur   = config.get("max_duration_s", 3600)
    project   = config.get("vertex_project_id")  # may be None
    region    = config.get("vertex_region", "us-central1")

    prompt = _SYSTEM_PROMPT.format(
        video_fps=video_fps,
        auto_upload_prefix=prefix.replace("{date}", today_iso()),
        max_duration_s=max_dur,
    )
    # vertex project/region flow through SubAgentSpec.params (a new
    # frozen-dict field) into the provider construction at dispatch.
    return SubAgentSpec(
        ...,
        system_prompt=prompt,
        params={"vertex_project_id": project, "vertex_region": region},
        ...,
    )
```

This is the ONE place we read user `config` in a sub-agent
`build()`. The Registry's field-level overrides (model, timeout_s,
etc.) still apply on top. Document this clearly so future sub-agent
authors understand the exception.

**One 0020 follow-up needed**: `SubAgentSpec` doesn't currently
have a `params: dict[str, Any]` field. Adding it is purely additive
(new optional field with default `{}`). The runner reads it and
threads values into the child's provider config. Without this, the
sub-agent has no way to communicate provider-specific config
(`project_id`, `region`) into the dispatch.

### Cost transparency in the description

The `description` field on the Spec is what the parent agent's LLM
sees in its tool schema. It includes a concrete cost note:

```python
description = (
    "Analyze a video at a gs:// URI or local file path. Returns "
    "structured JSON with summary, transcript, speakers, actions, "
    "object counts, subject tracking with bounding boxes, and scene "
    "attributes. Cost typically $0.20-0.40 for a 10-min video full "
    "analysis (~$0.22 input + $0.05-0.18 output depending on what "
    "you request). Use a narrow time-window task string and the "
    "minimum fields you need to keep cost down. "
    "[sub-agent: pinned to gemini-2.5-pro]"
)
```

Parent agents pick up this language and reason about cost when
deciding whether to dispatch.

---

## Failure modes

| Failure | Behavior |
|---|---|
| User passes local path, `arc-plugin-gcs` not installed | `gcs_upload` not in registry → `SubAgentError("tool 'gcs_upload' not available")` at dispatch. |
| User passes local path, file doesn't exist | `gcs_upload` raises ToolError; child's loop sees the error, includes it in final JSON `notes`, status=error in result. |
| User passes `gs://` URI not in `allowed_buckets` | `gcs_stat` raises `ToolError("bucket 'X' not in allowed_buckets")` — clear failure surfaced to parent. |
| Vertex AI API not enabled on project | `vertex_gemini` provider's init fails with a clear "enable aiplatform.googleapis.com" message; child session aborts; parent sees `status="error"`. |
| SA lacks `roles/aiplatform.user` | Vertex returns 403 PermissionDenied; provider surfaces as `ToolError` with IAM hint; child errors out. |
| Vertex can't read `gs://` URI (different project / SA lacks bucket access) | Vertex returns "failed to fetch file" error; surfaces in `notes`; status=error in result. |
| Video duration exceeds `max_duration_s` | Model returns `{"error": "video exceeds 60-minute limit", "duration_s": <actual>}` instead of fat schema. Parent agent sees this and reports to user. |
| Vertex rejects the video (corrupt, unsupported codec) | The Vertex call fails; sub-agent's error bubbles through child session; parent sees `SubAgentResult(status="error", error_message=...)`. |
| Vertex timeout (large video, slow processing) | Sub-agent's `timeout_s` (default 600s) trips; child cancelled; parent sees `status="timeout"`. |
| Auto-upload succeeded but Vertex analysis failed | Sub-agent returns error result with `auto_upload_uri` populated — parent can still offer cleanup. |
| Session GCS budget hit mid-analysis | The next `gcs_*` call raises `ToolError("session GCS budget exceeded")`; child surfaces; parent sees error result. |
| Sub-agent `max_dispatches_per_session` (3) reached | Subsequent calls denied via `subagent.quota_exceeded` per 0020. |
| Vertex returns malformed JSON | Child's loop has `max_turns=6` to retry; if all turns produce non-JSON, child errors out; surfaces to parent. |
| Vertex quota exceeded (project-level) | Vertex returns 429 ResourceExhausted; provider surfaces as `ToolError` with the project's quota limit; child errors out. |
| Subject IDs collide across re-runs of same video | Not a failure — IDs are dispatch-scoped, not durable. Parent should not assume `person_A` in run 1 = `person_A` in run 2. Document. |

---

## Observability

No new event types — sub-agent dispatch events (per 0020) and GCS
events (per 0021) cover the full life-cycle. A successful video
analysis emits roughly:

```
parent bus:
  subagent.dispatched   spec_name=video_analyst, child_session=...
  subagent.returned     status=ok, cost_usd=0.35, turns=3, tool_calls=2, wallclock_s=42.1

child bus:
  session.started        provider=vertex_gemini, model=gemini-2.5-pro
  turn.started
  llm.call.completed     model=gemini-2.5-pro, input_tokens=300, output_tokens=80  (initial planning turn)
  tool.call.started      tool_name=gcs_stat
  gcs.stat.completed     uri=gs://my-bucket/recordings/conf.mp4, content_type=video/mp4
  tool.call.completed
  llm.call.completed     model=gemini-2.5-pro, input_tokens=180k, output_tokens=8k  (the analysis call — Vertex fetches gs:// directly)
  turn.ended             success=true
  session.ended
```

Note the absence of `gcs.signed_url.issued` and any bridge-tool
events — Vertex's IAM-based `gs://` fetch eliminates those steps
from the chain. The "heavy" LLM call (180k input tokens) is the
one where Vertex actually fetches and analyzes the video.

Cost rolls up: child's `llm.call.completed` events carry input/output
tokens; an external observer (or the future TUI integration per
0020 follow-up) computes Vertex cost and surfaces it. The
`subagent.returned` payload includes the child's `cost_usd`
(currently 0.0 in v0.1 of dispatch per 0020 — to be filled in when
that follow-up lands).

---

## File layout (in the `arc-sub-agent-video` repo)

```
arc-sub-agent-video/
├── pyproject.toml                       # arc.subagents entry point
├── README.md                            # install, GCS dependency, usage, costs
├── CLAUDE.md                            # developer guide (already drafted; refresh post-impl)
├── LICENSE
├── .gitignore
├── src/arc_sub_agent_video/
│   ├── __init__.py
│   ├── spec.py                          # SubAgentSpec + build()
│   └── prompts/
│       └── system.md                    # ~700 lines, the methodology + schema rules
└── tests/
    ├── __init__.py
    ├── conftest.py                      # StubBuildContext
    ├── test_spec.py                     # spec shape, config-injected fields, expected_output
    └── test_prompt.py                   # prompt content assertions (key rules present)
```

**Plus arc-core changes** for the new provider:

```
v2/src/arc/providers/vertex_gemini.py             # NEW — Vertex AI provider (~250 lines)
v2/src/arc/providers/__init__.py                  # +case for "vertex_gemini" in build()
v2/src/arc/runtime/subagents/spec.py              # +params: dict field (additive)
v2/src/arc/runtime/subagents/runner.py            # thread spec.params into child provider config
v2/tests/unit/test_vertex_provider.py             # NEW — provider tests with mocked Vertex SDK
v2/tests/unit/test_subagent_spec.py               # +tests for new params field
```

No changes to `arc-plugin-gcs`. The plugin's existing tools
(`gcs_stat`, `gcs_upload`) work as-is with the new provider —
the provider observes tool-result shape and auto-attaches the
`gs://` URI, no plugin-side awareness required.

**No new pyproject deps.** The `google-genai` package (already a
v2 dep for the existing `gemini` provider) supports Vertex AI
mode out of the box via `Client(vertexai=True, ...)`. The
`vertex_gemini` provider just constructs the client differently.
Users who don't need Vertex incur no extra install cost; users
who do need it just need their GCP auth set up (already done for
the GCS plugin).

---

## Test plan

### `tests/test_spec.py` (in arc-sub-agent-video)
1. `build({}, ctx)` returns a `SubAgentSpec` with name `video_analyst`.
2. Spec is pinned to `provider="vertex_gemini"`, default `model="gemini-2.5-pro"`.
3. Tool allowlist is exactly `("gcs_stat", "gcs_upload")`.
4. `timeout_s=600`, `max_dispatches_per_session=3` (expensive default).
5. `expected_output` mentions every required top-level field.
6. Description mentions cost ranges.
7. `build({"video_fps": 0.5})` produces a spec whose system_prompt
   reflects `video_fps=0.5`.
8. `build({"max_duration_s": 1800})` produces a spec referencing
   30 minutes in the refusal rule.
9. `build({"auto_upload_prefix": "vid/{date}/"})` produces a spec
   whose prompt contains today's date in the prefix.
10. `build({"vertex_project_id": "my-proj", "vertex_region": "us-east1"})`
    produces a spec whose `params` field carries those values.

### `tests/unit/test_vertex_provider.py` (in arc v2)
1. Provider construction with valid `project_id` + `region` config succeeds.
2. Missing `project_id` raises clear `ValueError("vertex_gemini provider requires params.project_id")`.
3. Missing `google-genai` raises clear `ImportError` (reuses the same dep check as the existing `gemini` provider).
4. `chat()` with a text-only LLMRequest round-trips correctly (mocked Vertex SDK).
5. `chat()` with a tool result carrying `{uri: "gs://...", content_type: "video/mp4"}` auto-appends a `Part.from_uri` to the request (mocked, captured request inspection).
6. `chat()` ignores tool results with non-media content types (no auto-attach for `application/json` etc.).
7. `chat()` returns an LLMResponse with `.raw` populated for byte-faithful replay.
8. Vertex 403 maps to a clear error message mentioning `roles/aiplatform.user`.
9. Vertex 429 maps to a clear quota-exhausted message.

### `tests/test_prompt.py`
1. System prompt mentions: "fat schema", "subject registry",
   "representative_bbox", "[ymin, xmin, ymax, xmax]".
2. Mentions all 7 top-level optional fields by name
   (`scene_attributes`, `object_counts`, `subjects`, `transcript`,
   `actions`, `scenes`, `auto_upload_uri`).
3. Includes the 6-step tool playbook in order.
4. Includes the hard-limit refusal rule.
5. Includes "STRICT" output-format rules.

### Integration smoke (manual, opt-in)
Add a script under `scripts/smoke_test.sh` (not auto-run):
1. Place a 30-second test video at `gs://<test-bucket>/smoke/test.mp4`.
2. From the parent agent: "use subagent_video_analyst to summarize
   gs://<test-bucket>/smoke/test.mp4".
3. Verify the result JSON has `source_uri`, `duration_s`, `summary`.
4. Verify the `subagent.returned` event on the parent's bus shows
   `cost_usd > 0` (once cost roll-up is wired) and reasonable
   `turns`/`tool_calls` counts.

Real-video integration tests are NOT in CI — they cost money and
need real Gemini API + GCS access. The smoke script is for
developer-run validation.

---

## Open questions

1. **SDK choice for the new provider.** Two options: `google-genai`
   with `vertexai=True` (the existing arc gemini dep, supports Vertex
   mode out of the box) vs. `google-cloud-aiplatform` (the dedicated
   Vertex SDK, ~6x larger). **Resolution: use `google-genai` with
   `vertexai=True`.** Reuses the existing dep (no new install). The
   two providers (gemini + vertex_gemini) share request-translation
   helpers via a small shared module; only the client construction
   differs (`Client(api_key=...)` vs. `Client(vertexai=True,
   project=..., location=...)`).

2. **Should the sub-agent verify the URI's bucket is in the parent's
   `allowed_buckets` BEFORE auto-uploading a local file?** The
   `gcs_upload` call would fail anyway if the default_bucket isn't
   allowed, but a pre-check is friendlier. **Resolution:** rely on
   the existing `gcs_stat` / `gcs_upload` allowlist check. The
   error is clear; no special pre-check needed.

3. **Should `subagent.dispatched` events carry the input URI?**
   Useful for telemetry / audit ("which videos did the agent
   process?"). **Resolution:** yes, via `context_bundle` field
   passed to `SubAgentRunner.dispatch` (already in payload per
   0020). The parent agent puts the URI there explicitly.

4. **What about videos with sensitive content (PII, faces)?**
   Vertex sees the raw frames. If the user uploads a video with
   sensitive content, Vertex AI's terms of service apply (which
   are more permissive than the public Gemini API's — Vertex data
   stays in your GCP project). **Resolution:** document the data
   handling characteristics in the sub-agent's README. Vertex is
   actually better here than the public API.

5. **Speaker diarization quality on long videos?** Gemini's speaker
   IDs in the same video are usually consistent within a single
   call but can drift on >30min content. **Resolution:** document
   in `notes` field; user should accept some speaker-ID drift on
   long videos and re-run with narrower windows for high-stakes
   transcript work.

6. **Should the sub-agent emit a `video_analyst.completed` event
   with the parsed-JSON metrics (duration, subject count,
   action count)?** Useful for analytics but the JSON output is
   already in the `subagent.returned` event's payload (via the
   child's last assistant message). **Resolution:** no new event;
   readers extract from the existing event.

7. **`object_counts.estimated_unique` versus `max_at_any_frame` —
   are both meaningful?** For "how many cars passed in this video",
   estimated_unique is what the user wants (count of distinct
   vehicles). For "how crowded was the room?", max_at_any_frame.
   **Resolution:** both, always, per the schema. The model
   estimates as best it can. `estimated_unique` is generally
   `≥ max_at_any_frame` for moving subjects.

8. **What region to default to for Vertex?** Regions differ in
   model availability — `gemini-2.5-pro` is most reliably
   available in `us-central1`. **Resolution:** default
   `vertex_region: us-central1`. Document supported regions in
   the README; users override per-spec if they need a different
   region for data-residency reasons.

9. **Does the auto-attach behavior on the provider need an opt-out?**
   What if a future tool returns `{uri: "gs://...", content_type:
   "video/mp4"}` but the user does NOT want Vertex to fetch the
   video? Unlikely but possible. **Resolution:** opt-out via a
   sentinel in the tool result (e.g., `_skip_vertex_attach: true`)
   recognized by the provider. Don't ship this in v1 unless a use
   case appears.

---

## State

Designed. Not yet implemented.

Implementation order:
1. **arc v2 — vertex_gemini provider.** Write
   `arc/providers/vertex_gemini.py` using `google-genai` with
   `vertexai=True`. Register in `providers/__init__.py`. Add
   optional dep `google-genai[vertex]` (or pip extra). Unit
   tests with mocked Vertex SDK.
2. **arc v2 — SubAgentSpec `params` field.** Additive change to
   `runtime/subagents/spec.py`. Thread through
   `runtime/subagents/runner.py` into child provider config.
   Test that overrides land in the child's ProviderConfig.
3. **arc-sub-agent-video.** Write the spec + system.md, all the
   tests. References `provider="vertex_gemini"`; carries
   `vertex_project_id` / `vertex_region` via `params`.
4. **GCP one-time setup.** Enable Vertex AI API on the project,
   grant `roles/aiplatform.user` to the SA. Document in the
   sub-agent's README.
5. **Cross-repo integration smoke.** Manual test against a real
   30-second video in your bucket. Verify the JSON output shape +
   cost numbers match the design's estimates.

---

## Implementation notes

(Filled in after the spec lands.)
