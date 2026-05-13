# 0079i — Phase I: Fix Loop Migration (test-reconstruction worked example)

> **Read first:** `_plans/0079-runtime-as-god.md` (whole doc).
> **Depends on:** all prior 0079 phases (a–h).
> Final phase. Removes the last vestige of DRIFT-1 by demonstrating
> that the canonical fix-loop runs end-to-end with no iteration count
> in any step description.

## Goal

Take the most-painful drift case in the codebase — the `test-reconstruction`
workflow's "Repeat up to 8 times" loop encoded in a step description —
and prove the new architecture handles it cleanly:

- The skill declares **what** to do per iteration (read DiffReport,
  fix candidate, re-run diff_behavior).
- ContinuationStage owns **how many times** (config-bounded).
- The skill's `CompletionCriteria` (`StructuralCriteria` on
  `diff_behavior` + `all_match=true`) determines **when to stop**.
- The Monitor (after phase 0079h) can short-circuit early if
  diff_behavior already passed.

This is the **before/after** worked example the brief asked for in B.4.

## Before (current state, prior to 0079 series)

`src/workflows/implementations/test_reconstruction.py:38-110`

The whole file is one long `Plan` factory. Step 3 description (lines
77-110) hard-codes:
- "Repeat up to 8 times"
- "Stop only when all_match=true or after 8 iterations"
- A multi-paragraph bug-pattern crib sheet

The runtime infrastructure had **no idea** there was a loop happening.
The LLM either followed the description literally or didn't, and the
8-iteration cap was enforced by the LLM's good behavior, not by code.

## After (this phase)

The file becomes `src/skills/implementations/test_reconstruction.py`
with three roles split apart:

1. **`expand()`** — emit two concrete steps: identify paths, run
   diff_behavior. **Does not** include the fix step in the initial
   expansion. The fix is part of the continuation, not the initial
   plan.
2. **`continuation_steps(ctx, prior_results)`** — emit the fix-loop
   step set: read diff result → read candidate source → write fixed
   source → re-run diff_behavior. Returned only when the previous
   diff_behavior is a fail.
3. **`completion_criteria`** — `StructuralCriteria(tool_name="diff_behavior",
   predicate=diff_behavior_all_match)`.

The 8-iteration cap is now `config.runtime.continuation.max_iterations`
(default 5; raise to 8 in config.yml for this skill if you want
identical behavior to the old setting — see §6).

## Files

| File | Why |
|------|-----|
| `src/skills/implementations/test_reconstruction.py` | The migrated skill. |
| `config.yml` | Optional: bump `runtime.continuation.max_iterations` to `8`. |
| `_plans/0079i-fix-loop-migration.md` | This file (the worked example). |

## The new skill (full file)

**File:** `src/skills/implementations/test_reconstruction.py`

```python
"""Standalone reconstruction verification skill.

Triggered when the user wants to iteratively test a reconstructed
source file against an original binary using behavioral differential
testing.

The fix-loop (run diff → identify divergence → fix → re-run diff)
is owned by ContinuationStage. This skill declares only the initial
diff and the per-iteration fix steps — never how many times to loop.
"""
from __future__ import annotations
import re
from planning.schema import Step, ActionType
from runtime.schema import ContinuationDecision
from skills.base import Skill, SkillContext
from skills.criteria import (
    CompletionCriteria, StructuralCriteria, diff_behavior_all_match,
)


class TestReconstruction(Skill):
    name = "test-reconstruction"
    intent = (
        "Use this skill when the user wants to test, verify, or iterate on a "
        "reconstructed source file against an original binary. Runs diff_behavior "
        "between an oracle binary and a candidate source across boundary test "
        "cases. ContinuationStage drives the fix-loop until all cases match."
    )
    pattern = re.compile(
        r"iterate\s+on\s+(?:the\s+)?(?:code|source|clone|reconstruction)"
        r"|test\s+(?:the\s+)?(?:clone|reconstruction|source)\s+(?:against|vs)"
        r"|verify\s+(?:the\s+)?reconstruction"
        r"|does\s+\S+\s+match\s+(?:the\s+)?(?:original|binary)"
        r"|check\s+if\s+\S+\s+(?:matches|is\s+correct)"
        r"|run\s+diff_behavior"
        r"|behavioral\s+(?:test|diff|comparison)",
        re.IGNORECASE,
    )

    _target_re = re.compile(r"(?:^|\s)(_?[\w./\-]+(?:/[\w.\-]+)*)\b")

    @property
    def completion_criteria(self) -> CompletionCriteria:
        return StructuralCriteria(
            tool_name="diff_behavior",
            predicate=diff_behavior_all_match,
            on_met=ContinuationDecision.SYNTHESIZE,
        )

    # ── Initial expansion: identify paths + run diff_behavior ─────────

    def expand(self, ctx: SkillContext) -> list[Step]:
        message = ctx.original_query
        oracle, candidate = self._extract_paths(message)
        n = ctx.starting_step_number

        return [
            Step(
                step=n,
                description=(
                    f"Identify the oracle binary and candidate source file from the "
                    f"user's message and conversation context.\n"
                    f"Detected oracle: {oracle or 'unknown — ask user'}\n"
                    f"Detected candidate: {candidate or 'unknown — look for recent .c files in _tests/'}\n\n"
                    f"If either is missing, check the conversation for recently mentioned paths. "
                    f"Look for files matching *_clone.c or *_fixed.c in _tests/. "
                    f"Confirm both paths before proceeding."
                ),
                action_type=ActionType.CONVERSATION,
                tool=None,
            ),
            Step(
                step=n + 1,
                description=self._diff_step_description(oracle, candidate),
                action_type=ActionType.SHELL,
                tool="diff_behavior",
            ),
        ]

    # ── Continuation: fix-loop iteration ──────────────────────────────

    def continuation_steps(
        self, ctx: SkillContext, prior_results: list[Step],
    ) -> list[Step] | None:
        """Return the steps for one fix-and-retest iteration.

        Called by ContinuationStage when the previous diff_behavior step
        had all_match=false. The infrastructure (not the skill) decides
        how many times this is called — bounded by max_iterations and
        gated by the StructuralCriteria above.
        """
        # Recover oracle/candidate from the prior plan if we can find them.
        oracle, candidate = self._extract_paths(ctx.original_query)
        n = ctx.starting_step_number
        return [
            Step(
                step=n,
                description=(
                    f"Read the most recent DiffReport. Identify the failing test "
                    f"cases and what each mismatch implies about the candidate's "
                    f"implementation."
                ),
                action_type=ActionType.CONVERSATION,
                tool=None,
            ),
            Step(
                step=n + 1,
                description=(
                    f"Read the current candidate source from {candidate or '<candidate>'} "
                    f"with read_file. You must see what is on disk before editing it."
                ),
                action_type=ActionType.FILE_IO,
                tool="read_file",
            ),
            Step(
                step=n + 2,
                description=(
                    f"Fix every bug identified in the DiffReport. Write the "
                    f"corrected source to {candidate or '<candidate>'} with "
                    f"write_file.\n\n"
                    f"Common bug shapes to consider:\n"
                    f"  - candidate output longer than oracle for the same input → "
                    f"padding formula wrong (PKCS#7: pad = BLOCK − (len % BLOCK), "
                    f"always 1..BLOCK bytes, never extra).\n"
                    f"  - same length but content differs on every case including "
                    f"1-byte input → key derivation wrong (cyclic fill: "
                    f"key[i] = pass[i % len(pass)]; do not stop at NUL).\n"
                    f"  - single-block matches but multi-block diverges → "
                    f"CBC chaining missing (XOR each block with previous "
                    f"ciphertext; first block XORs with IV).\n"
                    f"  - content differs after fixing padding + key → check for "
                    f"hardcoded IV from prior recon analysis.\n\n"
                    f"Before your first fix, recall any prior deep-disassembly "
                    f"artifacts for this binary that may carry IV bytes or "
                    f"algorithm constants — look them up rather than guessing."
                ),
                action_type=ActionType.FILE_IO,
                tool="write_file",
            ),
            Step(
                step=n + 3,
                description=self._diff_step_description(oracle, candidate),
                action_type=ActionType.SHELL,
                tool="diff_behavior",
            ),
        ]

    # ── Helpers ───────────────────────────────────────────────────────

    def _diff_step_description(self, oracle: str | None, candidate: str | None) -> str:
        return (
            f"Run diff_behavior to compare oracle vs candidate:\n"
            f"  oracle_path: {oracle or '<oracle_path>'}\n"
            f"  oracle_type: native_binary\n"
            f"  candidate_path: {candidate or '<candidate_path>'}\n"
            f"  candidate_type: c_source (or cpp_source / python_source as appropriate)\n"
            f"  test_cases: standard boundary set:\n"
            f"    [{{'id':'enc_1b','args':['-e','pass','a']}},"
            f"{{'id':'enc_7b','args':['-e','pass','1234567']}},"
            f"{{'id':'enc_8b','args':['-e','pass','12345678']}},"
            f"{{'id':'enc_10b','args':['-e','pass','helloworld']}},"
            f"{{'id':'enc_15b','args':['-e','pass','123456789012345']}},"
            f"{{'id':'enc_16b','args':['-e','pass','1234567890123456']}},"
            f"{{'id':'enc_short','args':['-e','ab','helloworld']}},"
            f"{{'id':'enc_long','args':['-e','abcdefghijklmnop','helloworld']}}]\n"
            f"\n"
            f"Adjust test_cases to match the actual CLI interface if different from the above."
        )

    def _extract_paths(self, message: str) -> tuple[str | None, str | None]:
        oracle, candidate = None, None
        for m in self._target_re.finditer(message):
            tok = m.group(1)
            if "/" not in tok or tok.startswith("http"):
                continue
            lower = tok.lower()
            if any(lower.endswith(ext) for ext in (".c", ".cpp", ".py")):
                candidate = tok
            elif "clone" in lower or "fixed" in lower:
                candidate = tok
            elif oracle is None:
                oracle = tok
        return oracle, candidate
```

## What's gone (and why this is the win)

| Removed                                          | Reason                                  |
|--------------------------------------------------|-----------------------------------------|
| "Repeat up to 8 times"                           | Loop count is `config.runtime.continuation.max_iterations`. |
| "Stop only when all_match=true or after 8 iterations" | StructuralCriteria + iteration cap. |
| `flags=StepFlags()` on every step                | Phase 0079f deleted prescriptive flags. |
| `requires_synthesis=True` on the Plan            | Phase 0079f deleted the field.          |
| Bug-pattern crib sheet inside Step 3             | Moved into `continuation_steps()` description, but trimmed: it's now a *checklist* the LLM consults each iteration, not a one-shot blob. |
| Step 3 doing 4 different things (read DiffReport, read source, write fix, re-run diff) | Split into 4 atomic steps — the runtime can now monitor each individually. |

## Concrete fix-loop trace (expected log)

```
=== Workflow / skill match ===
  hint: 'test-reconstruction' (advisory; planner may override)

=== Planning ===
  planner emits: [Step 1 (CONVERSATION) — invoke skill:test-reconstruction]
  validation: VALID

=== Skill expansion ===
  step 1: skill:test-reconstruction → 2 concrete step(s)
  expanded plan: 2 step(s)

=== Plan critic ===
  critic: skipped — risk=low, complexity=4 < threshold 8

=== Step 1/2 [conversation] ===
  Identify oracle/candidate paths...
=== Step 2/2 [shell] tool=diff_behavior ===
  ... DiffReport: all_match=false, 4 of 8 cases diverge ...

=== Continuation ===
  criteria NOT_MET → LOOP
  continuation: LOOP iteration 1 — 4 new step(s)
=== Step 1/4 [conversation] ===
  Read the most recent DiffReport...
=== Step 2/4 [file_io] tool=read_file ===
=== Step 3/4 [file_io] tool=write_file ===
=== Step 4/4 [shell] tool=diff_behavior ===
  ... DiffReport: all_match=true ...
  monitor: skill 'test-reconstruction' criteria MET → GOAL_ACHIEVED
=== Goal achieved at step 4/4 ===

=== Continuation ===
  criteria MET (StructuralCriteria) → synthesize

=== Synthesizing ===
=== Done ===
```

The orchestration is fully visible in the log; nothing is hidden inside
a step description.

## Edge cases handled

| Scenario | Behavior |
|----------|----------|
| Initial diff_behavior already passes | Monitor returns `GOAL_ACHIEVED` at step 2/2 of the initial plan. ContinuationStage decides `SYNTHESIZE` immediately. No fix iteration runs. |
| Iteration cap reached without all_match | ContinuationStage logs cap and decides `SYNTHESIZE`. The synthesizer reports the partial progress. The user sees an honest summary. |
| `diff_behavior` step errors out (compilation failure, missing file) | Monitor heuristics flag the step → LLM monitor decides RETRY/REPLAN as today. Skill criteria check is never reached. |
| Candidate path can't be inferred | Step 1 (CONVERSATION) is a no-op LLM read; the LLM should ask the user via ESCALATE. Same as today. |

## Verification

```bash
pytest -x -q

# E2E: place a known-broken candidate at _tests/proc_clone.c that
# differs from _tests/proc on multi-block inputs.
# Run:
python -m src.main <<< "test the proc_clone reconstruction against _tests/proc"

# Expect:
#   - SkillHintStage hints test-reconstruction
#   - PlanningStage emits [skill:test-reconstruction]
#   - SkillExpansionStage expands to 2 steps
#   - First diff_behavior fails
#   - ContinuationStage LOOPs (iteration 1)
#   - 4-step fix iteration runs
#   - Second diff_behavior passes
#   - Monitor returns GOAL_ACHIEVED at the diff step
#   - ExecutionStage marks any remaining (none in this case) skipped
#   - ContinuationStage decides SYNTHESIZE
#   - Synthesizer produces the final summary

# Check no iteration counts leak:
rg -n "Repeat up to" src/skills/      # zero
rg -n "after \d+ iterations" src/skills/  # zero
```

## Done when

- [ ] `src/skills/implementations/test_reconstruction.py` matches the
      structure above.
- [ ] No iteration counts in any step description in any skill.
- [ ] End-to-end fix-loop runs against a known-broken candidate and
      converges within `max_iterations`.
- [ ] `pytest` green.
- [ ] §8 verification gate from `0079-runtime-as-god.md` is fully
      satisfied. (Take a final pass through that gate.)

## Final check after this phase

Re-read `_plans/0079-runtime-as-god.md` §8. Every checkbox should now
be true. If any isn't, identify which earlier phase regressed and fix
forward — do NOT amend a sealed phase.

If the §8 gate is fully green, the runtime-as-god initiative is
complete. The deferred observability initiative (see `MEMORY.md`
note about `project_observability_followup.md`) becomes the next
candidate plan, likely `0080-runtime-observability.md`.

---

## Implementation Status (2026-05-09)

All phases 0079a–0079i implemented in a single pass by Sonnet 4.6.

**§8 Gate verified:**
- [x] `Plan` has no `requires_synthesis` field.
- [x] `StepFlags`/`StepRuntimeState` has only runtime-state fields.
- [x] `PLAN_JSON_SCHEMA` removes `requires_synthesis` and the `flags` block.
- [x] `WorkflowMatchStage` replaced by `SkillHintStage` (never sets `context.plan`).
- [x] `_WORKFLOW_PATHS` gone from `council.py`; replaced by `_should_run_council`.
- [x] `ExecutionStage._step_utility_tools` removed; utility relations in `config.runtime.tool_policy.utility_tools`.
- [x] `ContinuationStage` in `src/runtime/stages/continuation.py`, wired in `agent.py`.
- [x] `SkillExpansionStage` in `src/runtime/stages/skill_expansion.py`, wired between Planning and EntityCritic.
- [x] `test-reconstruction` skill has `CompletionCriteria` (StructuralCriteria on `diff_behavior`).
- [x] `ExecutionMonitor` returns `StepDecision.GOAL_ACHIEVED`; ExecutionStage handles it.
- [x] 110 tests pass (2 pre-existing failures excluded: `test_sandbox_hardening`, `test_entity_critic`).

**Additional: Not in phase docs**
- Old `src/workflows/implementations/*.py` files updated to remove `requires_synthesis` constructor arg (Plan no longer accepts it).
- `test_run_state.py` updated to remove `requires_synthesis` assertion.
- `src/tools/toolsets.py` planning note updated to remove `requires_synthesis=true` reference.
- `src/runtime/run_state.py` `requires_synthesis` property removed from `PlanRun`.
