"""SubAgentTool — adapts a SubAgentSpec into the parent's tool registry.

The parent's LLM sees a tool named `subagent_<spec_name>`. Calling it
invokes the runner. On `ok` results, the tool returns the serialized
SubAgentResult as a JSON string. On error/timeout/cancelled, it raises
ToolError so the parent agent can recover or surface the failure.
"""
from __future__ import annotations

from typing import Any, ClassVar

from arc.runtime.scope import current_session_id, current_turn_id
from arc.runtime.subagents.runner import SubAgentRunner
from arc.runtime.subagents.spec import SubAgentSpec
from arc.tools.base import ToolError, ToolInputSchema


class SubAgentTool:
    """One per enabled spec. Lives in the parent's tool registry."""

    # Tool protocol requires ClassVars, but our tool is per-instance.
    name: ClassVar[str] = ""        # set per-instance in __init__
    description: ClassVar[str] = ""

    def __init__(self, spec: SubAgentSpec, runner: SubAgentRunner) -> None:
        self._spec = spec
        self._runner = runner
        # Per-instance shadows of the ClassVars — Python allows this.
        # The tool registry / loop read .name / .description off the instance.
        self.name = f"subagent_{spec.name}"
        self.description = (
            f"{spec.description}  "
            f"[sub-agent: pinned to {spec.provider}/{spec.model}; "
            f"returns JSON {{status, output, error, child_session_id, metrics}}]"
        )

    @property
    def input_schema(self) -> ToolInputSchema:
        return ToolInputSchema(
            properties={
                "task": {
                    "type": "string",
                    "description": (
                        "The task string handed to the sub-agent. Be concrete and "
                        "self-contained — the sub-agent does not see the parent's "
                        "transcript."
                    ),
                },
                "context_bundle": {
                    "type": "string",
                    "description": (
                        "Optional. Additional context the sub-agent needs that "
                        "wouldn't fit in `task` (e.g., a snippet of code to analyze, "
                        "a list of file paths). Prepended to the task."
                    ),
                },
            },
            required=["task"],
        )

    def execute(self, input: dict[str, Any]) -> str:
        task = str(input.get("task", "")).strip()
        if not task:
            raise ToolError("`task` is required and must be non-empty")
        context_bundle = input.get("context_bundle")
        if context_bundle is not None and not isinstance(context_bundle, str):
            raise ToolError("`context_bundle` must be a string when provided")

        parent_sid = current_session_id() or ""
        parent_tid = current_turn_id()

        result = self._runner.dispatch(
            self._spec.name,
            task,
            context_bundle=context_bundle if context_bundle else None,
            parent_session_id=parent_sid,
            parent_turn_id=parent_tid,
        )

        if result.status == "ok":
            return result.to_tool_result()

        # Non-OK statuses surface as ToolError so the parent agent's loop
        # treats them as tool failures and can recover.
        raise ToolError(
            f"sub-agent {self._spec.name} {result.status}: {result.error_message}"
        )
