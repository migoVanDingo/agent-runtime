"""SafetyGatePlugin — destructive-action confirmation via UserGate.

Single hook: before_tool_call. Fires after `guard` (priority 20 vs 10).
Pattern-matches `call.input["command"]` against the active catalog +
user-defined customs. On a match, prompts the user via UserGate; on
approval, remembers the pattern name for the rest of the session so
subsequent matches against the same pattern pass through silently.

See _design/0012-destructive-action-gate.md.
"""
from __future__ import annotations

import re
from typing import Any

from arc.plugins.safety_gate.catalog import DEFAULT_PATTERNS, Pattern, catalog_by_name
from arc.runtime.events import EventType, RuntimeEvent
from arc.runtime.hooks import ToolCall, ToolDenial
from arc.user_gate import EscalationRequest, UserGate


class SafetyGatePlugin:
    """Pattern-matches destructive shell commands and asks the user."""

    name = "safety-gate"
    version = "1.0.0"

    def __init__(
        self,
        *,
        enabled: bool,
        bypass_mode: bool,
        enabled_pattern_names: list[str],
        custom_patterns: list[Pattern],
        user_gate: UserGate,
    ) -> None:
        self._enabled = enabled
        self._bypass = bypass_mode
        self._gate = user_gate

        # Resolve enabled catalog patterns + custom patterns into one list
        catalog = catalog_by_name()
        active: list[Pattern] = []
        for name in enabled_pattern_names:
            if name in catalog:
                active.append(catalog[name])
            # Silently ignore unknown names — users shouldn't break startup
            # by typo'ing a pattern name they removed elsewhere.
        active.extend(custom_patterns)

        # Pre-compile regexes; pair each compiled pattern with its metadata
        self._patterns: list[tuple[Pattern, re.Pattern[str]]] = [
            (p, re.compile(p.regex)) for p in active
        ]

        # Per-session in-process cache of approved pattern names
        self._approved_this_session: set[str] = set()

        # Bus is wired post-construction so we can emit our own events
        self._bus: Any = None

    def bind_bus(self, bus: Any) -> None:
        self._bus = bus

    # ── Hook ───────────────────────────────────────────────────────────

    def before_tool_call(self, ctx, call: ToolCall) -> ToolCall | ToolDenial | None:
        if not self._enabled or self._bypass:
            return None

        command = call.input.get("command")
        if not isinstance(command, str):
            return None  # not a command-shape tool

        for pattern, regex in self._patterns:
            m = regex.search(command)
            if not m:
                continue

            # Remembered approval this session — pass through silently
            # but still emit an event so the audit trail is complete.
            if pattern.name in self._approved_this_session:
                self._emit(EventType.SAFETY_CONFIRMATION_ALLOWED, {
                    "tool_name": call.name,
                    "command": command,
                    "pattern_name": pattern.name,
                    "scope": "remembered",
                })
                return None

            # First match this session for this pattern — ask the user.
            self._emit(EventType.SAFETY_CONFIRMATION_REQUESTED, {
                "tool_name": call.name,
                "command": command,
                "pattern_name": pattern.name,
                "remembered": False,
            })

            approved = self._gate.prompt_for_escalation(EscalationRequest(
                tool_name=call.name,
                command=command,
                reason=(
                    f"destructive action ({pattern.name}): "
                    f"{pattern.description}"
                ),
            ))

            if approved:
                self._approved_this_session.add(pattern.name)
                self._emit(EventType.SAFETY_CONFIRMATION_ALLOWED, {
                    "tool_name": call.name,
                    "command": command,
                    "pattern_name": pattern.name,
                    "scope": "session",
                })
                return None

            self._emit(EventType.SAFETY_CONFIRMATION_DENIED, {
                "tool_name": call.name,
                "command": command,
                "pattern_name": pattern.name,
            })
            return ToolDenial(
                tool_call_id=call.tool_call_id,
                name=call.name,
                reason=(
                    f"destructive action ({pattern.name}) denied by user. "
                    f"Do not retry this command. If the user wants this done, "
                    f"they will rephrase or run it themselves."
                ),
            )

        # No pattern matched
        return None

    # ── Helpers ────────────────────────────────────────────────────────

    def _emit(self, event_type: str, payload: dict) -> None:
        if self._bus is not None:
            self._bus.emit(RuntimeEvent(
                type=event_type,
                stage="plugin",
                payload=payload,
            ))
