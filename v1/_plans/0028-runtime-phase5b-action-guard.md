# 0027b — Runtime Infrastructure Phase 5.5: Action Guard

## What

Pre-execution safety gate that inspects steps and tool calls *before*
they execute, blocking destructive actions and escalating risky ones.
Code-only, no LLM calls. This is the "stop time" layer — we catch
dangerous actions before they happen, not after.

## Why

The ExecutionMonitor (phase 5) only fires after a step completes. If the
planner produces "delete all files in /tmp" or the model calls
`bash_exec({"command": "rm -rf /"})`, the monitor sees it too late.

The guard sits in two places:

1. **Step level** — before `_run_step` begins, inspects the step
   description for destructive language
2. **Tool call level** — inside `_run_step` and `_run_loop`, between the
   model requesting a tool call and the tool actually executing

## Decisions

| Decision | Behavior |
|----------|----------|
| ALLOW | Proceed with execution |
| BLOCK | Do not execute. Return a safe refusal message as the tool result. The model sees the refusal and can adjust. |
| ESCALATE | (Future) Ask user for approval. Currently returns a "requires approval" message as the tool result. |

## What the guard catches

### Step-level (`check_step`)
- Descriptions containing destructive language: "delete all", "remove
  all", "wipe", "destroy", "purge" → ESCALATE

### Tool-call-level (`check_tool_call`)

**bash_exec** (highest risk surface):
- Dangerous command patterns → BLOCK:
  `rm -rf`, `rm -r`, `dd`, `mkfs`, `format`, `kill -9`, `killall`,
  `pkill`, `shutdown`, `reboot`, `halt`, `chmod 777`, `chmod -R`,
  `chown -R`, `> /dev/`, `curl|sh`, `wget|sh`
- `sudo` usage → ESCALATE
- Write commands (`tee`, `mv`, `cp`, `sed -i`, `chmod`, `chown`)
  targeting sensitive paths → ESCALATE

**delete_file** → always ESCALATE (any path)

**write_file** → ESCALATE if targeting sensitive paths
(`/etc`, `/usr`, `/var`, `~/.ssh`, `~/.env`, etc.)

**move_file** → ESCALATE if source or dest is sensitive

**strace/ltrace** → ESCALATE if attaching to a pid

Everything else → ALLOW

## Changes

### New files

- **`src/runtime/guard.py`** — `ActionGuard` class + `GuardDecision` enum:
  - `check_step(description, action_type) -> GuardDecision`
  - `check_tool_call(tool_name, tool_input) -> (GuardDecision, reason)`
  - `_check_shell_command(command) -> (GuardDecision, reason)`
  - Regex patterns for dangerous commands and sensitive paths

### Modified files

- **`src/agent.py`**:
  - `__init__`: creates `self.guard = ActionGuard()`
  - `_execute_plan()`: step-level guard check before `_run_step`. If
    BLOCK or ESCALATE, the step gets a refusal string as its result
    instead of executing. The monitor then assesses that result normally
    (and may decide to SKIP or REPLAN).
  - `_run_step()`: tool-call-level guard check before every
    `tool.execute()`. If BLOCK/ESCALATE, a refusal string is returned
    as the tool result. The model sees "Tool call blocked: reason" and
    can attempt a different approach.
  - `_run_loop()`: same tool-call-level guard for direct execution path.

- **`_plans/0022-runtime-infrastructure-design.md`**: updated flow diagram,
  module structure, and phases table to include ActionGuard.

## How BLOCK/ESCALATE flow through the system

When a tool call is blocked, the model receives the block message as the
tool result. This is important — the model can react:

```
Model: bash_exec({"command": "rm -rf /tmp/old"})
Guard: BLOCK — dangerous command pattern 'rm -rf'
Tool result returned to model: "Tool call blocked by safety policy: dangerous command pattern: 'rm -rf'"
Model: (adjusts approach) "I was unable to delete the files..."
```

The post-execution monitor then sees the step result and can decide
RETRY/REPLAN/CONTINUE based on whether the blocked action was critical
to the task.

## What does not change

- Tool implementations — unchanged
- ToolRegistry — unchanged
- ExecutionMonitor — unchanged (sees guard refusals as step results)
- No config changes needed (guard is always active — safety isn't optional)
