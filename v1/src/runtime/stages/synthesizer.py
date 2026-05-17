"""SynthesizerStage — synthesizes a coherent response from completed plan steps.

Runs whenever ContinuationStage returns OK. ContinuationStage returns DONE
when no synthesis is needed, short-circuiting this stage.

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

    Reads:  context.plan (non-None)
    Writes: context.response
    """

    name = "SynthesizerStage"

    def __init__(self, synthesizer: Synthesizer) -> None:
        self._synthesizer = synthesizer

    def run(self, context: PipelineContext) -> StageResult:
        # SynthesizerStage runs whenever the pipeline reaches it.
        # ContinuationStage decides whether we get here (returns OK)
        # or skip it (returns DONE).
        if context.plan is None:
            return StageResult(status=StageStatus.OK, updated_context=context)

        logger.info(banner("Synthesizing"))
        on_token = context.on_token  # Callable[[str], None] | None

        if on_token is not None:
            response = self._synthesizer.stream_synthesize(context.plan, on_token)
        else:
            response = self._synthesizer.synthesize(context.plan)

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
