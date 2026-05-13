"""Execution stage package.

Re-exports ExecutionStage from _execution_stage.py so callers using
`from runtime.stages.execution import ExecutionStage` continue to work.

ExecutionStage implementation lives in runtime/stages/_execution_stage.py.
Helpers extracted to this subpackage:
  step_prompt.py  — step_system() builder
  step_runner.py  — run_step() (tool loop + hooks)
  step_loop.py    — _StepLoopState + apply_decision()
"""
from runtime.stages._execution_stage import ExecutionStage  # noqa: F401

__all__ = ["ExecutionStage"]
