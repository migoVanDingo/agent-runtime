# 0001 — Raw Tool Agent Architecture

## Overview

A local CLI agent with a tool registry, structured logging, and in-memory conversation state.
Persistence (SQLite, DAL, outbox queue) is deferred to a future iteration.

---

## Components

### `settings.py` (existing)
Environment variable loading via pydantic-settings. Singleton via `lru_cache`.

### `logger.py`
Centralized logging config. One place to set format, level, and handlers.
Every module imports from here — no scattered `logging.basicConfig` calls.

### `tools/base.py`
Abstract base class every tool must implement:
- `name: str`
- `description: str`
- `input_schema` property → returns `InputSchema`
- `execute(tool_input: dict) -> str`
- `to_api_schema()` → produces the dict the Anthropic API expects

Also owns `ToolProperty` and `InputSchema` Pydantic models.

### `tools/registry.py`
Holds a `dict[str, BaseTool]`. Methods:
- `register(tool)` — adds a tool by name
- `get(name)` — retrieves a tool by name, raises `KeyError` if missing
- `to_api_schema()` — produces the full list of tool dicts for the API

### `tools/implementations/`
One file per tool. Each subclasses `BaseTool` and defines its own `input_schema`.

```
tools/implementations/
  __init__.py
  read_file.py
  list_files.py
  bash_exec.py
```

### `messenger.py`
Owns in-memory conversation state as plain dicts (never SDK objects).
Performs boundary conversion of Anthropic SDK objects on ingestion via `model_dump()`.

Methods:
- `add_user_message(content: str)`
- `add_assistant_message(content: list)` — converts SDK blocks to dicts here
- `add_tool_results(tool_results: list[dict])`
- `get_messages() -> list[dict]`

### `agent.py`
The agentic loop. Owns:
- Anthropic client
- Messenger
- ToolRegistry

Runs until `stop_reason == "end_turn"`. Dispatches tool calls via the registry.
Returns final text response to caller. Includes a default system prompt.

### `main.py`
CLI entrypoint. Starts a REPL — user types, agent responds.
Conversation context is maintained in-memory for the duration of the session.
`exit` or `quit` ends the session.

---

## Dependency Flow

```
main.py
  └── Agent
        ├── Messenger
        ├── ToolRegistry
        └── Anthropic client

logger.py     ← imported by any module that logs
settings.py   ← imported by agent.py
```

---

## Interaction Model

Program starts and enters a REPL loop. User types a message, agent responds.
Conversation history is kept in-memory (Messenger) so subsequent queries have full context.
Session ends when user types `exit` or `quit`, or sends EOF/Ctrl-C.

---

## Deferred to Future Iteration

- **SQLite persistence** via SQLModel (`models.py`, `database.py`)
- **Data access layer** (`dal/conversation_repo.py`, `dal/message_repo.py`, `dal/tool_call_repo.py`)
- **Outbox queue pattern** — background thread draining a `queue.Queue` for non-blocking DB writes
- **Event types:** `MessageEvent`, `ToolCallEvent`
- Store full tool output for auditing
- Conversation resumption by ID
- System prompt configuration via settings
