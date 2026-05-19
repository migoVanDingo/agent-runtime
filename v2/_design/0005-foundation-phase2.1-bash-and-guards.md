# 0005 — Phase 2.1: bash_exec + Guard plugin

**Status:** complete
**Phase:** 2.1
**Implements:** the "bash + guards" milestone in `0001-foundation-phase0-design.md` §9 timeline

## 1. Goals

1. **`bash_exec` tool** — subprocess-backed shell execution with timeout +
   output truncation + working-dir control. All knobs from config.
2. **Guard plugin** — `before_tool_call` hook with three policy buckets:
   - `allowlist_tools`: bypass all checks (e.g., `ls`, `echo`, `cat`)
   - `blocklist_patterns`: regex against the command — hard deny
   - `escalation_required_patterns`: regex — prompt the user (auto-deny
     when no interactive gate is wired)
3. **UserGate abstraction** — Protocol + `NoOpGate` (headless) +
   `TUIGate` (interactive). Plumbed into plugins via `PluginBuildContext`.
4. **Acceptance test**: "create a directory, write a haiku, summarize it"
   — proves multi-step bash workflows work end-to-end.

## 2. Non-goals

- **No sandboxing.** Host backend only. The guard is the only safety layer
  in phase 2.1. Sandbox via `sandbox-exec` / containerization is a future
  plugin, not a phase-2.1 commitment.
- **No bash session state.** Each `bash_exec` call is its own subprocess.
  No persistent shell, no env carryover between calls. (Working dir IS
  configurable per call.)
- **No streaming output.** Output is captured to completion or timeout.
- **No interactive subprocesses.** stdin is closed.

## 3. Design decisions

### A. Guard scope: command-string vs. tool-level policy

The guard inspects `tool_input["command"]` when present. For any tool
without a `command` field (i.e., everything but `bash_exec` for now), the
guard's pattern checks are skipped — the tool's place in `allowlist_tools`
is the only signal.

Rationale: in phase 2.1 the only "command-string" tool is `bash_exec`. As
we add more (e.g., `python_exec`, `git_exec`), they'd follow the same
shape. Tools with structured input (e.g., `delete_file`) would need
different policy and a separate hook later.

### B. Escalation gate is an injected dependency, not a global

`PluginBuildContext` gains a `user_gate: UserGate | None`. The guard
plugin uses it for escalation prompts. Different callers pass different
gates:

- `arc` (interactive) → `TUIGate` (prompts via prompt_toolkit)
- `arc run` (headless) → `NoOpGate` (auto-denies, logs reason)
- Tests → fakes

This keeps the guard plugin testable and avoids hidden global state.

### C. Failure mode: deny by default

If the gate fails or returns ambiguous answer, the guard denies. Safer to
block a legitimate command and surface the deny than to silently run
something dangerous.

### D. Bash semantics

- Runs via `subprocess.run(cmd, shell=True, ...)` — accepts shell
  metacharacters, pipes, redirects, heredocs.
- stdin closed (`stdin=subprocess.DEVNULL`).
- Captures stdout + stderr separately, joins for output.
- Timeout from config; raises after timeout, output up to that point
  is included with `Error: command timed out after Xs` appended.
- Output truncation: per-call limit from config; truncated output
  ends with `[truncated; original was N chars]`.
- Working directory: per-call `cwd` arg overrides config default, which
  falls back to `runtime.workspace`.

## 4. CLI changes

- No new subcommands. `arc run` and `arc` (interactive) both gain the
  guard automatically once it's enabled in default config.
- `arc bootstrap` writes a config that has `guard.enabled: true` and
  includes `bash_exec` in `tools.enabled`.

## 5. New files

```
src/arc/
  tools/
    bash_exec.py              # the tool
  plugins/
    guard/
      __init__.py             # re-export
      plugin.py               # GuardPlugin
  user_gate.py                # UserGate Protocol + NoOpGate + TUIGate
```

Plus updates to:
- `defaults.py` — enable guard, add `bash_exec` to `tools.enabled`
- `tools/__init__.py` — register `bash_exec` builder
- `plugins/__init__.py` — register `guard` builder, add `user_gate` to
  `PluginBuildContext`
- `cli.py` — construct gate (TUI vs NoOp), pass to plugin factory
- `tui/app.py` — construct `TUIGate` from its own console

## 6. Acceptance test

**Scenario:** "Create a directory `/tmp/<ws>/poems`, write a haiku about
coding to `poems/code.haiku`, then read it back and summarize in one
sentence."

**Expected behavior:**
1. Agent calls `bash_exec mkdir -p <path>` → allowed
2. Agent calls `bash_exec echo "..." > <path>/code.haiku` or `cat << EOF`
   → allowed
3. Agent calls `bash_exec cat <path>/code.haiku` → allowed (or in allowlist)
4. Agent emits prose summary

**Assertions:**
- All three bash calls succeed (exit 0)
- The poems file actually exists on disk after the run
- The final response is non-empty (the summary)
- No `tool.call.denied` events fired

**Tampering tests:** also assert the guard works:
- Send a prompt that would trip a blocklist pattern → `tool.call.denied`
  fires, agent doesn't crash
- Send a prompt that needs escalation in NoOpGate mode → denied

## 7. Open questions to resolve as we go

1. **What if the LLM tries `rm -rf /tmp/<ws>/poems` to clean up after?**
   The default blocklist pattern is `rm\s+-rf` which matches it. Decide:
   tighten to `rm\s+-rf\s+/` (only block deleting root-ish things) or
   keep broad. I'll start with broad — safer; if the test trips, narrow.
2. **Workspace-relative defaults.** Should `bash_exec` cwd default to
   `runtime.workspace` or the user's actual cwd? Going with
   `runtime.workspace` so the agent's bash calls are scoped.
3. **Output limits per call.** 50,000 chars from defaults is generous.
   Logged events include the full output; the LLM sees the truncated
   version. Should the recording also be truncated? No — for replay we
   need the full bytes the tool actually produced.

## 8. Implementation notes

### 8.1 What landed

| Task | File(s) | Status |
|------|---------|--------|
| #69 bash_exec tool | `arc/tools/bash_exec.py` | ✅ |
| #70 UserGate abstraction | `arc/user_gate.py` (Protocol + NoOpGate + TUIGate) | ✅ |
| #71 Guard plugin | `arc/plugins/guard/plugin.py` | ✅ |
| #72 CLI + TUI wiring | `arc/cli.py`, `arc/plugins/__init__.py`, `arc/defaults.py` | ✅ |
| #73 Acceptance test | `tests/integration/test_poem_workflow.py` | ✅ |

**Test coverage:** 22 bash_exec unit tests + 14 guard unit tests + 5 poem-workflow
acceptance tests against real Gemini. **208 total tests, all green.**

### 8.2 Bug caught during implementation

**The replay-vs-defaults divergence.** Enabling `bash_exec` in default config
made the recorded `llm.call.started` events list two tools instead of one.
Replay then diverged on hello-world recordings because the replay tool
registry built stubs from `tool_outputs_in_order` (only CALLED tools), not
from `tool_specs` (all OFFERED tools).

**Fix:** the replay registry now seeds its tool set from `tool_specs.keys()`,
which captures every tool the LLM was offered (regardless of whether it
was actually called). Bug also revealed a sub-bug — `sorted(names)` was
destroying the original tool order, so even with the right set the order
differed. Fixed by using an insertion-ordered dict instead of a set.

Lesson: **the runtime emits whatever it has, every time.** Anything the
runtime can introspect about itself (tool registry, hook chain, etc.) must
be reproducible by replay down to insertion order.

### 8.3 The "agent did the whole task in one bash call" lesson

The poem-workflow acceptance test originally asserted "at least 3 bash_exec
calls" (mkdir + write + read). The model instead chained `mkdir && echo ... && cat`
into a single call. Test was wrong — the assertion was about my expectation
of model behavior, not about correctness. Relaxed to "at least 1 call AND
the file ends up on disk."

Worth keeping in mind: acceptance tests should assert on outcomes (side
effects, final state) not on the number/shape of LLM choices. The model
is allowed to be efficient.

### 8.4 What works end-to-end (verified)

```bash
arc run "Create /tmp/foo, write a haiku, summarize it"
# → bash_exec mkdir + echo + cat all approved by guard
# → file exists on disk, summary returned

arc run "Use bash_exec to run: rm -rf /tmp/junk"
# → tool.call.denied event fires
# → "blocked pattern" reason in the denial
# → /tmp/junk untouched

arc run "Use bash_exec to run: curl https://example.com -o /tmp/page.html"
# → headless mode → NoOpGate auto-denies
# → "escalation denied" message logged to stderr
# → file not created

arc        # interactive mode
# → TUIGate is wired
# → escalation prompts appear inline with the rendered conversation
```

### 8.5 Operational state

| Thing | State |
|-------|-------|
| `arc run` with `bash_exec` | works, guard-protected |
| `arc` interactive with `bash_exec` | works, TUIGate prompts on escalation |
| Blocklist patterns | enforced (rm -rf, dd, mkfs, etc.) |
| Escalation patterns | enforced (curl, wget, sudo, ssh, etc.) |
| Replay still works with bash_exec | yes (fix in §8.2) |

### 8.6 What's intentionally out of scope

- **Sandboxing.** Host backend only. If you want isolation, run arc inside
  a container yourself. A sandbox plugin (sandbox-exec/firejail/container)
  is a future addition.
- **Persistent shell state.** Each `bash_exec` is its own subprocess.
  `cd` doesn't persist between calls; use `cwd` per call or chain with `&&`.
- **Streaming output.** Output captured to completion. No live tail.
- **Interactive subprocesses.** stdin is `DEVNULL`. Tools like `vim`,
  `less`, anything that waits on stdin → will fail.

## 9. Lessons for future phases

1. **Default-on plugins change recordings.** Anytime we add a plugin that
   participates in event emission (and the guard, while not in `on_event`,
   shows up in tool-call events via denials), older recordings can become
   incompatible with newer runtime defaults. Replay caught this. Replay
   will keep catching this. That's its job.

2. **The `tool_specs` extraction in the loader is load-bearing.** It's
   what makes the replay registry mirror the recording exactly. When we
   add more event types that reference runtime introspection (e.g.,
   active-plugin lists), the loader will need parallel extractions.

3. **Acceptance tests should assert on outcomes, not on LLM choices.**
   The model is free to be more efficient than our test expects. Lock
   in correctness (side effects, final state, no errors), not procedure.

4. **The plugin factory's `PluginBuildContext` is the right place to
   thread cross-cutting dependencies.** Adding `user_gate` was painless.
   If a future plugin needs (say) a metrics emitter or a network client,
   it goes here too — without polluting plugin manifests or config.

## 10. What's next

Per the design timeline in `0001` §9: **v2.1.5 — pause + resume**. The
`pause_check` hook gets a real implementation (signal handler? watch file?
TUI keybinding?), and time-travel becomes possible mid-turn.

After that: **v2.2 — branch + agent-rerun modes 4 + 5** (branch from
event N, full live mode for `arc replay`).

