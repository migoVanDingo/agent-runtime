"""Guard plugin — before_tool_call policy enforcement.

Three buckets in config:
  allowlist_tools                tools that bypass all checks
  blocklist_patterns             regex against command string → hard deny
  escalation_required_patterns   regex → prompt the gate (auto-denied
                                 if the gate is a NoOpGate)

Patterns are only checked against `tool_input["command"]`. For tools
without a command field (everything except bash_exec in phase 2.1),
the pattern checks don't fire — only the allowlist matters.
"""
from __future__ import annotations

import re
from typing import Any

from arc.runtime.hooks import ToolCall, ToolDenial
from arc.user_gate import EscalationRequest, UserGate


class GuardPlugin:
    """Wraps the policy + a UserGate for escalation."""

    name = "guard"
    version = "1.0.0"

    def __init__(
        self,
        *,
        allowlist_tools: list[str],
        blocklist_patterns: list[str],
        escalation_required_patterns: list[str],
        user_gate: UserGate,
    ) -> None:
        self._allowlist = set(allowlist_tools)
        self._block_res = [re.compile(p) for p in blocklist_patterns]
        self._escalate_res = [re.compile(p) for p in escalation_required_patterns]
        self._gate = user_gate

    # ── Hook ───────────────────────────────────────────────────────────

    def before_tool_call(self, ctx, call: ToolCall) -> ToolCall | ToolDenial | None:
        # Allowlisted tools always pass through unchanged
        if call.name in self._allowlist:
            return None

        # Pattern checks only apply to inputs with a command field
        command = call.input.get("command")
        if not isinstance(command, str):
            return None  # not a command-shape tool; no policy applies

        # Hard deny on blocklist match
        for pattern in self._block_res:
            m = pattern.search(command)
            if m:
                return ToolDenial(
                    tool_call_id=call.tool_call_id,
                    name=call.name,
                    reason=(
                        f"command matches a blocked pattern "
                        f"({pattern.pattern!r} matched {m.group()!r}). "
                        f"This category of command is not allowed."
                    ),
                )

        # Escalation: prompt the gate
        for pattern in self._escalate_res:
            m = pattern.search(command)
            if m:
                approved = self._gate.prompt_for_escalation(EscalationRequest(
                    tool_name=call.name,
                    command=command,
                    reason=(
                        f"command matches an escalation pattern "
                        f"({pattern.pattern!r} matched {m.group()!r})"
                    ),
                ))
                if approved:
                    return None  # pass through to execution
                return ToolDenial(
                    tool_call_id=call.tool_call_id,
                    name=call.name,
                    reason=(
                        f"escalation denied for pattern {pattern.pattern!r}"
                    ),
                )

        # No pattern matched; allow
        return None
