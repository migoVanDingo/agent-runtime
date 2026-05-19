# 0007 — Phase 2.2: Branch + Rerun (modes 4 + 5)

**Status:** complete
**Phase:** 2.2
**Closes:** the spec §11 replay-mode catalog. After this phase modes 1-5 all exist.

## 1. Goals

1. **Mode 4 — branch.** Restore a session's first N turns into a new
   session, then take a different path. Useful for "I want to try a
   different prompt at turn 3 of this conversation."
2. **Mode 5 — rerun.** Take the user inputs from a recorded session and
   run them again against a fresh agent (live LLM + live tools). Useful
   for scenario-level regression testing: "does this still work with my
   current config?"
3. **Acceptance tests** that exercise both modes against real Gemini.

## 2. Non-goals

- **Arbitrary-event branching.** Branching at any event index (e.g., mid-turn,
  between a tool call and its result) sounds tempting but is fragile —
  the model needs a coherent conversation state. Turn boundaries are
  always coherent. Stick to those.
- **Mode 5 with edits to recorded prompts.** That's just "run a script
  of prompts" — not really replay. If you want to tweak prompts, write
  a shell script that calls `arc run` repeatedly.
- **Branch + rerun as separate top-level commands.** Branch is a flag on
  `arc resume`. Rerun gets its own subcommand because the intent is
  different (no continuation, full re-execution).

## 3. Design decisions

### A. Branch = resume with `--at-turn N`

Branch is mechanically identical to resume — restore conversation, start
new session — except it stops restoring after N turns. So it becomes a
flag on the existing `arc resume` command rather than a separate one.

```
arc resume <id>                              # restore all turns
arc resume <id> --at-turn 2                  # restore first 2 turns, then continue
arc resume <id> --at-turn 2 --prompt "..."   # branch + new prompt in one shot
```

`--at-turn N` is **1-indexed and means "include up to and including
the Nth turn's complete response."** So `--at-turn 1` restores the
first user input AND the assistant's reply to it; the next turn the
agent runs is turn 2 in conversation terms.

Edge cases:
- `--at-turn 0` = no messages restored = fresh session (warn the user)
- `--at-turn N` where N > available turns = clamp + warn

### B. Rerun gets its own subcommand

`arc rerun <id>` because the intent is "scenario regression test":

- Load the original session's user inputs (one per turn)
- Build a fresh AgentSession with the CURRENT config
- Run each user input through `run_turn()` in order
- New session marked `rerun_of: <original>` in meta

No message restoration. No stubs. The LLM responds fresh; tools run
live. Side effects on the filesystem are real.

### C. The same `messages_from_events` does both branch and full resume

Add `max_turns: int | None = None` parameter. None = unlimited (current
behavior). Integer = stop after that many `turn.ended` events. Single
function, two call sites. No new module.

### D. Rerun lives in `arc/rerun/` for symmetry with `arc/replay/` and `arc/resume/`

Even though it's small, the parallel structure makes intent obvious.
Three folders, three modes-of-replay (sort of).

## 4. New files

```
src/arc/
  rerun/
    __init__.py
    extract.py          # user_inputs_from_session
```

Updates:
- `arc/resume/reconstruct.py` — add `max_turns` parameter
- `arc/cli.py`                 — `--at-turn` flag on `resume`, new `rerun` subcommand

## 5. CLI

```
arc resume <id>                         # everything
arc resume <id> --at-turn N             # branch
arc resume <id> --at-turn N --prompt X  # branch + immediate turn
arc resume <id> --no-tui                # restore-only

arc rerun <id>                          # mode 5
arc rerun <id> --stop-on-error          # bail on first turn that fails
```

## 6. Acceptance tests

Both modes need real Gemini to be meaningful.

### 6.1 Branch
- Record a multi-turn session (e.g., 2 turns: ask about files, then ask
  for file count)
- `arc resume <id> --at-turn 1 --prompt "Different follow-up"`
- Assert: new session's first `llm.call.started` has messages from turn 1
  (user + assistant) plus the new user prompt — NOT turn 2's messages
- Assert: new session's meta has `resumed_from` + `restored_message_count`
  matching turn 1's message count

### 6.2 Rerun
- Record a 1-turn session
- `arc rerun <id>`
- Assert: new session exists with `rerun_of: <original>` in meta
- Assert: new session's first `turn.started` content.user_input matches
  the original's
- Assert: the new session ran to completion (a real LLM call happened)

## 7. Open questions

1. **Turn numbering display.** `arc resume <id> --at-turn 2` — should
   we print "branched at turn 2 of 5"? Yes, helpful UX. Add a one-line
   summary.
2. **Mode 5 with paused/errored turns in the source.** A recorded session
   may have a turn that didn't complete (pause, error). Rerun should
   still attempt that user input — the rerun is fresh, the error may not
   recur. Document this.
3. **Should rerun be able to use a different model?** Yes, naturally —
   just edit `config.yml` before rerun. No flag needed.

## 8. Implementation notes

### 8.1 What landed

| Task | File(s) | Status |
|------|---------|--------|
| #82 Branch (`--at-turn`) | `arc/resume/reconstruct.py`, `arc/cli.py` | ✅ |
| #83 `arc rerun` CLI | `arc/rerun/extract.py`, `arc/cli.py` | ✅ |
| #84 Acceptance tests | `tests/integration/test_branch_and_rerun.py` | ✅ |

**Test coverage:** 14 unit tests (branch + rerun extraction) + 7 acceptance
tests against real Gemini. **251 tests total, all green.**

### 8.2 Branch is just `messages_from_events(max_turns=N)`

The whole branch feature is ~10 lines of code in `reconstruct.py`: a
parameter that counts `turn.ended` events and returns early when the
threshold is hit. The CLI wraps it with validation (clamp on too-high,
warn on zero, accept None). No new module, no parallel infrastructure.

This is the right level of reuse — branch and resume have the same
mechanics, just different scopes. Splitting them would have duplicated
code for the sake of separate names.

### 8.3 Rerun is a thin wrapper around `run_turn`

`arc rerun` is essentially:

```python
inputs = user_inputs_from_session(source)
for user_input in inputs:
    sess.run_turn(user_input)
```

No special engine, no replay machinery. The recording is just a script
of prompts; rerun runs the script. Live everything.

The meta marker `rerun_of` chains rerun history the same way `resumed_from`
chains resume history. You can rerun a rerun, branch a rerun, resume a
rerun. The chains compose cleanly.

### 8.4 Bug NOT caught (worth noting)

Unlike previous phases, this one had no real bugs during implementation.
Two reasons:

1. The extraction logic is small and pure (no state, no side effects).
2. The CLI handlers reuse patterns from `_cmd_resume` so the wiring
   was mechanical.

When a phase is mostly composition over existing primitives, bugs tend
to be in the primitives, not the composition. We caught those in earlier
phases.

### 8.5 Operational state

| Thing | State |
|-------|-------|
| `arc resume <id> --at-turn N` | works |
| `arc resume <id> --at-turn 0` | warns, restores nothing |
| `arc resume <id> --at-turn 99` (overshoot) | clamps + warns |
| Branched session marks `branched_at_turn` in meta | yes |
| `arc rerun <id>` | works, live LLM + tools |
| Rerun session marks `rerun_of` in meta | yes |
| Rerun --stop-on-error | works |
| Rerun on multi-turn source | works, all turns replayed in order |
| Rerun chains (rerun → rerun → rerun) | work |
| Branch chains (branch → branch) | work (each branch is its own session) |

### 8.6 Replay-mode catalog status (from spec §11)

| Mode | What | Phase | Status |
|------|------|-------|--------|
| 1 | Time-travel (pause + resume) | 2.1.5 | ✅ |
| 2 | Deterministic replay | 2.0.5 | ✅ |
| 3 | Agent-rerun with recorded tools (live LLM) | 2.0.5 | ✅ |
| 4 | Branch from turn N | 2.2 | ✅ |
| 5 | Agent-rerun with live tools (`arc rerun`) | 2.2 | ✅ |

**All 5 modes implemented. The foundation is structurally complete.**

## 9. Lessons for the next phase

1. **Reuse beats parallelism.** Branch and resume could have been
   separate code paths but didn't need to be. Same for rerun — it could
   have built its own "ScriptedTurnRunner" abstraction but `run_turn()`
   in a loop is the same thing without ceremony.

2. **Meta.json is becoming the session DAG.** Sessions now have
   `resumed_from`, `branched_at_turn`, `rerun_of`, `replay_of`,
   `replay_mode` — a small set of fields that lets you reconstruct the
   relationships between sessions. Worth considering whether to surface
   these via `arc sessions --tree` or similar in a future polish pass.
   Tracking for later.

3. **The 5-mode catalog from spec §11 paid off.** Having all five modes
   listed upfront in the design doc kept us from accidentally building
   only what felt necessary at the moment. Each mode forced a different
   composition of the same primitives, and the exercise validated that
   the primitives are well-chosen. If any mode had been impossible to
   build cleanly, it would have signaled a missing primitive.

## 10. Where the project stands after phase 2.2

The **runtime foundation is complete**. Every "where state lives, who
controls execution, how to inspect/reproduce" question has an answer
that's been exercised in tests:

- Where does the conversation live? → events.jsonl + restored as needed
- How do plugins compose? → registry with explicit hook orders
- How do we inspect a session? → events, meta, snapshot — all human-readable
- How do we reproduce a session? → 5 distinct modes, each with a CLI command
- How do we extend? → write a plugin, register a hook, done

What's NOT in the foundation:
- Skills, planner, monitor, council, RAG, sub-agents, context manager —
  these are **capability layers** that go on top, each as a plugin
- Sandbox isolation for `bash_exec` — also a plugin, future phase

The next phase (call it 3.0 or whatever) should be the first capability
plugin we add. The natural candidates are:
- **Context manager** (AFM-style budgeting) — needed once we hit long
  sessions that bust context
- **Sub-agents** — needed once we want delegation
- **A small monitor** — needed once we want autonomous error recovery

The choice depends on what use case we're prioritizing. That's a
product decision, not a runtime decision.

