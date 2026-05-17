"""Step runner — executes one plan step via ToolLoop.

Extracted from ExecutionStage._run_step to keep execution.py under 600 lines.
"""
from __future__ import annotations

import re

from planning.schema import Step
from runtime.tool_loop import ToolLoop, ToolLoopConfig
from app_config import config
from logger import get_logger

logger = get_logger(__name__)

# Raw tool errors that the model tends to wrap with explanatory prose,
# defeating the monitor's regex-based short-circuit. When the loop ends
# after one of these without recovery, surface the raw error directly
# as the step result so the monitor can detect it and force REPLAN.
_NON_RECOVERABLE_TOOL_ERROR_RE = re.compile(
    r"^Error: (?:sub-agent '[^']+' failed:|artifact store is not initialized)"
)


def run_step(
    *,
    step: Step,
    n_total: int,
    tools: list[dict],
    system: str,
    provider,
    messenger,
    context_mgr,
    tool_executor,
    user_gate,
    query: str,
    plan_start_index: int | None,
    step_display: int,
    checkpoint,
    parent_identity,
) -> str:
    """Build ToolLoopConfig + hooks, invoke ToolLoop.run, propagate tool_errors.

    Returns the step result string (last_tool_output if available, else response_text).
    Writes step.error on failure; clears it if a subsequent successful call fires
    on_error_cleared (when error_recovery_clears_step_error is configured).
    """
    step.error = None
    desc_short = step.description[:40] + "..." if len(step.description) > 40 else step.description
    authorized = frozenset(t["name"] for t in tools)

    step_identity = parent_identity
    if step_identity is not None:
        step_identity = step_identity.for_step_run()

    loop_cfg = ToolLoopConfig(
        max_iterations=config.runtime.execution_monitor.step_max_iterations,
        max_tool_calls=config.runtime.execution_monitor.step_max_tool_calls,
        max_consecutive_errors=3,
        authorized_tool_names=authorized,
        label="ExecutionStage",
    )

    class _StepHooks:
        def __init__(self, s: Step):
            self._step = s
            self.tool_errors: list[str] = []

        def on_tool_complete(self, tool_name: str, result: str) -> None:
            pass

        def on_max_tokens(self) -> None:
            self._step.error = "max_tokens"

        def on_error_cleared(self, n: int) -> None:
            if config.runtime.execution_monitor.error_recovery_clears_step_error:
                logger.info(f"  runtime: successful tool call after {n} error(s) — clearing step errors")
                self._step.error = None

    hooks = _StepHooks(step)
    loop = ToolLoop(
        provider=provider,
        messenger=messenger,
        context_mgr=context_mgr,
        tool_executor=tool_executor,
        user_gate=user_gate,
        config=loop_cfg,
        parent_identity=step_identity,
        checkpoint=checkpoint,
    )

    result = loop.run(
        system=system,
        tools=tools,
        query=query or step.description,
        plan_start_index=plan_start_index,
        hooks=hooks,
        resume_message=f"Step {step_display or step.step}/{n_total} — {desc_short}",
    )

    # Only propagate tool_errors to step.error when step.error is still set.
    # If step.error is None, the errors were already cleared by a subsequent
    # successful tool call (on_error_cleared fired) — don't re-flag the step.
    if result.tool_errors and step.error is not None:
        existing = step.error or ""
        step.error = (existing + "; tool errors: " + "; ".join(result.tool_errors)).lstrip("; ")

    # If the model wrapped a non-recoverable tool error (e.g. "Error: sub-agent
    # X failed:" or "Error: artifact store is not initialized") with prose, the
    # response_text won't match the monitor's short-circuit patterns and the
    # pipeline will fabricate downstream work. Surface the raw tool output
    # directly so the monitor sees the actual error and forces REPLAN.
    raw = result.last_tool_output_raw or ""
    if raw and _NON_RECOVERABLE_TOOL_ERROR_RE.match(raw.strip()):
        return raw

    # Return the raw tool output when available so StructuralCriteria can
    # inspect structured results (e.g. diff_behavior JSON with all_match).
    # For CONVERSATION steps (no tools), return the model's prose response.
    if result.last_tool_output:
        return result.last_tool_output
    return result.response_text
