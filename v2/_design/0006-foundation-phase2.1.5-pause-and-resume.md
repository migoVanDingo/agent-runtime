# 0006 — Phase 2.1.5: Pause + Resume (mode 1: time-travel)

**Status:** complete
**Phase:** 2.1.5
**Implements:** mode 1 (time-travel) from `0001-foundation-phase0-design.md` §11

## 1. Goals

1. **Pause works.** A running agent stops cleanly at the next cooperative
   checkpoint. No half-state, no orphan subprocesses. The session ends with
   a `paused` outcome and the recording is well-formed.
2. **Two trigger paths:** (a) external signal file
   (`<session_dir>/pause`) for scripting; (b) SIGINT (Ctrl+C) during an
   interactive TUI turn for humans. Both feed into the same plugin flag.
3. **Resume continues the conversation.** `arc resume <session_id>` loads
   the recorded conversation, starts a new session marked
   `resumed_from: <original>`, and runs new turns with the model seeing
   the full prior context.
4. **Acceptance test:** record a multi-step bash workflow, trigger pause
   mid-turn, assert agent stopped cleanly, resume, assert continuation works.

## 2. Non-goals

- **Mid-iteration pause.** Pause checks fire BETWEEN iterations of the
  ReAct loop. If the agent is mid-`provider.chat()` or mid-`tool.execute()`,
  pause waits for that call to finish. For LLM calls this can mean ~30s
  of lag; documented and accepted.
- **State serialization beyond events.jsonl.** No separate `pause_state.json`.
  The recording IS the state. Resume reconstructs the message list from
  events. Keeps one source of truth.
- **Pausing the LLM mid-stream.** No streaming yet (phase 1 spec); not in scope.
- **Pause across processes.** A signal file in the session dir can trigger
  pause if the process is watching. We don't try to attach to a different
  process or kill subprocesses.
- **Workspace snapshotting.** Filesystem side effects from before pause
  remain. If `mkdir foo` ran, `foo` is still there on resume. Forward-only
  filesystem is still the rule.

## 3. Design decisions

### A. Resume creates a NEW session, not extends the old one

The original session ends (with `paused`/`success=false` outcome). Resume
creates a fresh session with `resumed_from: <original_session_id>` in
meta.json. New `session_id`, new `events.jsonl`, new everything.

Rationale: append-only is cleaner. We can chain resumes (resume a resumed
session). We can see "this thread of work" by following the `resumed_from`
chain. And the original session is immutable — no risk of corruption when
resume goes sideways.

### B. Pause flag has two sources: file + in-process

Plugin holds an in-memory `_flag: bool`. `pause_check`:
- Returns immediately unless flag is set OR signal file exists
- If signal file exists: removes it, raises PauseRequested
- If flag is True: clears it, raises PauseRequested

Programmatic API: `request_pause()` sets the flag (used by TUI keybindings,
SIGINT handler).

This decouples the plugin from how pause was triggered. The plugin is
just a check; callers can use whatever mechanism they like.

### C. SIGINT during agent run → pause; SIGINT during prompt → exit

prompt_toolkit intercepts Ctrl+C as a key while it's reading input
(raises KeyboardInterrupt that's caught in TUIApp.run's try/except → exit).

While `run_turn()` is running, prompt_toolkit isn't reading keys, so a
SIGINT goes through Python's signal handler. We install a handler that
calls `pause_plugin.request_pause()`. Next pause_check raises, loop ends
cleanly.

This gives humans the natural "Ctrl+C to interrupt" experience without
killing the process.

### D. Resume continues the conversation, not the turn

The model sees the recorded conversation history (user, assistant, tool
messages). It's already trained to continue a conversation — no special
"you were paused, please continue" prompt needed unless the user provides
one.

If `arc resume <id> --prompt "..."` is given, that's the next user turn.
Otherwise: drop into interactive TUI mode for further turns.

### E. Reconstruction is from events.jsonl, not a separate state file

The replay loader already extracts user_inputs, llm_responses, and tool
outputs. Resume adds an extraction that interleaves them into a `Message`
list. Single source of truth (events.jsonl); one walk over events
produces both replay state and resume state.

## 4. Architecture

```
Trigger paths:
  external script:  touch <session_dir>/pause
  TUI keybinding:   plugin.request_pause()  via SIGINT handler

PauseResumePlugin (registered on pause_check, on_session_start):
  on_session_start → resolve signal file path under session dir
  pause_check      → if flag OR file: clear, raise PauseRequested

AgentSession loop:
  while True:
    pause_check fires → may raise
    ...iteration body...
  except PauseRequested:
    error_msg = "paused"
    (turn.ended event emitted with success=False, error="paused")
  finally:
    session.ended event emitted, meta.json updated

arc resume <session_id>:
  - load original session's events.jsonl + meta.json + config.snapshot.yml
  - reconstruct message list from events
  - build a new AgentSession with initial_messages=<reconstructed>
  - mark new session meta with resumed_from: <original>
  - if --prompt provided: run_turn(prompt) and exit
  - else: hand off to TUI for interactive continuation
```

## 5. New files

```
src/arc/
  plugins/
    pause_resume/
      __init__.py
      plugin.py             # PauseResumePlugin
  resume/
    __init__.py
    reconstruct.py          # build Message list from events
```

Updates:
- `arc/runtime/loop.py`     — `AgentSession.initial_messages` parameter
- `arc/cli.py`              — `arc resume <id>` subcommand
- `arc/tui/app.py`          — SIGINT handler that calls pause plugin
- `arc/plugins/__init__.py` — register `pause-resume` builder
- `arc/defaults.py`         — enable pause-resume plugin

## 6. CLI changes

New subcommand:

```
arc resume <session_id>                          # interactive continuation
arc resume <session_id> --prompt "..."           # one-shot next turn
arc resume <session_id> --no-tui                 # headless even without prompt
```

`<session_id>` doesn't need to be paused — resume works on any session
(useful for "continue this conversation from yesterday"). If the session
wasn't paused, we just continue the conversation.

## 7. Acceptance test

**Scenario:**
1. Bootstrap, start a multi-step bash workflow that has at least 2 iterations
2. Touch `<session_dir>/pause` before iteration 2's `pause_check`
3. Assert session ended with `error: "paused"` and `success: false`
4. Assert `turn.ended` + `session.ended` events present
5. Call `arc resume <session_id> --prompt "What were you doing?"`
6. Assert new session has `resumed_from` in meta
7. Assert new session's first `llm.call.started` has the FULL prior
   conversation (user, assistant, tool) before the new user turn
8. Assert agent's response acknowledges the prior context

The hardest part is timing: triggering pause between iterations is racy
unless we use a deterministic mechanism. Solution: a custom test plugin
that triggers pause from inside the loop (e.g., on the 2nd `pause_check`).
Test pause via a hook plugin rather than wall-clock timing.

## 8. Open questions

1. **What if the user pauses during the FIRST iteration?** The conversation
   has the user message but no assistant response yet. Resume gives the
   model the same user message — it'll just re-do what it was about to
   do. That's fine; not really a "resume" but a "retry" from the model's POV.

2. **What if resume is called on a non-paused (completed-normally) session?**
   We allow it. The conversation history is intact; the model continues.
   This becomes the "continue a previous conversation" feature for free.

3. **What if the recorded session used tools that aren't enabled in the
   current config?** Resume's new session uses the CURRENT config. If a
   tool is gone, the LLM sees the prior tool calls in history but can't
   call them anymore. Acceptable; the user can re-enable tools before
   resuming.

## 9. Implementation notes

### 9.1 What landed

| Task | File(s) | Status |
|------|---------|--------|
| #75 PauseResumePlugin | `arc/plugins/pause_resume/plugin.py` | ✅ |
| #76 AgentSession.initial_messages | `arc/runtime/loop.py` | ✅ |
| #77 Message reconstruction | `arc/resume/reconstruct.py` | ✅ |
| #78 `arc resume` CLI | `arc/cli.py` (`_cmd_resume`) | ✅ |
| #79 TUI SIGINT handler | `arc/tui/app.py` | ✅ |
| #80 Acceptance test | `tests/integration/test_pause_resume.py` | ✅ |

**Test coverage:** 17 unit tests + 5 acceptance tests against real Gemini.
**230 tests total, all green.**

### 9.2 Bug caught: meta.json race with the recorder

The first acceptance test failed because the resume CLI was writing
`resumed_from` to meta.json AFTER `sess.start()` but BEFORE `sess.end()`.
The JSONL recorder's `on_session_end` then overwrote meta.json from its
own internal dict, clobbering the resume marker.

**Fix:** write the resume metadata AFTER `sess.end()` returns. The
recorder is done at that point and our additions stick.

Lesson worth keeping: any plugin that writes to a file the runtime might
also write to needs explicit ordering. The recorder owns meta.json
during the session; resume's marker is appended after the recorder has
finished its final write.

### 9.3 SIGINT integration is minimal but works

The TUI's `_install_pause_on_sigint` finds the pause-resume plugin in the
hook registry, installs a SIGINT handler that calls `request_pause()`,
and restores the previous handler on exit. During prompt input, the
handler doesn't fire (prompt_toolkit intercepts Ctrl+C as a key). During
agent turns, Ctrl+C → flag set → next pause_check raises → loop ends
cleanly with `error="paused"`.

The find-plugin lookup walks `registry._chains` — that's reaching into
internals. Acceptable for now (the registry is part of the runtime, not
an external API), but if we ever want plugins to expose themselves more
formally, we'd add a `registry.find_by_name(name)` method.

### 9.4 Resume reuses replay's machinery, deliberately

`arc/resume/reconstruct.py` is a sibling to `arc/replay/`. Both load
events.jsonl, both walk events to build typed structures. The split is:

- `replay/loader.py` extracts queues and lookup tables for stubs
- `resume/reconstruct.py` extracts the conversation as a `Message[]`

They could be merged into one module, but keeping them separate makes
the intent obvious — replay's data is for stubbing, resume's data is for
seeding a new session.

### 9.5 Operational state

| Thing | State |
|-------|-------|
| External-trigger pause | works (`touch <session_dir>/pause`) |
| Programmatic pause | works (`plugin.request_pause()`) |
| Ctrl+C during interactive turn | pauses gracefully |
| Recording survives pause | yes — turn.ended + session.ended both fire |
| `arc resume <id> --prompt` | works, model sees prior conversation |
| `arc resume <id>` (interactive) | drops into TUI with prior conversation loaded |
| Resume from a NON-paused session | also works — doubles as "continue from yesterday" |
| Meta has `resumed_from` chain | yes, can chain resumes |

### 9.6 What's intentionally absent

- **Mid-iteration pause.** Pause waits for current LLM/tool call to finish.
  For 30s LLM calls, Ctrl+C has up to 30s of lag.
- **Workspace state preservation.** If the agent created files before
  pause, they're still there on resume. Forward-only filesystem.
- **Pause-resume of replay sessions.** Pausing a `arc replay` run isn't
  tested. Should work mechanically (same plugin) but the meaning is
  weird — replay is deterministic so why pause it.

## 10. Lessons for future phases

1. **The pause_check hook from phase 0 was the right primitive.** Adding
   pause/resume in this phase needed zero changes to the hook catalog —
   just a plugin that uses it. Whenever something "should be a primitive,"
   put it in the hook catalog early so future phases get it for free.

2. **The recording IS the source of truth for state.** Resume could have
   used a separate `pause_state.json` but didn't need to — every fact it
   restores comes from events.jsonl. This keeps the invariant that
   *the recording is sufficient to reproduce the session*. Replay,
   resume, and any future state-restoration feature should follow the
   same rule.

3. **Meta.json writes need ordering.** The recorder owns meta during the
   session. External writes happen before start OR after end. We learned
   this the hard way; documenting it here so the next plugin author
   doesn't.

4. **Reaching into `registry._chains` for plugin lookup is a smell.**
   It's fine for now, but if more code needs it we should add a public
   `registry.find_plugin_by_name()` method. Tracked as a small follow-up
   for phase 2.2.

## 11. What's next

Per the design timeline: **v2.2 — branch + agent-rerun modes 4 + 5**.

- **Mode 4 (branch)**: fork a session at event N, take a different path
- **Mode 5 (live tools + live LLM)**: basically `arc rerun` — replay
  user inputs but live-execute everything else

After v2.2, the foundation is structurally complete (modes 1-5 all
working). Subsequent phases would add capabilities: skills, planner,
context manager, sub-agents — each as a plugin that can be turned off.

