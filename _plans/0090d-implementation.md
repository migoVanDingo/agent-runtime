# 0090d — Implementation notes

> Companion to `_plans/0090-context-discipline-and-subagents.md` §6 0090d.

## What landed

The first concrete sub-agent (`GhidraAnalyst`), the toolset/registry wiring
needed to expose it as a `subagent_ghidra_analyst` tool, and the
deep-disassembly skill rewrite that delegates RE work to the analyst
instead of dumping the decompile into the main agent's context.

Two cross-cutting fixes also landed here (deferred from 0090c):
- Stages now honour `child._system_prompt_override` from `SubAgentSpec.system_prompt`.
- Escalation prompts are prefixed with the current scope tag.

## Files added

- `src/tools/implementations/subagents/ghidra_analyst.py` — the spec,
  system prompt, and JSON response schema. ~150 lines.
- `_plans/0090d-implementation.md` — this doc.

## Files modified

### Sub-agent system-prompt override propagation (deferred from 0090c)

- `src/agent.py:_build_pipeline`:
  - Reads `getattr(p, "_system_prompt_override", "") or config.agent.system_prompt`
    so the child agent's spec-driven prompt takes effect.
  - Passes `agent_system=system` to `RoutingStage(...)` so routing's
    system-prompt build uses the override too.
- `src/runtime/stages/routing.py`:
  - `__init__` gains `agent_system: str | None = None` parameter.
  - `run()` reads `base_system = self._agent_system or config.agent.system_prompt`
    when constructing the routing system prompt.

### Escalation prompt scope prefix (deferred from 0090c)

- `src/runtime/tool_executor.py:ToolCallExecutor.execute`:
  - When `guard_decision == GuardDecision.ESCALATE`, the escalation
    reason is now prefixed with `[<scope>]` for runtime/subagent scopes
    (main scope stays unprefixed). User sees:
    `[subagent:ghidra_analyst] host execution: ghidra_analyze on 'proc'`

### Plan schema

- `src/planning/schema.py:ActionType` gains `SUBAGENT = "subagent"`. Plan
  steps that dispatch a sub-agent declare this as their action_type so
  the validator can verify the toolset exists.

### Toolset registration

- `src/tools/toolsets.py`:
  - New `_build_subagent_toolset()` that imports all built-in sub-agent
    spec modules (triggering their `register_spec` side effects),
    constructs a `SubAgentTool` per registered spec, and bundles them
    into a `Toolset(name="subagent", ...)`.
  - `SUBAGENT = _build_subagent_toolset()` evaluated at module import.
  - Appended to `ALL_TOOLSETS` so the registry picks it up.

### Deep-disassembly skill rewrite

- `src/skills/implementations/deep_disassembly.py:DeepDisassembly.expand`:
  - **Before** (9 concrete steps): file_info, checksec, strings, nm,
    ghidra_analyze, ghidra_functions, ghidra_decompile, ghidra_find_constants,
    read_file (loads 12k+ chars of decompile into context), synthesis,
    optional write.
  - **After** (4 steps): file_info, **subagent_ghidra_analyst** (does
    everything ghidra+ recon would have done, returns structured JSON),
    synthesis (uses analyst's structured response), optional write.
  - The crypto-hint heuristics now live in the analyst's permanent
    system prompt instead of being injected per-step.

## The GhidraAnalyst spec

- **Name/scope tag:** `ghidra_analyst` → tool surface `subagent_ghidra_analyst`,
  scope contextvar value `subagent:ghidra_analyst`.
- **Toolsets:** `reversing`, `file_io`, `shell` — the three the analyst
  needs to do its job. Notably NOT `subagent` (the runner filters
  SubAgentTool out, so this is belt + suspenders).
- **System prompt:** ~80-line reverse-engineering methodology embedding
  every lesson we've learned in recent sessions: two's-complement constant
  trick, known crypto constant table, ECB-vs-CBC dynamic-test recipe, TEA-
  vs-XTEA structural distinguisher, IV byte spotting, dynamic verification
  protocol.
- **Response format:** `json` with schema covering algorithm, mode, iv,
  key_derivation, round_function, constants[], summary, verification_status.
  `algorithm` + `summary` are required; others are optional so the analyst
  can return partial findings without lying.
- **Timeout:** 900s (15 min). Ghidra first-run analysis can be slow even
  with the subprocess fix; allow generous headroom.
- **Max iterations:** 25. Enough for several iterative tool calls plus
  dynamic verification.

## Why this matters in concrete numbers

For the `proc` workload that hit the 119k routing call:

| Stage | Before (in main context) | After (delegated) |
|---|---|---|
| Decompile artifact in main context | ~3k tokens (read via read_file) | 0 tokens (lives in analyst) |
| Find-constants artifact | ~3k tokens | 0 tokens |
| Per-step intermediate reasoning | accumulates across 9 steps | 1 step in main, all inside analyst |
| Analyst's return to main | n/a | ~400-800 tokens of structured JSON |

Net main-context savings per analysis: ~5-7k tokens, on top of what
0090a/b already saved. Across a multi-turn session iterating on the
clone (the user's actual workflow), the compounding effect is large.

## Verification

- Compile-check: clean.
- All previous tests pass (175 total, zero new regressions).
- Smoke test:
  - `GhidraAnalystSpec` registered ✓, discoverable via `get_spec()` ✓.
  - `SUBAGENT` toolset contains `subagent_ghidra_analyst` tool ✓.
  - `ALL_TOOLSETS` includes `subagent` ✓.
  - `DeepDisassembly.expand` returns 4-step plan with step 2 =
    `subagent_ghidra_analyst` (action_type=`subagent`) ✓.

## What hasn't shipped yet

- A live end-to-end run of the new deep-disassembly skill against the
  `proc` binary. The reason is each Ghidra invocation through the
  analyst takes minutes (subprocess + JVM + first-run analysis) and
  hammers the providers. Smoke-tested at the structural level; the
  full E2E should run in the next user session.
- Per-spec provider/model config overrides — **0090e**.
- `arc subagent list` CLI — **0090e**.
- TUI spinner active-scope display — **0090e**.

## Open issues / known limitations

- **Analyst inherits parent's provider.** If you want the analyst on
  Claude Opus instead of whatever the main agent uses, you currently
  have no override path. 0090e adds the config-driven override.
- **No retry-on-bad-JSON for the analyst.** If the analyst produces
  malformed JSON, `SubAgentRunner._parse_json_response` returns None
  and the parent agent receives the raw text. The synthesizer step
  will probably handle it gracefully, but a structured-output retry
  loop would be more robust. Punted; revisit if telemetry shows
  parse failures.
- **The analyst's system prompt is large** (~3-4k tokens). That eats
  into the analyst's own context budget. Acceptable: the analyst has
  its own AFM context_mgr (inherited from parent) and 0090a's scope
  awareness applies to nested calls naturally (the analyst's own
  routing/skill_hint calls will be in `subagent:ghidra_analyst` scope,
  which is NEITHER `main` NOR `runtime`, so they use the larger
  message budget — appropriate for a deep-thinking agent).
- **No automated test for the deep-disassembly skill output.** The
  4-step structure is verified by smoke test, but a unit test that
  asserts the skill's expansion shape would be cheap insurance.
