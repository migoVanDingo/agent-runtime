# 07 — sub-agent policy enforcement (child guard + docker block)

**Mitigates:** `02-security-audit.md` H1 (sub-agents run tools unguarded) and H2
(`container_expert` has a raw host shell). Path "A" of the 2026-07-05 pass. This
is the finding that most undermined the guard/delegate enforcement model.

## The problem
A sub-agent's child `AgentSession` was built with `plugins.enabled=[]`, so
`guard`/`safety_gate` **never fired inside a sub-agent**. Meanwhile the guard's
`delegate_only_tools` deliberately routes dangerous tools *into* sub-agents, and
`container_expert` carries `bash_exec` (a raw host shell) — so delegating a
capability *removed* the safety layer, and the child could `docker run
--privileged`, `rm -rf`, etc. unguarded.

## The design decision
Child sessions now get a **hard-denylist guard built from the parent's guard
config** (`runner._child_policy_guard`), registered on `before_tool_call`.
Deliberately scoped:

| Inherited into child | Why |
|---|---|
| **blocklist_patterns** (rm -rf, dd, mkfs, fork bomb, block-device writes, **docker**) | the "never do this" set — safe to enforce headless |
| ~~escalation patterns (curl/wget)~~ | would prompt the parent's interactive gate **from the child's dispatch thread** (hangs/corrupts the TUI); and the sub-agent legitimately needs `curl` for health checks |
| ~~safety_gate~~ | its confirm-or-deny model can't run headless — a NoOp gate would auto-deny the `>` redirect the sub-agent uses to write Dockerfiles |
| ~~delegate_only_tools~~ | that rule is parent-only (`inside_subagent()`-gated); moot inside the child |

So the child guard = **blocklist only, empty escalation, NoOp gate**. Hard denies
are enforced inside the sub-agent; `curl` / file writes / cos tools stay usable.

**H2 companion — docker block.** Added `\bdocker(-compose)?\b` to the guard
blocklist (`defaults.py` + the live `.arc/config.yml`). Because the child now
inherits the blocklist, this blocks raw `docker` in `bash_exec` **inside the
sub-agent too** — closing the "shell out to docker instead of using cos" escape
in both parent and child. (`Dockerfile`/`dockerfile` don't match — no word
boundary; writing build contexts still works.)

## Verification
- New `tests/unit/test_subagent_runner.py::test_child_inherits_guard_and_denies_blocked_command`
  — a child whose model calls `sh(command="rm -rf /tmp/x")` has the call
  **denied** (the fake tool's `.executed` stays False), dispatch still returns ok.
- Full v2 unit suite: **770 passed**.

## Implementation notes / issues hit
- **First attempt reused the parent guard *instance*.** Rejected: (a) the guard's
  `on_event` would clobber its `delegate` tool-list state with the child's
  session.started list, breaking the parent's delegate rule after a dispatch;
  (b) it dragged the escalation patterns + interactive gate into the child
  thread. Building a fresh blocklist-only guard from `parent_config` avoids all
  of it. (The `parent_registry` plumbing added mid-refactor was reverted.)

## Residual (still open — the bigger H2 fix)
- `bash_exec` remains a **general host shell** in `container_expert`: it can read
  host files, write to disk, and run any non-blocklisted command, against a
  provider that sees the output. The catastrophic commands are blocked, but this
  is **not a sandbox**. The full fix — replace `bash_exec` with a temp-dir-scoped
  write tool + a dedicated curl tool — is deferred (new tooling).
- The child guard is blocklist-only by design; destructive-but-not-catastrophic
  ops that would normally hit `safety_gate` are not confirmed inside a sub-agent.
