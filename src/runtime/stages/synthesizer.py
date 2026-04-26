"""SynthesizerStage — synthesizes a coherent response from completed plan steps.

Runs only when plan.requires_synthesis is True. Skips (no-op) otherwise,
leaving context.response as set by ExecutionStage.
"""
from __future__ import annotations
from planning.synthesizer import Synthesizer
from runtime.pipeline_context import PipelineContext
from runtime.stage_base import Stage
from runtime.stage_result import StageResult, StageStatus
from runtime.utils import banner
from logger import get_logger

logger = get_logger(__name__)


class SynthesizerStage(Stage):
    """Synthesizes a final response from plan step results.

    Reads:  context.plan (must be non-None with requires_synthesis=True)
    Writes: context.response (overwrites ExecutionStage's placeholder)
    """

    name = "SynthesizerStage"

    def __init__(self, synthesizer: Synthesizer, spinner) -> None:
        self._synthesizer = synthesizer
        self._spinner = spinner

    def run(self, context: PipelineContext) -> StageResult:
        # No-op if no plan, or synthesis not required.
        if context.plan is None or not context.plan.requires_synthesis:
            return StageResult(status=StageStatus.OK, updated_context=context)

        self._spinner.start("Synthesizing response...")
        logger.info(banner("Synthesizing"))

        response = self._synthesizer.synthesize(context.plan)

        self._spinner.stop()
        logger.info(banner("Done"))

        context.response = response
        # DONE short-circuits the pipeline so DirectExecutionStage is never
        # reached after a plan has been executed and synthesized.
        return StageResult(status=StageStatus.DONE, updated_context=context)
