# AI Agent Systems: Architectures, Applications, and Evaluation

**Author:** Bin Xu, Arizona State University
**arXiv:** 2601.01743v1 (January 2026)

---

## What This Paper Is

A comprehensive survey of the AI agent landscape. It defines a unified taxonomy of agent components, orchestration patterns, learning strategies, application domains, and evaluation methods. Think of it as the map — it tells you what exists, how pieces fit together, and what the tradeoffs are.

---

## The Agent Transformer Abstraction

The paper defines an **agent transformer** as a tuple:

```
A = (π_θ, M, T, V, E)

π_θ  = transformer policy (the LLM)
M    = memory subsystem (retrieval, summaries, state)
T    = tools (APIs, code execution, search, databases)
V    = verifiers/critics (check proposals before execution)
E    = environment
```

At each step `t` the execution loop is:
1. Observe: `o_t ← Obs(E_t)`
2. Retrieve memory: `m_t ← Retrieve(M_t, o_t)`
3. Propose action: `ã_t ~ π_θ(· | o_t, m_t)`
4. Validate: `â_t ← Validate(V, ã_t)`
5. Execute + update: `E_{t+1} ← Exec(E_t, T, â_t)`

This is the loop you build in **Projects 1–3**.

---

## Key Patterns (You Will Implement These)

### ReAct (Reasoning + Acting)
Interleave deliberation tokens with tool calls:
```
Thought: I need to find what files are in the repo
Action: list_files(path=".")
Observation: [README.md, main.py, ...]
Thought: I can now read main.py
Action: read_file(path="main.py")
...
```
Source paper: Yao et al. (2023), arXiv:2210.03629

### MRKL (Modular Routing)
Route tasks to specialized tools. The LLM selects which tool, not how it works internally. Separates language understanding from deterministic computation.

### RAG (Retrieval-Augmented Generation)
Make retrieval a first-class tool. Ground decisions in retrieved evidence. Forces claims to be backed by actual content.

### Reflexion / Critics
Add a feedback channel: after acting, evaluate the outcome and revise. Reduces compounding errors.

### Tree-of-Thoughts
When single rollouts are unreliable, search over multiple candidate action paths. Trade compute for reliability.

---

## Memory Types

| Type | What it stores | Example |
|------|---------------|---------|
| Episodic | What happened (events, tool calls) | "At step 4, the test failed with error X" |
| Semantic | Facts about the world | "The repo uses pytest" |
| Procedural | How to do things (skills) | "To run tests: pytest tests/" |

All three are relevant in the coding assistant (Project 2+).

---

## Learning Stack

The paper describes three layers of agent learning:

1. **Learning strategies** — RL, imitation learning, in-context learning, optimization
2. **Agent systems** — modules with clear contracts (policy core, memory, tool routers, critics)
3. **Foundation model adaptation** — pretraining + finetuning for tool use, planning, grounding

For this curriculum:
- Projects 1–9: focus on layers 1 and 2
- Projects 10–11: implement layer 1 directly (IL and RL)

### Reinforcement Learning
- Optimizes long-horizon returns (maximize expected discounted reward)
- Useful when: environment is well-defined, interaction can be scaled, safety is a system property
- Challenge in tool-rich settings: sparse rewards, expensive rollouts, safety constraints
- In LLM agents: appears as RLHF, DPO (preference optimization)

### Imitation Learning
- Learn from expert demonstrations (structured traces: observations + rationales + tool calls + outcomes)
- Behavioral cloning: simplest form — train to match expert actions
- DAgger: iteratively collect corrective demos on states the policy induces
- Best when: high-quality traces exist, exploration is unsafe

### In-Context Learning
- Prompts define action formats, tool schemas, policies — no parameter update
- Chain-of-thought improves multi-step reasoning
- Failure modes: context growth, prompt injection via retrieved content, dilution of constraints

---

## Agent Taxonomy (Relevant to This Curriculum)

| Category | Description | Where in Curriculum |
|----------|-------------|---------------------|
| Generalist agents | Heterogeneous tasks, shared policy + modular tools | Projects 1–3 |
| Knowledge agents | RAG, grounded answers, citations | Project 6 |
| Logic/neuro-symbolic | LLM + deterministic tools, typed schemas | Projects 7+ |
| Coding agents | Repo search, multi-file edits, test execution | Project 2 |

---

## Evaluation (What to Measure)

The paper defines a metric vector. We will implement basic versions of these in Project 5 (Observability):

| Metric | Formula | Why it matters |
|--------|---------|----------------|
| Success rate | `(1/N) Σ s_i` | Did it actually work? |
| Token cost | `p_in * x_i + p_out * y_i` | Is it affordable? |
| Tool selection accuracy | Correct tool chosen | Quality of routing |
| Argument correctness | Schema-valid + semantically correct | Tool call reliability |
| Recovery rate | Succeeded after at least one tool failure | Robustness |
| Loop rate | `1 - uniq(τ_i) / T_i` | Is the agent spinning? |
| Violation rate | Unsafe actions / N | Safety |

---

## Design Trade-offs to Keep in Mind

| Trade-off | Description |
|-----------|-------------|
| Latency vs. accuracy | More deliberation (Tree-of-Thoughts) improves quality but costs time |
| Autonomy vs. controllability | More capable agents need stronger verification layers |
| Capability vs. reliability | A model that can do more can also fail more spectacularly |

---

## Key Quotes

> "The practical frontier is shifting from 'answering' to 'operating': agents are expected to maintain state, recover from tool failures, and justify actions with evidence traces."

> "Evaluate the agent transformer as a system, not a model."

> "The most impactful improvements often come from changing the decision loop rather than changing the base model."

---

## ArXiv Links for Cited Papers

| Paper | arXiv |
|-------|-------|
| ReAct | 2210.03629 |
| Reflexion | 2303.11366 |
| Tree-of-Thoughts | 2305.10601 |
| RAG | Lewis et al. NeurIPS 2020 |
| Toolformer | 2302.04761 |
| MRKL | 2205.00445 |
| Chain-of-Thought | 2201.11903 |
| SWE-bench | 2310.06770 |
| WebArena | 2307.13854 |
| AgentBench | 2308.03688 |
| ToolBench | 2309.03752 |
