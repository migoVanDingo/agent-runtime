from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum

# JSON schema for structured output enforcement (OpenAI response_format).
# Keeps the revision/planning loop from producing structurally invalid JSON.
PLAN_JSON_SCHEMA = {
    "name": "plan",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "original_query": {"type": "string"},
            "requires_synthesis": {"type": "boolean"},
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
                        "flags": {
                            "type": "object",
                            "properties": {
                                "retry": {"type": "boolean"},
                                "escalate": {"type": "boolean"},
                                "defer": {"type": "boolean"},
                            },
                            "required": ["retry", "escalate", "defer"],
                            "additionalProperties": False,
                        },
                    },
                    "required": ["step", "description", "action_type", "tool", "produces", "flags"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["original_query", "requires_synthesis", "steps"],
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
    CONVERSATION = "conversation"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass
class StepFlags:
    retry: bool = False
    escalate: bool = False
    defer: bool = False
    retry_count: int = 0
    deferred: bool = False
    skipped: bool = False

    def to_dict(self) -> dict:
        return {
            "retry": self.retry,
            "escalate": self.escalate,
            "defer": self.defer,
            "retry_count": self.retry_count,
            "deferred": self.deferred,
            "skipped": self.skipped,
        }

    @classmethod
    def from_dict(cls, data: dict) -> StepFlags:
        return cls(
            retry=data.get("retry", False),
            escalate=data.get("escalate", False),
            defer=data.get("defer", False),
            retry_count=data.get("retry_count", 0),
            deferred=data.get("deferred", False),
            skipped=data.get("skipped", False),
        )


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
    # ── Runtime state — migrating to runtime.run_state.StepRun ───────
    status: StepStatus = StepStatus.PENDING
    result: str | None = None
    error: str | None = None
    flags: StepFlags = field(default_factory=StepFlags)

    def to_dict(self) -> dict:
        return {
            "step": self.step,
            "description": self.description,
            "action_type": self.action_type.value,
            "tool": self.tool,
            "produces": self.produces,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "flags": self.flags.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> Step:
        return cls(
            step=data["step"],
            description=data["description"],
            action_type=ActionType(data["action_type"]),
            tool=data.get("tool"),
            produces=data.get("produces"),
            status=StepStatus(data.get("status", StepStatus.PENDING)),
            result=data.get("result"),
            error=data.get("error"),
            flags=StepFlags.from_dict(data.get("flags", {})),
        )


@dataclass
class Plan:
    original_query: str
    steps: list[Step]
    requires_synthesis: bool = True
    risk: str = "low"  # "low", "moderate", "high" — set by classifier

    def to_dict(self) -> dict:
        return {
            "original_query": self.original_query,
            "steps": [s.to_dict() for s in self.steps],
            "requires_synthesis": self.requires_synthesis,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Plan:
        return cls(
            original_query=data["original_query"],
            steps=[Step.from_dict(s) for s in data["steps"]],
            requires_synthesis=data.get("requires_synthesis", True),
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
