# 0033 — Architecture Review & Paper Comparison

**Date**: 2026-04-14
**Status**: Report (not a design or implementation proposal)

---

## 1. System Overview

The agent-runtime (`arc`) is a CLI agent built from scratch against the Anthropic, OpenAI, and Ollama APIs. It implements a plan-and-execute architecture with a multi-stage decision pipeline:

```
User Input
  → Intent Classifier (plan vs direct)
  → Planner (generates Step[] with declared tools)
  → Plan Validator (structural checks)
  → Plan Critic (adversarial review)
  → Step Executor (tool-per-step enforcement + monitoring)
  → Synthesizer (final response)
```

Nine composable subsystems: Provider, Messenger, Registry, Router, ContextManager, Classifier, Validator, Critic, Monitor — each independently configurable and disablable.

---

## 2. Reference Papers

| Short Name | Paper | Key Contribution |
|------------|-------|------------------|
| **CRUZ-RT** | CRUZ AI Runtime Infrastructure | Three-layer stack, execution-time control, closed-loop feedback |
| **AFM** | Cruz Adaptive Focus Memory | Non-destructive context compression, importance classification |
| **Council** | Multi-Agent Hallucination Mitigation | Heterogeneous expert consensus, intelligent triage |
| **SLM-TC** | SLM Efficient Tool Calling | Fine-tuned 350M model outperforms 175B at tool selection |
| **BIN** | AI Agent Systems Survey | RGB components (rule/graph/behavior-tree), hybrid architectures |

---

## 3. The Good

### 3.1 Execution-Time Control Layer (aligns with CRUZ-RT)

The runtime subsystems (classifier, validator, critic, monitor, guard) implement exactly what CRUZ-RT calls the "execution-time intervention" layer. Our system goes beyond what the paper describes:

- **Plan Critic** is a pre-execution adversarial stage that CRUZ-RT doesn't explicitly propose. The paper discusses runtime intervention during execution; we intervene *before* execution even starts. This prevents wasted tokens rather than recovering from them.
- **Tool-per-step enforcement** is a hard constraint the paper doesn't mention. CRUZ-RT relies on prompting; we physically remove tools from the schema so the model *cannot* deviate. This is strictly stronger.
- **Step result forwarding** (carrying previous step results in transition messages) implements CRUZ-RT's "closed-loop feedback" concretely — each step sees what the previous step produced.

**Verdict: Keep. This is our strongest architectural contribution.**

### 3.2 AFM-Inspired Context Management (aligns with AFM)

Our `ContextManager` implements the core AFM mechanism:
- Three fidelity levels (FULL / COMPRESSED / PLACEHOLDER)
- Multi-signal scoring (semantic similarity + recency decay + rule-based importance)
- Non-destructive packing (original history preserved in Messenger)
- Plan-awareness boost (messages from current plan execution get elevated importance)

The plan-awareness boost is *not* in the AFM paper and is a genuine improvement. AFM treats all messages equally within their scoring framework; we recognize that messages from the current plan are more important regardless of semantic similarity to the current query.

**Verdict: Keep. Plan-awareness is our contribution beyond AFM.**

### 3.3 Graceful Degradation (aligns with BIN)

BIN emphasizes that agent systems need predictability and failure recovery. Our system demonstrates this:
- Every subsystem can be disabled via config
- Planner fails → falls back to direct execution
- Critic parse fails → auto-approve
- Monitor parse fails → continue
- Tool execution wrapped in `safe_execute()` → error string, never crash

This is the BIN "rule-based guardrails" principle applied to the meta-architecture: the decision pipeline itself has rule-based fallbacks at every stage.

**Verdict: Keep. This is table-stakes for production but we do it well.**

### 3.4 Heuristic-First, LLM-Fallback Monitoring (aligns with BIN, SLM-TC)

The monitor uses fast regex heuristics before calling an LLM:
- Empty results, error indicators, step error fields → flagged
- Only flagged steps get LLM assessment
- LLM sees flags + context to make CONTINUE/RETRY/REPLAN/SKIP/ESCALATE decision

This mirrors BIN's recommendation for hybrid architectures: deterministic checks handle the common cases, LLM handles ambiguous ones. It also resonates with SLM-TC's finding that you don't need the biggest model for every decision.

**Verdict: Keep.**

### 3.5 Composition Over Inheritance

The Agent composes 9+ subsystems with no deep inheritance hierarchies. Each subsystem owns a single concern. This makes the system testable, configurable, and easy to reason about.

**Verdict: Keep.**

---

## 4. The Bad

### 4.1 No Multi-Agent Consensus (gap vs Council)

The Council paper demonstrates that heterogeneous multi-agent consensus reduces hallucinations by 35.9%. Our system uses single-model decision-making everywhere:
- One classifier decides plan vs direct
- One planner generates the plan
- One critic reviews the plan
- One monitor assesses each step

The critic is the closest thing to a "second opinion," but it's one voice, not a consensus of diverse models. When the critic and planner are both running on gpt-4o-mini, they share the same blindspots.

**Gap: Significant. Hallucination is our #1 reliability problem** (model lies about failed operations, over-selects tools, produces malformed plans). Council Mode's approach — multiple diverse models voting on decisions — could directly address this.

**What to adopt**: Council's intelligent triage is particularly relevant. Simple queries don't need consensus; complex multi-step plans do. We already have the classifier distinguishing plan vs direct — this could be extended to route complex plans through multi-model consensus.

### 4.2 No Specialized Tool-Selection Model (gap vs SLM-TC)

SLM-TC shows a fine-tuned 350M model achieves 77.55% tool-calling accuracy vs 26% for a general 175B model. We use general-purpose LLMs for tool selection (via the planner and router).

Our router uses embedding similarity (all-MiniLM-L6-v2) which is a step in the right direction — it's a small, specialized model doing one job. But it selects *toolsets*, not individual tools. The planner then selects individual tools using a general-purpose LLM.

**Gap: Moderate.** The critic partially compensates by challenging bad tool selections, but it's post-hoc correction rather than getting it right the first time. A specialized tool-selection model could replace or supplement the planner's tool-selection responsibility.

**What to adopt**: The SLM-TC approach of a lightweight, fine-tuned model specifically for tool selection. This could sit between the planner and critic, or replace the planner's tool-selection entirely while the planner focuses on step decomposition.

### 4.3 Critic Has No Tool Weight Awareness

The critic prompt tells the model to evaluate tool selection but doesn't provide information about tool cost or weight. It challenged `file_info` and `strings` (lightweight, fast, useful for local-specific facts) with the same vigor as `objdump` (produces 1.2M tokens). The critic needs to distinguish between a 10-token tool call and a million-token one.

**Gap: Implementation gap, not architectural.** The critic design is sound; the prompt just lacks cost information.

**What to fix**: Provide tool weight categories (lightweight/moderate/heavy) or estimated output sizes in the critic prompt. Lightweight tools should get a pass unless they're clearly irrelevant.

### 4.4 One-Round Critic Limit

The critic gets exactly one round: challenge → revision → done. If the planner's revision is malformed (which happens with gpt-4o-mini), the system falls back to the original plan — the one the critic just said was bad.

**Gap: Design limitation.** One round is reasonable to prevent infinite loops, but falling back to the *criticized* plan is wrong. The fallback should be a simpler plan or direct execution, not the plan that was just rejected.

**What to fix**: If revision fails, strip the challenged steps and execute only the unjustified ones. Or fall back to direct execution. Never execute a plan the critic explicitly challenged.

---

## 5. The Ugly

### 5.1 Planner Revision Fragility

The planner revision path (`planner.revise()`) is fragile:
- gpt-4o-mini produced JSON missing `action_type` field
- The code initially set `plan = None` which would have caused fallback to direct execution (now fixed to use original plan)
- There's no retry on revision parse failure
- The revision prompt asks the planner to defend/replace/drop steps, but doesn't enforce the response format strictly

This is the weakest link in the decision pipeline. Every other subsystem degrades gracefully; the planner revision path degrades *backwards* (using the criticized plan).

**Priority: High. Needs structural fix, not just prompt tuning.**

### 5.2 ESCALATE Is a No-Op

Both the ActionGuard and ExecutionMonitor have ESCALATE decisions that log warnings but take no action. The monitor can decide "this needs human review" but there's no mechanism to actually pause and ask the user.

From the logs: the model tried `brew install checksec` autonomously. The guard should have escalated. Instead, execution continued.

**Gap vs CRUZ-RT**: CRUZ-RT explicitly describes "human-in-the-loop" as a runtime intervention. We have the decision point but not the mechanism.

**Priority: High. This is a safety issue.** An agent that can install software, delete files, or run arbitrary shell commands needs a real escalation path.

### 5.3 Model Installs Software Autonomously

Related to 5.2: when `checksec` wasn't found, the model called `brew install checksec` without any user confirmation. The guard system didn't catch this because:
1. The guard checks tool names, not arguments
2. `bash_exec` is a legitimate tool — the *argument* is dangerous, not the tool

This is a class of problem the current architecture can't handle: **argument-level safety checks**. The guard operates at tool granularity, not argument granularity.

**Gap vs BIN**: BIN's rule-based guardrails operate at the action level, not just the tool level. A behavior-tree approach would check "is this a package installation?" before allowing `bash_exec("brew install ...")`.

**What to adopt**: Argument-level guard rules for dangerous commands. At minimum: package installation, file deletion outside workspace, network requests to unknown hosts, process killing.

### 5.4 Router Is Vestigial in Plan Mode

The `StaticRouter` selects toolsets based on embedding similarity and heuristic rules. But with tool-per-step enforcement, the router is only used in two cases:
1. Direct execution mode (no plan)
2. Fallback when a plan step has no declared tool (shouldn't happen after validator)

The embedding model (all-MiniLM-L6-v2) loads at startup and takes ~4 seconds. For plan-mode users, this is wasted time and memory.

**What to fix**: Lazy-load the embedding model. Or share it with the context manager (which already uses embeddings for scoring). Currently both load the model independently — this should be a single shared instance.

### 5.5 Vestigial Config: PlanningGate

`PlanningGateConfig` in `config.py` and `planning.gate` in `config.yml` still exist but are unused. The intent classifier replaced the planning gate. Dead config increases confusion.

**What to fix**: Remove `PlanningGateConfig`, `planning.gate` from config, and any gate references.

### 5.6 Direct Mode Error Detection Is Reactive, Not Preventive

In direct mode, the system detects that the model lied about a failed operation *after* the model has already composed its response. The correction is injected as a new message, forcing another LLM call.

This works but it's backwards: we're paying for two LLM calls (the lie + the correction) when we could have prevented the lie with one. The plan-mode approach (monitor assessing each step) is structurally better.

**Gap: Direct mode lacks the safety infrastructure of plan mode.** The runtime infrastructure only fully activates in plan mode.

---

## 6. Gaps vs Papers: What's Missing

### 6.1 From Council Paper: Intelligent Triage

Council's triage routes simple queries to a single model and complex ones to multi-model consensus. Our classifier distinguishes plan vs direct, but doesn't consider *confidence* or *risk*. A high-stakes plan (deleting files, running destructive commands) should get more scrutiny than a low-stakes one (reading a file and summarizing it).

**Recommendation**: Add a risk dimension to the classifier. High-risk plans → critic + potentially multi-model consensus. Low-risk plans → streamlined execution.

### 6.2 From AFM Paper: Importance Classification Dominance

AFM's key finding: importance classification is the dominant factor (83.3% pass rate with it, 0% without it). Our importance classification is simple rule-based:
- System messages → CRITICAL
- Tool results with errors → HIGH
- Large tool outputs → LOW

We don't have learned importance classification. AFM suggests this is where the most improvement potential lies.

**Recommendation**: Experiment with LLM-based importance scoring for key messages, or train a lightweight classifier. The rule-based approach works but may miss nuanced cases (e.g., a tool result that's small but contains a critical error buried in output).

### 6.3 From BIN Survey: Behavior Trees for Predictable Workflows

BIN argues that rule-based, graph-based, and behavior-tree (RGB) components remain valuable for predictable workflows. Our system is almost entirely LLM-driven for decisions. The only rule-based components are:
- Validator (structural checks)
- Monitor heuristics (error patterns)
- Router heuristic rules (keyword matching)
- Guard (tool allowlists)

Missing: **structured workflow patterns** for common tasks. If 80% of tasks follow a few patterns (analyze → summarize → write, read → modify → write, etc.), these could be behavior trees that bypass planning entirely.

**Recommendation**: Identify the 5-10 most common task patterns from logs. Implement them as fast-path behavior trees. Use LLM planning only when no pattern matches.

### 6.4 From SLM-TC: Decoupling Tool Selection from Reasoning

SLM-TC's core insight: tool selection and reasoning are different capabilities. A tiny model (350M params) can outperform GPT-3 at tool selection because it's a focused classification task, not a reasoning task.

Our system couples tool selection with reasoning in the planner: the same LLM call that decomposes the task also selects tools. The critic is a partial decoupling (reviewing tool selection separately), but it's still LLM-based.

**Recommendation**: Explore a lightweight tool-selection stage between step decomposition and tool assignment. The planner says "analyze the binary" and a specialized selector says "use file_info for that."

---

## 7. Where Our System Is Better

### 7.1 Tool-Per-Step Enforcement (better than all papers)

No paper proposes hard-constraining tool availability per step. CRUZ-RT discusses runtime intervention but relies on prompting. BIN discusses guardrails but at a higher level. Our approach — physically removing tools from the schema so the model cannot call them — is a stronger guarantee than any prompt-based control.

### 7.2 Pre-Execution Adversarial Review (better than CRUZ-RT)

CRUZ-RT's runtime intervention happens *during* execution. Our critic intervenes *before* execution, preventing wasted tokens. This is cheaper and more effective: it's better to not run objdump than to run it, see 1.2M tokens, and then try to recover.

### 7.3 Plan-Aware Context Management (extends AFM)

AFM doesn't consider plan structure in its scoring. Our plan-awareness boost — elevating importance of messages from the current plan execution — is a meaningful extension that preserves execution continuity.

### 7.4 Graceful Degradation Depth (aligns with but extends BIN)

BIN recommends hybrid architectures with rule-based fallbacks. Our system applies this at every layer of the decision pipeline, not just at the tool-execution layer. Every subsystem has a defined degradation path. This depth of graceful degradation is not discussed in any of the papers.

---

## 8. Priority Recommendations

### Must Fix (safety/correctness)
1. **Implement real ESCALATE** — user-in-the-loop for dangerous operations
2. **Argument-level guard** — catch `brew install`, `rm -rf`, etc. in bash_exec args
3. **Fix critic fallback** — never execute a plan the critic challenged; strip or simplify instead

### Should Fix (reliability)
4. **Planner revision retry** — retry once on parse failure before falling back
5. **Tool weight in critic prompt** — lightweight tools should get a pass
6. **Lazy-load embedding model** — or share single instance between router and context manager

### Could Improve (capability)
7. **Risk-aware triage** — high-risk plans get more scrutiny
8. **Behavior-tree fast paths** — common patterns bypass planning
9. **Multi-model consensus** for complex plans (Council Mode)
10. **Specialized tool selector** (SLM-TC approach)

### Cleanup
11. Remove vestigial PlanningGateConfig
12. Unify embedding model instance (router + context manager)
