# agent-runtime

A ground-up curriculum for building production-grade AI agent systems — from a raw tool-calling loop to a full runtime infrastructure with adaptive memory, failure detection, policy enforcement, and learning.

This repo is structured as a series of 12 progressive projects. Each one builds on the last. By the end you will have built something close to a Claude Code-style coding assistant, sitting on top of a real agent runtime infrastructure.

## Who this is for

Software engineers who understand systems but are new to agentic AI. You should be comfortable reading and writing code. You do not need an ML background — we build up to the learning components gradually.

## What you will build

```
Application Layer         ← Project 12: coding CLI
Agent Orchestration       ← Projects 1–3: ReAct loop, tools, memory
AI Runtime Infrastructure ← Projects 8–9: AFM + VIGIL (Cruz 2026)
  ├── Adaptive Focus Memory (AFM)
  └── VIGIL failure detection + recovery
Model Serving             ← Project 4: provider abstraction (Anthropic / OpenAI / Ollama)
External Tools + Env      ← Projects 1–2: bash, files, search, APIs
Observability             ← Project 5: passive trace logging
Policy + Safety           ← Project 7: permission system, allowlists
RAG                       ← Project 6: embeddings, retrieval
Learning                  ← Projects 10–11: imitation learning, RL feedback
```

## Projects

| # | Directory | Concept | Status |
|---|-----------|---------|--------|
| 1 | [`_projects/01-raw-tool-agent/`](_projects/01-raw-tool-agent/) | Tool schemas, ReAct loop, message history | — |
| 2 | [`_projects/02-coding-assistant/`](_projects/02-coding-assistant/) | File I/O, bash execution, safety gates | — |
| 3 | [`_projects/03-persistent-memory/`](_projects/03-persistent-memory/) | Episodic memory, context windowing | — |
| 4 | [`_projects/04-provider-abstraction/`](_projects/04-provider-abstraction/) | Unified interface: Anthropic + OpenAI + Ollama | — |
| 5 | [`_projects/05-observability/`](_projects/05-observability/) | Passive trace logging, metrics, replay | — |
| 6 | [`_projects/06-rag/`](_projects/06-rag/) | Embeddings, vector store, retrieval pipeline | — |
| 7 | [`_projects/07-policy-safety/`](_projects/07-policy-safety/) | Permission system, allowlists, safety gates | — |
| 8 | [`_projects/08-afm/`](_projects/08-afm/) | Adaptive Focus Memory: context compression during execution | — |
| 9 | [`_projects/09-vigil/`](_projects/09-vigil/) | Failure detection, recovery, rollback mid-run | — |
| 10 | [`_projects/10-imitation-learning/`](_projects/10-imitation-learning/) | Learn from demonstration traces | — |
| 11 | [`_projects/11-rl-feedback/`](_projects/11-rl-feedback/) | Reward signals, policy improvement | — |
| 12 | [`_projects/12-full-runtime/`](_projects/12-full-runtime/) | Everything integrated, production CLI | — |

## References

Two papers ground the architecture of this entire curriculum:

- **Bin Xu (2026)** — *AI Agent Systems: Architectures, Applications, and Evaluation* — taxonomy of agent components, orchestration patterns, evaluation. [`_references/bin-xu-ai-agent-systems.md`](_references/bin-xu-ai-agent-systems.md)
- **Christopher Cruz (2026)** — *AI Runtime Infrastructure* — formalizes the execution-time control layer (AFM + VIGIL). [`_references/cruz-ai-runtime-infrastructure.md`](_references/cruz-ai-runtime-infrastructure.md)

## Language

Projects 1–9 use **Python**. It is the simplest path through the agent and ML concepts.
The provider abstraction (Project 4) is designed so the runtime can be ported to TypeScript later.

You will need:
- Python 3.11+
- An Anthropic API key (Projects 1–3, 8–9)
- An OpenAI API key (Project 4, optional)
- Ollama installed locally (Project 4, optional)

## How to use this repo

Each project lives in its own directory under `_projects/`. Read the `README.md` inside before writing any code. The README contains the concept explanation, architecture, step-by-step build guide, and success criteria.

When returning to this repo after a break, read `PLAN.md` first to reorient, then open the project you are on.