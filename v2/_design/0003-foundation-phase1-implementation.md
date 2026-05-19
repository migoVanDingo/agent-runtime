# 0003 — Foundation (Phase 1): Implementation Notes

**Status:** complete (all 11 tasks #51–#61 done)
**Phase:** 1
**Implements:** the contract in `0001-foundation-phase0-design.md`
**Companion:** `0002-foundation-phase1-gemini-sdk-byte-fidelity.md` (resolved sub-decision)

This doc captures what was actually built in phase 1, what changed from the
spec, and bugs caught by end-to-end testing.

---

## 1. What landed (so far)

| Task | File(s) | Status |
|------|---------|--------|
| #51 Project skeleton | `pyproject.toml`, `Makefile`, `README.md`, `.env.example`, `.gitignore`, `src/arc/` package | ✅ |
| #52 Config + bootstrap | `arc/config.py`, `arc/bootstrap.py`, `arc/defaults.py` | ✅ |
| #53 Event schema + scope | `arc/runtime/events.py`, `arc/runtime/scope.py`, `arc/runtime/ids.py` | ✅ |
| #54 Hook protocols + bus | `arc/runtime/hooks.py`, `arc/runtime/bus.py` | ✅ |
| #55 Gemini provider | `arc/providers/base.py`, `arc/providers/gemini.py`, `arc/providers/__init__.py` | ✅ |
| #56 Tool base + `ls` | `arc/tools/base.py`, `arc/tools/ls.py`, `arc/tools/__init__.py` | ✅ |
| #57 ReAct loop | `arc/runtime/loop.py` | ✅ |
| #58 JSONL recorder plugin | `arc/plugins/jsonl_recorder/`, `arc/plugins/__init__.py` | ✅ |
| #59 `arc` CLI | `arc/cli.py` | ✅ |
| #60 Inline TUI | `arc/tui/app.py`, `arc/tui/render.py` | ✅ |
| #61 Hello-world acceptance | `tests/integration/test_hello_world.py` | ✅ |

**Test coverage:** 136 unit tests + 7 hello-world integration tests + 1 Gemini provider integration test = **143 tests, all green** (~20s total runtime).

## 2. Deviations from the phase 0 spec

### 2.1 Default model name was wrong

The spec listed `gemini-3.1-flash-live-preview`. The Gemini model API returns
404 for that name — it doesn't exist. The closest published name is
`gemini-3.1-flash-lite-preview` ("live" was a typo for "lite"). Updated:

- `defaults.py`, `_design/0001-foundation-phase0-design.md`,
  `_design/0002-foundation-phase1-gemini-sdk-byte-fidelity.md`,
  `tests/unit/test_config.py`

### 2.2 Switched Google SDK package

The spec said `google-generativeai`. That package is deprecated upstream
(prints a deprecation notice at import time). Switched to `google-genai`
(v2.3.0+). `pyproject.toml` reflects this. Byte-fidelity experiment was run
against the new SDK and confirmed clean — see doc 0002.

### 2.3 ID prefix format

Spec showed `ses_01HXYZ...` (snake_case + underscore separator). The
in-code convention became `SES01HXYZ...` (uppercase + no underscore) —
matches the look of session IDs in v1's logs (e.g., `SES01KRV1XJ7WK4...`)
and scans cleanly as a single token. Tests updated to match.

### 2.4 Recorder hooks_order in default config

The phase 0 spec snippet only registered `jsonl-recorder` against `on_event`.
That misses `on_session_start` (recorder needs to create its session dir
before any event arrives) and `on_session_end` (recorder needs to stamp
meta.json + index.jsonl on exit). Default config now registers all three:

```yaml
hooks_order:
  on_session_start: 10
  on_event: 100
  on_session_end: 10
```

### 2.5 Two extra config keys (the threshold scan)

User-requested mid-phase: scan for hardcoded user-tunables and move to config.
Two surfaced and moved to `plugins.*`:

- `plugins.failure_threshold` (was `DEFAULT_FAILURE_THRESHOLD = 3` in `bus.py`)
- `plugins.exception_message_max_chars` (was inline `[:500]` in `bus.py`)

`HookRegistry.__init__` now requires both as explicit kwargs (no defaults) so
callers must pass them from `config.plugins.*`.

### 2.6 Three new `runtime.*` config keys

The ReAct loop needed a system prompt and two wrap-up messages. Per the
no-hardcoded-defaults principle, they went into config:

- `runtime.system_prompt` — base prompt for every turn
- `runtime.iteration_cap_message` — injected when `max_iterations` is hit
- `runtime.tool_call_cap_message` — injected when `max_tool_calls_per_turn` is hit

### 2.7 New `ContentBlock.metadata` field

Added during the live Gemini end-to-end test. Required for Gemini 3+'s
`thought_signature` — see §3.2 below. Generic enough that other providers can
attach vendor-specific fields without polluting the universal type.

## 3. Bugs caught + fixed during implementation

### 3.1 Hook re-entry recursion in the bus

**Symptom:** First end-to-end recorder test triggered `RecursionError` in
`bus._record_failure`.

**Root cause:** When an `on_event` plugin raises, the registry emits a
`plugin.hook.failed` event via the bus. That event fans out to `on_event`
subscribers — including the broken plugin — which can re-fail and re-trigger
the same path. Infinite recursion.

**Fix:** Re-entry guard in `HookRegistry._record_failure`: when
`hook_name == "on_event"`, skip emitting the failure event. The failure count
still increments so auto-disable still works. Locked in by
`test_on_event_failure_does_not_recurse_infinitely`.

### 3.2 `start()` / `end()` event ordering vs hook ordering

**Symptom:** Same recorder test — events were emitted before
`on_session_start` ran, so the recorder hadn't created its session dir yet.

**Root cause:** `AgentSession.start()` was emitting `session.started`
before firing `on_session_start`. The recorder needs the hook to fire
*first* so its directory exists when events start arriving.

**Fix:** Reordered both lifecycle methods:

- `start()`: fire `on_session_start` → emit `session.started`
- `end()`: emit `session.ended` → fire `on_session_end`

This way the recorder is ready before events arrive, and the recorder's
final writes (meta.json stamping, index.jsonl append) happen after the
final event is in the log.

### 3.3 Gemini 3+ requires `thought_signature` on function_call echoes

**Symptom:** First end-to-end `arc run` call: first LLM call succeeded
(tool_use), tool dispatched fine, **second LLM call failed** with
`400 INVALID_ARGUMENT: Function call is missing a thought_signature`.

**Root cause:** When the model returns a `function_call` part, Gemini 3+
attaches a `thought_signature` (bytes). To send the conversation back in a
subsequent turn, you must echo the same `thought_signature` on the same
function_call part. The runtime was dropping it.

**Fix:** Two layers:

1. Added `ContentBlock.metadata: dict | None` field in `runtime/hooks.py`
2. Gemini provider captures `part.thought_signature` into metadata on
   response translation; re-emits it on request translation
3. Loop's `_block_to_dict` base64-encodes bytes in metadata so the event
   log stays JSON-safe

`metadata` is a generic escape hatch; any provider can attach vendor-specific
fields without changing the universal type.

## 4. Operational state

### 4.1 What works today (verified end-to-end with real Gemini)

```bash
arc bootstrap                                    # creates ~/.arc-v2/
arc run "List files in /tmp/foo"                # full ReAct loop, real LLM
arc sessions                                    # lists recorded sessions
arc show <session_id>                          # pretty-prints recorded events
arc config show / arc config path              # config inspection
arc --home <path>                              # ARC_HOME override
```

The hello-world acceptance from spec §10 was verified manually: 10 expected
event types fire in order, recording is well-formed JSONL, canonical
content matches what was sent on the wire. Task #61 will formalize this as
an automated integration test.

### 4.2 What doesn't work yet

- **`arc replay <id>`** — design exists in §10.3; implementation is v2.0.5.
- **Pause/resume, branch, agent-rerun** — phases v2.1.5 and v2.2.
- **`bash_exec` tool + guard plugin** — phase v2.1.

### 4.3 What's intentionally absent (and why)

Per phase 0 non-goals (§2 of the spec):

- No planner, monitor, council, validator, skills
- No RAG, no artifact store, no context manager
- No sub-agents
- No multi-provider abstraction (Gemini-only end-to-end)
- No sandbox isolation in phase 1 (host backend only via `bash_exec` in phase 2.1)
- No async runtime
- No filesystem snapshotting for branch/time-travel — forward-only is documented

## 5. Statistics

```
v2/
  src/arc/             :  ~2,000 lines Python (14 files)
  tests/unit/          :  ~1,800 lines (9 files)
  tests/integration/   :  ~240 lines (2 files)
  _design/             :  3 docs
  config.yml default   :  ~90 lines YAML

Test count             :  136 unit + 8 integration  (143 total, all green)
Test runtime           :  ~20 s (most spent in the live Gemini calls)
End-to-end             :  arc run + arc (interactive) both work with real Gemini
```

The minimal core (everything in `arc/runtime/` + `arc/cli.py` + `arc/config.py`
+ `arc/bootstrap.py`) totals about **1,000 lines of code** for a working
interactive agent with structured telemetry, plugin architecture, retry
policy, full lifecycle hooks, cooperative cancellation hooks, ReAct loop,
inline TUI with scrollback, and slash commands. Compared to v1's analogous
code surface, the simplification target is holding.

## 6. Lessons (record so phase 2 can learn from them)

1. **End-to-end smoke tests catch real bugs.** Both the recursion and the
   Gemini thought_signature issues were invisible to unit tests with mocks.
   Live API + filesystem + plugin chain is qualitatively different from
   each piece tested in isolation. Plan to live-smoke every phase before
   declaring it done.

2. **The "no hardcoded defaults" principle paid off twice already.**
   Once for the explicit threshold scan, once for the runtime cap messages —
   in both cases the principle made the right answer obvious rather than
   arguable.

3. **The recorder shape (start_hook → event_hook → end_hook with that
   ordering) generalizes.** Any "session-scoped resource" plugin (DB
   persister, metrics emitter, etc.) will want the same lifecycle. Worth
   documenting as a pattern for plugin authors when we add the second
   plugin.

4. **Provider-specific quirks need an escape hatch in the universal types.**
   `ContentBlock.metadata` is that hatch. Resist the urge to plumb specific
   field names through every layer.

5. **Field ordering in events.jsonl matters and is fragile.** Python's
   `json.dumps` preserves dict insertion order, so `RuntimeEvent.to_dict`
   constructs in spec order explicitly rather than relying on dataclass
   field order. Tests assert the order survives a round-trip.

## 7. What's next

Phase 1 is closed. Next milestone is **v2.0.5: replay validation** (criterion
4 from spec §10.3). It needs:

- A replayer that reads `events.jsonl`, injects recorded LLM responses
  in place of provider calls, and re-runs the loop
- An integration test that asserts the replayed session produces a
  byte-identical events.jsonl (modulo timestamps/event_ids — those are
  re-generated, but the message/tool content sequence must match exactly)

Then **v2.1: bash + guards** (bash_exec tool, guard plugin with allowlist/
blocklist/escalation, the "create dir, write poem, summarize" test).

Then **v2.1.5: pause + resume** — the pause_check hook gets a real
implementation, time-travel becomes possible.

Then **v2.2: branch + agent-rerun** — modes 3, 4, 5 from spec §11.

## 8. Phase 1 closing observations

Phase 1 came in essentially on the design spec — 1 typo (model name) and a
few obvious additions to config (the threshold scan + cap messages). No
structural surprises. The hook catalog from spec §4 has been exercised by
the runtime (which fires 9 of the 12 hooks) and 2 plugins (recorder, TUI)
without revealing any missing extension points.

The Gemini `thought_signature` bug (§3.3 above) was the one real surprise.
It points at a class of provider-specific quirks that the universal types
need to accommodate via `metadata` rather than via new fields. Worth
remembering when adding the next provider.

The acceptance test in §10.3 was the right gate. It caught nothing that
the unit tests missed, BUT having it pass against the real API gave a
qualitatively different confidence than the unit tests alone. Plan to
write equivalent acceptance tests for v2.0.5, v2.1, v2.1.5, v2.2 — each
phase has a small set of "did the foundation hold" questions, and an
automated test answers them in seconds.
