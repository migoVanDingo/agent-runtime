from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from runtime.pipeline_context import PipelineContext


class StageStatus(str, Enum):
    OK       = "ok"        # Stage completed successfully; advance to next stage.
    DONE     = "done"      # Pipeline complete; return context.response immediately.
    RETRY    = "retry"     # Transient failure; runner re-runs this stage with
                           # failure_reason injected into context.
    ASK_USER = "ask_user"  # Stage needs human input; runner shows user_message,
                           # appends the response to context.user_message, retries.
    ABORT    = "abort"     # Unrecoverable failure; runner jumps to the fallback
                           # stage (DirectExecutionStage).


@dataclass
class StageResult:
    status: StageStatus
    updated_context: PipelineContext

    # Populated only when status=ASK_USER.
    # This is the question presented to the user before the stage is retried.
    user_message: str | None = None

    # Populated for RETRY and ABORT; written to the session log by the runner.
    # Never shown to the user directly.
    reason: str | None = None
