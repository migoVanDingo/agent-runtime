# agent-runtime

A production-grade AI agent runtime with structured events, sandboxed shell
execution, multi-provider LLM support, artifact memory, and council-reviewed
planning.

## What it does

The agent takes a free-form user message, routes it through a staged pipeline,
executes a plan using tool-calling, and returns a synthesized response. It is
designed to be safe by default (sandboxed shell, path policy enforcement, safety
guard), observable (structured event stream alongside human-readable logs), and
extensible (pluggable providers, toolsets, workflows).

```
User message
    │
    ▼
RoutingStage        — single LLM call: classify intent + optional inline answer
DirectInlineStage   — short-circuit for clean conversational answers
WorkflowMatchStage  — match to a pre-built plan template (regex / classifier hint)
PlanningStage       — LLM planner: generate a multi-step plan
EntityCriticStage   — scrub hallucinated file paths from the plan
ValidatorStage      — structural validation gate
CouncilStage        — N-agent adversarial review of the plan
ExecutionStage      — step-by-step tool execution with monitor + guard
SynthesizerStage    — synthesize final response from step results
DirectExecutionStage — free-form ReAct loop (also the ABORT fallback)
```

## Key subsystems

| Subsystem | Location | Description |
|-----------|----------|-------------|
| Pipeline | `src/runtime/pipeline.py` | Ordered stage runner with OK/DONE/RETRY/ASK_USER/ABORT semantics |
| Providers | `src/providers/` | Anthropic, OpenAI, Ollama, Grok, DeepSeek, Gemini — unified interface |
| Toolsets | `src/tools/` | 11 toolsets (file_io, shell, analysis, crypto, web, data, artifacts, search, git, document, briefbot) |
| Sandbox | `src/runtime/sandbox/` | Docker (default) or host bash execution; path policy enforcement |
| Artifact store | `src/runtime/artifact_store.py` | SQLite-backed named artifact registry with decay, RAG recall, workflow discovery |
| Persistence | `src/db/` + `src/runtime/persistence.py` | SQLModel/Alembic ORM for sessions, plans, steps |
| Council | `src/runtime/council.py` | Multi-agent deliberation primitive (independent or debate mode) |
| Context manager | `src/runtime/context_manager.py` | AFM-inspired non-destructive context packing with fidelity levels |
| Events | `src/runtime/events/` | Structured JSONL event stream sidecar alongside human logs |
| Workflows | `src/workflows/` | Pre-built deterministic plan templates |

## Configuration

- **`config.yml`** — runtime tuning (context budget, sandbox policy, event settings, council scaling, etc.)
- **`.env`** — secrets (API keys, database URLs)

Both are loaded once at startup via `src/app_config.py`.

## Running

```bash
# Install dependencies
pip install -r requirements.txt

# Start the agent
PYTHONPATH=src python src/main.py

# With verbose logging
PYTHONPATH=src python src/main.py --verbose

# Resume a prior session
PYTHONPATH=src python src/main.py --resume
```

## Testing

```bash
# Run full test suite
make test

# Or directly
pytest tests/ -q
```

## Observability

Every session produces two files:
- `_logs/{session_id}.log` — human-readable structured log
- `_events/{session_id}.jsonl` — machine-readable event stream

Export events for analysis:
```bash
python scripts/export_events.py --events-dir _events --out _events/events.csv
```

## Design notes

- [`_plans/0051-architecture-and-pattern-review-claude.md`](_plans/0051-architecture-and-pattern-review-claude.md) — architectural review
- [`_plans/0053-runtime-refactor-design-claude.md`](_plans/0053-runtime-refactor-design-claude.md) — original refactor design
- [`_plans/0064-refactor-plan-v2.md`](_plans/0064-refactor-plan-v2.md) — active implementation plan
