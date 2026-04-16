# 0032 — Plan Critic: Adversarial Review Stage

**Date**: 2026-04-15
**Status**: Design
**Depends on**: 0031 (runtime fixes)

## Motivation

Testing across GPT-4o-mini, Haiku, and Anthropic models revealed a consistent problem: the planner over-selects tools. Given the prompt "analyze /bin/bash and write a summary," every model included objdump — which produces 1.2M tokens of disassembly for /bin/bash. None of that disassembly appeared in any of the final summaries. The models already know what bash is. The tools were useful only for local-specific facts: file type (Mach-O universal binary), version (bash-140, GNU bash 3.2.57), architecture (x86_64 + arm64e), file size (1.2M).

The planner treats tool selection like a checklist — "I have analysis tools, the user said analyze, so I'll use them all." A person wouldn't do this. A person would ask "what do I actually need to know?" and pick the minimum tools to get those answers. When a farmer harvests corn from their garden, they don't drive a combine through it.

We tried prompt-level guidance ("select only tools you need") but the same voice that selected the tools won't meaningfully challenge itself. We need a separate adversarial perspective — a **Plan Critic** — that pushes back on the planner's choices before any execution begins.

## Design

### Where it fits

```
User message
  │
  ├─► Intent Classifier → plan / direct
  │
  └─► [plan path]
        │
        Planner ──► Validator (structural) ──► Critic (adversarial) ──► Execute
                                                   │         ▲
                                                   │         │
                                                   └─────────┘
                                              (one round: challenge → revise)
```

The critic sits between structural validation and execution. It receives the original query and the validated plan. It returns either "approved" or a list of specific challenges. If challenged, the planner gets one retry with the critic's feedback. The revised plan goes straight to execution — no infinite loop.

### Plan schema changes

Add a `tool` field to each step. The planner declares which specific tool the step will use:

```json
{
  "step": 1,
  "description": "Get file type and architecture of /bin/bash",
  "action_type": "analysis",
  "tool": "file_info",
  "flags": {"retry": false, "escalate": false, "defer": false}
}
```

For `conversation` steps: `"tool": null`.
For write steps: `"tool": "write_file"`.

This serves two purposes:
1. The critic can evaluate specific tool choices, not vague action types.
2. At execution time, the runtime provides *only* the declared tool. Hard enforcement — the model physically cannot call objdump if the step says `"tool": "file_info"`.

### The Critic

#### Role

The critic is a separate LLM call using the runtime provider (cheap, fast — gpt-4o-mini or equivalent). It is not a rubber stamp. It is an adversary whose job is to poke holes in the plan, force the planner to justify every tool choice, and strip out anything that doesn't earn its place.

The critic does not rewrite the plan. It returns specific, pointed challenges that the planner must address in a revision.

#### Critic system prompt

```
You are a plan critic. Your job is to tear apart execution plans and find waste,
redundancy, and unjustified tool usage. You are tough but fair — if a plan is
genuinely sound, say so. But most plans aren't, and yours is the last checkpoint
before resources are spent executing.

You will receive a user's request and a proposed execution plan. For every step,
ask yourself:

1. JUSTIFY IT: Why does this step exist? What specific information does it
   produce that no other step produces and that the executor doesn't already
   know? If you can't articulate what unique value this step adds, challenge it.

2. PROPORTIONALITY: Is the tool proportionate to the task? A disassembly tool
   that generates millions of tokens to answer a question that needs a paragraph
   is not proportionate. Would you hire a forensic accountant to check your
   grocery receipt?

3. REDUNDANCY: Does this step duplicate information available from a lighter
   step? If strings already reveals version info, does nm add enough to justify
   its cost? Two tools that answer the same question is one tool too many.

4. ENVIRONMENT: Will this tool actually work? readelf requires binutils
   (not default on macOS). checksec requires a separate install. Planning steps
   around tools that will fail is worse than not planning them at all.

5. KNOWLEDGE CHECK: Does the executor already know the answer from training?
   Every major model knows what /bin/bash, /bin/ls, curl, python3 are and what
   they do. A tool call to "discover" widely-known information is ceremony,
   not analysis. Challenge it unless the tool reveals something the model
   genuinely cannot know (file size on this machine, exact version, local
   architecture, security hardening state).

6. ORDERING: Are the steps in the right dependency order? Does information
   flow logically from one step to the next? Is there a step that depends on
   output from a later step?

Be specific in your challenges. Don't say "this might not be needed." Say
"objdump on /bin/bash will produce ~1M tokens of disassembly. The user asked
for a summary. Name one thing objdump will tell you that you don't already
know and that will appear in the final summary."

Respond with a JSON object:

If the plan is sound:
{"verdict": "approved", "reasoning": "..."}

If you have challenges:
{"verdict": "challenged", "challenges": [
  {"step": 2, "tool": "objdump", "challenge": "...", "suggestion": "drop|replace|justify"},
  ...
]}

Suggestions:
- "drop": Remove this step entirely.
- "replace": Use a lighter tool instead (specify which).
- "justify": You're not sure — make the planner defend it.
```

#### Critic user prompt

```
User request: {original_query}

Proposed plan ({n_steps} steps):
{formatted_plan}

Available tools and what they provide:
{tool_descriptions}

Tear it apart.
```

The tool descriptions are included so the critic understands the cost/weight of each tool. Heavy tools (objdump, hexdump, strace) should be called out with approximate output sizes for common targets.

### Planner revision

When the critic returns challenges, the planner receives:

```
Your plan was reviewed by an adversarial critic. Address each challenge:

{challenges}

For each challenged step, either:
- Remove it if the critic is right
- Replace the tool with a lighter alternative
- Defend it with a specific justification (what unique information does this
  tool provide that will appear in the final output?)

Vague defenses like "it provides additional context" are not acceptable.
Name the specific fact the tool reveals.

Return a revised plan.
```

The planner must actually engage with the criticism, not just resubmit the same plan. If the revised plan still contains a challenged step, the planner must have provided a concrete defense.

### Execution: tool-per-step enforcement

At execution time, `_run_step` provides only the declared tool:

```python
if step.tool:
    tools = self.registry.get_tool_schema(step.tool)  # single tool
else:
    tools = []  # conversation step
```

This is a hard constraint. The model cannot call tools that aren't provided. No prompt-level "please only use file_info" that gets ignored — the tool simply isn't in the schema.

Exception: write steps may need `make_directory` alongside `write_file` (to create `_tests/` if it doesn't exist). The runtime can include a small allowlist of utility tools per action type.

### What the critic is NOT

- **Not a second planner.** It doesn't generate plans. It challenges them.
- **Not a safety gate.** ActionGuard handles destructive/risky actions. The critic handles waste and proportionality.
- **Not a loop.** One round of challenge → revise. If the planner can't satisfy the critic in one revision, we proceed with whatever the planner produced. We don't block execution — we improve it.

## Implementation plan

### Phase 1: Schema changes
- Add `tool: str | None` field to `Step` dataclass and `to_dict`/`from_dict`
- Update planner prompt to require `"tool"` field per step
- Update planner prompt with information-needs-first reasoning guidance
- Update example plans in prompt
- Validator: check that declared tool exists in registry

### Phase 2: Critic implementation
- New file: `src/runtime/critic.py`
  - `PlanCritic` class with `review(plan, original_query, tool_descriptions) -> CriticVerdict`
  - Uses runtime provider
  - Returns `CriticVerdict` (approved | challenged with list of challenges)
- New prompts in `src/runtime/prompts.py`
  - `CRITIC_SYSTEM_PROMPT`
  - `CRITIC_USER_TEMPLATE`
- New schema types in `src/runtime/schema.py`
  - `CriticVerdict`, `CriticChallenge`
- Config addition: `runtime.plan_critic.enabled: true`

### Phase 3: Integration
- Wire critic into `agent.py` between validator and execution
- When challenged: format challenges, send back to planner as revision prompt
- Planner revision: new method `Planner.revise(plan, challenges)` or extend existing `replan`
- Log critic output (challenges, verdict)

### Phase 4: Tool-per-step enforcement
- Modify `_run_step` to provide only the declared tool
- Add `ToolRegistry.get_tool_schema(tool_name)` method for single-tool lookup
- Define per-action-type utility allowlists (e.g., file_io steps also get `make_directory`)

### Phase 5: Monitor improvements (from 0031 observations)
- `_heuristic_triage`: check tool results from message history, not just model text response
- Flag `max_tokens` stop reason as incomplete step
- Track tool errors during step execution, pass to monitor

## Cost analysis

The critic adds 1-2 LLM calls to the planning phase:
- 1 call for the critic review (runtime provider — cheap)
- 0-1 calls for planner revision (main provider — only when challenged)

In exchange, it prevents:
- Unnecessary tool calls during execution (objdump alone saved ~5 tool iterations and 1.2M wasted tokens)
- Context manager blowout from oversized tool outputs
- Steps that fail due to unavailable tools (readelf, checksec on macOS)
- Wasted execution time on redundant steps

The planning phase gets ~2 seconds slower. Execution gets potentially minutes faster and dramatically more reliable.

## Example: before and after

**User request**: "Analyze /bin/bash, generate a thorough summary, write to _tests/bash.md"

### Before (current planner output)
```
Step 1 [analysis] file_info  — Identify file type
Step 2 [analysis] strings    — Extract printable strings
Step 3 [analysis] objdump    — Disassemble binary
Step 4 [analysis] nm         — Extract symbol table
Step 5 [analysis] readelf    — Examine ELF headers
Step 6 [analysis] checksec   — Check security features
Step 7 [file_io]  write_file — Write summary
```

### Critic challenges
```json
{
  "verdict": "challenged",
  "challenges": [
    {
      "step": 3,
      "tool": "objdump",
      "challenge": "objdump -d on /bin/bash will produce over a million tokens of x86/ARM disassembly. The user asked for a summary of what bash is and how it works — not a reverse engineering report. Name one fact from the disassembly that will appear in the final markdown file.",
      "suggestion": "drop"
    },
    {
      "step": 4,
      "tool": "nm",
      "challenge": "strings already extracts embedded text including function-related strings. What does the raw symbol table add for a summary? The user wants to know what bash does, not its internal symbol layout.",
      "suggestion": "justify"
    },
    {
      "step": 5,
      "tool": "readelf",
      "challenge": "readelf requires binutils which is not installed by default on macOS. This step will fail. Additionally, ELF header details are not relevant to a summary of what bash is and does.",
      "suggestion": "drop"
    },
    {
      "step": 6,
      "tool": "checksec",
      "challenge": "checksec is not installed on this system. Even if it were, security hardening flags are not relevant to a general summary unless the user specifically asked about security.",
      "suggestion": "drop"
    }
  ]
}
```

### After (revised plan)
```
Step 1 [analysis] file_info  — Identify file type and architecture
Step 2 [analysis] strings    — Extract version info and embedded text
Step 3 [file_io]  write_file — Write summary to _tests/bash.md
```

Three steps. No context budget blowout. No failed tool calls. The summary will contain the same information — because the information that matters was always in the model's training data plus two lightweight tool calls for local-specific facts.
