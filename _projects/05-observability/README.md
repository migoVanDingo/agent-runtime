# Project 5: Observability Layer

## Prerequisites
Projects 1–4.

## What You Will Build

A passive trace logging system that captures everything the agent does — every model call, every tool call, every result — with timing and token counts. You can replay traces, compute metrics, and debug failures without re-running the agent.

This is the **Observability / AgentOps** layer in Cruz's stack. It is **passive** — it observes and records but never intervenes. (Intervention comes in Projects 8–9.)

## Concepts

### Spans and Traces

Borrowed from distributed systems tracing (OpenTelemetry):

- A **trace** is one complete agent run (from user message to final response)
- A **span** is one unit of work within that trace (one model call, one tool call)
- Spans have: start time, end time, inputs, outputs, metadata

```
Trace: "Add docstrings to main.py"
├── Span: model_call [0ms → 1200ms] tokens=340/180
│   └── tool_call: read_file(path="main.py") [1210ms → 1215ms]
├── Span: model_call [1220ms → 2400ms] tokens=820/420
│   └── tool_call: write_file(path="main.py", ...) [2410ms → 2412ms]
└── Span: model_call [2420ms → 3100ms] tokens=1200/95
    └── (end_turn) "I've added docstrings to all 4 functions."
```

### What to Capture

```python
@dataclass
class Span:
    span_id: str
    trace_id: str
    type: str              # "model_call" | "tool_call" | "agent_turn"
    started_at: float      # unix timestamp
    ended_at: float | None
    input: dict            # what went in
    output: dict | None    # what came out
    metadata: dict         # tokens, model, tool_name, error, etc.

    @property
    def duration_ms(self) -> float:
        if self.ended_at is None:
            return -1
        return (self.ended_at - self.started_at) * 1000
```

### Metrics to Compute From Traces

From Bin Xu's evaluation section:
- **Success rate** — did the agent complete the task?
- **Total tokens** — input + output across all model calls
- **Total cost** — tokens × price per token
- **Tool call count** — how many tool calls in a trace
- **Loop rate** — did the agent repeat tool calls? `1 - unique_calls / total_calls`
- **Latency** — total time from user message to final response

## Architecture

```
Agent Code
    │
    │ wraps calls with
    ↓
┌──────────────────┐
│     Tracer       │  ← thin wrapper around agent + provider
│                  │
│  start_trace()   │
│  start_span()    │
│  end_span()      │
│  end_trace()     │
└────────┬─────────┘
         │ writes
         ↓
┌──────────────────┐
│  Trace Store     │  ← disk (one JSON file per trace)
│  .agent/traces/  │
└────────┬─────────┘
         │ read by
         ↓
┌──────────────────┐
│  Trace Viewer    │  ← CLI command: python -m tracer view <trace_id>
│  Metrics CLI     │  ← CLI command: python -m tracer metrics
└──────────────────┘
```

## Build Guide

### Step 1: Tracer class

```python
import uuid, time, json
from pathlib import Path
from dataclasses import dataclass, field, asdict

@dataclass
class Span:
    span_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    trace_id: str = ""
    type: str = ""
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    input: dict = field(default_factory=dict)
    output: dict | None = None
    metadata: dict = field(default_factory=dict)
    error: str | None = None

    def end(self, output=None, error=None):
        self.ended_at = time.time()
        self.output = output
        self.error = error

    @property
    def duration_ms(self):
        if not self.ended_at:
            return None
        return round((self.ended_at - self.started_at) * 1000, 1)

class Tracer:
    def __init__(self, store_dir: str = ".agent/traces"):
        self.store_dir = Path(store_dir)
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.current_trace_id: str | None = None
        self.spans: list[Span] = []

    def start_trace(self, task: str, metadata: dict = {}) -> str:
        self.current_trace_id = str(uuid.uuid4())[:12]
        self.spans = []
        span = Span(trace_id=self.current_trace_id, type="trace",
                    input={"task": task}, metadata=metadata)
        self.spans.append(span)
        return self.current_trace_id

    def span(self, type: str, input: dict, metadata: dict = {}) -> Span:
        s = Span(trace_id=self.current_trace_id, type=type,
                 input=input, metadata=metadata)
        self.spans.append(s)
        return s

    def end_trace(self, success: bool, output: str = ""):
        if self.spans:
            self.spans[0].end(output={"success": success, "result": output})
        self._save()

    def _save(self):
        if not self.current_trace_id:
            return
        path = self.store_dir / f"{self.current_trace_id}.json"
        path.write_text(json.dumps([asdict(s) for s in self.spans], indent=2))
```

### Step 2: Wrap provider calls

Create a `TracedProvider` that wraps any `LLMProvider`:

```python
class TracedProvider(LLMProvider):
    def __init__(self, provider: LLMProvider, tracer: Tracer):
        self.provider = provider
        self.tracer = tracer

    def model_id(self):
        return self.provider.model_id()

    def complete(self, messages, tools=None, system=None, max_tokens=4096):
        span = self.tracer.span(
            type="model_call",
            input={"message_count": len(messages), "has_tools": tools is not None},
            metadata={"model": self.provider.model_id()}
        )

        try:
            response = self.provider.complete(messages, tools, system, max_tokens)
            span.end(output={
                "stop_reason": response.stop_reason,
                "tool_calls": [t.name for t in response.tool_calls],
                "has_text": response.text is not None,
            }, )
            span.metadata["input_tokens"] = response.input_tokens
            span.metadata["output_tokens"] = response.output_tokens
            return response
        except Exception as e:
            span.end(error=str(e))
            raise
```

### Step 3: Wrap tool execution

```python
def traced_execute_tool(tracer: Tracer, tool_name: str, tool_input: dict,
                         execute_fn) -> str:
    span = tracer.span(
        type="tool_call",
        input={"tool": tool_name, "args": tool_input}
    )
    try:
        result = execute_fn(tool_name, tool_input)
        span.end(output={"result_length": len(str(result))})
        return result
    except Exception as e:
        span.end(error=str(e))
        return f"Error: {e}"
```

### Step 4: Metrics from traces

```python
def compute_metrics(trace_file: Path) -> dict:
    spans = json.loads(trace_file.read_text())

    model_calls = [s for s in spans if s["type"] == "model_call"]
    tool_calls = [s for s in spans if s["type"] == "tool_call"]

    total_input_tokens = sum(s["metadata"].get("input_tokens", 0) for s in model_calls)
    total_output_tokens = sum(s["metadata"].get("output_tokens", 0) for s in model_calls)

    tool_names = [s["input"]["tool"] for s in tool_calls]
    unique_tools = set(tool_names)
    loop_rate = 1 - len(unique_tools) / len(tool_names) if tool_names else 0

    trace_span = spans[0]
    total_duration = None
    if trace_span.get("ended_at") and trace_span.get("started_at"):
        total_duration = round((trace_span["ended_at"] - trace_span["started_at"]) * 1000)

    return {
        "trace_id": spans[0]["trace_id"],
        "model_calls": len(model_calls),
        "tool_calls": len(tool_calls),
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "loop_rate": round(loop_rate, 2),
        "duration_ms": total_duration,
        "success": trace_span.get("output", {}).get("success"),
    }
```

### Step 5: CLI viewer

```python
# python -m tracer view <trace_id>
# python -m tracer metrics

import sys
from pathlib import Path

def view_trace(trace_id: str):
    path = Path(f".agent/traces/{trace_id}.json")
    spans = json.loads(path.read_text())
    for span in spans:
        duration = ""
        if span.get("ended_at") and span.get("started_at"):
            ms = round((span["ended_at"] - span["started_at"]) * 1000)
            duration = f" [{ms}ms]"
        print(f"  [{span['type']}]{duration} {span['input']}")
        if span.get("error"):
            print(f"    ERROR: {span['error']}")

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "metrics"
    if cmd == "view" and len(sys.argv) > 2:
        view_trace(sys.argv[2])
    elif cmd == "metrics":
        for path in sorted(Path(".agent/traces").glob("*.json")):
            m = compute_metrics(path)
            print(m)
```

## Success Criteria

- [ ] Every model call creates a span with timing and token counts
- [ ] Every tool call creates a span with input and duration
- [ ] Trace saved to disk after each agent run
- [ ] Can view a trace and understand what the agent did, step by step
- [ ] Can compute: total tokens, cost, tool call count, loop rate, duration
- [ ] If the agent errors mid-run, the partial trace is still saved

## Why This Matters

The trace files from Project 5 become the training data for Projects 10 and 11 (imitation learning and RL). Every agent run is now a labeled example — you can see what worked and what didn't.
