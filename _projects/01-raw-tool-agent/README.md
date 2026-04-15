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
main.py  (REPL)
  └── Agent
        ├── BaseProvider        (interface)
        │     ├── AnthropicProvider
        │     └── OllamaProvider
        ├── Messenger           (in-memory conversation state)
        └── ToolRegistry        (tool lookup + API schema generation)
              └── BaseTool (ABC)
                    ├── File System Tools
                    └── Security / RE Tools
```

## Source Layout

```
src/
  main.py
  agent.py
  messenger.py
  logger.py
  settings.py
  utils.py
  providers/
    base.py
    anthropic.py
    ollama.py
    factory.py
  tools/
    base.py
    registry.py
    implementations/
      # File system
      read_file.py
      write_file.py
      read_file_lines.py
      list_files.py
      walk_directory.py
      search_files.py
      copy_file.py
      move_file.py
      delete_file.py
      make_directory.py
      bash_exec.py
      download_file.py
      get_working_directory.py
      environment_info.py
      # Security / RE
      strings_tool.py
      objdump_tool.py
      file_info.py
      hexdump_tool.py
      nm_tool.py
      ltrace_tool.py
      strace_tool.py
      readelf_tool.py
      checksec_tool.py
      grep_binary.py
      # Crypto / analysis
      hash_file.py
      base64_tool.py
      xor_decode.py
```

## Tools

### File System

| Tool | Description |
|---|---|
| `read_file` | Read the contents of a file |
| `write_file` | Write content to a file (creates or overwrites) |
| `read_file_lines` | Read a specific line range from a file |
| `list_files` | List files in a directory |
| `walk_directory` | Recursively walk a directory tree |
| `search_files` | Grep for a pattern across files (regex, glob filter, case option) |
| `copy_file` | Copy a file from source to destination |
| `move_file` | Move or rename a file or directory |
| `delete_file` | Delete a file (irreversible) |
| `make_directory` | Create a directory and any missing parents |
| `bash_exec` | Execute a bash command and return stdout + stderr |
| `download_file` | Download a file from a URL to a local path |
| `get_working_directory` | Return the current working directory |
| `environment_info` | Return OS, Python version, shell, hostname, user |

### Security / Reverse Engineering

| Tool | Description | macOS |
|---|---|---|
| `strings` | Extract printable strings from a binary | ✓ |
| `objdump` | Disassemble and analyze object files | ✓ |
| `file_info` | Determine file type | ✓ |
| `hexdump` | Hex dump of a file (uses `xxd`) | ✓ |
| `nm` | List symbols from an object file or binary | ✓ |
| `grep_binary` | Search for patterns in binary files | ✓ |
| `readelf` | Display ELF headers, sections, symbols | needs binutils |
| `checksec` | Check binary security properties (NX, ASLR, PIE, etc.) | needs checksec |
| `ltrace` | Trace library calls made by a program | Linux only |
| `strace` | Trace system calls made by a program | Linux only |

### Crypto / Analysis

| Tool | Description |
|---|---|
| `hash_file` | Compute MD5/SHA1/SHA256/SHA512 of a file |
| `base64_encode` | Base64-encode a string or hex bytes |
| `base64_decode` | Decode a base64 string, returns text or hex |
| `xor_decode` | XOR hex-encoded data against a repeating key |

## Prerequisites

```bash
pip install anthropic openai pydantic pydantic-settings python-dotenv ulid-py
```

Create a `.env` file in the repo root:

```
ANTHROPIC_API_KEY=your_key_here
ANTHROPIC_MODEL=claude-3-5-haiku-latest

# To use Ollama instead:
# LLM_PROVIDER=ollama
# OLLAMA_BASE_URL=http://localhost:11434/v1
# OLLAMA_MODEL=llama3.2
```

## Running

```bash
# Default (Anthropic)
python3 src/main.py

# Verbose logging to console
python3 src/main.py --verbose

# Ollama
LLM_PROVIDER=ollama python3 src/main.py
```

Type `exit` or `quit` to end the session. Session logs are written to `_logs/<session-id>.log`.

## Things to Observe

Once it works, try these experiments:
1. Print `messages` at each iteration. Watch it grow. This is the full agent state.
2. Ask for something that requires 3+ tool calls in sequence. Watch how it plans.
3. Ask for something impossible. What does it do?
4. Break one of the tools (return an error). Does the agent adapt?
5. Run it with Ollama and compare tool-calling reliability to Claude.

## What's Missing (Future Work)

| Gap | Notes |
|---|---|
| No conversation persistence | Messages live in memory only — session history is lost on exit |
| No memory beyond current context | Context window will eventually overflow |
| No safety gates | `bash_exec` and `delete_file` are unrestricted |
| No trace/observability | Logs exist but no structured spans or metrics |
| No failure recovery | If a tool crashes, the agent may loop or halt |
| `ltrace` Linux only | Dynamic tracing on macOS requires `dtruss` (needs SIP disabled) |
