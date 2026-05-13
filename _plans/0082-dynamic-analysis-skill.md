# 0082 — Dynamic Analysis Skill (LLDB + Ghidra)

## Problem

Static decompilation (Ghidra) shows structure — inferred code that is often noisy, hard to
consume in large chunks, and prone to 429 token-limit errors when fed to the LLM all at once.

For the specific goal of reconstructing a binary's behavior in code, what matters is not the
structure but the **behavior**: exact register values, exact data transformations with real inputs.

Dynamic analysis gives you that directly. With the right breakpoints, a two-input differential
trace tells you exactly what the cipher does — no decompile ambiguity, no byte-order guessing,
no 429s (register dumps are ~200 bytes each).

The right architecture is **Ghidra for WHERE, LLDB for WHAT**:
- Ghidra identifies functions, their addresses, and their rough shape (one function at a time)
- LLDB runs the binary with known inputs, captures register state at those addresses
- The agent synthesizes code from observed transformations, not from inferred pseudocode

---

## Architecture

```
dynamic-analysis skill
        │
        ├── Phase 1 (structure): ghidra_analyze, ghidra_functions
        │       → identifies candidate function(s) and addresses
        │
        ├── Phase 2 (one-function decompile): ghidra_decompile --function <name>
        │       → small targeted decompile (500-2000 chars), not all functions
        │       → paged to _analysis/<binary>/ghidra_decompile_<fn>.txt
        │       → tells agent WHERE to set breakpoints (loop entry, round boundary, exit)
        │
        ├── Phase 3 (initial trace): lldb_trace
        │       → run binary with oracle input, capture registers at key breakpoints
        │       → what goes in, what comes out, what intermediate values look like
        │
        ├── Phase 4 (differential trace): lldb_trace with second input
        │       → compare register states: what changes = input-dependent
        │       → what stays the same = key / constant
        │       → pinpoints key schedule, round count, delta value
        │
        ├── Phase 5 (step trace): lldb_step
        │       → walk through the inner loop a few instructions at a time
        │       → confirm exact operation: add vs xor, shift direction, key word selection
        │
        └── Phase 6 (synthesis): CONVERSATION or write_file
                → agent writes code from observed transformations
                → goal-aware: code if reconstructing, prose if reporting
```

---

## New Tools

### `lldb_trace`

Runs a binary under LLDB with specified breakpoints and captures register state at each hit.
Non-interactive — generates a command script, executes it, parses structured output.

**Input schema:**
```python
{
  "path":        str,           # binary path
  "args":        list[str],     # argv[1..] passed to the binary
  "breakpoints": list[str],     # addresses ("0x100000a1e") or symbol names ("entry")
  "registers":   list[str],     # which registers to capture (default: rax,rdi,rsi,rdx,rcx,r8-r15)
  "memory":      list[{         # optional memory regions to dump at each breakpoint
                   "expr": str, # LLDB expression for base address (e.g. "$rdi")
                   "size": int  # bytes to read
                 }],
  "max_hits":    int            # max times to capture per breakpoint (default 1)
}
```

**Output:**
```
[breakpoint 0x100000a1e — hit 1]
rax=0x0000000000000000  rdi=0x30c55b7f  rsi=0x622cbb6b
rcx=0x72636573          rdx=0x65737465  r8=0x74656372  r9=0x72636573
memory @ rdi: 7f 5b c5 30 6b bb 2c 62

[breakpoint 0x100000b40 — hit 1]
rax=0x5a8e6d2d  rdi=0x5626d2f4  ...
```

Plain text, small, no paging needed (always under 4k chars for typical traces).

**Implementation:** `src/tools/implementations/reversing/lldb_trace.py`
- Generates an LLDB Python command script
- Sets `target.disable-aslr true` so runtime addresses match Ghidra addresses exactly
- Runs `lldb <binary> -S <script> -- <args>` via subprocess
- Parses output: strips LLDB preamble, extracts register lines, formats cleanly
- Returns formatted string; weight = MODERATE

**ASLR note:** `target.disable-aslr true` in the LLDB script makes all addresses match Ghidra
exactly. Without this, the agent must account for the load slide, which adds complexity.

---

### `lldb_step`

Starts execution at a given address (or runs to it), then steps N instructions, capturing
register state after each step. Used for the fine-grained "walk through the round function"
phase.

**Input schema:**
```python
{
  "path":       str,
  "args":       list[str],
  "start":      str,      # address or symbol to run to first
  "steps":      int,      # number of instructions to step (default 20)
  "registers":  list[str]
}
```

**Output:** one register snapshot per step, labelled by instruction address and mnemonic.

```
step  1 @ 0x100000610  addl  (%rsp), %eax      rax=0x30c55b7f  rsi=0x622cbb6b  ...
step  2 @ 0x100000613  movl  %eax, (%rsp)      rax=0x1a3f2c90  ...
...
```

**Implementation:** `src/tools/implementations/reversing/lldb_step.py`
- Same LLDB script approach as `lldb_trace`
- Uses `thread step-inst` (stepi) N times, dumps registers after each

---

## New Skill: `dynamic-analysis`

### Intent

```python
intent = (
    "Use this skill when the user wants to understand what a binary does at runtime — "
    "trace execution with specific inputs, inspect register values at key points, "
    "or reconstruct source code from observed behavior rather than static decompilation. "
    "Preferred over deep-disassembly when: (a) static decompile was too noisy or incomplete, "
    "(b) the goal is code reconstruction and a known oracle input/output pair exists, "
    "(c) the user wants to verify a hypothesis about the algorithm with concrete runtime data."
)
```

### Step expansion (for a reconstruction goal)

```
Step 1  [analysis]    ghidra_analyze     — build project cache
Step 2  [reversing]   ghidra_functions   — get function list + addresses (tiny output)
Step 3  [conversation]                   — identify which function is the cipher entry
Step 4  [reversing]   ghidra_decompile   — decompile ONLY that function (--function arg)
Step 5  [reversing]   lldb_trace         — run with oracle input, capture at entry + exit
Step 6  [reversing]   lldb_trace         — run with second input, capture same points
Step 7  [conversation]                   — differential: what changed, what stayed, deduce algo
Step 8  [reversing]   lldb_step          — walk through inner loop, confirm exact operations
Step 9  [conversation/file_io]           — synthesize: write code from observed behavior
```

Steps 5-6 are the differential pair. The agent compares the two traces to separate
input-dependent values (plaintext) from constants (key, delta, IV).

Step 4 decompiles ONE function, not all — typically 500-2000 chars, well under paging threshold.
The agent uses it only to find loop entry addresses for breakpoints, not to reconstruct the algo.

### Goal-aware synthesis (step 9)

Same `_infer_synthesis` pattern as `deep-disassembly`:
- If goal is C reconstruction: write code matching observed transformations exactly
- If goal is report: summarize what the register traces revealed about the algorithm
- If goal is a specific output file: write to it

---

## Tool Integration

Add both tools to the `reversing` toolset (existing). No new toolset needed.

```python
# src/tools/toolsets.py — reversing toolset additions
"reversing": [
    ...existing tools...,
    "lldb_trace",
    "lldb_step",
]
```

Add routing hints so the router selects `dynamic` tools when the user says "trace", "step",
"register", "breakpoint", "runtime", "lldb", "gdb", "watch execution":

```yaml
# config.yml routing.toolset_descriptions
reversing: "... lldb trace step register breakpoint runtime dynamic execution watch"
```

---

## Implementation Phases

### Phase A — `lldb_trace` tool

**Files:**
- `src/tools/implementations/reversing/lldb_trace.py`

**Core implementation:**
```python
def execute(self, tool_input: dict) -> str:
    path = tool_input["path"]
    args = tool_input.get("args", [])
    breakpoints = tool_input.get("breakpoints", [])
    registers = tool_input.get("registers", ["rax","rdi","rsi","rdx","rcx","r8","r9","r10","r11","r12","r13","r14","r15"])
    max_hits = tool_input.get("max_hits", 1)

    script = _build_lldb_script(path, args, breakpoints, registers, max_hits)
    result = _run_lldb(path, script, timeout=30)
    return _parse_lldb_output(result)
```

`_build_lldb_script` generates:
```
settings set target.disable-aslr true
target create "<path>"
<for each breakpoint: breakpoint set --address <addr> OR --name <sym>>
run <args>
<at each stop: register read <regs>>
continue
quit
```

**Verification:** `lldb_trace` on `./proc` with args `["-e", "secret", "hello"]` and
breakpoint at `entry` returns register dump showing rdi pointing to the arg string.

---

### Phase B — `lldb_step` tool

**Files:**
- `src/tools/implementations/reversing/lldb_step.py`

Similar to Phase A but uses `thread step-inst` in a loop within the LLDB script.

---

### Phase C — `dynamic-analysis` skill

**Files:**
- `src/skills/implementations/dynamic_analysis.py`

Follows the same `Skill` base class pattern as `DeepDisassembly`. Extracts target binary,
optional oracle pair (input → expected output) from the user message. The oracle pair is
used to verify the reconstruction at step 9.

`_extract_oracle(message)` pattern — looks for:
- `"X gives Y"` or `"input X output Y"` or `"encrypts X to Y"`
- Returns `(input, expected)` tuple for use in the trace steps

---

### Phase D — Toolset wiring + routing

- Add `lldb_trace`, `lldb_step` to `reversing` toolset in `toolsets.py`
- Update `config.yml` routing description for `reversing` with dynamic analysis keywords
- Update guard policy: `lldb_trace` and `lldb_step` require ESCALATE (host execution,
  same as ghidra tools) — add to guard config

---

## What this changes about the reconstruction workflow

**Before (Ghidra-only):**
```
ghidra_decompile (all functions, 11k chars) → paged → agent can't read it →
tries to reconstruct from memory → wrong cipher → iterate forever
```

**After (Ghidra + LLDB):**
```
ghidra_functions (200 chars) → identify cipher function →
ghidra_decompile --function (1k chars) → find loop address →
lldb_trace x2 (400 chars total) → differential tells agent exact values →
lldb_step (500 chars) → confirms exact operation per instruction →
write code (correct first time or very close)
```

Total token cost for the dynamic phase: ~1,200 chars. No 429 risk.
The agent builds code from observed behavior — concrete values, not inferred structure.

---

## What is NOT in scope

- GDB (LLDB is native on macOS; GDB support can be added later as an alternate backend)
- Container-based dynamic analysis (that's plan 0077)
- Anti-debug bypass (the target binary doesn't have anti-debug; if needed, document as extension)
- Windows PE support
