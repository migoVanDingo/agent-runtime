from __future__ import annotations
from abc import ABC, abstractmethod
from runtime.pipeline_context import PipelineContext
from runtime.stage_result import StageResult


class Stage(ABC):
    """Base class for all pipeline stages.

    Each stage is responsible for:
      1. Reading the fields it needs from context.
      2. Performing its work (LLM call, regex pass, validation, etc.).
      3. Writing its outputs back into context.
      4. Returning a StageResult indicating what the runner should do next.

    Contract:
      - Stages must NOT raise exceptions for recoverable failures.
        Recoverable failures return RETRY; unrecoverable failures return ABORT.
        Only truly unexpected exceptions (programming errors) should propagate.
      - Stages must always return a StageResult whose updated_context reflects
        all writes made during the stage's run.
      - Stages must NOT mutate context fields that belong to a later stage.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable stage name used in session log banners."""
        ...

    @abstractmethod
    def run(self, context: PipelineContext) -> StageResult:
        """Execute this stage and return a result for the pipeline runner."""
        ...
