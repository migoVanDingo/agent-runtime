"""Built-in sub-agent specs that ship with arc.

Currently only `_test_echo` — a minimal spec used to exercise the runner
in tests. arc core ships zero user-facing built-in sub-agents; domain
specialists belong in plugin packages.
"""
from __future__ import annotations

from arc.runtime.subagents.builtins.test_echo import build_test_echo
from arc.runtime.subagents.spec import SubAgentSpec


def all_builtins() -> dict[str, SubAgentSpec]:
    """Return the built-in spec registry. New entries get added here."""
    return {
        "_test_echo": build_test_echo(),
    }
