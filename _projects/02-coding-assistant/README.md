# Project 2: Multi-Tool Coding Assistant

## Prerequisites
Complete Project 1. You should understand the ReAct loop, tool schemas, and message history.

## What You Will Build

A coding assistant that can read, write, and modify files in a repository. It can run tests, execute shell commands, and make multi-file edits. It asks for confirmation before doing anything destructive.

This is the first real application layer. It runs on top of the loop you built in Project 1.

## New Concepts

### Safety Gates
Some tool calls are reversible (reading a file). Some are not (deleting a file, running `rm -rf`). Before executing irreversible or high-risk actions, the agent should ask the human.

```
Agent wants to: write_file(path="main.py", content="...")

Safety check:
  - Is this a write? YES
  - Does the file exist? YES → this will overwrite
  - Requires confirmation: "Overwrite main.py? [y/N]"
```

This is a simplified version of what Cruz calls "permission gates for irreversible actions" — we will formalize it in Project 7.

### Structured Tool Design
A coding assistant needs a specific set of tools. Design them carefully — the quality of tool schemas directly affects agent reliability.

| Tool | Risk | Confirmation? |
|------|------|---------------|
| `read_file` | None | No |
| `list_files` | None | No |
| `search_in_files` | None | No |
| `write_file` | Overwrites existing | If file exists |
| `bash_exec` | Anything | Yes, always |
| `git_status` | None | No |
| `git_diff` | None | No |

### Context System Prompt
The agent needs a system prompt that tells it what it is and how to behave:

```python
SYSTEM_PROMPT = """You are a coding assistant with access to the local filesystem.

You help users understand, modify, and improve code. You have tools to:
- Read and list files
- Search within files
- Write or modify files
- Execute shell commands (with user confirmation)
- Check git status and diffs

Before making changes to existing files, read them first.
Before running shell commands, explain what the command will do.
Make small, focused changes. Prefer editing over rewriting.
"""
```

## Architecture

```
┌──────────────────────────────────────────────┐
│              Coding Assistant                │
│                                              │
│  system_prompt = "You are a coding asst..."  │
│  tools = [read_file, list_files, write_file, │
│           bash_exec, search, git_*]          │
│  safety = SafetyGate(policy)                 │
│  loop:                                       │
│    response = call_model(messages, tools)    │
│    for tool_call in response:                │
│      if safety.requires_confirm(tool_call):  │
│        confirm_with_user()                   │
│      result = execute(tool_call)             │
│      messages.append(result)                 │
└──────────────────────────────────────────────┘
```

## Build Guide

### Step 1: Expand the tool set

Add these tools to your Project 1 agent:

**search_in_files** — grep-like search:
```python
def search_in_files(pattern: str, path: str = ".", file_glob: str = "*.py") -> str:
    """Search for a pattern in files. Returns matching lines with file:line context."""
    import glob, fnmatch
    results = []
    for filepath in glob.glob(f"{path}/**/{file_glob}", recursive=True):
        try:
            with open(filepath) as f:
                for i, line in enumerate(f, 1):
                    if pattern.lower() in line.lower():
                        results.append(f"{filepath}:{i}: {line.rstrip()}")
        except Exception:
            pass
    return "\n".join(results[:50]) if results else "No matches found"
```

**write_file:**
```python
def write_file(path: str, content: str) -> str:
    """Write content to a file. Creates parent directories if needed."""
    import os
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    return f"Written {len(content)} bytes to {path}"
```

**git_status** and **git_diff:**
```python
def git_status() -> str:
    return bash_exec("git status --short")

def git_diff(path: str = "") -> str:
    cmd = f"git diff {path}" if path else "git diff"
    return bash_exec(cmd)
```

### Step 2: Build the safety gate

Create a `SafetyGate` class that decides whether a tool call needs confirmation:

```python
class SafetyGate:
    # Tools that always need confirmation
    ALWAYS_CONFIRM = {"bash_exec"}

    # Tools that need confirmation if target exists
    CONFIRM_IF_EXISTS = {"write_file"}

    def requires_confirmation(self, tool_name: str, tool_input: dict) -> tuple[bool, str]:
        """Returns (needs_confirm, reason)"""
        if tool_name in self.ALWAYS_CONFIRM:
            cmd = tool_input.get("command", "")
            return True, f"About to run: {cmd}"

        if tool_name in self.CONFIRM_IF_EXISTS:
            path = tool_input.get("path", "")
            if os.path.exists(path):
                return True, f"Will overwrite existing file: {path}"

        return False, ""

    def confirm(self, reason: str) -> bool:
        """Ask the user to confirm. Returns True if confirmed."""
        print(f"\n⚠️  {reason}")
        answer = input("Proceed? [y/N] ").strip().lower()
        return answer == "y"
```

### Step 3: Update the execution loop

Integrate the safety gate into your tool dispatch:

```python
def run_agent(user_message: str, working_dir: str = "."):
    safety = SafetyGate()

    # Set working directory context
    system = SYSTEM_PROMPT + f"\n\nCurrent working directory: {os.path.abspath(working_dir)}"

    messages = [{"role": "user", "content": user_message}]

    while True:
        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=8192,
            system=system,
            tools=TOOLS,
            messages=messages
        )

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            for block in response.content:
                if hasattr(block, "text"):
                    print(f"\n{block.text}")
            break

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue

                needs_confirm, reason = safety.requires_confirmation(block.name, block.input)

                if needs_confirm:
                    if not safety.confirm(reason):
                        result = "User declined this action."
                    else:
                        result = execute_tool(block.name, block.input)
                else:
                    result = execute_tool(block.name, block.input)

                print(f"[{block.name}] → {str(result)[:150]}")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result
                })

            messages.append({"role": "user", "content": tool_results})
```

### Step 4: Add a conversational interface

The assistant should support multi-turn conversation, not just one-shot tasks:

```python
def chat(working_dir: str = "."):
    """Multi-turn conversational mode."""
    safety = SafetyGate()
    messages = []
    system = SYSTEM_PROMPT + f"\n\nWorking directory: {os.path.abspath(working_dir)}"

    print(f"Coding assistant ready. Working in: {working_dir}")
    print("Type 'exit' to quit.\n")

    while True:
        user_input = input("You: ").strip()
        if user_input.lower() in ("exit", "quit"):
            break
        if not user_input:
            continue

        messages.append({"role": "user", "content": user_input})

        # Run the agent loop for this turn
        response_text = run_turn(messages, system, safety)

        print(f"\nAssistant: {response_text}\n")
```

### Step 5: Point it at a real repo

Test against a small project. Give it tasks like:
- "What does this codebase do?"
- "Find all functions that take a `path` argument"
- "Add docstrings to the functions in main.py"
- "Run the tests and tell me what's failing"

## Success Criteria

- [ ] Can read and describe an unfamiliar codebase
- [ ] Can search for patterns across files
- [ ] Asks for confirmation before writing files
- [ ] Asks for confirmation before running any shell command
- [ ] If user declines, agent handles gracefully and tries a different approach
- [ ] Multi-turn conversation works (agent remembers previous turns)
- [ ] Agent reads files before modifying them

## What's Missing (Addressed Later)

| Gap | Fixed in |
|-----|---------|
| Conversation history lost on exit | Project 3 |
| Context will overflow on large repos | Project 8 (AFM) |
| No logging of what the agent did | Project 5 |
| Safety policy is hardcoded | Project 7 |
| Locked to Anthropic | Project 4 |
