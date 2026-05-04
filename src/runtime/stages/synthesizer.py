"""SynthesizerStage — synthesizes a coherent response from completed plan steps.

Runs only when plan.requires_synthesis is True. Skips (no-op) otherwise,
leaving context.response as set by ExecutionStage.

Optional: if runtime.synthesis_quality.enabled is true, a council quality gate
runs after synthesis — only when the plan had at least one failure (retry or
replan). If the council finds the response inadequate it logs the gap; the
response is still returned (synthesis failures are advisory, not blocking).
"""
from __future__ import annotations
from planning.synthesizer import Synthesizer
from runtime.pipeline_context import PipelineContext
from runtime.stage_base import Stage
from runtime.stage_result import StageResult, StageStatus
from runtime.utils import banner
from app_config import config
from logger import get_logger

logger = get_logger(__name__)


def _plan_had_failures(plan) -> bool:
    """Return True if any step was retried or triggered a replan."""
    for s in plan.steps:
        if s.flags.retry_count > 0 or s.flags.skipped:
            return True
    return False


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
        if context.plan is None or not context.plan.requires_synthesis:
            return StageResult(status=StageStatus.OK, updated_context=context)

        self._spinner.start("Synthesizing response...")
        logger.info(banner("Synthesizing"))

        response = self._synthesizer.synthesize(context.plan)

        self._spinner.stop()
        logger.info(banner("Done"))

        # ── Optional quality gate ──────────────────────────────────────────
        sq = config.runtime.synthesis_quality
        if sq.enabled:
            run_gate = not sq.only_after_failures or _plan_had_failures(context.plan)
            if run_gate:
                response = self._quality_gate(response, context.plan, sq.n_councillors)

        context.response = response
        return StageResult(status=StageStatus.DONE, updated_context=context)

    def _quality_gate(self, response: str, plan, n_councillors: int) -> str:
        """Run a council quality check; logs gaps but always returns a response."""
        import dataclasses
        from runtime.council import Council
        from runtime.council_adapters import SynthesisQualityAdapter

        council_input = {
            "original_query": plan.original_query,
            "response": response[:1500],
            "plan_summary": plan.summary()[:800],
        }

        base_cfg = config.runtime.council
        active = base_cfg.councillors[:n_councillors]
        effective_cfg = dataclasses.replace(base_cfg, councillors=active, mode="independent")

        adapter = SynthesisQualityAdapter()
        council = Council(adapter=adapter, config=effective_cfg)
        result = council.deliberate(council_input=council_input, context="synthesis_quality",
                                    query=plan.original_query)
        verdict = result.final

        if verdict.passed:
            logger.info(f"  synthesis quality: PASS (confidence={verdict.confidence:.2f})")
        else:
            logger.info(
                f"  synthesis quality: FAIL (confidence={verdict.confidence:.2f}) "
                f"— gap: {verdict.gap[:120]}"
            )
        return response
