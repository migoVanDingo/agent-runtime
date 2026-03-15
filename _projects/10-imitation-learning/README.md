# Project 10: Imitation Learning

## Prerequisites
Projects 1–9. You need a library of agent traces from Project 5 (at least 20–30 runs).

## What You Will Build

A pipeline that learns a policy from demonstration traces — watching what a good agent did and training a model to imitate it. You'll implement behavioral cloning and the DAgger algorithm for robustness. The output is a fine-tuned prompt (or a small classifier) that guides the agent toward better tool selection and sequencing.

This implements the **Imitation Learning** section of Bin Xu's learning stack: "a pragmatic route to competent behavior."

## Concepts

### Why Imitation Learning?

RL requires a reward signal and many trials. IL is cheaper: you already have traces from your agent running on real tasks. Each trace is a labeled example — sequence of (state, action) pairs where "action" = the tool call the agent made.

The goal: given the current conversation state, predict which tool call the expert (the traced agent) would make next.

### Behavioral Cloning (BC)

The simplest form: supervised learning from demonstrations.

```
Dataset:
  (state_1, tool_call_1)
  (state_2, tool_call_2)
  ...

Model: state → predicted_tool_call

Training: minimize prediction error on dataset
```

**The covariate shift problem:** BC trains on states the expert visited. At test time, the agent makes slightly different choices → visits different states → the BC policy has never seen them → compounds errors.

### DAgger (Dataset Aggregation)

Fix for covariate shift: interactively collect more data in states the *learner* visits, not just the expert.

```
Round 1: Train on expert traces → deploy policy
Round 2: Run policy, when uncertain → ask expert → add to dataset
Round 3: Retrain on all data → deploy
Repeat
```

In practice: "ask expert" = ask the human to demonstrate the correct action when the agent is uncertain.

### What We're Learning

We're not fine-tuning the LLM weights (that requires a GPU cluster). Instead, we're learning:

1. **Tool selection policy**: given the current task description + history, which tool should I call next?
2. **Argument generation**: given tool selected, what are the right arguments?

We implement this as **few-shot example selection**: instead of fine-tuning, we retrieve the most similar demonstration traces and inject them as examples into the system prompt.

This is called **in-context learning** — no gradient updates, no GPU, pure prompt engineering backed by trace retrieval.

## Architecture

```
Trace Library (.agent/traces/*.json)
        │
        │ process
        ▼
┌─────────────────────┐
│   TraceProcessor    │
│                     │
│  extract (s, a)     │  ← state-action pairs
│  pairs from traces  │
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│   DemoIndex         │
│                     │
│  embed each state   │  ← uses embedder from Project 6
│  build vector index │
└────────┬────────────┘
         │ queried at runtime
         ▼
┌─────────────────────┐
│   ILPolicy          │
│                     │
│  retrieve k similar │
│  demonstrations     │
│  inject as examples │
└─────────────────────┘
```

## Build Guide

### Step 1: Extract state-action pairs from traces

Create `il/extractor.py`:

```python
import json
from pathlib import Path
from dataclasses import dataclass


@dataclass
class Demonstration:
    trace_id: str
    task: str              # original user goal
    state: str             # conversation state before the action
    action_type: str       # "tool_call" | "end_turn"
    action: dict           # {"tool": "read_file", "args": {"path": "..."}}
    outcome: str           # what happened after (tool result or final answer)
    success: bool          # did the trace end successfully?


def extract_demonstrations(trace_dir: str = ".agent/traces") -> list[Demonstration]:
    demos = []
    for path in Path(trace_dir).glob("*.json"):
        try:
            spans = json.loads(path.read_text())
            demos.extend(_extract_from_trace(spans))
        except Exception as e:
            print(f"Skip {path}: {e}")
    return demos


def _extract_from_trace(spans: list[dict]) -> list[Demonstration]:
    if not spans:
        return []

    trace_span = spans[0]
    trace_id = trace_span.get("trace_id", "")
    task = trace_span.get("input", {}).get("task", "")
    success = trace_span.get("output", {}).get("success", False)

    demos = []
    model_calls = [s for s in spans if s["type"] == "model_call"]
    tool_calls = [s for s in spans if s["type"] == "tool_call"]

    # Build a timeline: for each model call, what tool calls followed?
    for i, model_span in enumerate(model_calls):
        # State = task + all previous tool calls
        prior_tools = tool_calls[:i] if i < len(tool_calls) else tool_calls

        state_parts = [f"Task: {task}"]
        for tc in prior_tools[-5:]:  # last 5 tool calls as context
            tool_name = tc.get("input", {}).get("tool", "?")
            result_len = tc.get("output", {}).get("result_length", 0)
            state_parts.append(f"Called {tool_name} → {result_len} chars result")

        state = "\n".join(state_parts)

        # Action = the next tool call after this model call
        if i < len(tool_calls):
            tc = tool_calls[i]
            action = {
                "tool": tc.get("input", {}).get("tool", ""),
                "args": tc.get("input", {}).get("args", {}),
            }
            outcome = f"Result: {tc.get('output', {}).get('result_length', 0)} chars"
            action_type = "tool_call"
        else:
            # Last model call — ended turn
            action = {}
            outcome = trace_span.get("output", {}).get("result", "")
            action_type = "end_turn"

        demos.append(Demonstration(
            trace_id=trace_id,
            task=task,
            state=state,
            action_type=action_type,
            action=action,
            outcome=outcome,
            success=success,
        ))

    return demos
```

### Step 2: Build the demonstration index

Create `il/index.py`:

```python
import json
import pickle
from pathlib import Path
from .extractor import Demonstration
from rag.embedder import create_embedder


class DemoIndex:
    def __init__(
        self,
        index_path: str = ".agent/il/demo_index.pkl",
        embedder_backend: str = "openai",
    ):
        self.index_path = Path(index_path)
        self.embedder = create_embedder(embedder_backend)
        self.demos: list[Demonstration] = []
        self.vectors: list[list[float]] = []

    def build(self, demos: list[Demonstration], only_successful: bool = True):
        """Build the index from demonstrations. Filter to successful traces by default."""
        filtered = [d for d in demos if d.success] if only_successful else demos
        print(f"Indexing {len(filtered)} demonstrations (from {len(demos)} total)")

        texts = [d.state for d in filtered]
        # Batch embed
        batch_size = 32
        all_vectors = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            vectors = self.embedder.embed(batch)
            all_vectors.extend(vectors)

        self.demos = filtered
        self.vectors = all_vectors
        self._save()
        print(f"Demo index built: {len(self.demos)} demonstrations")

    def search(self, query: str, k: int = 3) -> list[tuple[Demonstration, float]]:
        """Find the k most similar demonstrations to a query state."""
        import numpy as np

        if not self.vectors:
            return []

        q = np.array(self.embedder.embed([query])[0])
        scores = []
        for v in self.vectors:
            v_arr = np.array(v)
            score = float(np.dot(q, v_arr) / (np.linalg.norm(q) * np.linalg.norm(v_arr) + 1e-10))
            scores.append(score)

        top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
        return [(self.demos[i], scores[i]) for i in top_indices]

    def _save(self):
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.index_path, "wb") as f:
            pickle.dump({"demos": self.demos, "vectors": self.vectors}, f)

    def load(self) -> bool:
        if not self.index_path.exists():
            return False
        with open(self.index_path, "rb") as f:
            data = pickle.load(f)
        self.demos = data["demos"]
        self.vectors = data["vectors"]
        return True
```

### Step 3: IL Policy (few-shot injection)

Create `il/policy.py`:

```python
from .index import DemoIndex
from .extractor import Demonstration


class ILPolicy:
    """
    Retrieval-based IL policy: inject similar demonstrations into the system prompt
    to guide the model toward expert behavior.
    """
    def __init__(self, demo_index: DemoIndex, k: int = 3):
        self.index = demo_index
        self.k = k

    def get_demonstrations_prompt(self, current_state: str) -> str:
        """Return a formatted demonstrations block for injection into system prompt."""
        results = self.index.search(current_state, k=self.k)
        if not results:
            return ""

        parts = ["## Relevant Demonstrations (from past successful runs)\n"]
        for i, (demo, score) in enumerate(results, 1):
            if score < 0.5:
                continue  # Skip low-relevance demos

            parts.append(f"### Example {i} (similarity={score:.2f})")
            parts.append(f"Task: {demo.task}")
            parts.append(f"State: {demo.state}")

            if demo.action_type == "tool_call":
                parts.append(f"Action taken: {demo.action['tool']}({demo.action.get('args', {})})")
            else:
                parts.append("Action taken: responded to user (end turn)")

            parts.append(f"Outcome: {demo.outcome}\n")

        return "\n".join(parts)

    def build_state(self, task: str, tool_history: list[dict]) -> str:
        """Build a state string from current task + tool history."""
        parts = [f"Task: {task}"]
        for tc in tool_history[-5:]:
            parts.append(f"Called {tc['tool']} → {tc.get('result_summary', '?')}")
        return "\n".join(parts)
```

### Step 4: DAgger loop

Create `il/dagger.py`:

```python
from .extractor import Demonstration
from .index import DemoIndex


class DAggerCollector:
    """
    Interactive data collection for DAgger.
    When the agent is uncertain, ask the human to demonstrate.
    """
    def __init__(self, index: DemoIndex, uncertainty_threshold: float = 0.4):
        self.index = index
        self.uncertainty_threshold = uncertainty_threshold
        self.new_demos: list[Demonstration] = []

    def is_uncertain(self, state: str) -> bool:
        """Check if the agent is in a low-coverage state."""
        results = self.index.search(state, k=1)
        if not results:
            return True
        _, best_score = results[0]
        return best_score < self.uncertainty_threshold

    def request_demonstration(self, state: str, task: str) -> Demonstration | None:
        """
        Ask the human to demonstrate the correct action.
        In a real system, this would open a UI. Here we use stdin.
        """
        print(f"\n[DAgger] Agent is uncertain about this state.")
        print(f"State:\n{state}")
        print("\nWhat should the agent do? (type 'skip' to skip)")

        tool_name = input("Tool name: ").strip()
        if tool_name == "skip":
            return None

        args_str = input("Args (JSON, or leave blank): ").strip()
        try:
            args = __import__("json").loads(args_str) if args_str else {}
        except Exception:
            args = {}

        demo = Demonstration(
            trace_id="dagger_" + __import__("uuid").uuid4().hex[:8],
            task=task,
            state=state,
            action_type="tool_call",
            action={"tool": tool_name, "args": args},
            outcome="[human demonstration]",
            success=True,
        )
        self.new_demos.append(demo)
        return demo

    def rebuild_index_with_new_demos(self):
        """Add new human demonstrations to the index."""
        if not self.new_demos:
            return
        all_demos = self.index.demos + self.new_demos
        self.index.build(all_demos, only_successful=False)
        self.new_demos = []
        print(f"[DAgger] Index rebuilt with {len(all_demos)} demonstrations")
```

### Step 5: CLI for building the demo index

Create `build_demo_index.py`:

```python
#!/usr/bin/env python3
"""
Build or rebuild the imitation learning demonstration index from traces.

Usage:
    python build_demo_index.py
    python build_demo_index.py --trace-dir .agent/traces --all
"""
import sys
from il.extractor import extract_demonstrations
from il.index import DemoIndex

if __name__ == "__main__":
    only_successful = "--all" not in sys.argv
    trace_dir = ".agent/traces"

    print(f"Extracting demonstrations from {trace_dir}...")
    demos = extract_demonstrations(trace_dir)
    print(f"Found {len(demos)} state-action pairs")

    index = DemoIndex()
    index.build(demos, only_successful=only_successful)
    print("Done.")
```

### Step 6: Inject into your agent's system prompt

```python
from il.policy import ILPolicy
from il.index import DemoIndex

demo_index = DemoIndex()
demo_index.load()
il_policy = ILPolicy(demo_index, k=3)

def build_system_prompt(task: str, tool_history: list[dict]) -> str:
    base = BASE_SYSTEM_PROMPT

    current_state = il_policy.build_state(task, tool_history)
    demos = il_policy.get_demonstrations_prompt(current_state)

    if demos:
        return base + "\n\n" + demos
    return base
```

## Success Criteria

- [ ] `extract_demonstrations()` produces at least 50 (state, action) pairs from 10 traces
- [ ] Demo index builds and saves without error
- [ ] Retrieved demonstrations are relevant to the query (spot check 5 examples)
- [ ] Agent with IL policy makes better initial tool choices than without (compare first tool call)
- [ ] DAgger collector prompts for a demonstration when similarity is below threshold
- [ ] Rebuilding index after DAgger session improves coverage

## What's Missing

| Gap | Fixed in |
|-----|---------|
| In-context IL is limited by prompt size | True fine-tuning requires GPU + training loop |
| BC is brittle to distribution shift | DAgger (implemented) partially fixes this |
| No evaluation metric | Project 11 adds success rate tracking |
| Demonstrations are unfiltered | Add quality filter: only use traces where task was completed |
