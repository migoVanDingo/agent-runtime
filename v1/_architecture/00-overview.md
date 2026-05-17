# 00 — One-page overview

arc is an LLM agent runtime. The user types something into a terminal
TUI; the agent does multi-step work using tools, possibly delegating
context-heavy sub-tasks to specialised child agents; the user gets a
response. The runtime owns all control flow — tools and skills are
passive participants that return data and never drive retries,
escalations, or replans.

## Layers, top-down

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Frontend                                                               │
│  - arc-tui (prompt_toolkit, alt-screen, mouse-friendly selection)        │
│  - arc --cli (legacy text loop)                                          │
│  - arc --print (one-turn headless)                                       │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │  AgentService protocol
                               │  send / events / pause / resume / cancel
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Service layer (src/service/)                                           │
│  InProcessAgentService — wraps agent.call() on a worker thread,         │
│  bridges runtime events → typed AgentEvent stream for the UI.            │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Agent (src/agent.py)                                                   │
│  Owns: Messenger, ContextStrategy, ToolRegistry, SkillRegistry,         │
│  Planner, Council, Synthesizer, ActionGuard, UserGate.                  │
│  Sets the runtime.scope.MAIN scope. Wraps each .call() in a            │
│  parent_context so sub-agents can find their parent.                    │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Pipeline (src/runtime/pipeline.py)                                     │
│  Runs stages in order. Each stage produces a StageResult                │
│  (OK / DONE / RETRY / ASK_USER / ABORT). Stages are passive — they      │
│  return state and the pipeline decides what to do.                      │
│                                                                         │
│  Runtime stages (enter "runtime" scope, use the smaller AFM budget):    │
│  - RoutingStage, SkillHintStage, ExecutionMonitor, ImportanceScorer      │
│                                                                         │
│  Main-provider stages (default "main" scope, larger budget):            │
│  - PlanningStage, SkillExpansionStage, EntityCriticStage,               │
│    ValidatorStage, CouncilStage, ExecutionStage, ContinuationStage,    │
│    SynthesizerStage, DirectExecutionStage                               │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
        ┌──────────────────────┴────────────────────────┐
        ▼                                               ▼
┌───────────────────────┐                ┌────────────────────────────────┐
│  Tools                │                │  Sub-agents                    │
│  (src/tools/)         │                │  (src/runtime/subagents/ +     │
│                       │                │   src/tools/implementations/   │
│  Each tool returns a  │                │   subagents/)                  │
│  string. Sandboxed    │                │                                │
│  by ActionGuard       │                │  Spawned by SubAgentTool       │
│  policy + the host    │                │  (a BaseTool wrapping a        │
│  sandbox manager.     │                │  SubAgentSpec). Each runs a    │
│                       │                │  scoped child Agent with its   │
│                       │                │  own context, returns text or  │
│                       │                │  structured JSON to parent.    │
│                       │                │                                │
│                       │                │  Enter "subagent:<name>" scope │
│                       │                │  for the duration of the run.  │
└───────┬───────────────┘                └────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  Providers (src/providers/)                                             │
│  Anthropic, OpenAI, Gemini, Grok, DeepSeek, Ollama — all behind         │
│  BaseProvider. Each chat() call is instrumented: emits llm.call.*       │
│  events with full prompt/response (blob-paged when large), cost,        │
│  tokens, latency, model identity.                                       │
└─────────────────────────────────────────────────────────────────────────┘

Crosscutting infrastructure (used by every layer above):

  runtime.events     — bus + schema v2 + JSONL/blob sinks + summary writer
  runtime.scope      — contextvar: "main" | "runtime" | "subagent:<name>"
  runtime.identity   — session/turn/pipeline/plan/step/tool_call IDs
  runtime.artifact_store — cross-session memory, decay, recall, RAG
  runtime.persistence — agent.db (SQLModel + Alembic)
  rag                — LanceDB chunk store + global warehouse
  session_paths      — ARC_HOME-rooted layout for everything on disk
```

## Key invariants — the things that shouldn't break

1. **Runtime owns control flow.** Tools/skills/sub-agents return data;
   they never decide retries, replans, escalations, or pause/cancel.
   The pipeline + monitor make those decisions based on returned data.
2. **`runtime/` never imports from `service/` or `ui/`.** `service/`
   never imports from `ui/`. The boundary lets us swap frontends.
3. **Sub-agents cannot spawn sub-agents.** Hard-prohibited via registry
   filter + contextvar tripwire (see doc 04).
4. **Every LLM call's context is scope-aware.** `runtime.scope` is the
   single source of truth for which budget AFM uses, which tag goes on
   log records, and which `agent_scope` lands on telemetry events
   (see doc 03).
5. **All on-disk state lives under `ARC_HOME`.** Sessions, RAG, artifact
   store, Ghidra cache, blob storage. The project directory stays clean
   (see doc 07).
6. **Telemetry is comprehensive and lossless.** Every meaningful runtime
   decision emits a structured `RuntimeEvent`. Large content pages to
   blobs; nothing important is sampled out (see doc 05).

If you're about to change something that bumps against one of these,
update the corresponding doc and the relevant plan in `_plans/`.
