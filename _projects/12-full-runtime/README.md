# Project 12: Full Runtime — Integration

## Prerequisites
All previous projects (1–11).

## What You Will Build

Everything integrated into a single, production-quality CLI that implements Cruz's complete AI Runtime Infrastructure stack. One command runs a fully-instrumented, policy-governed, memory-managed, failure-resilient, learning agent.

This is the capstone. Every component from every prior project becomes a layer in a coherent runtime.

## The Stack (Fully Assembled)

```
┌─────────────────────────────────────────────────────────────────┐
│                        User / CLI                               │
│                  python -m agent "task description"             │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│                     AgentRuntime                                │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  RL Runner (Project 11)                                  │   │
│  │  → selects strategy arm based on task type               │   │
│  └──────────────────────┬───────────────────────────────────┘   │
│                         │                                       │
│  ┌──────────────────────▼───────────────────────────────────┐   │
│  │  VIGIL Monitor (Project 9)                               │   │
│  │  → watches for loops, drift, error cascades              │   │
│  └──────────────────────┬───────────────────────────────────┘   │
│                         │                                       │
│  ┌──────────────────────▼───────────────────────────────────┐   │
│  │  AFM Manager (Project 8)                                 │   │
│  │  → manages context window dynamically                    │   │
│  └──────────────────────┬───────────────────────────────────┘   │
│                         │                                       │
│  ┌──────────────────────▼───────────────────────────────────┐   │
│  │  TracedProvider (Project 5)                              │   │
│  │  → wraps any LLMProvider, records all model calls        │   │
│  └──────────────────────┬───────────────────────────────────┘   │
│                         │                                       │
│  ┌──────────────────────▼───────────────────────────────────┐   │
│  │  LLMProvider (Project 4)                                 │   │
│  │  → Anthropic / OpenAI / Ollama (runtime-swappable)       │   │
│  └──────────────────────┬───────────────────────────────────┘   │
│                         │                                       │
│  ┌──────────────────────▼───────────────────────────────────┐   │
│  │  PolicyEngine (Project 7)                                │   │
│  │  → gates every tool call, writes audit log               │   │
│  └──────────────────────┬───────────────────────────────────┘   │
│                         │                                       │
│  ┌──────────────────────▼───────────────────────────────────┐   │
│  │  Tools (Projects 1–2)                                    │   │
│  │  read_file / write_file / bash_exec / git_* /            │   │
│  │  retrieve_code (Project 6) / remember (Project 3)        │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  IL Policy (Project 10) — injected into system prompt    │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Memory (Project 3)                                      │   │
│  │  EpisodicLog / SemanticStore                             │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

## Build Guide

### Step 1: AgentRuntime class

Create `runtime/agent_runtime.py` — the single integration point for all components:

```python
import uuid
import time
from pathlib import Path

from providers import create_provider, LLMProvider
from providers.traced import TracedProvider
from tracer import Tracer
from policy.engine import PolicyEngine, load_policy_from_config
from afm.manager import AFMManager
from afm.types import ItemType
from vigil.monitor import VIGILMonitor
from rl.runner import RLRunner
from rl.strategies import ARM_MAP
from il.policy import ILPolicy
from il.index import DemoIndex
from memory.episodic import EpisodicLog
from memory.semantic import SemanticStore
from rag.retriever import Retriever
from tools import TOOLS, execute_tool


BASE_SYSTEM = """You are a coding assistant with access to the local filesystem.

You help users understand, modify, and improve code.

Before making changes to existing files, read them first.
Before running shell commands, explain what the command will do.
Make small, focused changes. Prefer editing over rewriting.
"""


class AgentRuntime:
    def __init__(
        self,
        provider_name: str = "anthropic",
        model: str | None = None,
        working_dir: str = ".",
        config_path: str = ".agent/policy.json",
    ):
        self.working_dir = working_dir

        # Layer 1: Provider
        base_provider = create_provider(provider_name, model)

        # Layer 2: Tracer
        self.tracer = Tracer()

        # Layer 3: TracedProvider
        self.provider = TracedProvider(base_provider, self.tracer)

        # Layer 4: Policy
        self.policy = load_policy_from_config(config_path)

        # Layer 5: AFM
        self.afm = AFMManager(provider=base_provider, token_budget=80_000)

        # Layer 6: VIGIL
        self.vigil = VIGILMonitor(max_tokens_per_run=60_000)

        # Layer 7: RL
        self.rl = RLRunner()

        # Layer 8: IL
        self.demo_index = DemoIndex()
        self.demo_index.load()
        self.il_policy = ILPolicy(self.demo_index, k=3)

        # Memory
        self.episodic = EpisodicLog()
        self.semantic = SemanticStore()

        # RAG (optional — only if index exists)
        rag_index = Path(".agent/rag/index.pkl")
        self.retriever = Retriever() if rag_index.exists() else None

    def run(self, task: str, interactive: bool = True) -> str:
        """Run a single task end-to-end."""

        # RL: select strategy
        arm_name = self.rl.select_strategy(task)
        strategy = ARM_MAP.get(arm_name)
        print(f"[Runtime] Strategy: {arm_name} | Provider: {self.provider.model_id()}")

        # Start trace
        trace_id = self.tracer.start_trace(task, metadata={"strategy_arm": arm_name})

        # AFM: set goal
        self.afm.set_goal(task)

        # Build system prompt
        system = self._build_system(task, arm_name)

        # Initialize conversation
        messages = [{"role": "user", "content": task}]
        self.episodic.append({"type": "user_message", "content": task})

        # AFM: add initial message
        self.afm.add_item(ItemType.USER_MESSAGE, {"role": "user", "content": task})

        tool_history = []
        success = False
        result_text = ""

        try:
            while True:
                self.afm.next_turn()
                self.vigil.maybe_checkpoint(messages)

                # AFM: get managed context
                managed_messages = self.afm.prepare_context()
                full_system = system + "\n\n" + self.afm.get_system_addendum()

                # Call model
                response = self.provider.complete(
                    messages=managed_messages,
                    tools=TOOLS,
                    system=full_system,
                )

                tool_calls_this_turn = []
                tool_errors_this_turn = []

                if response.stop_reason == "end_turn":
                    result_text = response.text or ""
                    success = True

                    # AFM: record
                    self.afm.add_item(ItemType.ASSISTANT_TEXT, {
                        "role": "assistant", "content": result_text
                    })

                    print(f"\n{result_text}")
                    break

                # Process tool calls
                if response.tool_calls:
                    tool_results = []

                    for tc in response.tool_calls:
                        tool_calls_this_turn.append({
                            "name": tc.name, "input": tc.input
                        })

                        # AFM: record tool call
                        self.afm.add_item(ItemType.TOOL_CALL, {
                            "tool": tc.name, "args": tc.input
                        })

                        # Policy gate
                        result = self.policy.execute_with_policy(
                            tc.name,
                            tc.input,
                            execute_tool,
                            confirm_fn=self._confirm if interactive else None,
                        )

                        if result.startswith("[BLOCKED"):
                            tool_errors_this_turn.append(result)

                        tool_history.append({
                            "tool": tc.name,
                            "result_summary": str(result)[:100],
                        })

                        print(f"  [{tc.name}] → {str(result)[:120]}")

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tc.id,
                            "content": result,
                        })

                        # AFM: record tool result
                        self.afm.add_item(ItemType.TOOL_RESULT, {
                            "role": "user",
                            "content": tool_results[-1:],
                        })

                        self.episodic.append({
                            "type": "tool_call",
                            "tool": tc.name,
                            "result_length": len(str(result)),
                        })

                    messages.append({"role": "user", "content": tool_results})

                # VIGIL: record turn and check for failures
                self.vigil.record_turn(
                    tool_calls=tool_calls_this_turn,
                    tool_errors=tool_errors_this_turn,
                    text_response=response.text,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    goal=task,
                )

                failure = self.vigil.check()
                if failure:
                    messages, should_continue = self.vigil.handle_failure(
                        failure, messages,
                        confirm_fn=self._confirm if interactive else None,
                    )
                    if not should_continue:
                        print("[Runtime] Aborting — unrecoverable failure.")
                        break

        except KeyboardInterrupt:
            print("\n[Runtime] Interrupted by user.")
        except Exception as e:
            print(f"[Runtime] Error: {e}")
            raise
        finally:
            # Always save trace
            self.tracer.end_trace(success=success, output=result_text)

            # RL: update reward
            trace_path = f".agent/traces/{trace_id}.json"
            if Path(trace_path).exists():
                self.rl.record_outcome(task, trace_path)

        return result_text

    def _build_system(self, task: str, arm_name: str) -> str:
        system = BASE_SYSTEM

        # Add working directory context
        import os
        system += f"\n\nWorking directory: {os.path.abspath(self.working_dir)}"

        # Add RL strategy suffix
        strategy = ARM_MAP.get(arm_name)
        if strategy:
            system += strategy.system_prompt_suffix

        # Add semantic memory
        facts = self.semantic.get_all()
        if facts:
            lines = ["\n## What I know about this project:"]
            for k, v in facts.items():
                lines.append(f"- {k}: {v}")
            system += "\n".join(lines)

        # Add IL demonstrations
        il_state = self.il_policy.build_state(task, [])
        demos = self.il_policy.get_demonstrations_prompt(il_state)
        if demos:
            system += "\n\n" + demos

        return system

    def _confirm(self, reason: str) -> bool:
        print(f"\n[Policy] {reason}")
        return input("Proceed? [y/N] ").strip().lower() == "y"
```

### Step 2: CLI entry point

Create `agent_cli.py`:

```python
#!/usr/bin/env python3
"""
The full runtime CLI.

Usage:
    # Single task
    python agent_cli.py "add docstrings to auth.py"

    # Interactive chat mode
    python agent_cli.py --chat

    # Use a different provider
    python agent_cli.py --provider openai --model gpt-4o "explain this codebase"

    # Use a local model
    python agent_cli.py --provider ollama --model qwen2.5-coder "run the tests"

    # View traces
    python -m tracer metrics
    python -m tracer view <trace_id>

    # View audit log
    cat .agent/audit.jsonl | python -m json.tool | less

    # RL report
    python -m rl report

    # Rebuild RAG index
    python index_codebase.py src/**/*.py

    # Rebuild IL demo index
    python build_demo_index.py
"""
import argparse
import sys
from runtime.agent_runtime import AgentRuntime


def main():
    parser = argparse.ArgumentParser(description="Agent Runtime CLI")
    parser.add_argument("task", nargs="?", help="Task to run")
    parser.add_argument("--chat", action="store_true", help="Interactive chat mode")
    parser.add_argument("--provider", default="anthropic",
                        choices=["anthropic", "openai", "ollama"])
    parser.add_argument("--model", default=None)
    parser.add_argument("--dir", default=".", help="Working directory")
    parser.add_argument("--no-confirm", action="store_true",
                        help="Skip all confirmation prompts")
    args = parser.parse_args()

    runtime = AgentRuntime(
        provider_name=args.provider,
        model=args.model,
        working_dir=args.dir,
    )

    if args.chat:
        print(f"Agent Runtime ready. Provider: {args.provider}")
        print("Type 'exit' to quit, 'report' for RL stats.\n")

        while True:
            try:
                user_input = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if user_input.lower() in ("exit", "quit"):
                break
            elif user_input.lower() == "report":
                runtime.rl.report()
                continue
            elif not user_input:
                continue

            runtime.run(user_input, interactive=not args.no_confirm)

    elif args.task:
        runtime.run(args.task, interactive=not args.no_confirm)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
```

### Step 3: Project structure

Your final repo layout should look like:

```
agent-runtime/
├── agent_cli.py              ← entry point
├── index_codebase.py         ← build RAG index
├── build_demo_index.py       ← build IL index
│
├── runtime/
│   └── agent_runtime.py      ← integration layer (this project)
│
├── providers/                ← Project 4
│   ├── base.py
│   ├── anthropic_provider.py
│   ├── openai_provider.py
│   ├── ollama_provider.py
│   └── traced.py             ← TracedProvider wrapper
│
├── tracer/                   ← Project 5
│   └── __init__.py
│
├── memory/                   ← Project 3
│   ├── episodic.py
│   └── semantic.py
│
├── rag/                      ← Project 6
│   ├── types.py
│   ├── chunker.py
│   ├── embedder.py
│   ├── vector_store.py
│   ├── indexer.py
│   └── retriever.py
│
├── policy/                   ← Project 7
│   ├── types.py
│   ├── rules.py
│   └── engine.py
│
├── afm/                      ← Project 8
│   ├── types.py
│   ├── scorer.py
│   ├── compressor.py
│   └── manager.py
│
├── vigil/                    ← Project 9
│   ├── types.py
│   ├── detectors.py
│   ├── recovery.py
│   └── monitor.py
│
├── il/                       ← Project 10
│   ├── extractor.py
│   ├── index.py
│   └── policy.py
│
├── rl/                       ← Project 11
│   ├── reward.py
│   ├── bandit.py
│   ├── strategies.py
│   └── runner.py
│
├── tools/                    ← Projects 1–2
│   └── __init__.py
│
├── .agent/                   ← runtime data (gitignore this)
│   ├── traces/               ← trace files
│   ├── rag/                  ← vector index
│   ├── il/                   ← demo index
│   ├── rl/                   ← bandit state
│   ├── audit.jsonl           ← policy audit log
│   ├── afm_log.jsonl         ← AFM drop log
│   ├── episodic.jsonl        ← memory log
│   └── semantic.json         ← semantic memory
│
├── _projects/                ← project READMEs
├── _references/              ← paper summaries
├── PLAN.md
└── README.md
```

### Step 4: Add `.gitignore`

```
.agent/
__pycache__/
*.pyc
.env
*.pkl
```

### Step 5: Smoke test

Run the full stack against a small task:

```bash
# 1. Index your own codebase
python index_codebase.py "**/*.py"

# 2. Run a task
python agent_cli.py "what does this codebase do? summarize the main modules"

# 3. Inspect the trace
python -m tracer metrics

# 4. Run a modification task
python agent_cli.py "add type hints to the Span class in tracer/__init__.py"

# 5. Check the audit log
cat .agent/audit.jsonl | tail -20

# 6. Run 10 tasks, then check RL stats
python -m rl report

# 7. Build IL index from accumulated traces
python build_demo_index.py

# 8. Run with a local model
python agent_cli.py --provider ollama --model qwen2.5-coder "list all Python files"
```

## Success Criteria

- [ ] `python agent_cli.py "describe this codebase"` runs end-to-end without errors
- [ ] Trace is written to `.agent/traces/` after each run
- [ ] Audit log is written to `.agent/audit.jsonl`
- [ ] VIGIL fires at least once during a deliberately loopy task
- [ ] AFM drops context items during a long multi-turn session
- [ ] RL bandit shows differentiated strategy preferences after 15+ runs
- [ ] IL policy injects relevant demonstrations into system prompt
- [ ] Works with at least two providers (e.g., Anthropic + Ollama)
- [ ] `--chat` mode supports 10+ turn conversations without crashing

## What You've Built

Looking back at Cruz's stack:

```
┌──────────────────────────────────────┐
│  Application Layer                   │  ← agent_cli.py
├──────────────────────────────────────┤
│  Orchestration Layer                 │  ← AgentRuntime
├──────────────────────────────────────┤
│  AI Runtime Infrastructure           │  ← AFM + VIGIL + Policy
│  (Cruz arXiv:2603.00495)            │
├──────────────────────────────────────┤
│  Model Serving Layer                 │  ← LLMProvider
├──────────────────────────────────────┤
│  External Tools                      │  ← tools/
└──────────────────────────────────────┘
          ↕ (passive, cross-cutting)
     Observability Layer               ← tracer/ + audit
```

And from Bin Xu's agent architecture, you've implemented:
- **π_θ** (policy): RL bandit + IL demonstrations
- **M** (memory): Episodic + Semantic + AFM for working memory
- **T** (tools): Full coding tool set with safety gates
- **V** (verifiers): VIGIL failure detection
- **E** (environment): Filesystem + shell + git

This is a real AI Runtime Infrastructure implementation. Not a toy. Not a framework wrapper. Built from first principles.

## Extensions to Explore

- **Multi-agent**: Spawn sub-agents for parallelizable subtasks (e.g., test generation + refactoring in parallel)
- **Streaming**: Switch to streaming API for faster perceived response time
- **Web interface**: Replace stdin/stdout with a simple FastAPI + WebSocket server
- **Fine-tuning**: Export training pairs from your best traces to fine-tune a local model via Ollama
- **Tree-of-Thought**: Give the agent a planning phase before execution (use VIGIL to detect when TOT helps)
- **Self-improvement**: Let the agent modify its own system prompt and measure the reward change
