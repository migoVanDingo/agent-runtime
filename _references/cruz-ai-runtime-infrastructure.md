# AI Runtime Infrastructure

**Author:** Christopher Cruz
**arXiv:** 2603.00495v1 (February 2026)

Related papers by same author:
- VIGIL: arXiv:2512.07094 (2025)
- Adaptive Focus Memory (AFM): arXiv:2511.12712 (2025)

---

## The Core Argument

Most agent failures don't happen at model invocation time. They happen **during execution** — after planning has begun:
- Context windows overflow
- Intermediate reasoning drifts off task
- Tool errors compound across steps
- Safety risks emerge mid-workflow

Existing infrastructure doesn't address this:
- **Model serving** (batching, caching) optimizes individual calls, ignores multi-step behavior
- **Orchestration frameworks** define execution plans but don't adapt once running
- **Observability/AgentOps** captures logs for offline analysis but can't intervene
- **Post-hoc safety** filters outputs after generation — too late

Cruz proposes a new layer: **AI Runtime Infrastructure** — sits between model serving and the application, actively observing and intervening *while the agent is running*.

---

## The Full Agentic Stack

```
┌─────────────────────────────────────────────┐
│              Application Layer               │
│  Product logic, user interaction,            │
│  business constraints, task objectives       │
│                    │                         │
│           progress / escalations             │
├────────────────────↓────────────────────────┤
│           Agent Orchestration Layer          │
│  Task decomposition, planning,               │
│  control flow graphs, tool routing,          │
│  prompt templates                            │
│                    │  ↑ control signals      │
│                    │  (retry/reroute/stop)   │
├────────────────────↓────────────────────────┤  ← THE NEW LAYER
│       AI Runtime Infrastructure              │
│  (Execution-Time Control Layer)              │
│                                              │
│  • Observe execution state + history         │
│  • Long-horizon memory management            │
│  • Failure detection + recovery              │
│  • Runtime safety/cost/latency policies      │
│  • Execution-time optimization               │
│                    │                         │
│           tokens / latency / outputs         │
├────────────────────↓────────────────────────┤
│        Model Serving / Inference             │
│  LLM inference, KV cache, batching,          │
│  GPU scheduling, inference optimization      │
│                    │                         │
│       tool results / environment state       │
├────────────────────↓────────────────────────┤
│         External Tools + Environment         │
│  APIs, databases, web/browsers,              │
│  file systems, OS/network,                   │
│  human-in-the-loop (optional)                │
└─────────────────────────────────────────────┘
                    ↕
       Observability / AgentOps (Passive)
       Logs, traces, metrics, offline eval
       (spans entire stack, does NOT intervene)
```

---

## Three Required Properties

A system is AI runtime infrastructure if and only if it has all three:

1. **Execution-time intervention** — can modify agent behavior *while it is running*, not just before or after
2. **Long-horizon state awareness** — maintains visibility across many steps, not just the current prompt
3. **Closed-loop control** — execution signals continuously inform subsequent interventions (a feedback loop)

---

## Six Design Principles

| Principle | What it means |
|-----------|---------------|
| Execution-Time Intervention | Can act during a run, not just plan before or analyze after |
| Long-Horizon State Awareness | Tracks history across dozens/hundreds of steps |
| Closed-Loop Control | Observation feeds back into intervention continuously |
| Model-Agnostic Operation | Works without modifying the underlying model |
| Application-Agnostic Control | Reusable across applications — no domain-specific logic encoded here |
| Safety/Cost/Reliability as Runtime Concerns | These are evaluated dynamically as execution unfolds, not just at the end |

---

## Early Systems

### VIGIL (arXiv:2512.07094) — Runtime-Aware Precursor
- Analyzes structured execution logs to detect: anomalous behavior, degraded performance, policy violations
- Can trigger remediation actions or human escalation
- **Limitation:** operates *outside* the execution loop. Influence happens through external remediation after failure is detected — not continuous in-loop control.
- **Role in curriculum:** Precursor that shows the need for tighter integration. We implement a VIGIL-style system in **Project 9**.

### AFM — Adaptive Focus Memory (arXiv:2511.12712) — True Runtime Infrastructure
- Operates *directly within* the agent execution loop
- Continuously observes execution state and intervenes in real time
- Dynamically allocates, compresses, and reweights memory during execution
- **Satisfies all three required properties:**
  - Intervenes by modifying contextual inputs to the model as execution unfolds
  - Reasons over long-horizon state across many agent steps
  - Participates in closed-loop control (execution signals → intervention → affects next step)
- **Role in curriculum:** The primary runtime infrastructure implementation. **Project 8**.

---

## What the Runtime Layer Does (Implementation Targets)

For **Project 8 (AFM)**:
- Track token usage and context window utilization per step
- Detect when context is approaching limits
- Compress older/less-relevant content (summarize, drop, or archive)
- Reweight memory: boost content relevant to the current subtask
- Do all of this without changing the model or the application

For **Project 9 (VIGIL)**:
- Maintain an execution state model: current goal, progress, recent tool outcomes
- Detect failure patterns:
  - **Loop detection:** same tool called with same args repeatedly
  - **Goal drift:** agent actions no longer connected to stated objective
  - **Stalled progress:** N steps without meaningful progress
  - **Error cascade:** tool failures increasing in frequency
- Trigger recovery: retry with different approach, roll back to checkpoint, escalate to human

---

## Key Quotes

> "Many of the most significant challenges in production agentic systems arise not at model invocation time, but during execution itself."

> "Once an agent has entered an unrecoverable execution state, logging the failure provides insight but does not restore correctness, efficiency, or safety."

> "Runtime infrastructure treats execution itself as an optimization surface."

> "AI runtime infrastructure does not replace existing layers but composes with them."

> "As agents become more autonomous and are entrusted with higher-impact tasks, execution-time control is likely to become a foundational requirement rather than an optional enhancement."

---

## Why This Changes the Framing of the Entire Curriculum

Without this paper, you might build a coding assistant with some nice features. With it, you realize you are building **infrastructure** that a coding assistant runs on top of. The distinction matters:

- Infrastructure is reusable across applications
- Infrastructure is model-agnostic
- Infrastructure is the thing that makes agents reliable at scale

The whole point of the 12-project series is to arrive at a runtime that satisfies Cruz's three properties — and then put a coding assistant on top as the first real application.
