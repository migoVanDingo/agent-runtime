from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum

# StepRuntimeState and StepFlags live in runtime/run_state.py (the runtime owns
# their lifecycle).  Re-exported here so existing imports from planning.schema
# continue to work without changes.
from runtime.run_state import StepRuntimeState, StepFlags  # noqa: F401 — re-export

# JSON schema for structured output enforcement (OpenAI response_format).
# Keeps the revision/planning loop from producing structurally invalid JSON.
PLAN_JSON_SCHEMA = {
    "name": "plan",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "original_query": {"type": "string"},
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "step": {"type": "integer"},
                        "description": {"type": "string"},
                        "action_type": {
                            "type": "string",
                            "enum": [
                                "analysis",
                                "file_io",
                                "shell",
                                "crypto",
                                "web",
                                "data",
                                "artifacts",
                                "search",
                                "git",
                                "document",
                                "briefbot",
                                "conversation",
                            ],
                        },
                        "tool": {"type": ["string", "null"]},
                        "produces": {"type": ["string", "null"]},
                    },
                    "required": ["step", "description", "action_type", "tool", "produces"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["original_query", "steps"],
        "additionalProperties": False,
    },
}


class ActionType(str, Enum):
    ANALYSIS = "analysis"
    FILE_IO = "file_io"
    SHELL = "shell"
    CRYPTO = "crypto"
    WEB = "web"
    DATA = "data"
    ARTIFACTS = "artifacts"
    SEARCH = "search"
    GIT = "git"
    DOCUMENT = "document"
    BRIEFBOT = "briefbot"
    REVERSING = "reversing"
    SYMBOLIC = "symbolic"
    SUBAGENT = "subagent"  # 0090d — dispatched via SubAgentTool wrappers
    CONVERSATION = "conversation"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"



@dataclass
class Step:
    """Planner spec for one step.

    The fields `status`, `result`, `error`, and `flags.retry_count` are
    runtime-execution state that belongs in `runtime.run_state.StepRun`.
    They are kept here during the transition; new code should use
    `PlanRun.from_plan(plan)` and read execution state from `StepRun`.
    """
    step: int
    description: str
    action_type: ActionType
    tool: str | None = None
    produces: str | None = None
    # ── Runtime state ──────────────────────────────────────────────────
    status: StepStatus = StepStatus.PENDING
    result: str | None = None
    error: str | None = None
    flags: StepRuntimeState = field(default_factory=StepRuntimeState)

    def to_dict(self) -> dict:
        # flags is runtime state, not part of the plan JSON contract.
        # It is intentionally omitted so serialized plans remain planner-only data.
        return {
            "step": self.step,
            "description": self.description,
            "action_type": self.action_type.value,
            "tool": self.tool,
            "produces": self.produces,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Step:
        # flags is runtime-only; it is never serialized into plan JSON (see to_dict).
        # If a stale payload happens to include a "flags" key, we ignore it and
        # always start with a fresh StepRuntimeState() so the planner cannot
        # smuggle runtime state into the execution engine.
        return cls(
            step=data["step"],
            description=data["description"],
            action_type=ActionType(data["action_type"]),
            tool=data.get("tool"),
            produces=data.get("produces"),
            status=StepStatus(data.get("status", StepStatus.PENDING)),
            result=data.get("result"),
            error=data.get("error"),
        )


@dataclass
class Plan:
    original_query: str
    steps: list[Step]
    # risk is set by the routing classifier; council reads it.
    # Descriptive (the assessment of the request), not prescriptive.
    risk: str = "low"

    def to_dict(self) -> dict:
        return {
            "original_query": self.original_query,
            "steps": [s.to_dict() for s in self.steps],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Plan":
        return cls(
            original_query=data["original_query"],
            steps=[Step.from_dict(s) for s in data["steps"]],
        )

    def summary(self) -> str:
        """Plain-text summary of completed steps for the synthesizer prompt."""
        lines = []
        for s in self.steps:
            if s.status == StepStatus.COMPLETED and s.result:
                lines.append(f"Step {s.step} ({s.description}): {s.result}")
            elif s.status == StepStatus.ERROR:
                lines.append(f"Step {s.step} ({s.description}): failed")
        return "\n".join(lines) if lines else "No steps completed."
