# Project 9: VIGIL — Runtime Failure Detection & Recovery

## Prerequisites
Projects 1–8. You need the traced, policy-governed agent with AFM.

## What You Will Build

A reflective runtime layer that watches the agent's execution in real time, detects failure patterns (loops, goal drift, error cascades), and triggers recovery actions (retry with different strategy, rollback, escalate to human).

This implements the core concepts from Cruz's **VIGIL** framework (arXiv:2512.07094) — the second active intervention system in his AI Runtime Infrastructure stack.

## The Distinction From Observability

**Project 5 (observability):** Passively records what happened. Writes traces. Never intervenes.

**VIGIL:** Actively monitors execution. Detects problems. Intervenes to fix them.

```
Observability:  Agent does X → record X → done
VIGIL:          Agent does X → evaluate X → problem? → intervene → resume
```

Both run simultaneously. VIGIL uses the observability traces as input.

## Failure Patterns VIGIL Detects

### 1. Tool Call Loop
The agent calls the same tool with the same (or very similar) arguments multiple times without progress.

```
Turn 3: read_file("auth.py")
Turn 4: read_file("auth.py")   ← same file, still no write
Turn 5: read_file("auth.py")   ← loop detected
```

### 2. Goal Drift
The agent's actions diverge from the original user goal. Measured by comparing tool call types/targets against the stated goal.

```
Goal: "add docstrings to auth.py"
Turn 7: write_file("unrelated_module.py", ...)   ← drift
Turn 8: bash_exec("pip install something")        ← drift
```

### 3. Error Cascade
The agent encounters an error, tries to fix it, creates a new error, tries to fix that, and spirals.

```
Turn 4: bash_exec("pytest") → FAIL
Turn 5: write_file("fix.py") → write error
Turn 6: bash_exec("pytest") → FAIL (different error)
Turn 7: write_file("fix2.py") → FAIL
← error cascade: 4 consecutive failures
```

### 4. Stall
The agent produces text responses without calling any tools or making progress toward the goal for too many turns.

### 5. Token Burn
The agent is consuming tokens at an abnormal rate relative to progress.

## Recovery Actions

When a failure pattern is detected, VIGIL selects a recovery action:

| Pattern | Recovery |
|---------|---------|
| Tool loop | Inject hint: "You've read this file already. What's your next step?" |
| Goal drift | Inject goal reminder: "Remember, your task is: {original_goal}" |
| Error cascade | Roll back to last known good state; try different strategy |
| Stall | Force tool use: set `tool_choice = "any"` |
| Token burn | Escalate to human: "Agent is consuming excessive tokens. Abort?" |

## Architecture

```
Agent execution loop
    │
    │ after each turn
    ▼
┌────────────────────────────────┐
│          VIGIL Monitor         │
│                                │
│  analyze_turn(span, history)   │
│    → DetectionResult           │
│                                │
│  Detectors:                    │
│    LoopDetector                │
│    GoalDriftDetector           │
│    ErrorCascadeDetector        │
│    StallDetector               │
│    TokenBurnDetector           │
└────────────┬───────────────────┘
             │
       failure found?
       ┌─────┴─────┐
       │ yes       │ no
       ▼           ▼
  RecoveryEngine  continue
       │
       ├── INJECT: add message to context
       ├── ROLLBACK: restore checkpoint
       ├── RETRY: re-run turn with hint
       └── ESCALATE: ask human
```

## Build Guide

### Step 1: Types

Create `vigil/types.py`:

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class FailureType(Enum):
    TOOL_LOOP = "tool_loop"
    GOAL_DRIFT = "goal_drift"
    ERROR_CASCADE = "error_cascade"
    STALL = "stall"
    TOKEN_BURN = "token_burn"


class RecoveryAction(Enum):
    INJECT_HINT = "inject_hint"
    INJECT_GOAL = "inject_goal"
    ROLLBACK = "rollback"
    ESCALATE = "escalate"
    ABORT = "abort"


@dataclass
class DetectionResult:
    detected: bool
    failure_type: FailureType | None = None
    severity: float = 0.0   # 0.0 = none, 1.0 = critical
    evidence: str = ""
    recovery: RecoveryAction | None = None
    recovery_hint: str = ""


@dataclass
class AgentTurn:
    turn_number: int
    tool_calls: list[dict]   # [{"name": "read_file", "input": {...}}]
    tool_errors: list[str]
    text_response: str | None
    input_tokens: int
    output_tokens: int
    goal: str
```

### Step 2: Detectors

Create `vigil/detectors.py`:

```python
from .types import AgentTurn, DetectionResult, FailureType, RecoveryAction


class LoopDetector:
    """Detect repeated tool calls with same or similar arguments."""

    def __init__(self, window: int = 5, threshold: int = 3):
        self.window = window        # look back N turns
        self.threshold = threshold  # flag if same call seen this many times

    def detect(self, history: list[AgentTurn]) -> DetectionResult:
        recent = history[-self.window:]
        call_counts: dict[str, int] = {}

        for turn in recent:
            for call in turn.tool_calls:
                # Normalize key: tool name + first arg value
                key = call["name"]
                if call.get("input"):
                    first_val = str(list(call["input"].values())[0])[:50]
                    key += f":{first_val}"
                call_counts[key] = call_counts.get(key, 0) + 1

        max_repeats = max(call_counts.values()) if call_counts else 0
        if max_repeats >= self.threshold:
            repeated = [k for k, v in call_counts.items() if v >= self.threshold]
            return DetectionResult(
                detected=True,
                failure_type=FailureType.TOOL_LOOP,
                severity=min(1.0, max_repeats / 5),
                evidence=f"Tool called {max_repeats}x in last {self.window} turns: {repeated}",
                recovery=RecoveryAction.INJECT_HINT,
                recovery_hint=(
                    f"You've called these tools repeatedly without progress: {repeated}. "
                    "Try a different approach. What's blocking you?"
                )
            )

        return DetectionResult(detected=False)


class GoalDriftDetector:
    """Detect when agent actions diverge from the stated goal."""

    def __init__(self, drift_threshold: int = 3):
        self.drift_threshold = drift_threshold

    def _is_relevant_tool_call(self, call: dict, goal: str) -> bool:
        """Heuristic: does this tool call seem related to the goal?"""
        goal_lower = goal.lower()
        call_str = str(call).lower()

        # Extract goal keywords
        goal_words = set(goal_lower.split())
        relevant_words = {w for w in goal_words if len(w) > 4}  # ignore short words

        # Check if any relevant words appear in the tool call
        for word in relevant_words:
            if word in call_str:
                return True

        return False

    def detect(self, history: list[AgentTurn]) -> DetectionResult:
        if len(history) < 3:
            return DetectionResult(detected=False)

        recent = history[-5:]
        goal = history[-1].goal if history else ""
        if not goal:
            return DetectionResult(detected=False)

        drift_count = 0
        for turn in recent:
            for call in turn.tool_calls:
                if not self._is_relevant_tool_call(call, goal):
                    drift_count += 1

        if drift_count >= self.drift_threshold:
            return DetectionResult(
                detected=True,
                failure_type=FailureType.GOAL_DRIFT,
                severity=min(1.0, drift_count / 6),
                evidence=f"{drift_count} tool calls appear unrelated to goal: '{goal}'",
                recovery=RecoveryAction.INJECT_GOAL,
                recovery_hint=f"Remember your task: {goal}. Focus on that."
            )

        return DetectionResult(detected=False)


class ErrorCascadeDetector:
    """Detect consecutive errors suggesting a spiral."""

    def __init__(self, cascade_threshold: int = 3):
        self.cascade_threshold = cascade_threshold

    def detect(self, history: list[AgentTurn]) -> DetectionResult:
        if len(history) < self.cascade_threshold:
            return DetectionResult(detected=False)

        recent = history[-self.cascade_threshold:]
        consecutive_errors = sum(1 for t in recent if len(t.tool_errors) > 0)

        if consecutive_errors >= self.cascade_threshold:
            errors = [e for t in recent for e in t.tool_errors]
            return DetectionResult(
                detected=True,
                failure_type=FailureType.ERROR_CASCADE,
                severity=min(1.0, consecutive_errors / 4),
                evidence=f"{consecutive_errors} consecutive turns with errors: {errors[-3:]}",
                recovery=RecoveryAction.ROLLBACK,
                recovery_hint=(
                    "You're in an error spiral. Step back. Describe what you're trying "
                    "to accomplish and a different approach you could take."
                )
            )

        return DetectionResult(detected=False)


class StallDetector:
    """Detect agent producing text without making tool calls."""

    def __init__(self, stall_turns: int = 3):
        self.stall_turns = stall_turns

    def detect(self, history: list[AgentTurn]) -> DetectionResult:
        if len(history) < self.stall_turns:
            return DetectionResult(detected=False)

        recent = history[-self.stall_turns:]
        no_tool_turns = sum(1 for t in recent if len(t.tool_calls) == 0)

        if no_tool_turns >= self.stall_turns:
            return DetectionResult(
                detected=True,
                failure_type=FailureType.STALL,
                severity=0.5,
                evidence=f"No tool calls in last {self.stall_turns} turns",
                recovery=RecoveryAction.INJECT_HINT,
                recovery_hint="You haven't used any tools recently. What tool can move you forward?"
            )

        return DetectionResult(detected=False)


class TokenBurnDetector:
    """Detect abnormal token consumption."""

    def __init__(self, max_tokens_per_run: int = 50_000):
        self.max_tokens = max_tokens_per_run

    def detect(self, history: list[AgentTurn]) -> DetectionResult:
        total_tokens = sum(t.input_tokens + t.output_tokens for t in history)

        if total_tokens > self.max_tokens:
            return DetectionResult(
                detected=True,
                failure_type=FailureType.TOKEN_BURN,
                severity=min(1.0, total_tokens / (self.max_tokens * 2)),
                evidence=f"Total tokens: {total_tokens:,} (limit: {self.max_tokens:,})",
                recovery=RecoveryAction.ESCALATE,
                recovery_hint=f"Agent has consumed {total_tokens:,} tokens. Consider aborting."
            )

        return DetectionResult(detected=False)
```

### Step 3: Recovery engine

Create `vigil/recovery.py`:

```python
from .types import DetectionResult, RecoveryAction, AgentTurn
import copy


class Checkpoint:
    def __init__(self, messages: list[dict], turn: int):
        self.messages = copy.deepcopy(messages)
        self.turn = turn


class RecoveryEngine:
    def __init__(self, max_recovery_attempts: int = 3):
        self.max_recovery_attempts = max_recovery_attempts
        self.checkpoints: list[Checkpoint] = []
        self.recovery_count: int = 0

    def save_checkpoint(self, messages: list[dict], turn: int):
        """Save a checkpoint every N turns for rollback."""
        self.checkpoints.append(Checkpoint(messages, turn))
        # Keep only last 5 checkpoints
        self.checkpoints = self.checkpoints[-5:]

    def recover(
        self,
        result: DetectionResult,
        messages: list[dict],
        confirm_fn=None,
    ) -> tuple[list[dict], bool]:
        """
        Apply recovery action. Returns (new_messages, should_continue).
        """
        self.recovery_count += 1

        if self.recovery_count > self.max_recovery_attempts:
            print(f"[VIGIL] Max recovery attempts reached. Escalating.")
            return messages, False

        print(f"[VIGIL] {result.failure_type.value} detected (severity={result.severity:.2f})")
        print(f"[VIGIL] Evidence: {result.evidence}")

        action = result.recovery

        if action == RecoveryAction.INJECT_HINT:
            messages = messages + [{
                "role": "user",
                "content": f"[VIGIL System Note] {result.recovery_hint}"
            }]
            print(f"[VIGIL] Injected hint into context")
            return messages, True

        elif action == RecoveryAction.INJECT_GOAL:
            messages = messages + [{
                "role": "user",
                "content": f"[VIGIL System Note] {result.recovery_hint}"
            }]
            return messages, True

        elif action == RecoveryAction.ROLLBACK:
            if self.checkpoints:
                ckpt = self.checkpoints[-1]
                print(f"[VIGIL] Rolling back to turn {ckpt.turn}")
                messages = ckpt.messages + [{
                    "role": "user",
                    "content": f"[VIGIL Recovery] {result.recovery_hint}"
                }]
                return messages, True
            else:
                print(f"[VIGIL] No checkpoint available, injecting hint instead")
                messages = messages + [{
                    "role": "user",
                    "content": f"[VIGIL Recovery] {result.recovery_hint}"
                }]
                return messages, True

        elif action == RecoveryAction.ESCALATE:
            print(f"\n[VIGIL ESCALATION] {result.evidence}")
            if confirm_fn:
                should_continue = confirm_fn(f"VIGIL detected {result.failure_type.value}. Continue?")
                return messages, should_continue
            return messages, False

        elif action == RecoveryAction.ABORT:
            return messages, False

        return messages, True
```

### Step 4: VIGIL Monitor

Create `vigil/monitor.py`:

```python
from .types import AgentTurn, DetectionResult
from .detectors import (
    LoopDetector, GoalDriftDetector, ErrorCascadeDetector,
    StallDetector, TokenBurnDetector
)
from .recovery import RecoveryEngine


class VIGILMonitor:
    def __init__(
        self,
        max_tokens_per_run: int = 50_000,
        max_recovery_attempts: int = 3,
        checkpoint_interval: int = 5,
    ):
        self.detectors = [
            LoopDetector(window=5, threshold=3),
            GoalDriftDetector(drift_threshold=3),
            ErrorCascadeDetector(cascade_threshold=3),
            StallDetector(stall_turns=3),
            TokenBurnDetector(max_tokens_per_run=max_tokens_per_run),
        ]
        self.recovery = RecoveryEngine(max_recovery_attempts)
        self.history: list[AgentTurn] = []
        self.checkpoint_interval = checkpoint_interval
        self.turn_count = 0

    def record_turn(
        self,
        tool_calls: list[dict],
        tool_errors: list[str],
        text_response: str | None,
        input_tokens: int,
        output_tokens: int,
        goal: str,
    ) -> AgentTurn:
        turn = AgentTurn(
            turn_number=self.turn_count,
            tool_calls=tool_calls,
            tool_errors=tool_errors,
            text_response=text_response,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            goal=goal,
        )
        self.history.append(turn)
        self.turn_count += 1
        return turn

    def check(self) -> DetectionResult | None:
        """Run all detectors. Return first failure found (highest severity)."""
        results = []
        for detector in self.detectors:
            result = detector.detect(self.history)
            if result.detected:
                results.append(result)

        if not results:
            return None

        # Return the most severe failure
        return max(results, key=lambda r: r.severity)

    def maybe_checkpoint(self, messages: list[dict]):
        """Save a checkpoint every N turns."""
        if self.turn_count % self.checkpoint_interval == 0:
            self.recovery.save_checkpoint(messages, self.turn_count)

    def handle_failure(
        self,
        result: DetectionResult,
        messages: list[dict],
        confirm_fn=None,
    ) -> tuple[list[dict], bool]:
        return self.recovery.recover(result, messages, confirm_fn)
```

### Step 5: Integrate into your agent loop

```python
from vigil.monitor import VIGILMonitor

vigil = VIGILMonitor(max_tokens_per_run=50_000)
goal = user_message

while True:
    vigil.maybe_checkpoint(messages)

    response = provider.complete(messages=messages, tools=TOOLS, system=system)

    tool_calls_made = []
    tool_errors = []

    if response.tool_calls:
        for tc in response.tool_calls:
            tool_calls_made.append({"name": tc.name, "input": tc.input})
            try:
                result = policy.execute_with_policy(tc.name, tc.input, execute_tool)
                if result.startswith("[BLOCKED"):
                    tool_errors.append(result)
            except Exception as e:
                tool_errors.append(str(e))

    # Record this turn with VIGIL
    vigil.record_turn(
        tool_calls=tool_calls_made,
        tool_errors=tool_errors,
        text_response=response.text,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        goal=goal,
    )

    # Check for failures
    failure = vigil.check()
    if failure:
        messages, should_continue = vigil.handle_failure(
            failure, messages, confirm_fn=confirm_with_user
        )
        if not should_continue:
            print("[VIGIL] Aborting due to unrecoverable failure.")
            break
        continue  # Re-run the turn with modified context

    if response.stop_reason == "end_turn":
        break
```

## Success Criteria

- [ ] Agent that calls `read_file` 4 times in a row gets a VIGIL hint and changes approach
- [ ] Agent that drifts off-task gets a goal reminder injected
- [ ] Agent in an error spiral gets rolled back to the last checkpoint
- [ ] Token burn limit triggers escalation to human
- [ ] VIGIL interventions appear in the trace from Project 5
- [ ] Agent recovers successfully from at least 2 failure types in a live test

## What's Missing

| Gap | Fixed in |
|-----|---------|
| Goal drift detection is keyword-based | Use embeddings (Project 6) for semantic drift detection |
| Rollback only restores messages, not file state | Snapshot filesystem using git stash before each turn |
| No ML-based failure prediction | Project 11 (RL): train a failure predictor from traces |
| VIGIL runs after the fact, not mid-generation | True streaming intervention requires token-level hooks |
