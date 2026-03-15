# Project 1: Raw Tool-Calling Agent

## What You Will Build

A minimal command-line agent that can use tools. You give it a task in plain text, it reasons about what tools to call, calls them, observes the results, and continues until the task is done.

No framework. No abstraction. Raw Anthropic SDK + a `while` loop.

## Concepts

### The ReAct Loop
ReAct (Yao et al. 2023, arXiv:2210.03629) is the foundational pattern for tool-using agents:

```
while not done:
    thought  = model.think(history)        # "I need to list the files first"
    action   = model.decide(thought)       # tool_call: list_files(path=".")
    result   = execute_tool(action)        # ["README.md", "main.py", ...]
    history += [thought, action, result]   # append everything
```

Each iteration the model sees the full history — all its previous thoughts, all tool calls, all results. This is how it knows what happened and what to do next.

### Tool Schemas
Tools are described to the model as JSON schemas. The model reads the schema and produces a structured call — not free-form text. This is the key that makes tool calling reliable:

```json
{
  "name": "read_file",
  "description": "Read the contents of a file",
  "input_schema": {
    "type": "object",
    "properties": {
      "path": {
        "type": "string",
        "description": "Path to the file"
      }
    },
    "required": ["path"]
  }
}
```

### Message History
The Anthropic API is stateless. You pass the entire conversation history on every call. Your code maintains the list of messages and appends to it each turn.

```
messages = [
  {"role": "user", "content": "What is in the current directory?"},
  {"role": "assistant", "content": [{"type": "tool_use", "name": "list_files", ...}]},
  {"role": "user", "content": [{"type": "tool_result", "content": "[README.md, ...]"}]},
  {"role": "assistant", "content": "The directory contains README.md and ..."}
]
```

## Architecture

```
┌─────────────────────────────────────────┐
│              Your Script                │
│                                         │
│  messages = []                          │
│  while not done:                        │
│    response = anthropic.call(messages)  │
│    if tool_call in response:            │
│      result = run_tool(tool_call)       │
│      messages.append(result)            │
│    else:                                │
│      print(response.text)               │
│      done = True                        │
└─────────────────────────────────────────┘
         │
         ↓ tool calls
┌─────────────────────┐
│       Tools         │
│  • list_files       │
│  • read_file        │
│  • bash_exec        │
└─────────────────────┘
```

## Prerequisites

```bash
pip install anthropic
export ANTHROPIC_API_KEY=your_key_here
```

## Build Guide

### Step 1: Hello, tool use

Create `agent.py`. Start with a single hardcoded tool call to see what the raw API returns:

```python
import anthropic

client = anthropic.Anthropic()

tools = [
    {
        "name": "list_files",
        "description": "List files in a directory",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path"}
            },
            "required": ["path"]
        }
    }
]

response = client.messages.create(
    model="claude-opus-4-6",
    max_tokens=1024,
    tools=tools,
    messages=[{"role": "user", "content": "What files are in the current directory?"}]
)

print(response)
print(response.stop_reason)   # should be "tool_use"
print(response.content)       # list of content blocks, one is a ToolUseBlock
```

Run it. Read every field of the response. Understand the shape before writing more code.

### Step 2: Execute the tool

Write the actual tool function and execute the call the model requested:

```python
import os

def list_files(path: str) -> str:
    try:
        files = os.listdir(path)
        return "\n".join(files)
    except Exception as e:
        return f"Error: {e}"

def execute_tool(tool_name: str, tool_input: dict) -> str:
    if tool_name == "list_files":
        return list_files(tool_input["path"])
    return f"Unknown tool: {tool_name}"
```

### Step 3: Build the loop

Now connect everything into the ReAct loop:

```python
def run_agent(user_message: str):
    messages = [{"role": "user", "content": user_message}]

    while True:
        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=4096,
            tools=tools,
            messages=messages
        )

        # Add assistant response to history
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            # Model finished, extract final text
            for block in response.content:
                if hasattr(block, "text"):
                    print(f"\nAgent: {block.text}")
            break

        if response.stop_reason == "tool_use":
            # Execute each tool call and collect results
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    print(f"\n[Tool call: {block.name}({block.input})]")
                    result = execute_tool(block.name, block.input)
                    print(f"[Result: {result[:200]}]")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result
                    })

            # Feed results back to model
            messages.append({"role": "user", "content": tool_results})
```

### Step 4: Add more tools

Add these two tools and their implementations:

**read_file:**
```python
{
    "name": "read_file",
    "description": "Read the contents of a file",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {"type": "string"}
        },
        "required": ["path"]
    }
}
```

**bash_exec:**
```python
{
    "name": "bash_exec",
    "description": "Execute a bash command and return stdout + stderr",
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The bash command to run"}
        },
        "required": ["command"]
    }
}
```

For `bash_exec`, use `subprocess.run`:
```python
import subprocess

def bash_exec(command: str) -> str:
    result = subprocess.run(
        command, shell=True, capture_output=True, text=True, timeout=30
    )
    output = result.stdout
    if result.stderr:
        output += f"\nSTDERR: {result.stderr}"
    return output if output else "(no output)"
```

### Step 5: Add a CLI entry point

```python
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        task = " ".join(sys.argv[1:])
    else:
        task = input("Task: ")
    run_agent(task)
```

Try: `python agent.py "how many Python files are in the current directory"`

## Success Criteria

- [ ] Agent can answer "what files are in this directory" by calling `list_files`
- [ ] Agent can read a file and summarize its contents using `read_file`
- [ ] Agent can run `ls -la` via `bash_exec` and interpret the output
- [ ] Agent can chain multiple tool calls (e.g., list files, then read one)
- [ ] Agent terminates cleanly when there are no more tool calls to make
- [ ] You can follow the message history and understand every entry

## Things to Observe

Once it works, try these experiments:
1. Print `messages` at each iteration. Watch it grow. This is the full agent state.
2. Ask for something that requires 3+ tool calls in sequence. Watch how it plans.
3. Ask for something impossible. What does it do?
4. Break one of the tools (return an error). Does the agent adapt?

## What You Just Built

This is the foundation that everything else in this curriculum builds on. You now have:
- The execution loop (observe → think → act)
- Tool schema definition
- Tool dispatch
- Message history management

Every subsequent project adds to or wraps this core pattern.

## What's Missing (Addressed in Later Projects)

| Gap | Fixed in |
|-----|---------|
| No conversation persistence | Project 3 |
| No memory beyond the current context | Project 3 |
| Hardcoded to Anthropic | Project 4 |
| No trace logging | Project 5 |
| No safety gates (bash_exec is dangerous) | Projects 2, 7 |
| Context will eventually overflow | Project 8 |
| No failure recovery | Project 9 |
