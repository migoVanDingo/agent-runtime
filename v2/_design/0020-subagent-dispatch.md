# 0020 — Sub-agent dispatch

## Motivation

The runtime today is single-tier: one agent loop, one provider, one
tool registry, one system prompt.  Two real use cases break that model:

1. **Provider specialization.**  Gemini has native video ingest (mp4
   upload, timestamped captions, spatial grounding).  No other provider
   we ship does.  If the parent session is running Claude or a local
   model, video analysis is impossible — unless the runtime can dispatch
   a *child* agent that pins its own provider.
2. **Context discipline.**  Domain-heavy tasks (reverse-engineering a
   binary, summarizing a research corpus, analyzing a video) generate
   enormous intermediate context — disassembly listings, transcripts,
   per-frame detections — that the parent agent doesn't need to see.
   Forcing this through the parent's context window wastes tokens and
   crowds out the actual task.

The motivating wedge is video: an `arc-plugin-video` package would ship
`extract_frames`, `transcribe_audio`, `detect_speakers` as tools AND a
`video_analyst` sub-agent pinned to Gemini that orchestrates them and
returns a structured JSON timeline.  The parent session — whatever
provider it's running — calls one tool, gets the JSON back, never
touches a raw frame.

This phase ships **sub-agent dispatch** as a parallel first-class
extension system, separate from but architecturally similar to plugins.

---

## Scope

In:
- New API package `arc.subagent_api` (v0.1) — frozen surface for
  sub-agent authors.  Exports `SubAgentSpec`, `SubAgentResult`,
  `SubAgentDispatchContext`, `SubAgentError`, `SubAgentTimeoutError`,
  `SubAgentRecursionError`.
- New runtime package `arc/runtime/subagents/` — `Runner`, `Registry`,
  `SubAgentTool` adapter, recursion tripwire.
- New entry-point group `arc.subagents` for plugin-shipped specs.
- New `subagents:` block in `config.yml` for config-defined specs and
  per-spec overrides (provider, model, timeout, system prompt).
- New CLI: `arc subagents {list,info,enable,disable,show <name>}`.
- New event types: `subagent.dispatched`, `subagent.returned`,
  `subagent.aborted`, `subagent.quota_exceeded`,
  `subagent.circuit_tripped`, `subagent.retry_attempted`.
  All carry the `agent_scope` field already in place per
  `runtime/scope.py`.
- **Dispatch guards** — per-session per-spec quota (default 5),
  consecutive-failure circuit breaker (hard-lock after 2 in a row),
  transient-error internal retry (capped at 2 with exponential backoff).
- **TUI surfaces** — config-time checkbox menu (`arc subagents` no
  args) with per-spec detail view and a test-dispatch action;
  run-time status panel showing live child progress during a
  dispatch (turn count, latest tool call, cost, elapsed).
- Child session writes a full nested session dir under
  `$ARC_HOME/sessions/<parent_sid>/subagents/<spec>-<child_sid>/` —
  replayable standalone, queryable as part of the parent.
- Sync dispatch only.  Parent's tool call blocks until child returns
  one structured result.
- Per-dispatch timeout, cancellation propagation on parent Ctrl+C.
- Built-in test spec only (no shipped user-facing built-in specs).

Out (deferred):
- **Async / parallel dispatch.**  The Runner interface is shaped so an
  `dispatch_async()` + `await_result()` pair can be added later without
  breaking sync callers, but no async machinery ships in this phase.
- **Recursion.**  Hard-prohibited at two layers (registry filter +
  contextvar tripwire).  No depth-2 escape hatch, no opt-in.  When a
  real use case appears, it gets its own design doc.
- **Streaming to parent.**  Child events go to telemetry/UI/logs;
  parent's LLM context only sees `subagent.dispatched` →
  `subagent.returned` as a single tool-call pair.  Streaming partial
  results would defeat context isolation.
- **In-tree user-facing specs.**  `arc` core ships zero domain
  specialists.  All real specs live in plugin packages or the user's
  config file.  A `_test_echo` spec ships only to exercise the runner
  in tests.
- **Cross-session sub-agent reuse / pinning.**  Each dispatch spins up
  a fresh child session.  No persistent child agent that lives across
  multiple dispatches.

---

## The conceptual line: sub-agent vs tool vs plugin

| | Tool | Sub-agent | Plugin |
|---|---|---|---|
| What it is | Pure function with a schema | Scoped child runtime with its own loop | Lifecycle hooks + optional tool pack |
| Input/output | Args in, result out | Task string + context bundle in, structured result out |  Hook events in, side-effects + tool contributions out |
| LLM involvement | None (or single-shot, opaque to runtime) | Full agent loop, its own provider/model | None directly |
| State | Stateless or session-scoped | Per-dispatch micro-session | Session-scoped via `on_session_start`/`on_session_end` |
| Telemetry | Wrapped in `tool.call.*` events | Wrapped in `subagent.*` events + full nested events.jsonl | Wrapped in hook-fire events |
| Author API | `arc.plugin_api` → `Tool`, `ToolError` | `arc.subagent_api` → `SubAgentSpec`, `SubAgentResult` | `arc.plugin_api` (hooks) |
| Discovery | Bundled in a plugin or built-in | `arc.subagents` entry-point OR config file | `arc.plugins` entry-point OR built-in |
| User CLI | (none — listed via parent plugin) | `arc subagents` | `arc plugins` |

A tool that internally calls an LLM (e.g., a summarization tool) is
**still a tool**, not a sub-agent.  The line is: sub-agent = scoped
child runtime with its own agent loop; tool = single-shot function,
even if it happens to call an LLM internally.

A pip package can ship both a plugin (tools, hooks) and a sub-agent —
`arc-plugin-video` exposes the frame/audio tools via `arc.plugins` and
the `video_analyst` spec via `arc.subagents`.  Two separate registrations
in the same package, toggled independently in `arc plugins` and
`arc subagents`.

---

## Architecture

```
src/arc/
  subagent_api.py             ← NEW — v0.1 public surface re-export
  runtime/
    scope.py                  ← unchanged (already supports subagent:<name>)
    subagents/
      __init__.py             ← NEW — Spec, Result, Error types
      registry.py             ← NEW — discovery (entry-points + config)
      runner.py               ← NEW — sync dispatch, child session lifecycle
      tool_adapter.py         ← NEW — SubAgentTool: wraps a spec as a Tool
      tripwire.py             ← NEW — _inside_subagent contextvar + guard
  cli.py                      ← +`subagents` subcommand
  config.py                   ← +`_parse_subagents`
  defaults.py                 ← +DEFAULT_SUBAGENTS (= {})
tests/unit/test_subagent_runner.py
tests/unit/test_subagent_registry.py
tests/unit/test_subagent_tripwire.py
tests/unit/test_subagent_config.py
tests/unit/test_subagent_tool_adapter.py
tests/integration/test_subagent_dispatch_real.py
```

### The Spec

```python
@dataclass(frozen=True)
class SubAgentSpec:
    name: str                              # registry key, used in subagent_<name> tool
    description: str                       # shown to parent agent in tool schema
    provider: str                          # "anthropic" | "gemini" | "ollama" | ...
    model: str                             # provider-specific model id
    system_prompt: str                     # child's system prompt
    tools: tuple[str, ...]                 # tool names the child gets access to
    timeout_s: float = 300.0
    max_turns: int = 25
    api_key_env: str | None = None         # override; defaults from provider catalog
    base_url: str | None = None            # override; defaults from provider catalog
    expected_output: str | None = None     # appended to system prompt: "Return JSON shaped …"
    max_dispatches_per_session: int = 5    # parent-loop guard; ToolError when exhausted
    max_consecutive_failures: int = 2      # circuit breaker; hard-lock for the session when hit
    max_transient_retries: int = 2         # runner-internal retry cap for network/rate-limit/5xx
    source: Literal["builtin", "plugin", "config"] = "plugin"
    source_package: str | None = None      # for plugin specs, the dist name
```

`tools` is an explicit allowlist of tool names.  The child's tool
registry is built by intersecting the parent's full tool registry
(including plugin-contributed tools) with this allowlist.  A name in
the allowlist that isn't available at dispatch time is a hard error —
caught at spec registration when possible, at dispatch otherwise.

### The Result

```python
@dataclass(frozen=True)
class SubAgentResult:
    status: Literal["ok", "error", "timeout", "cancelled"]
    output: str                            # final assistant text (or structured-output JSON string)
    error_message: str | None
    child_session_id: str
    cost_usd: float
    turns: int
    tool_calls: int
    wallclock_s: float
    retries_attempted: int                 # transient retries the runner absorbed

    def to_tool_result(self) -> str:
        """Serialize as the string the parent's tool call returns."""
        return json.dumps({
            "status": self.status,
            "output": self.output,
            "error": self.error_message,
            "child_session_id": self.child_session_id,
            "metrics": {
                "cost_usd": self.cost_usd,
                "turns": self.turns,
                "tool_calls": self.tool_calls,
                "wallclock_s": self.wallclock_s,
            },
        })
```

The string returned by `to_tool_result()` is what the parent's LLM
sees.  Nothing else from the child's transcript reaches the parent's
context.

### The Runner

```python
class SubAgentRunner:
    """Spawn a child AgentSession for one dispatch and return its result."""

    def __init__(self, registry: SubAgentRegistry, arc_home: Path, bus: EventBus): ...

    def dispatch(
        self,
        spec_name: str,
        task: str,
        *,
        context_bundle: str | None = None,
        parent_session_id: str,
        parent_turn: int,
    ) -> SubAgentResult: ...
```

The runner:
1. Checks the tripwire (`_inside_subagent.get()`) — raises
   `SubAgentRecursionError` if already inside a sub-agent.
2. Looks up the spec from the registry (config overrides plugin spec
   overrides built-in defaults — see precedence below).
3. Resolves the child's tool registry by intersecting `spec.tools`
   with the parent's available tools.
4. Builds a child `Config`: provider section swapped per spec; tools
   filtered; system prompt set to `spec.system_prompt` (+ optional
   expected-output suffix); plugins limited to recorder + log_writer
   (no guard/safety_gate/sliding_window unless the spec opts in
   explicitly — children are short, scoped, and noisy plugins muddy the
   child's transcript).
5. Creates a child session dir at
   `$ARC_HOME/sessions/<parent_sid>/subagents/<spec_name>-<child_sid>/`.
6. Pushes scope `subagent:<spec_name>` and sets the
   `_inside_subagent` contextvar to True.
7. Emits `subagent.dispatched` on the **parent's** bus with `{spec_name,
   provider, model, child_session_id, parent_turn}`.
8. Constructs a fresh `AgentSession` with the child config, runs it
   with the initial user turn = `task` (prefixed with the optional
   `context_bundle`), with a wall-clock timeout enforced via
   `signal.SIGALRM` on Unix or a watchdog thread elsewhere.
9. On completion: extracts the final assistant message, packages a
   `SubAgentResult`, emits `subagent.returned` on the parent's bus
   with the metrics, restores scope + tripwire, returns the result.
10. On timeout: cancels the child's loop, the child writes a
    `session.aborted` event with `reason=timeout`, parent emits
    `subagent.aborted` with the same reason, returns a `SubAgentResult`
    with `status="timeout"`.
11. On Ctrl+C from the parent: the parent's signal handler sets a
    cancellation flag the runner checks between child turns; child
    aborts cleanly; result returned with `status="cancelled"`.

### The Tool Adapter

```python
class SubAgentTool(Tool):
    """Adapts a SubAgentSpec as a regular Tool callable by the parent agent."""

    name: str                              # f"subagent_{spec.name}"
    description: str                       # spec.description + boilerplate
    input_schema: ToolInputSchema          # {"task": str, "context_bundle": str?}

    def __init__(self, spec: SubAgentSpec, runner: SubAgentRunner): ...

    def __call__(self, *, task: str, context_bundle: str | None = None,
                 _session: SessionContext) -> str:
        result = self._runner.dispatch(
            spec_name=self._spec.name,
            task=task,
            context_bundle=context_bundle,
            parent_session_id=_session.session_id,
            parent_turn=_session.current_turn,
        )
        if result.status == "ok":
            return result.to_tool_result()
        # Surface failures as ToolError so the parent agent can recover.
        raise ToolError(f"sub-agent {self._spec.name}: {result.status} — {result.error_message}")
```

`SubAgentTool` instances are registered into the parent's tool registry
at session start, AFTER plugin tools are merged (so plugin tools are
available for spec.tools intersection).  Registration is skipped if
`_inside_subagent.get() is True` — the registry filter that prevents
recursion at discovery time.

### Dispatch guards: quota, circuit breaker, retry

Three independent mechanisms bound how many times a sub-agent can be
invoked or retried.  All state is per parent session, kept on the
`SubAgentRunner` keyed by spec name.  Nothing persists across sessions.

**Quota.**  Each spec has a `max_dispatches_per_session` (default 5).
The runner increments a counter on every dispatch attempt
(successful or not — quota is a cost ceiling, not a success ceiling).
When the counter equals the cap, the next dispatch fails immediately
with a `ToolError("sub-agent quota exceeded: video_analyst 5/5
dispatches used this session")`.  The parent agent has to make do
with prior results.  Emits `subagent.quota_exceeded` event on first
denial of each spec per session.

**Consecutive-failure circuit breaker.**  Each spec has a
`max_consecutive_failures` (default 2).  The runner tracks consecutive
non-OK results (status `error` or `timeout`) per spec.  On a successful
dispatch the counter resets to zero.  When the counter equals the cap,
the spec is marked "tripped" for the rest of the session — every
subsequent dispatch fails immediately with a
`ToolError("sub-agent circuit tripped: video_analyst failed 2 times in
a row; locked for this session")`.  No auto-unlock, no manual reset.
Emits `subagent.circuit_tripped` event when the breaker trips.

Quota and breaker are independent: a spec can hit the quota (cost
ceiling) without ever failing, or hit the breaker (reliability ceiling)
before the quota.  Whichever trips first wins, and both register
denials count against the quota counter.

**Transient-error internal retry.**  Network errors (`httpx.ConnectError`,
`httpx.ReadTimeout`), rate-limit responses (HTTP 429), and provider
5xx responses get retried inside the runner with exponential backoff
(0.5s, 2s, 8s), capped at `max_transient_retries` (default 2).  These
retries do NOT consume a dispatch slot (one logical dispatch = one
slot regardless of how many transient retries were absorbed).  Each
retry emits a `subagent.retry_attempted` event with `{spec, attempt,
error_class, backoff_s}`.  If all retries are exhausted, the dispatch
fails normally — surfaces to the parent as `status="error"` and counts
toward the circuit breaker.

Logical errors (timeout, child's tool error, model returned malformed
output, child hit `max_turns`) are **never** retried internally — they
surface to the parent immediately so the parent agent can adapt the
task, change the prompt, or give up.  The circuit breaker is the only
thing that caps re-attempts of logical errors.

Classification (transient vs. logical) reuses the provider-side
error classification already used by the top-level retry loop.

### The Registry

```python
class SubAgentRegistry:
    """Discovers specs from three sources, applies precedence, returns merged set."""

    def discover(self, config: Config) -> dict[str, SubAgentSpec]:
        builtins = self._load_builtins()           # currently {"_test_echo": ...}
        plugins = self._load_entry_points()        # arc.subagents group
        config_defined = self._load_config(config) # subagents: {} block
        return self._merge(builtins, plugins, config_defined)
```

Precedence (later wins):
1. Built-in defaults
2. Plugin-shipped specs (one spec per entry-point)
3. Config-file overrides

Config can both define *new* specs and *override fields* of existing
ones (e.g., pin provider, change model, tighten timeout).  Override is
field-level: missing fields in the config block inherit from the
underlying spec.  The merged spec carries `source` reflecting the
deepest layer that touched it ("plugin" if a plugin defined it and
config didn't override; "config" if config overrode any field).

### Recursion prohibition: two independent layers

**Layer 1 — registry filter (policy):** when a child `AgentSession`
constructs its tool registry, `SubAgentTool` adapters are simply not
added.  The child literally cannot see sub-agent dispatch as a tool.
Implemented in `AgentSession._merge_subagent_tools()` by checking
`_inside_subagent.get()`.

**Layer 2 — contextvar tripwire (safety net):** even if some bug or
trick produced a `SubAgentTool` reference inside a child, calling
`SubAgentRunner.dispatch()` checks the contextvar first and raises
`SubAgentRecursionError` immediately.

Both layers exist on purpose.  They catch different failure modes:
forgotten registry filter vs. clever bypass (closures, stale
references, tests that construct a Runner manually).  Cost is ~20
lines total.

### Child session: full nested AgentSession

The child runs a complete `AgentSession`, not a stripped-down loop.
This means all existing observability, plugins, replay, byte-faithful
recording works for children identically to top-level sessions —
because they ARE top-level sessions, just nested under the parent's dir.

Consequences:
- Child writes its own `events.jsonl`, `meta.json`, `session.log`,
  `config.snapshot.yml` under `<parent_sid>/subagents/<spec>-<child_sid>/`.
- `arc replay <child_sid>` works on a child session by giving the full
  nested path: `arc replay <parent_sid>/subagents/<spec>-<child_sid>`.
  Replay treats it as any other session.
- `arc log <parent_sid>` shows the parent's events including the
  `subagent.*` triplet.  To see the child's internals, run
  `arc log <parent_sid>/subagents/<spec>-<child_sid>`.
- Cost and token telemetry roll up naturally: each session's
  events.jsonl has the totals; the parent's `meta.json` is updated at
  `subagent.returned` to include the child's cost in a `subagent_costs`
  field (additive across multiple dispatches).

---

## Config surface

```yaml
# ~/.arc/config.yml

subagents:
  # Override a plugin-shipped spec — only the fields you list are touched
  video_analyst:
    timeout_s: 600
    model: gemini-2.5-pro              # plugin shipped gemini-2.5-flash

  # Define a brand-new spec from scratch — pure composition, no Python
  log_grepper:
    description: "Search log files for evidence of a specific pattern; return JSON summary."
    provider: anthropic
    model: claude-haiku-4-5
    system_prompt: |
      You are a focused log analyst.  Given a path and a pattern, use
      bash and read to find matching lines.  Return JSON shaped:
      {"matches": [{"file": str, "line": int, "context": str}], "total": int}.
    tools: [bash, read]
    timeout_s: 90
    expected_output: '{"matches": [...], "total": int}'
```

Override-only specs (top key matches an existing plugin spec) require
no `provider`/`model`/`system_prompt`/`tools` if the underlying spec
already has them.  New specs require all four.  `_parse_subagents` in
`config.py` enforces this and gives field-pointing errors.

---

## CLI surface

```
arc subagents                  list all discovered specs with source + status
arc subagents list             non-interactive print (same content)
arc subagents show <name>      pretty-print the merged spec (post-precedence)
arc subagents info <name>      same as show, kept for symmetry with arc plugins
arc subagents enable <name>    flip enabled=true in config
arc subagents disable <name>   flip enabled=false in config (still discovered, not callable)
```

`arc subagents` output:

```
Sub-agents (3 enabled, 1 disabled):

  ENABLED   video_analyst       gemini/gemini-2.5-pro    plugin: arc-plugin-video
            log_grepper         anthropic/claude-haiku   config
            _test_echo          gemini/gemini-2.5-flash  builtin
  DISABLED  briefbot_researcher anthropic/claude-sonnet  plugin: arc-plugin-briefbot
```

`enable`/`disable` writes through `arc/setup/writer.py` (same
comment-preserving writer plugins use).  Disabled specs don't get a
`SubAgentTool` registered — they're inert until re-enabled.

Toggle of plugin-shipped specs is independent of the plugin's overall
toggle: you can have `arc-plugin-video` enabled (tools available)
while `video_analyst` is disabled (sub-agent not callable), or vice
versa.

---

## TUI surfaces

Two distinct surfaces.  Both ship in this phase.

### Config-time menu — `arc subagents` (no args) and `/subagents` slash command

```
arc subagents — manage sub-agent specs

  [x] video_analyst            gemini / gemini-2.5-pro      plugin: arc-plugin-video
  [x] log_grepper              anthropic / claude-haiku     config
  [x] _test_echo               gemini / gemini-2.5-flash    builtin
  [ ] briefbot_researcher      anthropic / claude-sonnet    plugin: arc-plugin-briefbot

[space] toggle  [enter] details  [t] test-dispatch  [e] edit override  [q] quit
```

Pressing `enter` on a row opens the detail view:

```
video_analyst (plugin: arc-plugin-video)

  description       Analyze a video file and return a structured timeline JSON
                    with transcript, speaker boxes, scene boundaries.
  provider          gemini
  model             gemini-2.5-pro                            (overridden from -flash)
  system_prompt     [527 chars — press 'p' to view in pager]
  tools             extract_frames, transcribe_audio, detect_speakers, ls
  timeout_s         600                                       (overridden from 300)
  max_turns         25
  max_dispatches    5  per session
  failure_lock      after 2 consecutive failures
  expected_output   {"timeline": [...], "speakers": {...}}

  Recent dispatches (this $ARC_HOME):
    01JK4F...  2026-05-23 14:22  ok       3 turns   8 tool calls  $0.14  47s
    01JK4G...  2026-05-23 14:35  timeout  25 turns 41 tool calls  $0.31  600s

[t] test-dispatch  [e] edit override  [b] back  [q] quit
```

`t` (test-dispatch) prompts the user for a task string, dispatches the
spec interactively, streams the run-time status panel (see below)
until completion, and shows the result.  No parent agent involved —
the user IS the parent for this dispatch.  Invaluable for spec
authoring: you can iterate on system prompt + tool list and verify
the spec works end-to-end before integrating it into a real session.

`e` (edit override) opens `$EDITOR` on the relevant `config.yml`
`subagents.<name>` block; on save, the menu re-discovers and shows
the updated values.

`/subagents` inside the running TUI follows the same subprocess pattern
as `/replay` (per 0019 implementation note 7): spawns `arc subagents`
as a subprocess, gives it the terminal, redraws the parent TUI on exit.

### Run-time status panel — during a sub-agent dispatch in a session

When a parent session dispatches a sub-agent, the TUI's render swaps
the spinner for a structured status line under the current turn:

```
> analyze the conf room recording

  ▸ Dispatching subagent_video_analyst…
    └ gemini-2.5-pro · turn 3/25 · 8 tool calls · $0.04 · 14s elapsed
    └ now: transcribe_audio(path="/tmp/conf-room.mp4")
```

Updates are driven by events from the child's bus (subscribed to by
the TUI for the lifetime of the dispatch).  The transcribed text and
tool results are NOT shown — that's the whole point of context
isolation — only structural progress (turn count, current tool name,
running cost).

On completion, the status block collapses to a one-line summary in
the transcript:

```
✓ subagent_video_analyst → ok  (3 turns, 8 tool calls, $0.14, 47s)
  result: {"timeline": [...], "speakers": {...}}  [320 chars — '/show <id>' for full]
```

On abort:

```
✗ subagent_video_analyst → timeout  (25 turns, 41 tool calls, $0.31, 600s)
  /log <parent>/subagents/video_analyst-<child> for child transcript
```

Implementation: `tui/subagent_status.py` owns the live block, hooks
the child's EventBus on `subagent.dispatched`, unhooks on
`subagent.returned`/`subagent.aborted`.  Pure prompt_toolkit /
Rich — no new deps.

---

## Failure modes

| Failure | Behavior |
|---|---|
| Spec name collision between two plugin entry-points | First one wins (load order); a `subagent.discovery.collision` warning is emitted; second is dropped. |
| Spec references a tool that doesn't exist | Registration succeeds with a warning; dispatch fails immediately with `SubAgentError("tool 'foo' not available")`. |
| Provider in spec isn't installed (e.g., gemini spec, no `google-genai`) | Dispatch fails with `SubAgentError` carrying the underlying `ImportError`; child session is never created. |
| Timeout exceeded mid-turn | Child loop cancels at next turn boundary; child writes `session.aborted (reason=timeout)`; parent gets `SubAgentResult(status="timeout")`; tool call raises `ToolError` to the parent agent. |
| Parent receives SIGINT during dispatch | Runner sets cancellation flag; child aborts at next turn boundary; child writes `session.aborted (reason=user_cancelled)`; parent's signal handler proceeds with parent-level cancellation. |
| Child hits its own `max_turns` | Child ends normally with `session.ended (reason=max_turns)`; parent gets `SubAgentResult(status="ok")` with whatever the last assistant message was; up to the parent agent to judge whether the output is useful. |
| Child's provider returns an error | Child session aborts; parent gets `SubAgentResult(status="error")` with the provider's error message. |
| Child somehow calls `subagent_*` (registry filter bypassed) | Tripwire raises `SubAgentRecursionError`; child surfaces this as a tool error; child can recover or end the turn. |
| Config defines a spec referencing an unknown provider | Caught at `_parse_subagents`, exits 2 with the same "known providers" list `build()` emits. |
| Plugin spec defines a tool whose plugin is disabled | Same as "tool doesn't exist" — dispatch fails clearly. |
| User Ctrl+C during dispatch, child is mid-tool-call | The tool call completes (we don't interrupt tool execution mid-call — same policy as top-level sessions); child aborts at the next turn boundary. |
| Parent agent calls the same spec 6th time in one session | `ToolError("quota exceeded: <spec> 5/5 used")`; counter does not increment past cap; `subagent.quota_exceeded` emitted on first denial. |
| Spec fails twice in a row (timeout, error, etc.) | Spec is hard-locked for the session; every subsequent dispatch immediately raises `ToolError("circuit tripped: <spec> failed 2× in a row")`; `subagent.circuit_tripped` emitted on trip. |
| Network blip during child's first LLM call | Runner retries internally with backoff (0.5s, 2s); each retry emits `subagent.retry_attempted`; transparent to parent if any retry succeeds; surfaces as `status="error"` only if all retries exhaust. |
| Quota exhausted AND circuit tripped (both fire on same dispatch) | Quota check happens first; quota error is what the parent sees; circuit-tripped state is still recorded. |

---

## Observability

Three new event types in `runtime/events.py`:

- `subagent.dispatched` — `{spec_name, provider, model, child_session_id,
   parent_turn, task_chars}`.  Emitted on the **parent's** bus when
   the runner is about to spawn the child.
- `subagent.returned` — `{spec_name, child_session_id, status,
   cost_usd, turns, tool_calls, wallclock_s, output_chars}`.  Emitted
   on the parent's bus on normal completion.
- `subagent.aborted` — `{spec_name, child_session_id, reason, turns,
   wallclock_s}` where `reason` is `"timeout" | "user_cancelled" |
   "provider_error" | "recursion_blocked"`.  Emitted on the parent's
   bus on abnormal completion.
- `subagent.quota_exceeded` — `{spec_name, cap, denied_task_chars}`.
   Emitted on parent's bus when a dispatch is refused due to quota.
- `subagent.circuit_tripped` — `{spec_name, consecutive_failures,
   triggering_child_session_id}`.  Emitted on parent's bus when the
   breaker trips.  Subsequent dispatch denials in the same session
   re-use the existing trip state and do NOT re-emit.
- `subagent.retry_attempted` — `{spec_name, attempt, error_class,
   backoff_s, child_session_id}`.  Emitted on parent's bus on each
   transient retry.  Useful for telemetry on flaky providers.

The child's own session bus emits the full normal event stream
(`session.started`, `turn.started`, `llm.call.completed`, etc.) into
its own `events.jsonl`.  These do NOT propagate to the parent's bus —
each session owns its own bus.  The two are linked only by the
`child_session_id` field carried in the parent's events.

`agent_scope` field on every event is auto-populated from
`runtime/scope.py`.  Parent events carry `scope=main`; child events
carry `scope=subagent:<spec_name>`.  Queries / log views / cost roll-ups
can filter or aggregate by scope.

One-line formatters added to `plugins/log_writer/formatter.py` for the
three new event types so `arc log` is readable.

---

## File layout

```
src/arc/subagent_api.py              ← NEW — v0.1 public surface
src/arc/runtime/subagents/
  __init__.py                        ← NEW — re-exports
  registry.py                        ← NEW
  runner.py                          ← NEW
  tool_adapter.py                    ← NEW
  tripwire.py                        ← NEW
  builtins/
    __init__.py                      ← NEW — registers _test_echo
    test_echo.py                     ← NEW — minimal spec for tests
src/arc/runtime/subagents/guards.py  ← NEW — quota counter, circuit breaker, retry classifier
src/arc/runtime/events.py            ← +SUBAGENT_DISPATCHED, SUBAGENT_RETURNED, SUBAGENT_ABORTED,
                                       +SUBAGENT_QUOTA_EXCEEDED, SUBAGENT_CIRCUIT_TRIPPED, SUBAGENT_RETRY_ATTEMPTED
src/arc/runtime/loop.py              ← child-session construction path
src/arc/config.py                    ← +_parse_subagents
src/arc/defaults.py                  ← +DEFAULT_SUBAGENTS = {}
src/arc/cli.py                       ← +`subagents` subcommand
src/arc/plugins/log_writer/formatter.py  ← +six new event lines
src/arc/setup/writer.py              ← already comment-preserving; new subagents: block handled generically
src/arc/tui/subagent_menu.py         ← NEW — config-time checkbox menu + detail view + test-dispatch
src/arc/tui/subagent_status.py       ← NEW — run-time status panel during a dispatch
src/arc/tui/app.py                   ← wires subagent_status into the live render
src/arc/tui/render.py                ← +collapsed one-line summary for completed/aborted dispatches

tests/unit/test_subagent_api_surface.py   ← v0.1 exports are stable
tests/unit/test_subagent_registry.py
tests/unit/test_subagent_config.py
tests/unit/test_subagent_runner.py
tests/unit/test_subagent_tripwire.py
tests/unit/test_subagent_tool_adapter.py
tests/unit/test_subagent_quota.py
tests/unit/test_subagent_circuit_breaker.py
tests/unit/test_subagent_transient_retry.py
tests/unit/test_subagent_menu.py
tests/unit/test_subagent_status_panel.py
tests/integration/test_subagent_dispatch_real.py  ← skipped without API keys
```

No new third-party deps.  Reuses existing `AgentSession`, EventBus,
provider registry, config parser, writer.

---

## Test plan

Unit (`test_subagent_api_surface.py`):
1. `from arc.subagent_api import SubAgentSpec, SubAgentResult,
   SubAgentDispatchContext, SubAgentError, SubAgentTimeoutError,
   SubAgentRecursionError` succeeds — surface frozen for v0.1.
2. No symbol re-exported from `arc.runtime.subagents` accidentally
   (e.g., `Runner`, `Registry` are NOT public).

Unit (`test_subagent_registry.py`):
1. Built-in `_test_echo` is always present.
2. Plugin entry-point discovery picks up a fake entry-point group.
3. Config-defined spec is loaded.
4. Config field-level override merges (only `model` changes, other
   fields inherit).
5. Config spec referencing unknown provider → clear parse error.
6. Spec name collision between two plugins → first wins, warning
   emitted.

Unit (`test_subagent_config.py`):
1. New-spec config block requires provider/model/system_prompt/tools.
2. Override-only config block requires only the overridden fields.
3. Disabled specs are loaded but flagged `enabled=False`.
4. Round-trip through `writer.py` preserves comments and ordering.

Unit (`test_subagent_runner.py`):
1. `dispatch()` constructs a child session, runs it, returns
   `SubAgentResult(status="ok")` (mocked provider).
2. Child session dir is created at the nested path.
3. Parent's bus receives `subagent.dispatched` + `subagent.returned`.
4. Child's bus is separate — its events do NOT appear on parent's bus.
5. Timeout fires → `status="timeout"`, child writes
   `session.aborted (reason=timeout)`.
6. Cancellation flag → `status="cancelled"`.
7. `expected_output` is appended to the child's system prompt.
8. Spec referencing missing tool → `SubAgentError` at dispatch.
9. Provider mismatch (e.g., gemini spec on a system with no
   google-genai) → `SubAgentError` surfacing ImportError.

Unit (`test_subagent_tripwire.py`):
1. Top-level dispatch: tripwire is False, registry filter allows
   SubAgentTool registration in the parent.
2. Inside a child session: tripwire is True, SubAgentTool registration
   is skipped during child registry build (layer 1).
3. Manually instantiating `SubAgentTool` inside a child and calling it
   → `SubAgentRecursionError` (layer 2).
4. After dispatch completes: tripwire is restored to False.

Unit (`test_subagent_tool_adapter.py`):
1. `SubAgentTool.name == f"subagent_{spec.name}"`.
2. Schema declares `task` (required) and `context_bundle` (optional).
3. Call with `status="ok"` returns JSON string with the expected
   shape (parseable, includes `metrics`).
4. Call with `status="error"` raises `ToolError`.

Unit (`test_subagent_quota.py`):
1. Spec with `max_dispatches_per_session=3`: dispatches 1-3 succeed,
   dispatch 4 raises `ToolError("quota exceeded …")`.
2. `subagent.quota_exceeded` event emitted once on first denial; not
   re-emitted on subsequent denials of the same spec.
3. Quota is per-spec, not global — exhausting `video_analyst` does
   not affect `log_grepper`.
4. Quota state resets when a new parent session starts.
5. Per-spec override via config — spec ships with 5, config sets 2,
   third dispatch is the first denial.

Unit (`test_subagent_circuit_breaker.py`):
1. Two consecutive `status="error"` dispatches → breaker trips on
   the second; third dispatch raises `ToolError("circuit tripped …")`
   without invoking the runner.
2. One failure followed by one success → counter resets; subsequent
   failures need 2 more in a row to trip.
3. Timeout and error both count as failures; success and cancelled
   do not (cancelled is user-initiated, not a spec problem).
4. `subagent.circuit_tripped` emitted exactly once on the trip; not
   on subsequent denials.
5. Breaker is per-spec.
6. Trip persists for the rest of the session; no auto-reset.

Unit (`test_subagent_transient_retry.py`):
1. Mock provider raising `httpx.ConnectError` on first call, OK on
   second → runner returns `status="ok"`, `retries_attempted=1`.
2. Mock raising 429 three times → runner exhausts retries (default 2),
   returns `status="error"`; `subagent.retry_attempted` emitted twice.
3. Mock raising `ToolError` (logical) → NOT retried; surfaces as
   `status="error"` after first failure.
4. Backoff sequence is 0.5s, 2s (verifiable via captured sleep calls).
5. Transient retries do NOT consume a dispatch slot — one dispatch
   with two retries still counts as 1 toward quota.

Unit (`test_subagent_menu.py`):
1. Menu lists discovered specs grouped by source (builtin/plugin/config).
2. Toggle action writes through `setup/writer.py` and preserves
   surrounding comments.
3. Detail view renders the merged spec (post-precedence) including
   overrides marked clearly.
4. Test-dispatch action invokes the runner with the user-provided
   task and renders the result block.
5. Recent-dispatches list reads from `$ARC_HOME/sessions/*/subagents/`
   (most recent 5).

Unit (`test_subagent_status_panel.py`):
1. Panel subscribes to child bus on `subagent.dispatched`, unsubscribes
   on `subagent.returned`.
2. Live block renders the latest `tool.call.started` tool name.
3. Cost + elapsed update on `llm.call.completed` events.
4. On `subagent.aborted`, panel renders the abort summary with reason.
5. Panel does NOT render tool results or LLM response text (context
   isolation invariant).

Integration (`test_subagent_dispatch_real.py`):
1. Skip unless both `ANTHROPIC_API_KEY` and `GEMINI_API_KEY` are set.
2. Define a config-only spec pinning Gemini with `ls` + `bash` tools.
3. Run a parent session against Anthropic with one user turn asking
   it to "use the test sub-agent to count files in /tmp".
4. Assert parent's events.jsonl includes
   `subagent.dispatched`/`subagent.returned`.
5. Assert child's events.jsonl exists at the nested path and has at
   least one `llm.call.completed` from Gemini.
6. Assert cost in parent's `meta.json` includes the child's cost.

Smoke:
- Add a `_test_echo` spec to config-disabled by default.
- Enable it with `arc subagents enable _test_echo`.
- Run a session, prompt the agent to call `subagent__test_echo` with
  any task.
- Inspect `arc log <parent_sid>` for the dispatch triplet.
- Inspect `arc log <parent_sid>/subagents/_test_echo-<child_sid>` for
  the child's full transcript.
- Confirm `arc replay <parent_sid>/subagents/_test_echo-<child_sid>`
  replays the child standalone.

---

## Open questions

1. **Should child sessions count toward `$ARC_HOME` session listing
   (`arc sessions`)?**
   Resolution: yes, but indented under the parent in the default
   output (and filtered out from `arc sessions --top-level`).  They
   are real sessions; pretending otherwise breaks replay symmetry.

2. **Should the parent's cost cap (max_cost plugin from 0019, if
   enabled) include child costs?**
   Resolution: yes.  The plugin lives on the parent's bus; when the
   parent emits `subagent.returned`, the plugin reads the
   `cost_usd` field and accumulates.  Defending against the
   "child runs 100 turns and burns the budget" failure mode is
   exactly the cap's job.

3. **What if two sub-agent specs declare the same tool name (via
   their `tools` allowlist) but mean different things?**
   Resolution: there's no conflict.  `tools` is an allowlist of names
   into the parent's already-resolved tool registry.  If the parent
   has one `bash` tool, both specs get the same `bash`.  Sub-agents
   don't bring their own tools; the plugin that ships the sub-agent
   ships the tools separately via `arc.plugins`.

4. **Should sub-agents have access to plugins like `safety_gate`
   inside their session?**
   Resolution: opt-in per spec via a `plugins:` field in `SubAgentSpec`
   (default: `["jsonl_recorder", "log_writer"]`).  Most specs don't
   want guard/safety_gate cluttering the child's loop, but a spec
   that runs destructive bash can opt in.  Document this in
   `arc-plugin-template`.

5. **Should the parent see a sub-agent's intermediate `tool.call.*`
   events on its own bus, even if filtered out of LLM context?**
   Resolution: no.  Parent's bus and child's bus are separate.
   Cross-bus event propagation adds complexity and a leakage risk
   (a plugin on parent's bus reacting to a child's tool call would
   break the isolation guarantee).  Telemetry that wants to see
   everything queries both events.jsonl files; the scope tag makes
   the join trivial.

6. **Should `arc replay` of a parent session re-dispatch the
   sub-agents (live), or replay them deterministically from the
   child's recorded events?**
   Resolution: deterministic by default — replay the parent's events
   including the recorded `subagent.returned` payload, treat the
   sub-agent dispatch as a recorded tool result.  `--live-subagents`
   flag (future) re-runs them live, mirroring `--live-llm`.  Out of
   scope for this phase; record now in a way that allows both later
   (the `subagent.returned` event already carries the child's full
   output).

7. **Should a sub-agent spec be able to declare its own
   sub-agents-as-tools (i.e., choose which of the registered specs
   it can dispatch)?**
   Resolution: moot under the no-recursion rule — children get zero
   `subagent_*` tools.  Question reopens only if recursion is ever
   allowed.

8. **Default quota — too tight at 5?**
   Resolution: 5 is a reasonable starting point but obviously
   spec-dependent.  Plugin authors set their own default in the
   shipped `SubAgentSpec`; users override per-spec in config.  Video
   analysis is expensive and slow → ship at 2.  log_grepper is cheap
   and fast → ship at 20.  The runtime default is for newly-defined
   config-only specs that didn't set one.

9. **Does the circuit breaker count a quota-exceeded denial as a
   failure?**
   Resolution: no.  Quota and breaker are independent failure modes
   measuring different things (cost ceiling vs. reliability).  A
   quota denial means "stop spending", not "this spec is broken".
   The dispatch never actually runs, so there's no spec behavior to
   judge.

10. **Should the run-time status panel be opt-out for users who
    prefer a quieter TUI?**
    Resolution: yes, `tui.subagent_status_panel: true` config flag,
    defaults to true.  Users who run many short dispatches and find
    the panel noisy can disable; the collapsed one-line summary in
    the transcript on completion is always shown regardless.

11. **Test-dispatch from the menu — does it count toward the spec's
    quota?**
    Resolution: no.  The menu's test-dispatch is for spec authoring
    and debugging; counting it would conflate two different
    activities.  The `SubAgentRunner` accepts a `count_against_quota:
    bool = True` parameter; the menu passes `False`.  Events are
    still emitted normally (with an `interactive=True` tag) so the
    test dispatches show up in telemetry.

---

## State

Designed.  Not yet implemented.

---

## Implementation notes

(Filled in during implementation, per repo convention; this section
exists for the post-landing pass.)
