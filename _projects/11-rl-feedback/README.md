# Project 11: RL Feedback Loop

## Prerequisites
Projects 1–10. You need traces, IL policy, VIGIL monitoring, and observability.

## What You Will Build

A reinforcement learning feedback loop that improves the agent's behavior over time by measuring outcomes and using reward signals to update its decision-making. No GPU required — we implement this as **reward-guided prompt optimization**: learning which system prompt variants, tool orderings, and strategy patterns lead to better outcomes.

This implements the **RL section** of Bin Xu's learning stack: "reward signals from environment feedback for long-horizon optimization."

## Concepts

### What Is the Reward?

In coding tasks, reward signals are concrete and measurable:

| Signal | How to Measure | Weight |
|--------|---------------|--------|
| Tests pass | `pytest` exit code == 0 | High |
| Task completed | Agent's `end_turn` with success | Medium |
| User accepts response | Explicit approval / no correction | Medium |
| Token efficiency | Fewer tokens for same outcome | Low |
| Loop rate | Lower is better | Low |
| Latency | Faster is better | Low |

```python
reward = (
    1.0 * test_pass +
    0.5 * task_completed +
    0.3 * user_accepted -
    0.1 * (total_tokens / 10_000) -
    0.2 * loop_rate
)
```

### Policy Representation

We're not doing gradient descent on LLM weights. Instead, we optimize **discrete choices** that the runtime controls:

1. **System prompt variants** — which prompt phrasing leads to better outcomes?
2. **Tool ordering** — should we always read before writing, or is it task-dependent?
3. **Model selection** — for this task type, which model performs best?
4. **AFM parameters** — what relevance threshold works best for this task category?

These are bandit problems — multi-armed bandits with context (contextual bandits).

### Contextual Multi-Armed Bandit

At each decision point, we choose an "arm" (a strategy) based on context (the current task type). We update arm values based on observed rewards.

```
Task type: "add tests"
  Arm 1: "read file first" policy → avg reward: 0.82
  Arm 2: "search first"    policy → avg reward: 0.61
  Arm 3: "write directly"  policy → avg reward: 0.34

At runtime: choose arm 1 (highest avg reward for this task type)
```

We use **Upper Confidence Bound (UCB)** to balance exploitation vs exploration:

```
UCB_score = avg_reward + C * sqrt(ln(total_plays) / arm_plays)
```

This automatically explores under-tried arms while exploiting known good ones.

### Reward Attribution

One key challenge: in a 10-turn agent run, which turn's decisions deserve credit for the final reward? We use **discounted reward attribution**:

- Final turn (caused end_turn): 100% of reward
- Second-to-last: 90%
- Third-to-last: 81%
- ...

```python
discount = 0.9
turn_reward[t] = final_reward * (discount ** (total_turns - t))
```

## Architecture

```
Agent Run
    │
    │ produces trace
    ▼
┌──────────────────────┐
│   RewardEvaluator    │
│                      │
│  eval_trace(trace)   │
│  → RewardSignal      │
└────────┬─────────────┘
         │
         ▼
┌──────────────────────┐
│   PolicyOptimizer    │
│   (ContextualBandit) │
│                      │
│  update(context,     │
│         arm,         │
│         reward)      │
│                      │
│  select_arm(context) │
└────────┬─────────────┘
         │ arm selection
         ▼
┌──────────────────────┐
│   Agent Runtime      │
│                      │
│  uses selected arm   │
│  (prompt, strategy,  │
│   model, params)     │
└──────────────────────┘
```

## Build Guide

### Step 1: Reward evaluator

Create `rl/reward.py`:

```python
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RewardSignal:
    total_reward: float
    components: dict   # {"test_pass": 1.0, "token_eff": -0.12, ...}
    trace_id: str


def eval_test_outcome(working_dir: str = ".") -> float:
    """Run pytest and return 1.0 if passes, 0.0 if fails."""
    result = subprocess.run(
        ["python", "-m", "pytest", "--tb=no", "-q"],
        capture_output=True,
        text=True,
        cwd=working_dir,
        timeout=60,
    )
    return 1.0 if result.returncode == 0 else 0.0


def eval_trace(trace_path: str, working_dir: str = ".") -> RewardSignal:
    """
    Compute reward from a trace file + optionally running tests.
    """
    spans = json.loads(Path(trace_path).read_text())
    trace_span = spans[0]
    trace_id = trace_span.get("trace_id", "")

    # Component 1: success flag from trace
    success = float(trace_span.get("output", {}).get("success", False))

    # Component 2: token efficiency
    model_calls = [s for s in spans if s["type"] == "model_call"]
    total_tokens = sum(
        s["metadata"].get("input_tokens", 0) + s["metadata"].get("output_tokens", 0)
        for s in model_calls
    )
    token_efficiency = max(0.0, 1.0 - total_tokens / 20_000)

    # Component 3: loop rate (lower is better)
    tool_calls = [s for s in spans if s["type"] == "tool_call"]
    tool_names = [s["input"].get("tool", "") for s in tool_calls]
    unique_ratio = len(set(tool_names)) / max(1, len(tool_names))
    loop_penalty = 1.0 - unique_ratio

    # Component 4: latency (normalized)
    total_ms = None
    if trace_span.get("ended_at") and trace_span.get("started_at"):
        total_ms = (trace_span["ended_at"] - trace_span["started_at"]) * 1000
    latency_score = max(0.0, 1.0 - (total_ms or 0) / 60_000)  # normalize to 60s

    components = {
        "success": success,
        "token_efficiency": token_efficiency,
        "loop_penalty": -loop_penalty,
        "latency": latency_score,
    }

    total = (
        1.0 * success +
        0.2 * token_efficiency +
        -0.2 * loop_penalty +
        0.1 * latency_score
    )

    return RewardSignal(
        total_reward=round(total, 3),
        components=components,
        trace_id=trace_id,
    )
```

### Step 2: Contextual bandit

Create `rl/bandit.py`:

```python
import json
import math
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class ArmStats:
    name: str
    total_reward: float = 0.0
    plays: int = 0

    @property
    def avg_reward(self) -> float:
        return self.total_reward / max(1, self.plays)

    def ucb_score(self, total_plays: int, c: float = 1.41) -> float:
        if self.plays == 0:
            return float("inf")  # Explore untried arms first
        return self.avg_reward + c * math.sqrt(math.log(total_plays) / self.plays)


class ContextualBandit:
    """
    Multi-armed bandit with context (task type).
    Context is a string key; arms are strategy names.
    """
    def __init__(self, arms: list[str], store_path: str = ".agent/rl/bandit.json"):
        self.arm_names = arms
        self.store_path = Path(store_path)
        # {context: {arm_name: ArmStats}}
        self.stats: dict[str, dict[str, ArmStats]] = {}
        self.load()

    def _get_or_init(self, context: str) -> dict[str, ArmStats]:
        if context not in self.stats:
            self.stats[context] = {
                name: ArmStats(name=name) for name in self.arm_names
            }
        return self.stats[context]

    def select_arm(self, context: str) -> str:
        """UCB arm selection."""
        arms = self._get_or_init(context)
        total_plays = sum(a.plays for a in arms.values())
        best_arm = max(arms.values(), key=lambda a: a.ucb_score(total_plays))
        return best_arm.name

    def update(self, context: str, arm: str, reward: float):
        """Update arm statistics after observing a reward."""
        arms = self._get_or_init(context)
        if arm in arms:
            arms[arm].total_reward += reward
            arms[arm].plays += 1
        self.save()

    def stats_report(self) -> str:
        lines = ["=== Bandit Stats ==="]
        for context, arms in sorted(self.stats.items()):
            lines.append(f"\nContext: {context}")
            for arm_name, stats in sorted(arms.items(), key=lambda x: -x[1].avg_reward):
                lines.append(
                    f"  {arm_name:30s} avg={stats.avg_reward:.3f} "
                    f"plays={stats.plays}"
                )
        return "\n".join(lines)

    def save(self):
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        for ctx, arms in self.stats.items():
            data[ctx] = {
                name: {"total_reward": s.total_reward, "plays": s.plays}
                for name, s in arms.items()
            }
        self.store_path.write_text(json.dumps(data, indent=2))

    def load(self):
        if not self.store_path.exists():
            return
        data = json.loads(self.store_path.read_text())
        for ctx, arms in data.items():
            self.stats[ctx] = {}
            for name, stats in arms.items():
                self.stats[ctx][name] = ArmStats(
                    name=name,
                    total_reward=stats["total_reward"],
                    plays=stats["plays"],
                )
```

### Step 3: Strategy arms

Define the arms — the choices we're optimizing. Create `rl/strategies.py`:

```python
from dataclasses import dataclass


@dataclass
class StrategyArm:
    name: str
    system_prompt_suffix: str
    afm_keep_threshold: float = 0.65
    preferred_first_tool: str | None = None


# Define strategy variants to explore
STRATEGY_ARMS = [
    StrategyArm(
        name="read_first",
        system_prompt_suffix=(
            "\nAlways read relevant files before making changes. "
            "Start with read_file or search_in_files."
        ),
        preferred_first_tool="read_file",
    ),
    StrategyArm(
        name="search_first",
        system_prompt_suffix=(
            "\nStart by searching the codebase to understand the scope before reading individual files."
        ),
        preferred_first_tool="retrieve_code",
    ),
    StrategyArm(
        name="direct_edit",
        system_prompt_suffix=(
            "\nBe decisive. If the task is clear, make changes directly. "
            "Don't over-read before acting."
        ),
        preferred_first_tool=None,
    ),
    StrategyArm(
        name="test_driven",
        system_prompt_suffix=(
            "\nRun tests early and often. Check what's failing before making changes. "
            "Use bash_exec('pytest') to verify progress."
        ),
        preferred_first_tool="bash_exec",
    ),
]

ARM_NAMES = [a.name for a in STRATEGY_ARMS]
ARM_MAP = {a.name: a for a in STRATEGY_ARMS}


def classify_task(task: str) -> str:
    """
    Classify task into a context bucket for the bandit.
    Simple keyword-based classification.
    """
    task_lower = task.lower()

    if any(w in task_lower for w in ["test", "pytest", "spec", "assert"]):
        return "testing"
    elif any(w in task_lower for w in ["fix", "bug", "error", "broken", "fail"]):
        return "bugfix"
    elif any(w in task_lower for w in ["add", "implement", "create", "build", "write"]):
        return "feature"
    elif any(w in task_lower for w in ["refactor", "rename", "move", "clean"]):
        return "refactor"
    elif any(w in task_lower for w in ["explain", "describe", "what", "how", "why"]):
        return "explain"
    else:
        return "general"
```

### Step 4: RL-aware agent runner

Create `rl/runner.py`:

```python
from .bandit import ContextualBandit
from .strategies import classify_task, ARM_MAP, ARM_NAMES
from .reward import eval_trace, RewardSignal
from pathlib import Path


class RLRunner:
    """
    Wraps the agent with a bandit-guided strategy selector.
    After each run, evaluates the reward and updates the bandit.
    """
    def __init__(self):
        self.bandit = ContextualBandit(arms=ARM_NAMES)

    def select_strategy(self, task: str) -> str:
        context = classify_task(task)
        arm_name = self.bandit.select_arm(context)
        return arm_name

    def get_system_prompt_addition(self, arm_name: str) -> str:
        arm = ARM_MAP.get(arm_name)
        return arm.system_prompt_suffix if arm else ""

    def record_outcome(self, task: str, trace_path: str):
        """After a run completes, evaluate and update the bandit."""
        context = classify_task(task)

        # We need to know which arm was used — store it in the trace metadata
        spans = __import__("json").loads(Path(trace_path).read_text())
        arm_name = spans[0].get("metadata", {}).get("strategy_arm", "read_first")

        reward = eval_trace(trace_path)
        self.bandit.update(context, arm_name, reward.total_reward)

        print(f"[RL] Task type: {context}, Arm: {arm_name}, Reward: {reward.total_reward:.3f}")
        print(f"[RL] Components: {reward.components}")

    def report(self):
        print(self.bandit.stats_report())
```

### Step 5: Integrate into the main agent loop

```python
from rl.runner import RLRunner
from tracer import Tracer  # from Project 5

rl = RLRunner()

def run_task(task: str):
    # 1. Select strategy
    arm_name = rl.select_strategy(task)
    strategy = ARM_MAP[arm_name]
    print(f"[RL] Selected strategy: {arm_name}")

    # 2. Start trace (with strategy metadata)
    tracer = Tracer()
    trace_id = tracer.start_trace(task, metadata={"strategy_arm": arm_name})

    # 3. Build system prompt with strategy suffix
    system = BASE_SYSTEM + strategy.system_prompt_suffix

    # 4. Run agent (Projects 2–9 integration)
    try:
        result = run_agent(task, system=system, tracer=tracer)
        success = True
    except Exception as e:
        result = str(e)
        success = False

    # 5. Save trace
    tracer.end_trace(success=success, output=result)

    # 6. Update bandit with reward
    trace_path = f".agent/traces/{trace_id}.json"
    rl.record_outcome(task, trace_path)

    return result
```

### Step 6: Analysis CLI

```python
# python -m rl report
# python -m rl eval <trace_id>

import sys
from rl.runner import RLRunner
from rl.reward import eval_trace
from pathlib import Path

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"

    if cmd == "report":
        rl = RLRunner()
        rl.report()

    elif cmd == "eval" and len(sys.argv) > 2:
        trace_id = sys.argv[2]
        path = f".agent/traces/{trace_id}.json"
        reward = eval_trace(path)
        print(f"Trace: {reward.trace_id}")
        print(f"Total reward: {reward.total_reward:.3f}")
        for k, v in reward.components.items():
            print(f"  {k}: {v:.3f}")

    elif cmd == "replay":
        # Replay all traces and rebuild bandit from scratch
        rl = RLRunner()
        rl.bandit.stats = {}
        for path in sorted(Path(".agent/traces").glob("*.json")):
            import json
            spans = json.loads(path.read_text())
            task = spans[0].get("input", {}).get("task", "")
            rl.record_outcome(task, str(path))
        rl.report()
```

## Success Criteria

- [ ] Reward evaluator produces a score for every trace in `.agent/traces/`
- [ ] Bandit selects an arm for each task type
- [ ] After 20+ runs, the bandit shows differentiated arm preferences per task type
- [ ] `python -m rl report` shows meaningful strategy stats
- [ ] At least one task type converges to a clearly preferred strategy (avg reward gap > 0.2)
- [ ] `python -m rl replay` reconstructs bandit state from all historical traces

## What's Missing

| Gap | Fixed in |
|-----|---------|
| Strategy arms are hand-designed | Auto-discover strategy patterns from traces using clustering |
| No confidence intervals on arm stats | Thompson sampling gives Bayesian uncertainty |
| Reward is computed offline | Online reward (tests run in real-time) gives faster feedback |
| No fine-tuning | True RL on LLM weights (RLHF) requires project-specific GPU infra |
| Task classification is keyword-based | Use embeddings (Project 6) for task clustering |
