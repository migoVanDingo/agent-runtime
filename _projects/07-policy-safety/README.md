# Project 7: Policy & Safety Layer

## Prerequisites
Projects 1–6. You should have a working agent with RAG and provider abstraction.

## What You Will Build

A formal policy layer that governs what the agent is allowed to do. Every tool call passes through a policy engine before execution. The engine checks permission tiers, allowlists, irreversibility ratings, and rate limits — then either allows, blocks, or escalates to the human.

This formalizes the crude `SafetyGate` from Project 2 into a real **policy system** — one of the core components of Cruz's AI Runtime Infrastructure.

## Concepts

### Why a Formal Policy?

Project 2's `SafetyGate` was hard-coded: "always confirm bash, confirm write if file exists." That breaks immediately in real use:

- You want bash to run `pytest` without asking but not `rm -rf`
- You want file writes in `/tmp` to be silent but writes to production config to require approval
- You want to enforce rate limits so a looping agent doesn't make 1000 API calls

A policy engine separates **what the agent can do** from **how the agent does it**. Rules live in one place and are easy to audit, change, and test.

### Permission Tiers

```
TIER 0 — Deny: never allowed
TIER 1 — Allow silently: safe reads, list ops
TIER 2 — Allow with logging: writes to temp dirs, git reads
TIER 3 — Allow with confirmation: writes to tracked files, any git mutation
TIER 4 — Require explicit unlock: destructive ops, network calls, secrets access
```

### Irreversibility Rating

Each tool action has a reversibility score:
- **Reversible**: read_file, list_files, git_status → always tier 1
- **Low risk**: write to new file → tier 2
- **Medium risk**: overwrite existing file → tier 3
- **High risk**: delete file, run arbitrary bash → tier 3–4
- **Catastrophic**: push to remote, drop database → tier 4

### Audit Log

Every decision the policy engine makes (allow, block, escalate) gets written to an audit log. This is separate from the observability traces — it records *governance* decisions, not performance metrics.

```
2026-03-15T10:23:01Z ALLOW  bash_exec "pytest tests/" [tier=1, rule=test_runner_allowlist]
2026-03-15T10:23:05Z ALLOW  write_file "src/auth.py" [tier=3, confirmed=true]
2026-03-15T10:23:09Z BLOCK  bash_exec "rm -rf /" [tier=0, rule=destructive_command]
2026-03-15T10:23:12Z ESCALATE bash_exec "git push" [tier=4, waiting_for_approval]
```

## Architecture

```
Agent
  │
  │ wants to call tool
  ▼
┌─────────────────────────────┐
│       PolicyEngine          │
│                             │
│  evaluate(tool, input)      │
│    → check rules in order:  │
│      1. hardcoded denials   │
│      2. allowlists          │
│      3. tier classification │
│      4. rate limits         │
│    → return Decision        │
└────────────┬────────────────┘
             │
      ┌──────┴──────┐
      ▼             ▼
  ALLOW          ESCALATE ──→ AuditLog
      │             │
      ▼             ▼
  execute      ask human
      │             │
      ▼             ▼
  AuditLog     AuditLog
```

## Build Guide

### Step 1: Decision types

Create `policy/types.py`:

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class Decision(Enum):
    ALLOW = "allow"
    BLOCK = "block"
    ESCALATE = "escalate"   # allow if human confirms


@dataclass
class PolicyDecision:
    decision: Decision
    reason: str
    rule_name: str
    tier: int = 0

    @property
    def is_allowed(self) -> bool:
        return self.decision == Decision.ALLOW

    @property
    def needs_confirmation(self) -> bool:
        return self.decision == Decision.ESCALATE


@dataclass
class PolicyRule:
    name: str
    description: str
    evaluate: Callable[[str, dict], PolicyDecision | None]
    # Return None to pass to next rule, or a PolicyDecision to short-circuit
```

### Step 2: Built-in rules

Create `policy/rules.py`:

```python
import re
import os
from .types import PolicyDecision, Decision, PolicyRule


def make_destructive_command_rule() -> PolicyRule:
    """Block obviously destructive shell commands."""
    DESTRUCTIVE_PATTERNS = [
        r"\brm\s+-rf\b",
        r"\bdd\b.*of=/dev",
        r"\bmkfs\b",
        r"\bformat\b",
        r">\s*/dev/sd",
        r"\bchmod\s+000\b",
        r"\bshutdown\b",
        r"\breboot\b",
    ]

    def evaluate(tool_name: str, tool_input: dict) -> PolicyDecision | None:
        if tool_name != "bash_exec":
            return None
        cmd = tool_input.get("command", "")
        for pattern in DESTRUCTIVE_PATTERNS:
            if re.search(pattern, cmd):
                return PolicyDecision(
                    decision=Decision.BLOCK,
                    reason=f"Matches destructive command pattern: {pattern}",
                    rule_name="destructive_command",
                    tier=0,
                )
        return None

    return PolicyRule(
        name="destructive_command",
        description="Block dangerous shell commands",
        evaluate=evaluate,
    )


def make_safe_reads_rule() -> PolicyRule:
    """Silently allow read-only operations."""
    SAFE_TOOLS = {"read_file", "list_files", "search_in_files",
                  "git_status", "git_diff", "retrieve_code"}

    def evaluate(tool_name: str, tool_input: dict) -> PolicyDecision | None:
        if tool_name in SAFE_TOOLS:
            return PolicyDecision(
                decision=Decision.ALLOW,
                reason="Read-only operation",
                rule_name="safe_reads",
                tier=1,
            )
        return None

    return PolicyRule(
        name="safe_reads",
        description="Silently allow read-only tools",
        evaluate=evaluate,
    )


def make_bash_allowlist_rule(allowed_patterns: list[str]) -> PolicyRule:
    """Allow specific bash command patterns without confirmation."""
    compiled = [re.compile(p) for p in allowed_patterns]

    def evaluate(tool_name: str, tool_input: dict) -> PolicyDecision | None:
        if tool_name != "bash_exec":
            return None
        cmd = tool_input.get("command", "")
        for pattern in compiled:
            if pattern.match(cmd):
                return PolicyDecision(
                    decision=Decision.ALLOW,
                    reason=f"Matches allowlist pattern: {pattern.pattern}",
                    rule_name="bash_allowlist",
                    tier=2,
                )
        return None

    return PolicyRule(
        name="bash_allowlist",
        description="Allow specific bash commands without confirmation",
        evaluate=evaluate,
    )


def make_file_write_rule(auto_approve_dirs: list[str] | None = None) -> PolicyRule:
    """
    File writes in auto-approved dirs are tier 2.
    All other file writes escalate (require confirmation).
    """
    approved = [os.path.abspath(d) for d in (auto_approve_dirs or ["/tmp"])]

    def evaluate(tool_name: str, tool_input: dict) -> PolicyDecision | None:
        if tool_name != "write_file":
            return None
        path = os.path.abspath(tool_input.get("path", ""))
        for approved_dir in approved:
            if path.startswith(approved_dir):
                return PolicyDecision(
                    decision=Decision.ALLOW,
                    reason=f"Write to approved directory: {approved_dir}",
                    rule_name="file_write",
                    tier=2,
                )
        action = "overwrite" if os.path.exists(path) else "create"
        return PolicyDecision(
            decision=Decision.ESCALATE,
            reason=f"Will {action} file: {path}",
            rule_name="file_write",
            tier=3,
        )

    return PolicyRule(
        name="file_write",
        description="Escalate file writes outside approved dirs",
        evaluate=evaluate,
    )


def make_git_mutation_rule() -> PolicyRule:
    """Escalate any git command that mutates state."""
    MUTATION_PATTERNS = [r"git\s+(push|commit|reset|rebase|merge|checkout\s+-[bB]|branch\s+-[dD])"]

    def evaluate(tool_name: str, tool_input: dict) -> PolicyDecision | None:
        if tool_name != "bash_exec":
            return None
        cmd = tool_input.get("command", "")
        for pattern in MUTATION_PATTERNS:
            if re.search(pattern, cmd):
                return PolicyDecision(
                    decision=Decision.ESCALATE,
                    reason=f"Git mutation command: {cmd}",
                    rule_name="git_mutation",
                    tier=3,
                )
        return None

    return PolicyRule(
        name="git_mutation",
        description="Escalate git mutation commands",
        evaluate=evaluate,
    )


def make_default_bash_rule() -> PolicyRule:
    """Catch-all: escalate any bash command not matched by earlier rules."""
    def evaluate(tool_name: str, tool_input: dict) -> PolicyDecision | None:
        if tool_name == "bash_exec":
            cmd = tool_input.get("command", "")
            return PolicyDecision(
                decision=Decision.ESCALATE,
                reason=f"Unclassified bash command requires confirmation",
                rule_name="default_bash",
                tier=3,
            )
        return None

    return PolicyRule(
        name="default_bash",
        description="Escalate unclassified bash commands",
        evaluate=evaluate,
    )
```

### Step 3: Policy engine

Create `policy/engine.py`:

```python
from datetime import datetime, timezone
from pathlib import Path
import json
from .types import PolicyDecision, Decision, PolicyRule
from .rules import (
    make_destructive_command_rule,
    make_safe_reads_rule,
    make_bash_allowlist_rule,
    make_file_write_rule,
    make_git_mutation_rule,
    make_default_bash_rule,
)


class AuditLog:
    def __init__(self, path: str = ".agent/audit.jsonl"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, tool_name: str, tool_input: dict, decision: PolicyDecision,
               confirmed: bool | None = None):
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool": tool_name,
            "input_summary": str(tool_input)[:200],
            "decision": decision.decision.value,
            "reason": decision.reason,
            "rule": decision.rule_name,
            "tier": decision.tier,
            "confirmed": confirmed,
        }
        with open(self.path, "a") as f:
            f.write(json.dumps(entry) + "\n")


class RateLimiter:
    def __init__(self, max_calls_per_minute: int = 60):
        self.max_calls = max_calls_per_minute
        self.calls: list[float] = []

    def check(self) -> bool:
        import time
        now = time.time()
        self.calls = [t for t in self.calls if now - t < 60]
        if len(self.calls) >= self.max_calls:
            return False
        self.calls.append(now)
        return True


class PolicyEngine:
    def __init__(
        self,
        rules: list[PolicyRule] | None = None,
        audit_path: str = ".agent/audit.jsonl",
        max_calls_per_minute: int = 60,
    ):
        self.rules = rules or self._default_rules()
        self.audit = AuditLog(audit_path)
        self.rate_limiter = RateLimiter(max_calls_per_minute)

    def _default_rules(self) -> list[PolicyRule]:
        """Default safe policy for a coding assistant."""
        return [
            make_destructive_command_rule(),   # BLOCK dangerous commands first
            make_safe_reads_rule(),            # ALLOW reads silently
            make_bash_allowlist_rule([         # ALLOW common dev commands
                r"pytest(\s|$)",
                r"python\s+-m\s+pytest",
                r"git\s+(status|diff|log|show)",
                r"ls(\s|$)",
                r"cat\s+",
                r"echo\s+",
            ]),
            make_file_write_rule(auto_approve_dirs=["/tmp"]),
            make_git_mutation_rule(),
            make_default_bash_rule(),          # Catch-all escalation
        ]

    def evaluate(self, tool_name: str, tool_input: dict) -> PolicyDecision:
        # Rate limit check
        if not self.rate_limiter.check():
            decision = PolicyDecision(
                decision=Decision.BLOCK,
                reason="Rate limit exceeded",
                rule_name="rate_limiter",
                tier=0,
            )
            self.audit.record(tool_name, tool_input, decision)
            return decision

        # Evaluate rules in order; first match wins
        for rule in self.rules:
            result = rule.evaluate(tool_name, tool_input)
            if result is not None:
                return result

        # Default: allow anything not covered (shouldn't reach here with good rules)
        return PolicyDecision(
            decision=Decision.ALLOW,
            reason="No matching rule — default allow",
            rule_name="fallthrough",
            tier=2,
        )

    def execute_with_policy(
        self,
        tool_name: str,
        tool_input: dict,
        execute_fn,
        confirm_fn=None,
    ) -> str:
        """
        Evaluate policy, optionally confirm with human, then execute.

        confirm_fn: callable(reason: str) -> bool
            If None, escalation defaults to blocking.
        """
        decision = self.evaluate(tool_name, tool_input)

        if decision.decision == Decision.BLOCK:
            self.audit.record(tool_name, tool_input, decision)
            return f"[BLOCKED by policy] {decision.reason}"

        if decision.decision == Decision.ESCALATE:
            if confirm_fn is None:
                self.audit.record(tool_name, tool_input, decision, confirmed=False)
                return f"[ESCALATION — no confirm handler] {decision.reason}"

            confirmed = confirm_fn(decision.reason)
            self.audit.record(tool_name, tool_input, decision, confirmed=confirmed)
            if not confirmed:
                return "User declined."

        else:
            self.audit.record(tool_name, tool_input, decision)

        # Execute
        try:
            return execute_fn(tool_name, tool_input)
        except Exception as e:
            return f"Error: {e}"
```

### Step 4: Integrate into your agent

Replace the `SafetyGate` calls in your agent loop with the `PolicyEngine`:

```python
from policy.engine import PolicyEngine

# Initialize once
policy = PolicyEngine()

def confirm_with_user(reason: str) -> bool:
    print(f"\n[POLICY] Requires confirmation: {reason}")
    return input("Proceed? [y/N] ").strip().lower() == "y"

# In your tool dispatch loop:
result = policy.execute_with_policy(
    tool_name=tool_name,
    tool_input=tool_input,
    execute_fn=execute_tool,
    confirm_fn=confirm_with_user,
)
```

### Step 5: Make the policy configurable

Load policy rules from a YAML/JSON config so you can tune without code changes:

```python
# .agent/policy.json
{
    "bash_allowlist": [
        "pytest",
        "python -m pytest",
        "git status",
        "git diff",
        "ls",
        "echo"
    ],
    "auto_approve_dirs": ["/tmp", ".agent/"],
    "max_calls_per_minute": 60,
    "tier4_tools": ["git push", "pip install", "curl", "wget"]
}
```

```python
def load_policy_from_config(config_path: str = ".agent/policy.json") -> PolicyEngine:
    import json
    from pathlib import Path
    if not Path(config_path).exists():
        return PolicyEngine()  # defaults

    config = json.loads(Path(config_path).read_text())
    rules = [
        make_destructive_command_rule(),
        make_safe_reads_rule(),
        make_bash_allowlist_rule(config.get("bash_allowlist", [])),
        make_file_write_rule(config.get("auto_approve_dirs")),
        make_git_mutation_rule(),
        make_default_bash_rule(),
    ]
    return PolicyEngine(
        rules=rules,
        max_calls_per_minute=config.get("max_calls_per_minute", 60),
    )
```

## Success Criteria

- [ ] `rm -rf /` is blocked without asking the user
- [ ] `pytest tests/` runs without asking the user
- [ ] `git push` asks the user before running
- [ ] File writes to the working directory ask the user
- [ ] Every decision is written to `.agent/audit.jsonl`
- [ ] Rate limiter fires after exceeding the configured call count
- [ ] Policy rules can be updated in `.agent/policy.json` without code changes

## What's Missing

| Gap | Fixed in |
|-----|---------|
| Rules are static — agent can't negotiate | Projects 8–9 add intervention hooks |
| No policy for agent-to-agent calls | Project 12 (multi-agent orchestration) |
| Confirmation is blocking (stdin) | Replace with async approval in a real UI |
| No diff/preview before confirmation | Add a `preview` field to `PolicyDecision` |
