"""SubAgentResult — the structured outcome of one dispatch.

The parent agent sees this serialized as JSON via `to_tool_result()`.
That string is the entire contribution to the parent's LLM context; the
child's transcript stays in its own session dir.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

ResultStatus = Literal["ok", "error", "timeout", "cancelled"]


@dataclass(frozen=True)
class SubAgentResult:
    """Frozen outcome of one sub-agent dispatch."""

    status: ResultStatus
    output: str                          # final assistant text or structured-output JSON string
    error_message: str | None
    child_session_id: str
    cost_usd: float
    turns: int
    tool_calls: int
    wallclock_s: float
    retries_attempted: int = 0           # transient retries the runner absorbed

    def to_tool_result(self) -> str:
        """Serialize as the string returned by the parent's tool call.

        Stable JSON shape — sub-agent authors and parent system prompts
        can rely on it. Use compact separators so it fits parent context
        efficiently.
        """
        return json.dumps(
            {
                "status": self.status,
                "output": self.output,
                "error": self.error_message,
                "child_session_id": self.child_session_id,
                "metrics": {
                    "cost_usd": self.cost_usd,
                    "turns": self.turns,
                    "tool_calls": self.tool_calls,
                    "wallclock_s": self.wallclock_s,
                    "retries_attempted": self.retries_attempted,
                },
            },
            separators=(",", ":"),
        )
