# arc ecosystem — architecture

*Code-review deliverable. Describes how arc v2 and its satellites fit together.
Diagrams are ASCII so they render anywhere.*

---

## 1. The landscape

The ecosystem is one **runtime** surrounded by **out-of-tree extensions** that
attach through three narrow, versioned seams.

```
                         ┌───────────────────────────────────────────────┐
                         │                  arc v2 runtime                │
                         │   (ReAct loop · hook bus · events · providers) │
                         └───────────────────────────────────────────────┘
                            ▲              ▲                 ▲
             plugin_api ────┘   subagent_api ──┘    mcp (built-in plugin) ─┘
             (in-process)       (scoped child)       (external processes)
                 │                    │                       │
    ┌────────────┼─────────┐   ┌──────┴────────┐      ┌───────┴───────────┐
    │            │         │   │               │      │                   │
 ghidra      websearch   gcs  container_expert video  cos MCP server   proxmox, …
 angr        briefbot         (Gemini/local)  (Vertex) (Docker plane)   (third-party)
```

Three extension mechanisms, chosen deliberately:

| Seam | Public API | Runs where | For |
|---|---|---|---|
| **Plugin** | `arc.plugin_api` v0.1 | in the parent process, in the hook chain | tools + lifecycle/policy hooks |
| **Sub-agent** | `arc.subagent_api` v0.2 | a scoped child `AgentSession` | delegating a whole task to a focused agent with its own provider |
| **MCP server** | the `mcp` built-in plugin | a separate process (stdio / HTTP) | standalone/third-party services (e.g. cos) |

The guiding principle, stated in `v2/CLAUDE.md` and honored throughout:
**the runtime mediates, the model drives, plugins extend.** Policy is not in
the runtime; it is in plugins. Every observable moment is an event. Everything
user-tunable lives in `config.yml` (seeded by `defaults.py`).

---

## 2. arc's three layers

```
src/arc/
┌─ Layer 1 — runtime/ (always present, minimal, no policy) ──────────────┐
│   loop.py     the ReAct loop (the only thing that "drives")            │
│   hooks.py    12 hook Protocols + payload dataclasses                  │
│   bus.py      HookRegistry (priority-ordered) + EventBus              │
│   events.py   RuntimeEvent + the EventType catalog (~60 types)         │
│   scope.py    session/turn/scope contextvars                          │
│   ids.py      self-contained ULID generator                           │
│   subagents/  sub-agent dispatch (runner, registry, guards, tripwire) │
└───────────────────────────────────────────────────────────────────────┘
┌─ Layer 2 — plugins/ + mcp/ (all optional, quarantinable) ─────────────┐
│   guard, safety_gate, jsonl_recorder, log_writer, pause_resume,       │
│   sliding_window_context               (built-ins)                    │
│   mcp/            MCP client subsystem, exposed AS a built-in plugin   │
│   + external plugins discovered via the arc.plugins entry-point group │
└───────────────────────────────────────────────────────────────────────┘
┌─ Layer 3 — providers/ + tools/ + tui/ + setup/ + replay/ … ───────────┐
│   providers/  gemini, anthropic, vertex_gemini, ollama, llama_cpp     │
│   tools/      ls, bash_exec (the only built-in tools)                 │
│   tui/        prompt_toolkit + Rich inline UI                         │
│   setup/      the sidebar+content setup hub                           │
│   replay/ resume/ rerun/   the five replay modes                      │
└───────────────────────────────────────────────────────────────────────┘
```

**Dependency rule (mostly upheld):** Layer 1 must not import Layer 2/3. A plugin
must import only `arc.plugin_api`, never `arc.tools.base` / `arc.runtime.*`.
(The audit checks both directions — see `03-code-quality.md`.)

---

## 3. The ReAct loop + hook chain

One user turn drives this cycle until the model stops calling tools. Hooks are
the extension points; the runtime fires them, plugins implement them.

```
 user input
    │
    ▼
 on_turn_start ────────────── plugins may rewrite the UserInput
    │
    ▼
 pack_context ─────────────── context strategy trims history (sliding_window …)
    │
    ▼
 before_llm_call ──────────── plugins may rewrite the LLMRequest
    │
    ▼
 ╔════════════╗   provider.chat(req)   ┌──────────────┐
 ║  Provider  ║ ─────────────────────▶ │ LLMResponse  │  (.raw = byte-faithful)
 ╚════════════╝                        └──────────────┘
    │
    ▼
 after_llm_call ───────────── plugins may rewrite the LLMResponse
    │
    ├── text only ─────────────────────────────▶ emit, end turn
    │
    └── tool_use blocks
           │
           ▼
        for each tool call:
           before_tool_call ── guard/safety_gate → ToolCall | ToolDenial
             │        │
             │        └── denied → synthesize a denial result, skip execution
             ▼
           tool.execute()
             │
             ▼
           after_tool_call ── plugins may rewrite the ToolResult
             │
             ▼
           feed results back as the next assistant/tool messages ──┐
                                                                    │
    ◀───────────────────────── loop (bounded by max_iterations) ───┘

 on_turn_end ──────────────── plugins observe the outcome
```

Cross-cutting, every iteration:
- **`pause_check`** fires between steps; the `pause_resume` plugin raises to
  checkpoint. Sub-agents reuse it for watchdog cancellation.
- **`on_event`** fires for every `RuntimeEvent`; recorders/log-writers subscribe.

### The hook protocol (11 live + 1 dead)

```
lifecycle : on_session_start · on_session_end · on_turn_start · on_turn_end
llm       : before_llm_call · after_llm_call
tools     : before_tool_call · after_tool_call
context   : pack_context
observe   : on_event
control   : pause_check
dead      : assess_step   ← defined in hooks.py + ALL_HOOK_NAMES + bus mapping,
                            but the runtime NEVER fires it. A plugin implementing
                            it silently never runs. See 03-code-quality.md — wire
                            it or delete the contract.
```

Each is a `Protocol` in `hooks.py`. A plugin implements any subset. The
`HookRegistry` orders implementations by the integer priority in each plugin's
`hooks_order` (lower = earlier). Built-ins pin explicit priorities (guard's
`before_tool_call: 10` runs before safety_gate's `20`); external plugins get
auto-registered at priority 50.

---

## 4. Observability & the five replay modes

**Events are the source of truth.** Nothing else is authoritative — the human
log, the meta files, replay, resume, branch and rerun all rebuild from
`events.jsonl`.

```
 every observable moment ──▶ RuntimeEvent ──▶ EventBus ──▶ on_event subscribers
                                                              │
                        ┌─────────────────────────────────────┼──────────────┐
                        ▼                     ▼                ▼              ▼
                 jsonl_recorder          log_writer      TUI renderer   metrics
                 events.jsonl            session.log     (scrollback)   (subagent)
```

The **byte-faithful replay contract**: every `LLMResponse.raw` carries the
provider's full response as a JSON-faithful dict, so replay reconstructs a run
without re-calling the API. The five modes:

| Mode | Command | What it does |
|---|---|---|
| 1 time-travel | `arc resume <id> --at-turn N` | rewind to a turn, continue |
| 2 deterministic replay | `arc replay <id>` | re-emit from `.raw`, no API |
| 3 live-LLM replay | `arc replay <id> --live-llm` | replay inputs, fresh LLM |
| 4 branch | `arc resume … --prompt` | fork a new path at a turn |
| 5 rerun | `arc rerun <id>` | replay user inputs vs a fresh agent |

This is arc's signature capability and the strongest differentiator vs. a
black-box CLI agent (see `04-strengths-and-differentiators.md`).

---

## 5. Plugin model (in-process extensions)

```
 startup
   │
   ▼
 discovery.py  ── walks entry_points(group="arc.plugins")
   │                each → build(config, build_ctx) -> plugin object
   ▼
 enablement.py ── first run: prompt the user once, persist to config.yml
   │
   ▼
 AgentSession.start()
   ├─ on_session_start fired
   ├─ _merge_plugin_tools()      provides_tools() → registry
   ├─ _merge_subagent_tools()    subagent specs → SubAgentTool adapters
   └─ _bind_bus_to_tools()       tools with bind_bus(bus) get the event bus
```

Two plugin **shapes**:
- **Session-scoped** (briefbot, gcs): own a handle/DB/model via
  `on_session_start`/`on_session_end`; contribute tools built in the lifecycle.
- **Stateless tool pack** (websearch): `build()` makes the tools;
  `provides_tools()` returns them.

**Quarantine:** a plugin that throws more than `plugins.failure_threshold`
times (default 3) is disabled for the session — *the runtime handles this*, so
plugins are told **not** to catch defensively.

---

## 6. Sub-agent dispatch

A sub-agent is **pure declarative data** (`SubAgentSpec`) that the runtime turns
into a scoped child `AgentSession`. The parent sees one tool: `subagent_<name>`.

```
 parent AgentSession
   │  model calls subagent_container_expert(task="…")
   ▼
 SubAgentTool.execute
   ▼
 SubAgentRunner.dispatch
   ├─ guards.py       per-session quota / consecutive-failure circuit
   ├─ tripwire.py     recursion prohibition (depth-1 only)
   ├─ build child ProviderConfig  (spec.provider/model/base_url/params)
   ├─ build child AgentSession    (fresh registry+bus — ISOLATED)
   │     tools = spec.tools ∩ parent registry
   │     system = spec.system_prompt (+ expected_output sketch)
   │  with subagent_scope():            ← inside_subagent() == True here
   │     child.run_turn(task)           ← full ReAct loop, watchdog timeout
   │        every child event ──▶ _bridge_progress ──▶ parent bus
   ▼                                        (SUBAGENT_PROGRESS → TUI scrollback)
 final child message  ──▶  SubAgentResult  ──▶  parent's tool result (structured JSON)
```

Key properties:
- **Context isolation** — the child's working transcript never enters the
  parent's context; the parent gets only the final structured result.
- **Provider independence** — the child picks its own provider/model. This is
  why video analysis pins Vertex Gemini and container work pins Flash — and why
  either can be repointed at a local Ollama/llama.cpp model via config override
  (the Registry merges field-level overrides onto the spec after `build()`).
- **Two enforcement models** — a sub-agent is enforced by *capability* when only
  the child can do the work (video → Vertex ingest), or by *policy* when it
  shares tools with the parent (containers → the `guard` `delegate_only_tools`
  rule, gated on `inside_subagent()`).

---

## 7. MCP client subsystem

arc consumes external MCP servers as first-class, gated, observable tools. MCP
is a **built-in plugin** (`mcp`), not a top-level config section — its servers
live under `plugins.enabled[mcp].config.servers`.

```
 sync arc                         │  async MCP SDK (anyio)
                                  │
 McpManager  ── run_coroutine_    │   background asyncio loop (one thread)
   .call_tool  threadsafe ───────▶│     ├─ actor coroutine per server
                                  │     │    (open · use · close in one task —
                                  │     │     anyio cancel-scope safe)
 McpTool (adapter)                │     └─ stdio / streamable-HTTP transports
   name = {prefix}_{tool}         │
   schema → arc ToolInputSchema   │
```

- The async SDK is bridged to sync arc via a background loop + per-server actor
  coroutines + `run_coroutine_threadsafe`.
- `tool_prefix` is `str | None`: unset → server name; explicit `""` → native
  tool names (so cos exposes `container_run`, not `container_container_run`).
- Tool schemas from MCP are adapted into arc `Tool`s; for Gemini, schemas are
  further sanitized (`anyOf`/`additionalProperties` stripped) before the call.

---

## 8. container-orchestration-service (cos)

A standalone, harness-agnostic Docker control plane. Consumed by arc via MCP;
usable directly via `cos` CLI or the core library.

```
 cos.mcp_server (FastMCP, streamable-HTTP :8770)   cos.cli (`cos …`)
        │                                                │
        └───────────────────┬────────────────────────────┘
                            ▼
                     cos.core.backend.DockerBackend  ── docker-py ──▶ Docker daemon
                            │
        ┌───────────────────┼─────────────────────────────────────┐
        │ WorkloadSpec (spec.py)   EnvSpec: image|build|base+prov  │
        │ run_job (ephemeral) · ensure_env (persistent)            │
        │ networks · images (build-once-run-many) · gc             │
        │ state = container/image/network LABELS (labels.py)       │
        └──────────────────────────────────────────────────────────┘
```

- **State is labels, not a DB** — `cos.managed=true` (+ owner/lifecycle/ttl/name)
  reconstructs everything after a restart.
- **Job-dispatch vision (design 0024):** an engine plugin authors a structured
  spec (data), cos runs it in a container. This is how the angr-on-macOS wheel
  problem dies — angr runs in a Linux container, never on the host.

**Trust model — read `02-security-audit.md`.** cos drives the Docker daemon,
which is root-equivalent. Its MCP server is unauthenticated on loopback. That is
the single most important security boundary in the whole ecosystem.

---

## 9. End-to-end: "run three containers that talk to each other"

Putting all the seams together — the exact flow observed in testing:

```
 user ─▶ arc (haiku) ─▶ [guard: container_* is delegate-only] ─▶ denied
                     └─▶ subagent_container_expert(task)
                            │
                     SubAgentRunner ─▶ child AgentSession (Gemini Flash)
                            │  inside_subagent()==True → guard allows container_*
                            │  child drives its own ReAct loop:
                            │     image_build(relay-app) once
                            │     network_create(relay-net)
                            │     container_ensure ×3 (image=relay-app)
                            │     container_logs / curl  ← health-check
                            │  every tool call ─▶ SUBAGENT_PROGRESS ─▶ your scrollback
                            ▼
                     structured JSON {status: healthy, containers, checks}
                            │
        cos MCP tools ◀─────┘  (container_ensure → DockerBackend → Docker daemon)
```

Six components cooperate — guard (policy), sub-agent (isolation + methodology +
provider choice), MCP (transport), cos (Docker), events (you watching it happen),
config (all of it tunable) — and none of them is the runtime having an opinion
baked in. That is the architecture working as intended.
