# 0009 — Dynamic Tool Loading: Design

## Overview

Organize tools into named toolsets and route the right subset to the model on each request.
Reduces token usage, improves small model reliability, and scales cleanly as the tool library grows.

One routing strategy for all providers:
- **Static routing** — agent code inspects the user message and conversation state, selects toolsets in Python
- No model round trip required, deterministic, works on all providers including small local models
- Dynamic routing (model picks its own toolsets) deferred — revisit if static routing proves insufficient

---

## Motivation

28 tools sent on every request causes two problems:
1. **Token waste** — full schemas bloat the context on every call
2. **Small model degradation** — models like Mistral 7B struggle to reason about tool selection
   when given too many choices, and often fall back to plain text responses

With Gemma 4's native function calling support, this problem shrinks but doesn't disappear.
Toolset routing is still the right architecture at scale.

---

## Phases

### Phase 1 — Toolset Model
**File:** `src/tools/toolset.py`

Define a `Toolset` dataclass:
- `name: str` — identifier (e.g. `"analysis"`)
- `description: str` — what kind of tasks this toolset handles
- `tools: list[BaseTool]`

No registry changes yet — just the data model.

---

### Phase 2 — Registry Updates
**File:** `src/tools/registry.py`

Add toolset-aware methods alongside existing tool registration:
- `register_toolset(toolset: Toolset)` — registers the toolset and indexes its tools by name
- `get_toolset_tools(name: str) -> list[BaseTool]`
- `get_toolset_schema(names: list[str]) -> list[dict]` — union of schemas for a list of toolset names
- `toolset_names() -> list[str]` — for inspection/logging

Existing `register()` and `get()` methods unchanged — individual tool registration still works.

---

### Phase 3 — Static Router
**File:** `src/routing/static_router.py`, `src/routing/__init__.py`

Inspects the user message and conversation history, returns a list of toolset names.
Runs in Python — no model call, no latency.

**Routing rules:**

| Signal | Toolsets loaded |
|---|---|
| Binary paths, RE keywords (disassemble, binary, analyze, strings, symbols, objdump, elf, nm) | `analysis` |
| Write/save/create/output to file, summarize | `file_io` |
| Hash, encode, decode, xor, encrypt, base64 | `crypto` |
| Run, execute, shell, command, bash | `shell` |
| No tool signals detected | `[]` (no tools — pure conversation turn) |
| Fallback (ambiguous but not clearly conversational) | `file_io` + `shell` |

Multiple toolsets can be returned — a task like "analyze /bin/ls and write a summary" returns
`analysis` + `file_io`.

**Per-turn re-routing:**
The router runs on every iteration of the agent loop, not just once at `call()` entry.
It receives the full conversation history so it can adjust toolsets as the task evolves.
Example: first turn loads `analysis`, second turn (writing the result) loads `file_io`.

---

### Phase 4 — Toolset Definitions
**File:** `src/tools/toolsets.py`

Define and export the default toolsets. Called once during agent init.

| Toolset | Tools |
|---|---|
| `file_io` | read_file, write_file, read_file_lines, list_files, walk_directory, search_files, copy_file, move_file, delete_file, make_directory, download_file, get_working_directory, environment_info |
| `shell` | bash_exec |
| `analysis` | strings, objdump, file_info, hexdump, nm, ltrace, strace, readelf, checksec, grep_binary |
| `crypto` | hash_file, base64_encode, base64_decode, xor_decode |

---

### Phase 5 — Wire into Agent
**File:** `src/agent.py`

- Agent initializes toolsets (via `toolsets.py`) instead of registering tools individually
- On each loop iteration, router inspects current message + conversation history → returns toolset names
- Agent calls `registry.get_toolset_schema(names)` to get the filtered tool list for that iteration
- If router returns `[]`, agent sends request with no tools (pure conversation)
- Tool dispatch via `registry.get(name)` unchanged

---

## Routing Logic (per turn)

```
loop iteration N:
  1. router.select(current_message, conversation_history) → [toolset_names]
  2. registry.get_toolset_schema(toolset_names) → [tool_schemas]
  3. provider.chat(messages, tools=tool_schemas, system=SYSTEM_PROMPT)
  4. handle response (end_turn or tool_use)
  5. if tool_use → execute tools, add results, go to iteration N+1
```

---

## Dependency Flow (post-implementation)

```
agent.py
  └── StaticRouter
        └── keyword/pattern matching on message + history → toolset names

  └── ToolRegistry
        └── Toolsets → filtered schemas per iteration

src/tools/toolset.py     ← Toolset dataclass
src/tools/toolsets.py    ← default toolset definitions
src/routing/static_router.py ← routing logic
```

---

## What Does Not Change

- `BaseTool` and all tool implementations
- `BaseProvider` interface
- `Messenger`
- `main.py`

---

## Deferred

- **Dynamic routing** — model picks its own toolsets from a menu. Useful for genuinely ambiguous
  tasks. Revisit after static routing is validated in practice.
- **Per-session toolset config** — user specifies active toolsets via CLI flag or settings
- **Custom toolsets** — e.g. a `ctf` toolset, a `code_review` toolset
- **Toolset overlap cap** — warn or limit when multiple toolsets together exceed a token threshold
- **Planning phase integration** — when a planning loop is added, routing logic moves there

