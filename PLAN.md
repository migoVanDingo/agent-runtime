# Agent Runtime — Roadmap

## The Stack We Are Building

```
┌─────────────────────────────────────────┐
│           Application Layer             │  Project 12
│   Coding CLI / user interaction         │
├─────────────────────────────────────────┤
│        Agent Orchestration Layer        │  Projects 1–3
│   ReAct loop, tool routing, planning    │
├─────────────────────────────────────────┤
│      AI Runtime Infrastructure          │  Projects 8–9
│   AFM (memory) + VIGIL (recovery)       │  ← Cruz (2026)
├─────────────────────────────────────────┤
│     Policy + Safety + RAG               │  Projects 6–7
│   Allowlists, permissions, retrieval    │
├─────────────────────────────────────────┤
│       Model Serving / Providers         │  Project 4
│   Anthropic / OpenAI / Ollama           │
├─────────────────────────────────────────┤
│      External Tools + Environment       │  Projects 1–2
│   Bash, files, APIs, search             │
└─────────────────────────────────────────┘
         ↕ (spans entire stack)
     Observability / AgentOps             │  Project 5
     Traces, metrics, replay              │  (passive, no intervention)
```

## Projects

| # | Name | Layer | Key Concepts | Status |
|---|------|-------|--------------|--------|
| 1 | Raw Tool-Calling Agent | Orchestration | Tool schemas, ReAct loop, message history | TODO |
| 2 | Multi-Tool Coding Assistant | Application | File I/O, bash, safety confirmation | TODO |
| 3 | Persistent Memory + Conversation | Memory | Episodic memory, context windowing, summaries | TODO |
| 4 | Provider Abstraction | Model layer | Unified LLMProvider interface, Anthropic + OpenAI + Ollama | TODO |
| 5 | Observability Layer | AgentOps | Span-based trace logging, token counts, latency, replay | TODO |
| 6 | RAG System | Memory/retrieval | Embeddings, vector store, semantic retrieval, grounded answers | TODO |
| 7 | Policy + Safety Layer | Runtime | Permission tiers, allowlists, irreversibility gates, audit log | TODO |
| 8 | Adaptive Focus Memory (AFM) | Runtime infra | Execution-time context compression, relevance reweighting | TODO |
| 9 | VIGIL — Failure Detection | Runtime infra | Goal drift detection, loop detection, mid-run recovery, rollback | TODO |
| 10 | Imitation Learning | Learning | Demonstration trace collection, behavioral cloning, few-shot bootstrap | TODO |
| 11 | RL Feedback Loop | Learning | Reward signal (test pass/fail, user accept), policy improvement | TODO |
| 12 | Full Runtime + Coding CLI | Full stack | All layers integrated, production-grade CLI tool | TODO |

## Key Design Decisions

- **Language:** Python 3.11+ for all projects
- **No LangChain/LangGraph** for the first 9 projects — build raw against provider SDKs
- **Model-agnostic from Project 4 onward** — provider abstraction enables swapping models
- **Each project is self-contained** — it can be read and run independently
- **Cruz stack is the target architecture** — the runtime infrastructure layer (Projects 8–9) is the thesis of this repo

## References

- Bin Xu (2026) arXiv:2601.01743 — agent taxonomy, evaluation, ReAct, MRKL, tool use
- Christopher Cruz (2026) arXiv:2603.00495 — AI runtime infrastructure, AFM, VIGIL
- Christopher Cruz (2025) arXiv:2511.12712 — Adaptive Focus Memory
- Christopher Cruz (2025) arXiv:2512.07094 — VIGIL

## Progress Log

| Date | Event |
|------|-------|
| 2026-03-15 | Repo initialized, roadmap written, references documented |
