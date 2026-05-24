"""Built-in `_test_echo` sub-agent — test fixture only.

Exercises the runner end-to-end against a real provider. Tight quota /
short timeout so accidental dispatch in production doesn't run away.
"""
from __future__ import annotations

from arc.runtime.subagents.spec import SubAgentSpec


_SYSTEM_PROMPT = (
    "You are a test echo sub-agent. The parent will hand you a task string. "
    "Your job is to repeat the task back as a JSON object of the form "
    '{"echo": "<the task verbatim>", "length": <character count>}. '
    "Return ONLY the JSON object — no prose, no markdown fences. "
    "You have no tools available; respond in your first message."
)


def build_test_echo() -> SubAgentSpec:
    return SubAgentSpec(
        name="_test_echo",
        description=(
            "Echo the task string back as JSON. Built-in test sub-agent — "
            "not for production use."
        ),
        provider="anthropic",
        model="claude-haiku-4-5",
        system_prompt=_SYSTEM_PROMPT,
        tools=(),
        timeout_s=30.0,
        max_turns=2,
        max_dispatches_per_session=3,
        max_consecutive_failures=2,
        max_transient_retries=1,
        expected_output='{"echo": str, "length": int}',
        source="builtin",
    )
