"""Guard plugin — before_tool_call policy enforcement.

Four buckets in config:
  allowlist_tools                tools that bypass all checks
  delegate_only_tools            tool-name globs a PARENT session may not call
                                 directly — allowed only inside a sub-agent
                                 (forces orchestration through its verifying
                                 owner). Denied with a hint naming the owner.
  blocklist_patterns             regex against command string → hard deny
  escalation_required_patterns   regex → prompt the gate (auto-denied
                                 if the gate is a NoOpGate)

Patterns are only checked against `tool_input["command"]`. For tools
without a command field (everything except bash_exec in phase 2.1),
the pattern checks don't fire — only the allowlist + delegate rules matter.
"""
from __future__ import annotations

import fnmatch
import re
from typing import Any

from arc.runtime.hooks import ToolCall, ToolDenial
from arc.runtime.subagents.tripwire import inside_subagent
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
        delegate_only_tools: dict[str, str] | None = None,
    ) -> None:
        self._allowlist = set(allowlist_tools)
        self._block_res = [re.compile(p) for p in blocklist_patterns]
        self._escalate_res = [re.compile(p) for p in escalation_required_patterns]
        self._gate = user_gate
        # glob -> owner sub-agent tool name. A parent-session call to a matching
        # tool is denied with a hint to route through `owner`.
        self._delegate_only = dict(delegate_only_tools or {})
        # Learned from session.started. None = not yet known → we DON'T enforce
        # the delegate rule (fail open), so a missing owner never bricks a tool.
        self._known_tools: set[str] | None = None

    # ── Hooks ──────────────────────────────────────────────────────────

    def on_event(self, ctx, event) -> None:
        # Capture the FINAL tool list (built-in + plugin + sub-agent tools) so
        # the delegate rule can tell whether an owner sub-agent still exists.
        if event.type == "session.started":
            self._known_tools = set(event.payload.get("tools") or [])

    def before_tool_call(self, ctx, call: ToolCall) -> ToolCall | ToolDenial | None:
        # Allowlisted tools always pass through unchanged
        if call.name in self._allowlist:
            return None

        # Delegate-only tools: a parent session may not call them directly.
        # Inside a sub-agent's session the owner runs them freely (the guard
        # isn't in the child registry today, but gate explicitly so the rule
        # stays correct if child plugins are ever opted in).
        if self._delegate_only and not inside_subagent():
            for glob, owner in self._delegate_only.items():
                if not fnmatch.fnmatchcase(call.name, glob):
                    continue
                # Fail open unless the owner sub-agent is actually available —
                # if it's disabled/uninstalled, don't redirect to a tool that
                # doesn't exist (that would brick the capability entirely).
                # break (not return) so normal command checks still apply.
                if self._known_tools is None or owner not in self._known_tools:
                    break
                return ToolDenial(
                        tool_call_id=call.tool_call_id,
                        name=call.name,
                        reason=(
                            f"{call.name!r} cannot be called directly from the main "
                            f"session. Route this work through {owner} — the sub-agent "
                            f"that owns it and health-checks before reporting. Call "
                            f"{owner} with a task describing the goal instead."
                        ),
                    )

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
