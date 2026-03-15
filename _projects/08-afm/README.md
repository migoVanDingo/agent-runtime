# Project 8: Adaptive Focus Memory (AFM)

## Prerequisites
Projects 1–7. You need the persistent agent, provider abstraction, and observability layer.

## What You Will Build

An execution-time memory manager that **actively intervenes** in what goes into the context window — deciding which information to keep, compress, or drop as the conversation grows. Unlike the passive summarizer from Project 3, AFM continuously monitors context pressure and reweights content based on relevance to the current goal.

This implements the key ideas from Cruz's **Adaptive Focus Memory** (arXiv:2511.12712), which is one of the two active intervention systems in his AI Runtime Infrastructure stack.

## The Distinction That Matters

**Project 3 (passive):** When context hits 80% full → summarize oldest messages → done.

**AFM (active):** At every turn, evaluate every item in context against the current task. Compute relevance scores. Keep high-relevance items verbatim. Compress medium-relevance items. Drop low-relevance items. Update continuously, not just at a threshold.

```
                    Project 3           AFM
─────────────────────────────────────────────
When it runs:       threshold (80%)     every turn
What it measures:   size only           relevance to goal
Decision granule:   whole message       individual items
Strategy:           summarize old       score + reweight
Intervenes during   no                  yes
execution?
```

## Concepts

### Context as a Managed Resource

The context window is not a FIFO queue. It is a **resource** with a capacity budget. AFM manages it like a memory allocator:

```
Budget: 100,000 tokens
├── System prompt:            1,200 tokens [pinned]
├── Semantic facts:             800 tokens [pinned]
├── Active goal:                150 tokens [pinned]
├── Recent turns (last 3):    4,200 tokens [recent window]
├── Relevant tool results:    8,400 tokens [scored, kept]
├── Compressed older turns:   2,100 tokens [summarized]
└── Available:               83,150 tokens
```

### Relevance Scoring

Each item in context gets a score against the current goal. Three signals:

1. **Recency** — newer items score higher (decays with age)
2. **Semantic similarity** — embedding similarity to current task description
3. **Type priority** — tool results from current task > tool results from 5 turns ago

```python
relevance = (
    0.4 * recency_score +
    0.4 * semantic_similarity +
    0.2 * type_priority
)
```

### Compression Tiers

Based on relevance score:
- **Score > 0.7** → keep verbatim
- **Score 0.4–0.7** → compress (summarize to 20% of original size)
- **Score < 0.4** → drop (only keep a one-line record that it existed)

### Pinned Items

Some items are always kept regardless of score:
- System prompt
- Current user message
- Current goal / task statement
- Any item the agent explicitly marked as important

## Architecture

```
Every turn:
    Agent calls model
        │
        ▼
    AFMManager.prepare_context(messages, goal)
        │
        ├── Score all items against goal
        ├── Apply compression to medium items
        ├── Drop low items (log to episodic)
        ├── Add goal + semantic memory header
        └── Return trimmed context
        │
        ▼
    model.complete(trimmed_context, ...)
        │
        ▼
    AFMManager.update(response)
        ├── Record what was kept/dropped
        └── Update relevance model
```

## Build Guide

### Step 1: Context item types

Create `afm/types.py`:

```python
from dataclasses import dataclass, field
from enum import Enum

class ItemType(Enum):
    SYSTEM = "system"           # system prompt (always pinned)
    USER_MESSAGE = "user_msg"   # user turn
    ASSISTANT_TEXT = "asst_text"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    SUMMARY = "summary"         # compressed block
    GOAL = "goal"               # current task statement (pinned)
    SEMANTIC = "semantic"       # facts from semantic store (pinned)


PINNED_TYPES = {ItemType.SYSTEM, ItemType.GOAL, ItemType.SEMANTIC}


@dataclass
class ContextItem:
    item_id: str
    type: ItemType
    content: str | dict    # raw message content
    turn: int              # which agent turn produced this
    token_estimate: int = 0
    relevance: float = 1.0
    pinned: bool = False

    def __post_init__(self):
        if self.type in PINNED_TYPES:
            self.pinned = True
        if not self.token_estimate:
            self.token_estimate = len(str(self.content)) // 4  # ~4 chars/token
```

### Step 2: Relevance scorer

Create `afm/scorer.py`:

```python
import math
import time
from .types import ContextItem, ItemType


TYPE_PRIORITY = {
    ItemType.GOAL: 1.0,
    ItemType.SEMANTIC: 0.9,
    ItemType.TOOL_RESULT: 0.7,
    ItemType.USER_MESSAGE: 0.6,
    ItemType.ASSISTANT_TEXT: 0.5,
    ItemType.TOOL_CALL: 0.4,
    ItemType.SUMMARY: 0.3,
}


def recency_score(item: ContextItem, current_turn: int, decay: float = 0.15) -> float:
    """Exponential decay: score = e^(-decay * age_in_turns)"""
    age = max(0, current_turn - item.turn)
    return math.exp(-decay * age)


def type_score(item: ContextItem) -> float:
    return TYPE_PRIORITY.get(item.type, 0.5)


def keyword_overlap(item_text: str, goal_text: str) -> float:
    """Simple keyword overlap — no embedding needed."""
    goal_words = set(goal_text.lower().split())
    item_words = set(item_text.lower().split())
    if not goal_words:
        return 0.5
    overlap = len(goal_words & item_words)
    return min(1.0, overlap / max(1, len(goal_words) * 0.3))


def score_item(item: ContextItem, goal: str, current_turn: int) -> float:
    """Compute composite relevance score for a context item."""
    if item.pinned:
        return 1.0

    recency = recency_score(item, current_turn)
    type_p = type_score(item)
    keyword = keyword_overlap(str(item.content), goal)

    return 0.4 * recency + 0.4 * keyword + 0.2 * type_p
```

### Step 3: Compressor

Create `afm/compressor.py`:

```python
from providers.base import LLMProvider


def compress_items(items: list[dict], provider: LLMProvider) -> str:
    """
    Ask the model to compress a list of context items into a dense summary.
    Uses a cheap/fast model if available.
    """
    content = "\n\n".join([str(item.get("content", "")) for item in items])
    if len(content) < 200:
        return content  # not worth compressing

    messages = [{
        "role": "user",
        "content": (
            "Compress the following conversation history into a dense summary. "
            "Keep: tool call names and their key results, decisions made, errors encountered. "
            "Drop: verbose output, repeated content, irrelevant details. "
            "Write 3–8 bullet points.\n\n"
            f"{content[:6000]}"
        )
    }]

    response = provider.complete(messages, max_tokens=300)
    return response.text or content[:500]
```

### Step 4: AFM Manager

Create `afm/manager.py`:

```python
import uuid
from pathlib import Path
import json

from .types import ContextItem, ItemType, PINNED_TYPES
from .scorer import score_item
from .compressor import compress_items
from providers.base import LLMProvider


KEEP_VERBATIM_THRESHOLD = 0.65
COMPRESS_THRESHOLD = 0.35


class AFMManager:
    def __init__(
        self,
        provider: LLMProvider,
        token_budget: int = 80_000,
        log_path: str = ".agent/afm_log.jsonl",
    ):
        self.provider = provider
        self.token_budget = token_budget
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

        self.items: list[ContextItem] = []
        self.current_turn: int = 0
        self.current_goal: str = ""

    def set_goal(self, goal: str):
        self.current_goal = goal

    def add_item(self, type: ItemType, content, pinned: bool = False) -> ContextItem:
        item = ContextItem(
            item_id=str(uuid.uuid4())[:8],
            type=type,
            content=content,
            turn=self.current_turn,
            pinned=pinned,
        )
        self.items.append(item)
        return item

    def next_turn(self):
        self.current_turn += 1

    def prepare_context(self) -> list[dict]:
        """
        Score all items, apply compression strategy,
        return a flat list of messages within the token budget.
        """
        # Score everything
        for item in self.items:
            if not item.pinned:
                item.relevance = score_item(item, self.current_goal, self.current_turn)

        # Sort: pinned first, then by relevance desc
        sorted_items = sorted(
            self.items,
            key=lambda i: (0 if i.pinned else 1, -i.relevance)
        )

        # Apply compression tiers
        kept = []
        to_compress = []
        tokens_used = 0

        for item in sorted_items:
            if item.pinned:
                kept.append(item)
                tokens_used += item.token_estimate
            elif item.relevance >= KEEP_VERBATIM_THRESHOLD:
                kept.append(item)
                tokens_used += item.token_estimate
            elif item.relevance >= COMPRESS_THRESHOLD:
                to_compress.append(item)
            else:
                self._log_dropped(item)

        # Compress medium-relevance items into a single summary block
        if to_compress:
            compressed_text = compress_items(
                [{"content": i.content} for i in to_compress],
                self.provider
            )
            summary_item = ContextItem(
                item_id="summary",
                type=ItemType.SUMMARY,
                content=compressed_text,
                turn=self.current_turn,
                pinned=False,
                relevance=0.5,
            )
            kept.append(summary_item)
            tokens_used += summary_item.token_estimate

        # If still over budget, drop lowest-relevance non-pinned items
        while tokens_used > self.token_budget:
            non_pinned = [i for i in kept if not i.pinned]
            if not non_pinned:
                break
            lowest = min(non_pinned, key=lambda i: i.relevance)
            kept.remove(lowest)
            tokens_used -= lowest.token_estimate
            self._log_dropped(lowest)

        # Convert to message format (chronological order)
        kept_by_turn = sorted(kept, key=lambda i: i.turn)
        messages = []
        for item in kept_by_turn:
            if item.type in (ItemType.SYSTEM, ItemType.GOAL, ItemType.SEMANTIC):
                continue  # These go in system prompt, not messages
            if isinstance(item.content, dict):
                messages.append(item.content)
            elif isinstance(item.content, str):
                messages.append({"role": "user", "content": item.content})

        return messages

    def get_system_addendum(self) -> str:
        """Content to prepend to system prompt: goal + semantic facts."""
        parts = []
        if self.current_goal:
            parts.append(f"## Current Goal\n{self.current_goal}")
        for item in self.items:
            if item.type == ItemType.SEMANTIC:
                parts.append(str(item.content))
        return "\n\n".join(parts)

    def _log_dropped(self, item: ContextItem):
        entry = {
            "turn": self.current_turn,
            "item_id": item.item_id,
            "type": item.type.value,
            "relevance": item.relevance,
            "tokens": item.token_estimate,
            "action": "dropped",
        }
        with open(self.log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
```

### Step 5: Integrate with your agent

Replace the `ContextManager` from Project 3 with `AFMManager`:

```python
from afm.manager import AFMManager
from afm.types import ItemType
from providers import create_provider

provider = create_provider("anthropic")
afm = AFMManager(provider=provider, token_budget=80_000)

def run_turn(user_message: str, goal: str = ""):
    afm.set_goal(goal or user_message)
    afm.next_turn()

    # Add the user message
    afm.add_item(ItemType.USER_MESSAGE, {"role": "user", "content": user_message})

    while True:
        # Get managed context
        messages = afm.prepare_context()
        system = BASE_SYSTEM + "\n\n" + afm.get_system_addendum()

        response = provider.complete(messages=messages, system=system, tools=TOOLS)

        # Record assistant response
        afm.add_item(ItemType.ASSISTANT_TEXT, {
            "role": "assistant",
            "content": response.text or ""
        })

        if response.stop_reason == "end_turn":
            return response.text

        if response.tool_calls:
            for tc in response.tool_calls:
                afm.add_item(ItemType.TOOL_CALL, {
                    "tool": tc.name, "args": tc.input
                })
                result = execute_tool(tc.name, tc.input)
                afm.add_item(ItemType.TOOL_RESULT, {
                    "role": "user",
                    "content": [{"type": "tool_result",
                                 "tool_use_id": tc.id,
                                 "content": result}]
                })
```

## Success Criteria

- [ ] Agent runs a 20+ turn task without hitting context limit
- [ ] `.agent/afm_log.jsonl` shows items being scored and dropped
- [ ] Relevant earlier context survives compression (e.g., the goal statement)
- [ ] Irrelevant old tool outputs are dropped or compressed
- [ ] Agent still correctly answers "what did you do two turns ago?" for important events
- [ ] Token usage is visibly lower than without AFM (check via traces from Project 5)

## What's Missing

| Gap | Fixed in |
|-----|---------|
| Relevance is keyword-based, not embedding-based | Swap in vector similarity from Project 6 for better scoring |
| Compression uses same model (expensive) | Use a cheaper/local model for compression |
| No UI for inspecting what was kept/dropped | Build a viewer: `python -m afm view` |
| AFM state not persisted between sessions | Serialize items to disk like episodic log |
