# 0034 — Improvement Roadmap

**Date**: 2026-04-16
**Status**: Proposed
**Depends on**: 0033 (Architecture Review)

---

## Guiding Principles

1. **Safety before features.** The agent can run shell commands and write files. An ESCALATE that does nothing is a safety hole, not a TODO.
2. **Fix what's broken before adding what's missing.** Fragile revision paths and vestigial config are friction for every future change.
3. **Each phase must be independently shippable.** No phase should leave the system in a worse state than before it started.
4. **Test with real interactions.** Every phase ends with a logged session demonstrating the change.

---

## Phase 1 — Safety & Correctness

*The agent currently installs software, ignores its own ESCALATE decisions, and falls back to plans the critic just rejected. Fix these first.*

### 1a. Real ESCALATE: User-in-the-Loop

**Problem**: `GuardDecision.ESCALATE` and `StepDecision.ESCALATE` log a warning and continue. The model ran `brew install checksec` without asking.

**Design**:
- Add an `Escalation` dataclass: `reason: str`, `tool_name: str | None`, `tool_input: dict | None`, `source: str` (guard | monitor | critic).
- Add a `UserGate` interface with one method: `prompt(escalation: Escalation) -> bool` (approve/deny).
- Default implementation: `CLIUserGate` — prints the escalation to stdout, waits for y/n input.
- Wire into `agent.py`:
  - In `_run_step`: if guard returns ESCALATE, call `user_gate.prompt()`. If denied → skip step, write "user denied" to `step.error`.
  - In `_execute_plan`: if monitor returns ESCALATE, same flow.
- Future: `UserGate` can be swapped for a web UI, Slack bot, or auto-approve in testing.

**Files**: new `src/runtime/escalation.py`, modify `src/agent.py`, `src/main.py`.

### 1b. Argument-Level Guard for bash_exec

**Problem**: The guard catches `rm -rf` but misses `brew install`, `pip install`, `apt-get`, `npm install -g`, `curl ... | sh` piped through variables, and `python -c "import os; os.system(...)"`.

**Design**:
- Extend `_DANGEROUS_COMMANDS` regex in `guard.py` with package-manager patterns:
  - `brew install|uninstall|remove`
  - `pip install|uninstall`, `pip3 install|uninstall`
  - `apt-get install|remove|purge`, `apt install|remove`
  - `npm install -g`, `yarn global add`
  - `gem install`, `cargo install`
- These should be ESCALATE, not BLOCK — the user might genuinely want them.
- Add a `_PACKAGE_MANAGERS` regex separate from `_DANGEROUS_COMMANDS` for clarity.
- Add pattern for `python -c` and `python3 -c` → ESCALATE (arbitrary code execution outside the tool sandbox).

**Files**: modify `src/runtime/guard.py`.

### 1c. Fix Critic Fallback

**Problem**: If the planner's revision is malformed, the system executes the *original* plan — the one the critic just challenged. This defeats the purpose of the critic.

**Design**:
- When revision fails and the critic's verdict was CHALLENGED:
  1. Take the original plan.
  2. Remove steps whose suggestion was `"drop"`.
  3. Keep steps whose suggestion was `"justify"` (benefit of the doubt).
  4. For `"replace"` suggestions: keep the step but log a warning (we can't auto-replace without a valid revision).
  5. Re-validate the stripped plan. If it has zero steps → fall back to direct execution.
  6. If it has steps → execute the stripped plan.
- This is strictly better than either executing the criticized plan or falling back to direct.

**Files**: modify `src/agent.py` (the critic integration block in `call()`).

### 1d. Planner Revision Retry

**Problem**: gpt-4o-mini produced JSON missing `action_type`. One retry with feedback ("your response was missing the action_type field") would likely succeed.

**Design**:
- In `planner.revise()`: if `_parse()` returns None, retry once with a correction message: `"Your previous response was not valid JSON or was missing required fields. Return ONLY the JSON plan object."`.
- Cap at 1 retry (same pattern as `plan()` with `retry_on_invalid`).

**Files**: modify `src/planning/planner.py`.

---

## Phase 2 — Critic & Planning Refinement

*The critic works but is miscalibrated. Fix its judgment before building on top of it.*

### 2a. Tool Weight Categories in Critic Prompt

**Problem**: The critic challenged `file_info` (returns ~100 chars) with the same intensity as `objdump` (returns ~1M tokens). It lacks cost information.

**Design**:
- Add a `weight` property to `BaseTool`: `"lightweight"`, `"moderate"`, or `"heavy"`.
  - Lightweight: file_info, strings, hash_file, base64_encode/decode, list_files, get_working_directory, environment_info, read_file_lines, search_files (~100-1000 chars output)
  - Moderate: nm, read_file, write_file, bash_exec, grep_binary, xor_decode, copy_file, move_file, delete_file, make_directory, download_file, walk_directory (~1K-50K chars)
  - Heavy: objdump, hexdump, readelf, strace, ltrace, checksec (~50K+ chars or requires installation)
- `ToolRegistry.get_tool_description()` includes weight.
- Update `CRITIC_SYSTEM_PROMPT` with guidance: *"Lightweight tools (file_info, strings, hash_file) are cheap — challenge them only if clearly irrelevant. Moderate tools deserve scrutiny. Heavy tools (objdump, hexdump, strace) must be explicitly justified — they can produce massive output and dominate the context budget."*

**Files**: modify `src/tools/base.py`, all tool implementations (add `weight` property), `src/tools/registry.py`, `src/runtime/prompts.py`.

### 2b. Risk-Aware Intent Classification

**Problem**: The classifier makes a binary plan/direct decision. A plan that reads files gets the same scrutiny as a plan that deletes them. Council paper shows intelligent triage matters.

**Design**:
- Extend classifier output to include a `risk` field: `"low"`, `"moderate"`, `"high"`.
  - Low: read-only operations, analysis, summarization.
  - Moderate: file writes within working directory, non-destructive shell commands.
  - High: file deletion, shell commands modifying system state, operations on paths outside working directory.
- Store risk on the Plan object (new field `risk: str`).
- Use risk downstream:
  - Low-risk plans: critic can be skipped (config option `critic_skip_low_risk: bool`).
  - High-risk plans: monitor uses stricter heuristics (any tool error → flag, not just patterns).
  - Future: high-risk plans → multi-model consensus (Phase 4).

**Files**: modify `src/runtime/classifier.py`, `src/runtime/prompts.py`, `src/planning/schema.py`, `src/agent.py`, `src/config.py`.

### 2c. Vestigial Cleanup

**Problem**: `PlanningGateConfig` and `planning.gate` config are dead code. Router loads embedding model even when only plan-mode is used.

**Design**:
- Remove `PlanningGateConfig` from `config.py`.
- Remove `planning.gate` from `config.yml`.
- Remove any gate references in code (if any remain).
- Lazy-load embedding model in `StaticRouter`: load on first `select()` call, not in `__init__`.
- Share embedding model instance: `StaticRouter` and `ContextManager` should accept an optional pre-loaded model. If `ContextManager` already loaded it, pass it to `StaticRouter` (or vice versa). Simplest approach: a module-level `get_embedding_model()` that caches.

**Files**: modify `src/config.py`, `config.yml`, `src/routing/static_router.py`, `src/runtime/context_manager.py`, new `src/embeddings.py` (shared model loader).

---

## Phase 3 — Direct Mode Hardening

*Plan mode has validator, critic, guard, monitor. Direct mode has almost none of that. Bring direct mode closer to parity.*

### 3a. Guard in Direct Mode

**Problem**: In `_run_loop()` (direct mode), tool calls bypass the guard entirely. Only plan-mode steps go through `check_step` and `check_tool_call`.

**Design**:
- In `_run_loop()`, before executing each tool call, run `guard.check_tool_call(tool_name, tool_input)`.
- If BLOCK → return error string to model, don't execute.
- If ESCALATE → call `user_gate.prompt()`. If denied → return denial string to model.
- This is straightforward — the guard is already instantiated in `Agent.__init__`.

**Files**: modify `src/agent.py` (`_run_loop` method).

### 3b. Lightweight Direct-Mode Monitor

**Problem**: Direct mode has the `last_had_errors` injection (telling the model "previous tool calls had errors"), but no structured assessment. The model can loop on failing tools indefinitely.

**Design**:
- Track consecutive tool errors in `_run_loop()`. If 3+ consecutive tool calls produce errors → inject a stronger correction: *"Multiple consecutive tool calls have failed. Stop and report the issue to the user rather than retrying."*
- Track total tool calls per turn. If > 10 tool calls in a single turn → inject: *"You have made many tool calls. Wrap up and respond to the user."*
- These are heuristic-only (no LLM calls) — keeping direct mode fast.

**Files**: modify `src/agent.py` (`_run_loop` method).

### 3c. Direct-Mode Token Awareness

**Problem**: In direct mode, the context manager is called before the LLM call, but there's no awareness of how much output the model is generating. A tool result of 500K tokens (e.g., from `strings` on a large binary) goes straight into the context without assessment.

**Design**:
- Before adding a tool result to the messenger in `_run_loop()`, check its size.
- If a tool result exceeds a configurable threshold (e.g., `direct_mode_max_tool_result_chars: 50000`), truncate it and append `"[truncated — output was {n} chars, showing first {threshold}]"`.
- This is a simple guard, not a replacement for proper context management. It prevents the most egregious cases.

**Files**: modify `src/agent.py` (`_run_loop` method), `src/config.py`.

---

## Phase 4 — Multi-Model Consensus (Council Mode Lite)

*The Council paper's full approach (heterogeneous expert panels with structured debate) is heavyweight. Implement a pragmatic subset.*

### 4a. Dual-Model Plan Validation

**Problem**: The planner and critic can share blindspots when they're the same model (or same model family). Council paper shows diverse models reduce hallucination by 35.9%.

**Design**:
- New config: `consensus.enabled: bool`, `consensus.second_provider: str`, `consensus.second_model: str`.
- When consensus is enabled and the plan is high-risk (from Phase 2b):
  1. Send the plan to a second model (different provider/family) with the critic prompt.
  2. Merge the two critic results: if *either* critic challenges a step, it's challenged.
  3. The planner revision sees challenges from both critics, labeled by source.
- When consensus is disabled or plan is not high-risk: single critic (current behavior).
- This is Council Mode's "intelligent triage" principle: don't consensus everything, only what matters.

**Implementation considerations**:
- Requires a second provider to be configured. If not configured, consensus silently falls back to single-critic.
- The second critic call can run in parallel with the first (both are independent).
- Cost: one extra LLM call on high-risk plans only. Acceptable.

**Files**: modify `src/runtime/critic.py`, `src/config.py`, `config.yml`, `src/agent.py`.

### 4b. Step-Level Confidence Scoring

**Problem**: The monitor makes binary pass/fail decisions. Council paper shows that confidence-weighted consensus is more robust than simple voting.

**Design**:
- Extend monitor's LLM assessment to include a `confidence` field (0.0-1.0).
- Log confidence alongside decision.
- Use confidence downstream:
  - Low confidence CONTINUE (< 0.5) → flag for review but proceed.
  - Low confidence RETRY (< 0.5) → skip instead of retrying (the monitor isn't sure, don't waste a retry).
- This is lightweight — just an extra field in the monitor's JSON response.

**Files**: modify `src/runtime/monitor.py`, `src/runtime/prompts.py`, `src/runtime/schema.py`.

---

## Phase 5 — Structured Workflows (BIN-Inspired)

*BIN survey argues that rule-based and behavior-tree components remain valuable for predictability. Add structured fast-paths for common patterns.*

### 5a. Workflow Templates

**Problem**: Common tasks (analyze binary → write summary, read file → modify → write) always go through full LLM planning. This is slow and error-prone for predictable patterns.

**Design**:
- New `src/workflows/` module with a `WorkflowMatcher` class.
- Workflow templates are Python classes (not config — they encode logic, not data):
  ```python
  class AnalyzeAndSummarize(Workflow):
      """Matches: 'analyze X and write to Y'"""
      pattern = re.compile(r"analyze\s+(\S+)\s+.*(?:write|save|output)\s+.*?(\S+\.(?:md|txt))")

      def generate_plan(self, match) -> Plan:
          target, output = match.groups()
          return Plan(steps=[
              Step(tool="file_info", description=f"Identify file type of {target}", ...),
              Step(tool="strings", description=f"Extract strings from {target}", ...),
              Step(tool="write_file", description=f"Write analysis summary to {output}", ...),
          ])
  ```
- `WorkflowMatcher.match(message) -> Plan | None` tries each template in order.
- Wire into `agent.py`: after classifier says "plan", try `workflow_matcher.match()` first. If it returns a Plan, skip the LLM planner entirely → go straight to validator → critic → execution.
- If no workflow matches → fall through to LLM planner (current behavior).

**Benefits**: Zero LLM calls for planning common tasks. Deterministic. Fast. Still goes through critic and monitor for safety.

**Files**: new `src/workflows/__init__.py`, `src/workflows/base.py`, `src/workflows/matcher.py`, `src/workflows/templates/` (one file per workflow pattern), modify `src/agent.py`.

### 5b. Workflow Discovery from Logs

**Problem**: We don't know which patterns are common without analyzing real usage.

**Design**:
- Script (`scripts/analyze_logs.py`) that reads all files in `_logs/`, extracts user messages and resulting plans, and clusters them by:
  - Number of steps
  - Tool sequence (e.g., [file_info, strings, write_file])
  - Action type sequence
- Output: frequency table of plan patterns, top 10 most common tool sequences.
- Use this data to prioritize which workflow templates to build.

**Files**: new `scripts/analyze_logs.py`.

---

## Phase 6 — Advanced Context Management

*Our context manager implements AFM's core mechanism but misses some of AFM's findings.*

### 6a. LLM-Assisted Importance Classification

**Problem**: AFM's key finding — importance classification is the dominant factor (83.3% vs 0%). Our importance classification is rule-based and coarse (system=CRITICAL, large tool output=LOW). We miss nuanced cases.

**Design**:
- After each step completes, run a lightweight LLM call (runtime provider) to classify the step result's importance: CRITICAL, HIGH, MEDIUM, LOW.
- The LLM sees: the original query, the step description, and the first 500 chars of the result.
- Store importance on the message metadata (new field in messenger or a parallel index).
- Context manager uses LLM-assigned importance instead of (or blended with) rule-based importance.

**Cost concern**: One extra LLM call per step. Mitigate by:
- Only classifying in plan mode (direct mode uses rule-based, as today).
- Only classifying tool results (not user messages or system messages).
- Using the cheapest available model.

**Files**: modify `src/runtime/context_manager.py`, `src/messenger.py` (add importance metadata), new classification logic.

### 6b. Compression Quality Improvement

**Problem**: `_compress_message()` truncates text and summarizes write_file content, but the compression is mechanical (first N chars). AFM's COMPRESSED fidelity implies intelligent summarization.

**Design**:
- For COMPRESSED fidelity tool results: instead of truncating to `compressed_max_chars`, use a one-shot LLM call to summarize the tool result in ≤ `compressed_max_chars`.
- The LLM sees: tool name, step description, full result, and instruction "Summarize this tool output in under {N} characters, preserving key facts and values."
- Cache summaries (same result → same summary). Store alongside the message.

**Cost concern**: One LLM call per compressed message. Mitigate by:
- Only summarizing when the original exceeds 2x the compressed limit (small results don't need summarization).
- Caching: if the message was already summarized, reuse it.
- Using the cheapest available model.

**Files**: modify `src/runtime/context_manager.py`.

---

## Phase Summary

| Phase | Focus | Key Deliverables | Complexity |
|-------|-------|------------------|------------|
| **1** | Safety & Correctness | Real ESCALATE, argument guard, critic fallback fix, revision retry | Medium |
| **2** | Critic & Planning | Tool weights, risk classification, vestigial cleanup, shared embeddings | Medium |
| **3** | Direct Mode | Guard, loop limits, token awareness | Low |
| **4** | Multi-Model Consensus | Dual-critic for high-risk plans, confidence scoring | Medium |
| **5** | Structured Workflows | Workflow templates, log analysis | Medium |
| **6** | Advanced Context | LLM importance classification, intelligent compression | High |

## Dependency Graph

```
Phase 1 (safety) ─── standalone, do first
    │
Phase 2 (critic) ─── depends on 1c (critic fallback)
    │
    ├── Phase 3 (direct mode) ─── depends on 1a (ESCALATE mechanism)
    │
    ├── Phase 4 (consensus) ─── depends on 2b (risk classification)
    │
    └── Phase 5 (workflows) ─── depends on 2c (cleanup, shared embeddings)
            │
            Phase 6 (context) ─── independent but benefits from all above
```

Phases 3, 4, and 5 can be worked in parallel after Phase 2 is complete.

---

## What This Roadmap Does NOT Include

- **Fine-tuned tool selection model** (SLM-TC): Requires training data and infrastructure we don't have yet. Revisit after workflow analysis (Phase 5b) reveals whether tool selection is still a bottleneck after the critic improvements.
- **Full Council Mode**: The complete multi-agent debate protocol from the paper is overkill for a CLI tool. Phase 4a implements the pragmatic subset (dual-critic on high-risk plans). Expand only if hallucination remains a problem.
- **Persistent memory across sessions**: Out of scope for this roadmap. Worth exploring but orthogonal to the execution reliability focus here.
- **Web UI / API server mode**: The `UserGate` interface (Phase 1a) is designed to be swappable, but building a web frontend is a separate project.
