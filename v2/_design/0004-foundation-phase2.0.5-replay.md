# 0004 — Phase 2.0.5: Replay (modes 2 + 3)

**Status:** complete
**Phase:** 2.0.5
**Implements:** spec criterion 4 in `0001-foundation-phase0-design.md` §10.3,
plus user-asked-for replay mode 3 (live LLM + stubbed tools)

## 1. Goals

1. **Mode 2 — deterministic replay** (the gate). Re-run a recorded session
   with stubbed LLM + stubbed tools and assert the new event log matches
   the original after normalization. Catches "the recorder forgot to capture
   field X."
2. **Mode 3 — agent-rerun with recorded tools.** Live LLM, stubbed tools.
   Lets the user test prompt/model changes against a known scenario without
   re-running real tools.
3. **`arc replay <session_id>` CLI.** Default mode 2; `--live-llm` switches
   to mode 3.
4. **A useful diff** when replay diverges — line numbers, first divergence
   point, the actual difference.

## 2. Non-goals (deferred)

- **Mode 1 (time-travel / pause-resume)** — phase v2.1.5
- **Mode 4 (branch from turn N)** — phase v2.2
- **Mode 5 (live LLM + live tools)** — just use `arc run` against a
  different prompt; trivial wrapper if we want it later
- **Workspace snapshotting** — filesystem state is still forward-only;
  documented limitation
- **Replay nesting / sub-agents** — n/a until sub-agents exist (post-phase 2.2)

## 3. Design decisions (confirmed)

### A. What "byte-identical" means with regenerated timestamps + IDs

Normalize both sides before diffing. The normalizer replaces:

- `event_id` → `EVT_REPLAY_<index>` (so refs by index are stable)
- `parent_event_id` → resolved to the same normalized form
- `session_id` → `SES_REPLAY`
- `turn_id` → `TRN_REPLAY_<index>`
- `ts` → empty string
- `ts_monotonic_ns` → 0
- `duration_ms` → 0 (varies by run)
- `tool_use_id` in content blocks → `TCL_REPLAY_<index>` (provider-generated, varies)

Everything else — `type`, `stage`, `severity`, `payload` (except `tool_call_id`),
`content` — must match exactly. This catches: missing fields, key ordering
drift, normalized values, dropped messages.

### B. How recorded tool outputs are substituted

Replace the configured tool registry with a `ReplayingToolRegistry` whose
`.get(name)` returns a stub tool. The stub's `execute()` returns the next
recorded output for that tool.

Two strategies:

- **Mode 2 (deterministic)**: FIFO queue of recorded outputs per tool name.
  Pop on each call. If queue empty when called → loud error (recording
  diverged from runtime behavior, that's a bug).
- **Mode 3 (live LLM)**: lookup by `(name, canonical_input)` where
  `canonical_input = json.dumps(input, sort_keys=True)`. Recording → lookup
  table. If lookup misses → loud error: "live LLM called `<tool>` with
  inputs the recording doesn't cover — can't continue."

For mode 3, the same `(name, canonical_input)` can appear multiple times
in the recording. Each occurrence enqueues its output; calls pop in FIFO
order within that key.

This keeps the hook system untouched. No new return type from
`before_tool_call` needed.

### C. Divergence handling

Fail loud. The replay runs to completion (it always finishes — stubs always
return), then the diff runs, and the diff:

- Exits non-zero
- Prints a unified diff of the normalized event sequence
- Points at the first divergent event by index
- Names the field(s) that differ

If a tool stub runs out of recorded outputs (mode 2) or can't satisfy a
lookup (mode 3), the stub raises `ReplayDivergenceError`. The loop catches
it, emits a `replay.diverged` event, and ends the turn. The diff then has
something useful to point at.

## 4. Architecture

```
arc replay <session_id>
  │
  ├─ resolve home, load source session
  │    sessions/<id>/{events.jsonl, meta.json, config.snapshot.yml}
  │
  ├─ ReplayData = load(session_dir)   # parses everything into queues
  │
  ├─ build a fresh AgentSession using:
  │    - ReplayProvider(replay_data.llm_responses)   [mode 2 only]
  │    OR
  │    - real provider built from config.snapshot     [mode 3]
  │    - ReplayingToolRegistry(replay_data)
  │    - JSONLRecorder (writes a NEW session dir, marked replay_of=<original>)
  │
  ├─ for user_input in replay_data.user_inputs:
  │      sess.run_turn(user_input)
  │
  └─ diff(new events.jsonl, original events.jsonl)
       exit 0 if match, non-zero if diverged
```

## 5. New files

```
src/arc/replay/
  __init__.py         # `from arc.replay import ...`
  loader.py           # ReplayData + parse(session_dir)
  provider.py         # ReplayProvider
  tools.py            # ReplayingToolRegistry + ReplayingTool stub
  diff.py             # normalize() + diff()
  errors.py           # ReplayDivergenceError, MissingRecordingError
```

Plus a `_replay_of` field in the new session's `meta.json` so chains are
traceable.

Plus `tests/integration/test_replay.py` for the acceptance test.

## 6. New CLI

```
arc replay <session_id>                    # mode 2 — strict byte-identical
arc replay <session_id> --live-llm         # mode 3 — live LLM, stubbed tools
arc replay <session_id> --no-record        # don't write a new session dir
                                            # (just diff against original)
arc replay <session_id> --diff-only        # skip replay, just check that
                                            # the recording is parseable
```

Phase 2.0.5 ships the first two. The other flags are listed for future-
proofing; we can add them when needed.

## 7. Acceptance test

```python
# pseudo
def test_replay_is_byte_identical():
    # 1. Bootstrap, run hello-world against real Gemini, get session_id
    run_hello_world()
    sid = latest_session_id()

    # 2. Replay it in mode 2
    rc = main(["replay", sid])
    assert rc == 0   # no divergence
```

Plus: a test that **intentionally tampers** with the recording (drop a tool
output, mutate an LLM response) and asserts replay catches it with a clear
diff. This proves divergence detection isn't silently failing.

## 8. Open questions to resolve as we go

- **Order-vs-content matching for mode 3.** If the LLM calls tool X twice
  with input A then B, but the recording had B then A, the FIFO-per-key
  strategy still works (each lookup is by name+input). But if the LLM
  calls X with NEW input C, the lookup misses → error. That's the right
  behavior; flagging only because users may find it strict.
- **Should the replayed session itself be replayable?** Probably yes —
  it's just another session with a recording. No special handling needed.
- **Diff output format.** Unified diff vs JSON Patch vs custom — start with
  Python's `difflib.unified_diff` over JSON-line strings; revisit if it's
  unhelpful in practice.

## 9. Implementation notes

### 9.1 What landed

| Task | File(s) | Status |
|------|---------|--------|
| #62 Replay loader | `arc/replay/loader.py` + `errors.py` | ✅ |
| #63 ReplayProvider | `arc/replay/provider.py` | ✅ |
| #64 Replay tool stubs | `arc/replay/tools.py` | ✅ |
| #65 `arc replay` CLI | `arc/cli.py` (added `_cmd_replay`) | ✅ |
| #66 Replay diff | `arc/replay/diff.py` | ✅ |
| #67 Acceptance test | `tests/integration/test_replay_acceptance.py` | ✅ |

**Test coverage:** 21 unit tests (`test_replay.py`) + 4 acceptance tests
(`test_replay_acceptance.py`) — all green. Mode 2 byte-identical replay
confirmed against real Gemini recordings. Tampering detection confirmed
(mutate a recorded tool output → diff catches it with first-divergence pointer).

### 9.2 Bug caught during implementation

**The "spurious divergence on tool spec mismatch" problem.** First end-to-end
acceptance run failed with the diff pointing at `llm.call.started` event #2:

```
- "tools":[{"name":"ls","description":"List files and directories at the given path...","input_schema":{"properties":{"depth":...,"path":...}}}]
+ "tools":[{"name":"ls","description":"(replay stub for ls)","input_schema":{"properties":{}}}]
```

The runtime always emits `llm.call.started` with whatever the tool registry
holds. Replay stubs had generic descriptions + empty schemas, so the recorded
list and the replayed list always diverged — even though the LLM was stubbed
and never saw either.

**Fix:** added `tool_specs: dict[str, dict]` to `ReplayData`. The loader
walks the recorded `llm.call.started` events and extracts each tool's
original description + input_schema. `ReplayingTool` mirrors them when the
runtime introspects the registry to build the request.

Lesson worth keeping: any field the runtime emits to events.jsonl, replay
must reproduce *exactly*. Generic stubs aren't enough; stubs have to mimic
the recorded shape down to the strings.

### 9.3 What replay covers (and what it doesn't)

**Covered:**
- LLM responses preserved byte-faithfully (incl. `thought_signature` bytes
  base64'd through metadata, then decoded on load)
- Tool outputs preserved byte-faithfully (`output` field is a verbatim string)
- Event causation chains (parent_event_id normalization keeps them comparable)
- Multi-turn sessions (one user_input per turn played in order)
- Mode 2 (full deterministic) AND mode 3 (live LLM + stubbed tools)

**Intentionally not covered (deferred):**
- Pause/resume mid-replay (phase 2.1.5)
- Branching from arbitrary event N (phase 2.2)
- Sub-agent recordings (post-2.2 once sub-agents exist)
- Workspace state — if a recorded session wrote files, replay doesn't
  recreate them. Replay just runs the agent loop; side effects are out of
  scope until we have snapshotting.

### 9.4 Open issues for future phases

1. **Mode 3 with `--live-llm` is implemented but not acceptance-tested.**
   Adding a test that runs mode 3 against real Gemini would burn API budget
   for marginal gain — the unit tests cover the by-call lookup logic. Worth
   adding a hand-run smoke test when we revisit replay in 2.2.

2. **The diff layer treats `raw_provider_response` as opaque.** This means
   we don't catch "the provider SDK returned a structurally different
   response with the same surface text." For now that's acceptable — the
   surface text is what the LLM produced, and that's what matters for
   reproduction. Revisit if we hit issues.

3. **Cross-version replay.** Recording produced by `schema_version: 1`
   should be replayable by `schema_version: 2` (when we bump). The
   normalizer would need migrations. Not relevant yet.

## 10. Lessons (record so phase 2.1+ can learn from them)

1. **Real acceptance tests catch what unit tests don't.** The tool-spec
   divergence wasn't visible to any of the 21 unit tests. Running against
   a real recording caught it in seconds. Plan to write acceptance tests
   for every phase from now on, even when they cost API tokens.

2. **"Byte-identical" means *normalized* byte-identical.** It's tempting
   to skip the normalization step and just diff raw files. That would fail
   100% of the time on real recordings (event IDs change, timestamps change).
   The normalizer IS the contract — what's volatile vs. what's stable.

3. **The lookup-by-input strategy (mode 3) is strict by design.** If the
   live LLM picks a tool call the recording didn't see, we error out
   rather than guess. This is the right call — silent divergence in mode
   3 would defeat the "test prompt changes" use case.

4. **The replay engine doesn't touch the original session.** It writes a
   NEW session dir with `replay_of` set in meta.json. That way the
   original is untouched, replays are chainable, and you can `arc replay`
   a replay.

