"""SubAgentTool — adapter that exposes a SubAgentSpec as a BaseTool.

This is how skills invoke sub-agents. The skill emits a step with
``tool=subagent_<spec.name>``; the tool's ``execute`` dispatches through
``SubAgentRunner``. From the planner's and ExecutionStage's perspective,
sub-agents are just tools that take a task string and return a result.

The tool's name is intentionally prefixed with ``subagent_`` so:
- it can never collide with a regular tool name,
- ``SubAgentRunner._build_narrowed_registry`` can filter them out by class
  (the prefix is for humans; class identity is the enforcement),
- ``arc plugin doctor`` / ``arc subagent list`` can introspect easily.
"""
from __future__ import annotations

from typing import Any

from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from runtime.subagents.spec import SubAgentSpec


class SubAgentTool(BaseTool):
    """Wrap a ``SubAgentSpec`` so the agent can invoke it via the tool surface."""

    # Sub-agent responses are designed to be compact summaries (often <2KB JSON);
    # the dispatch model itself is the cost control. Marking HEAVY forced the
    # tool_executor to page even small responses to disk, replacing the analyst's
    # structured JSON with an unreadable stub — see SES01KRV1XJ7WK4177X1KHDYEWQ4B.
    weight = ToolWeight.MODERATE

    def __init__(self, spec: SubAgentSpec):
        self._spec = spec
        self.name = f"subagent_{spec.name}"
        self.description = (
            f"Delegate to the {spec.name!r} sub-agent. {spec.description} "
            f"Returns the sub-agent's final response. "
            + (
                f"The sub-agent will return JSON matching its schema."
                if spec.response_format == "json"
                else "The sub-agent returns a freeform text answer."
            )
        )

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "task": ToolProperty(
                    type="string",
                    description=(
                        "The full task description for the sub-agent. Provide "
                        "enough context that the sub-agent can act without "
                        "additional clarification — it has no view of your "
                        "conversation history."
                    ),
                ),
            },
            required=["task"],
        )

    def execute(self, tool_input: dict) -> str:
        """Dispatch through SubAgentRunner. Returns the child's response text."""
        # The tool layer has no parent reference. ExecutionStage's tool-call
        # path knows about the parent agent and threads it in via a closure /
        # tool-level shim. For now we use a process-level helper that pulls
        # the active parent from a contextvar set by the calling Agent.
        from runtime.subagents.runner import SubAgentRunner
        from runtime.subagents.context import current_parent_agent, current_pause_check, current_parent_turn_id

        task = tool_input.get("task", "")
        if not task or not isinstance(task, str):
            return "Error: subagent tool requires a non-empty 'task' string"

        parent = current_parent_agent()
        if parent is None:
            return (
                "Error: no parent agent registered for sub-agent dispatch. "
                "This usually means the tool was invoked outside an agent.call() context."
            )

        runner = SubAgentRunner()
        result = runner.run(
            self._spec,
            task,
            parent=parent,
            pause_check=current_pause_check(),
            parent_turn_id=current_parent_turn_id(),
        )

        if not result.ok:
            return f"Error: sub-agent {self._spec.name!r} failed: {result.error}"

        # Format the response for the parent agent. If structured, prefer the
        # structured view (already validated against schema); fall back to text.
        if result.structured is not None:
            import json
            return json.dumps(result.structured, indent=2, ensure_ascii=False)
        return result.text

    @property
    def spec(self) -> SubAgentSpec:
        """Expose the underlying spec for introspection / registry filtering."""
        return self._spec
