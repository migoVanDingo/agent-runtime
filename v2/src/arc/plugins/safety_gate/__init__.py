"""Destructive-action gate — prompts the user before tools run destructive commands.

Sister plugin to `guard`. Where guard categorically bans or escalates,
safety_gate is purely a human-in-the-loop check: this command is hard to
undo, do you really mean it?

See _design/0012-destructive-action-gate.md.
"""
from arc.plugins.safety_gate.plugin import SafetyGatePlugin
from arc.plugins.safety_gate.catalog import DEFAULT_PATTERNS, Pattern

__all__ = ["SafetyGatePlugin", "DEFAULT_PATTERNS", "Pattern"]
