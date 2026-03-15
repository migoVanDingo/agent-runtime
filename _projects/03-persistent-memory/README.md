# Project 3: Persistent Memory + Conversation

## Prerequisites
Projects 1 and 2. You should have a working multi-tool coding assistant.

## What You Will Build

Memory that survives between sessions. The agent remembers past conversations, can recall relevant context, and manages what goes into the context window. When the context gets long, it summarizes rather than throwing things away.

## Concepts

### Three Types of Memory (Bin Xu taxonomy)

| Type | What it stores | Lifetime | Implementation |
|------|---------------|----------|----------------|
| **Episodic** | What happened — events, tool calls, outcomes | Persists across sessions | Log to disk as JSONL |
| **Semantic** | Facts learned — "this repo uses pytest", "user prefers short functions" | Persists, updated over time | Key-value store on disk |
| **Working** | Current context window — the active conversation | This session only | In-memory messages list |

### Context Window Management

The context window is finite (~200k tokens for Claude, but costs money). As conversations grow:
1. Recent messages stay verbatim in the context
2. Older messages get summarized
3. Important facts get promoted to semantic memory
4. Very old content gets compressed or dropped

```
Context window:
├── System prompt                  (always present)
├── Semantic memory summary        (facts, always present)
├── Recent conversation (last N)   (verbatim)
└── Current turn                   (new)

Archive (on disk, not in context):
├── Full episodic log              (complete history)
└── Semantic store                 (all known facts)
```

### Summarization
When the window gets full, ask the model to summarize old messages before dropping them:

```python
summary = model.summarize(old_messages)
# "User asked me to add tests to auth.py. I found 3 functions without tests.
#  I added tests for login(), logout(), and validate_token(). Tests pass."
```

## Architecture

```
┌─────────────────────────────────────────────┐
│              Memory Manager                 │
│                                             │
│  episodic_log: List[Event]  ← disk (JSONL) │
│  semantic_store: Dict       ← disk (JSON)  │
│  working_memory: List[Msg]  ← in-memory    │
│                                             │
│  get_context() → messages for current call  │
│  update(new_events)                         │
│  summarize_if_needed()                      │
│  save() / load()                            │
└─────────────────────────────────────────────┘
```

## Build Guide

### Step 1: Episodic log

Create a simple append-only log of everything that happens:

```python
import json
from datetime import datetime
from pathlib import Path

class EpisodicLog:
    def __init__(self, path: str = ".agent/episodic.jsonl"):
        self.path = Path(path)
        self.path.parent.mkdir(exist_ok=True)

    def append(self, event: dict):
        event["timestamp"] = datetime.utcnow().isoformat()
        with open(self.path, "a") as f:
            f.write(json.dumps(event) + "\n")

    def load_recent(self, n: int = 50) -> list[dict]:
        if not self.path.exists():
            return []
        lines = self.path.read_text().strip().split("\n")
        return [json.loads(l) for l in lines[-n:] if l]

    def load_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(l) for l in self.path.read_text().strip().split("\n") if l]
```

### Step 2: Semantic store

Key-value facts the agent learns and can update:

```python
class SemanticStore:
    def __init__(self, path: str = ".agent/semantic.json"):
        self.path = Path(path)
        self.path.parent.mkdir(exist_ok=True)
        self.data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            return json.loads(self.path.read_text())
        return {}

    def set(self, key: str, value: str):
        self.data[key] = {"value": value, "updated": datetime.utcnow().isoformat()}
        self._save()

    def get(self, key: str) -> str | None:
        entry = self.data.get(key)
        return entry["value"] if entry else None

    def get_all(self) -> dict:
        return {k: v["value"] for k, v in self.data.items()}

    def _save(self):
        self.path.write_text(json.dumps(self.data, indent=2))
```

### Step 3: Context window manager

This is the core piece — decides what goes into the model call:

```python
class ContextManager:
    def __init__(self, max_tokens: int = 100_000):
        self.max_tokens = max_tokens
        self.messages: list[dict] = []

    def add(self, message: dict):
        self.messages.append(message)

    def get_context(self, semantic_store: SemanticStore) -> list[dict]:
        """Return messages that fit in the context window."""
        # Always include: semantic memory summary at the top
        # Then: recent messages, newest first, until we'd exceed budget
        # (Token counting is approximate: ~4 chars per token)

        semantic_summary = self._build_semantic_summary(semantic_store)
        budget = self.max_tokens - len(semantic_summary) // 4

        # Walk messages from newest to oldest, keep until budget is hit
        result = []
        chars_used = 0
        for msg in reversed(self.messages):
            msg_chars = len(json.dumps(msg))
            if chars_used + msg_chars > budget * 4:
                break
            result.insert(0, msg)
            chars_used += msg_chars

        return result

    def _build_semantic_summary(self, store: SemanticStore) -> str:
        facts = store.get_all()
        if not facts:
            return ""
        lines = ["## What I know about this project:"]
        for k, v in facts.items():
            lines.append(f"- {k}: {v}")
        return "\n".join(lines)

    def needs_summarization(self) -> bool:
        total_chars = sum(len(json.dumps(m)) for m in self.messages)
        return total_chars > self.max_tokens * 4 * 0.8  # 80% full
```

### Step 4: Summarization

When the context is getting full, summarize old messages:

```python
def summarize_old_messages(client, messages: list[dict]) -> str:
    """Ask the model to summarize a block of conversation."""
    summary_prompt = [
        {"role": "user", "content": f"""Summarize this conversation history concisely.
Focus on: what was asked, what tools were called, what was learned, what was changed.

{json.dumps(messages, indent=2)}

Write a 3-5 sentence summary."""}
    ]
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",  # use cheaper model for summaries
        max_tokens=500,
        messages=summary_prompt
    )
    return response.content[0].text

def maybe_summarize(context_manager: ContextManager, client) -> bool:
    """Summarize if needed. Returns True if summarization happened."""
    if not context_manager.needs_summarization():
        return False

    # Keep the last 10 messages verbatim, summarize the rest
    cutoff = max(0, len(context_manager.messages) - 10)
    old_messages = context_manager.messages[:cutoff]
    recent_messages = context_manager.messages[cutoff:]

    if not old_messages:
        return False

    summary = summarize_old_messages(client, old_messages)

    # Replace old messages with a single summary message
    summary_message = {
        "role": "user",
        "content": f"[Summary of earlier conversation: {summary}]"
    }
    context_manager.messages = [summary_message] + recent_messages
    return True
```

### Step 5: Give the agent a memory tool

Let the agent write to semantic memory when it learns something important:

```python
{
    "name": "remember",
    "description": "Store an important fact for future reference. Use this when you learn something about the project, user preferences, or environment that should persist across sessions.",
    "input_schema": {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Short identifier (e.g. 'test_command', 'project_type')"},
            "value": {"type": "string", "description": "What to remember"}
        },
        "required": ["key", "value"]
    }
}
```

### Step 6: Tie it all together

```python
class PersistentAgent:
    def __init__(self, working_dir: str = "."):
        self.working_dir = working_dir
        self.episodic = EpisodicLog()
        self.semantic = SemanticStore()
        self.context = ContextManager()
        self.client = anthropic.Anthropic()

    def chat(self, user_message: str) -> str:
        # Load previous context if resuming
        if not self.context.messages:
            recent = self.episodic.load_recent(20)
            for event in recent:
                if "message" in event:
                    self.context.add(event["message"])

        # Add new message
        self.context.add({"role": "user", "content": user_message})
        self.episodic.append({"type": "user_message", "content": user_message})

        # Maybe summarize before calling model
        maybe_summarize(self.context, self.client)

        # Get context for this call
        messages = self.context.get_context(self.semantic)

        # ... run the ReAct loop ...
        # ... log tool calls and results to episodic log ...
        # ... handle "remember" tool specially (write to semantic store) ...
```

## Success Criteria

- [ ] Agent remembers the conversation after you restart the script
- [ ] Agent can recall what files it modified in a previous session
- [ ] When you tell the agent "always use 4-space indentation", it remembers
- [ ] Context window does not crash with a very long conversation
- [ ] Old context gets summarized, not just truncated
- [ ] Episodic log on disk contains a complete record of what happened

## Observe

- Look at `.agent/episodic.jsonl` after a session. This is your trace — the ground truth of what happened.
- Look at `.agent/semantic.json`. This is what the agent "knows" persistently.
- Watch the summarization trigger. What does the summary look like? Is anything lost?

## What's Missing

| Gap | Fixed in |
|-----|---------|
| Memory is flat — no semantic search | Project 6 (RAG) |
| Summarization is lossy and naive | Project 8 (AFM) actively manages what to keep |
| No metrics on context usage | Project 5 |
| Provider is still hardcoded | Project 4 |
