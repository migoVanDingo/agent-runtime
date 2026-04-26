from __future__ import annotations
from typing import Callable
from runtime.stage_base import Stage
from runtime.pipeline_context import PipelineContext
from runtime.stage_result import StageResult, StageStatus
from logger import get_logger

logger = get_logger(__name__)

# Maximum times the runner will re-run a stage on RETRY before escalating to ABORT.
_MAX_RETRIES_PER_STAGE = 2

# Maximum times the runner will ask the user a clarifying question for a single
# stage before escalating to ABORT.
_MAX_ASK_USER_PER_STAGE = 1


class Pipeline:
    """Ordered stage runner with defined transition semantics.

    Transition rules per StageStatus returned by a stage:

      OK        → Advance to the next stage. Reset retry_count to 0.
      DONE      → Return context.response immediately (short-circuit).
      RETRY     → Re-run the same stage after injecting result.reason into
                  context.failure_reason. If retry_count >= _MAX_RETRIES_PER_STAGE,
                  treat as ABORT instead.
      ASK_USER  → Present result.user_message to the user via user_input_fn.
                  Append the user's response to context.user_message as a
                  clarification, then re-run the same stage. If ask count for
                  this stage >= _MAX_ASK_USER_PER_STAGE, treat as ABORT instead.
      ABORT     → Skip remaining stages; run the fallback stage (DirectExecutionStage)
                  and return its response. If the fallback itself ABORTs, return "".
    """

    def __init__(
        self,
        stages: list[Stage],
        fallback_stage: Stage,
        user_input_fn: Callable[[str], str],
    ) -> None:
        self._stages = stages
        self._fallback_stage = fallback_stage
        self._user_input_fn = user_input_fn

    def run(self, context: PipelineContext) -> str:
        """Run all stages in order and return the final response string."""
        ask_counts: dict[str, int] = {}
        idx = 0

        while idx < len(self._stages):
            stage = self._stages[idx]
            context.retry_count = 0
            context.failure_reason = None

            result = self._run_stage(stage, context, ask_counts)
            context = result.updated_context

            if result.status == StageStatus.DONE:
                logger.info(f"  pipeline: DONE from '{stage.name}'")
                return context.response or ""

            if result.status == StageStatus.ABORT:
                logger.info(f"  pipeline: ABORT from '{stage.name}' — {result.reason or 'no reason given'}")
                return self._run_fallback(context)

            # StageStatus.OK — advance to next stage
            idx += 1

        return context.response or ""

    def _run_stage(
        self,
        stage: Stage,
        context: PipelineContext,
        ask_counts: dict[str, int],
    ) -> StageResult:
        """Run a single stage, handling RETRY and ASK_USER loops internally."""
        retry_count = 0
        ask_count = ask_counts.get(stage.name, 0)

        while True:
            result = stage.run(context)
            context = result.updated_context

            if result.status in (StageStatus.OK, StageStatus.DONE, StageStatus.ABORT):
                return result

            if result.status == StageStatus.RETRY:
                retry_count += 1
                if retry_count > _MAX_RETRIES_PER_STAGE:
                    logger.info(
                        f"  pipeline: '{stage.name}' exceeded max retries "
                        f"({_MAX_RETRIES_PER_STAGE}) — converting to ABORT"
                    )
                    return StageResult(
                        status=StageStatus.ABORT,
                        updated_context=context,
                        reason=f"max retries exceeded: {result.reason}",
                    )
                logger.info(
                    f"  pipeline: retrying '{stage.name}' "
                    f"({retry_count}/{_MAX_RETRIES_PER_STAGE}): {result.reason}"
                )
                context.retry_count = retry_count
                context.failure_reason = result.reason
                continue

            if result.status == StageStatus.ASK_USER:
                ask_count += 1
                ask_counts[stage.name] = ask_count
                if ask_count > _MAX_ASK_USER_PER_STAGE:
                    logger.info(
                        f"  pipeline: '{stage.name}' exceeded max user prompts "
                        f"({_MAX_ASK_USER_PER_STAGE}) — converting to ABORT"
                    )
                    return StageResult(
                        status=StageStatus.ABORT,
                        updated_context=context,
                        reason="max user prompts exceeded",
                    )
                question = result.user_message or "Can you clarify your request?"
                logger.info(f"  pipeline: '{stage.name}' asking user: {question}")
                user_response = self._user_input_fn(question)
                context.user_message = context.user_message + "\n\nClarification: " + user_response
                context.failure_reason = None
                continue

    def _run_fallback(self, context: PipelineContext) -> str:
        """Run the fallback stage. Returns its response or "" if it also ABORTs."""
        logger.info(f"  pipeline: running fallback '{self._fallback_stage.name}'")
        result = self._fallback_stage.run(context)
        if result.status == StageStatus.ABORT:
            logger.info("  pipeline: fallback also ABORTed — returning empty response")
            return ""
        return result.updated_context.response or ""
