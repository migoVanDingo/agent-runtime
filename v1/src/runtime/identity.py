"""Runtime identity primitives.

These IDs are intentionally lightweight and independent from persistence
models. They give logs, events, artifact memory, SQL rows, and future replay
data a shared correlation vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional

try:
    import ulid
except ModuleNotFoundError:  # pragma: no cover - exercised only in minimal envs
    ulid = None
import uuid


def new_id(prefix: str) -> str:
    """Return a prefixed ULID suitable for runtime correlation."""
    if ulid is not None:
        return f"{prefix}{ulid.new().str}"
    return f"{prefix}{uuid.uuid4().hex.upper()}"


@dataclass(frozen=True)
class RuntimeIdentity:
    session_id: str
    turn_id: Optional[str] = None
    pipeline_run_id: Optional[str] = None
    plan_id: Optional[str] = None
    plan_run_id: Optional[str] = None
    step_run_id: Optional[str] = None
    tool_call_id: Optional[str] = None
    user_id: Optional[str] = None
    project_id: Optional[str] = None

    @classmethod
    def new_session(
        cls,
        *,
        session_id: str | None = None,
        user_id: str | None = None,
        project_id: str | None = None,
    ) -> "RuntimeIdentity":
        return cls(
            session_id=session_id or new_id("SESS"),
            user_id=user_id,
            project_id=project_id,
        )

    def for_turn(self, turn_id: str | None = None) -> "RuntimeIdentity":
        return replace(
            self,
            turn_id=turn_id or new_id("TURN"),
            pipeline_run_id=None,
            plan_id=None,
            plan_run_id=None,
            step_run_id=None,
            tool_call_id=None,
        )

    def for_pipeline(self, pipeline_run_id: str | None = None) -> "RuntimeIdentity":
        return replace(self, pipeline_run_id=pipeline_run_id or new_id("RUN"))

    def for_plan(self, plan_id: str | None = None) -> "RuntimeIdentity":
        return replace(self, plan_id=plan_id or new_id("PLAN"))

    def for_plan_run(self, plan_run_id: str | None = None) -> "RuntimeIdentity":
        return replace(self, plan_run_id=plan_run_id or new_id("PRUN"))

    def for_step_run(self, step_run_id: str | None = None) -> "RuntimeIdentity":
        return replace(self, step_run_id=step_run_id or new_id("SRUN"))

    def for_tool_call(self, tool_call_id: str | None = None) -> "RuntimeIdentity":
        return replace(self, tool_call_id=tool_call_id or new_id("TCALL"))

    def to_event_fields(self) -> dict[str, str | None]:
        return {
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "pipeline_run_id": self.pipeline_run_id,
            "plan_id": self.plan_id,
            "plan_run_id": self.plan_run_id,
            "step_run_id": self.step_run_id,
            "tool_call_id": self.tool_call_id,
            "user_id": self.user_id,
            "project_id": self.project_id,
        }
