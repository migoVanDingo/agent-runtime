"""User-in-the-loop escalation mechanism.

When the guard or monitor decides ESCALATE, the agent needs to ask the
user for approval before proceeding. This module defines the protocol
and a CLI implementation.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol
from logger import get_logger

logger = get_logger(__name__)


@dataclass
class Escalation:
    """A request for user approval."""
    reason: str
    source: str  # "guard", "monitor", "critic"
    tool_name: str | None = None
    tool_input: dict | None = None
    council_run_id: str | None = None        # set when escalation originated from a council decision
    council_councillor_labels: list[str] | None = None  # which councillors drove the challenge


class UserGate(Protocol):
    """Protocol for user-in-the-loop approval."""

    def prompt(self, escalation: Escalation) -> bool:
        """Ask the user for approval. Returns True to proceed, False to deny."""
        ...


class CLIUserGate:
    """Interactive CLI implementation — prints to stdout, reads y/n from stdin."""

    def prompt(self, escalation: Escalation) -> bool:
        print(f"\n{'─' * 52}")
        print(f"  ⚠  ESCALATION — {escalation.source}")
        print(f"  {escalation.reason}")
        if escalation.tool_name:
            print(f"  Tool: {escalation.tool_name}")
        if escalation.tool_input:
            # Show the input but truncate long values
            display = {}
            for k, v in escalation.tool_input.items():
                s = str(v)
                display[k] = s[:200] + "..." if len(s) > 200 else s
            print(f"  Input: {display}")
        print(f"{'─' * 52}")

        try:
            answer = input("  Allow? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return False

        approved = answer in ("y", "yes")
        logger.info(f"  escalation: user {'approved' if approved else 'denied'} — {escalation.reason}")

        # Record user outcome against the originating council run (if any)
        if escalation.council_run_id:
            from runtime.council_metrics import get_metrics_writer
            writer = get_metrics_writer()
            if writer:
                all_labels = escalation.council_councillor_labels or []
                # "sided with" = models whose final recommendation matched the user's action
                # For a critic escalation: challengers recommended blocking/modifying the step.
                # If user approved → sided with approvers, overrode challengers (and vice versa).
                writer.record_user_outcome(
                    run_id=escalation.council_run_id,
                    user_action="approved" if approved else "denied",
                    sided_with=[],    # populated by caller who knows the challenge context
                    overrode=all_labels,
                )

        return approved


class AutoDenyGate:
    """Non-interactive gate that always denies. For testing or headless mode."""

    def prompt(self, escalation: Escalation) -> bool:
        logger.info(f"  escalation: auto-denied (headless) — {escalation.reason}")
        return False
