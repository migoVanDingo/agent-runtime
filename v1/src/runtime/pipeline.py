from __future__ import annotations
from typing import Callable
from runtime.stage_base import Stage
from runtime.pipeline_context import PipelineContext
from runtime.stage_result import StageResult, StageStatus
from runtime.events import RuntimeEvent, get_event_bus, get_runtime_identity
from logger import get_logger

logger = get_logger(__name__)

def _max_retries() -> int:
    from app_config import config
    return getattr(config.runtime.pipeline, "max_retries_per_stage", 2)

def _max_ask_user() -> int:
    from app_config import config
    return getattr(config.runtime.pipeline, "max_ask_user_per_stage", 1)


class Pipeline:
    """Ordered stage runner with defined transition semantics.

    Transition rules per StageStatus returned by a stage:

      OK        → Advance to the next stage. Reset retry_count to 0.
      DONE      → Return context.response immediately (short-circuit).
      RETRY     → Re-run the same stage after injecting result.reason into
                  context.failure_reason. If retry_count >= _max_retries(),
                  treat as ABORT instead.
      ASK_USER  → Present result.user_message to the user via user_input_fn.
                  Append the user's response to context.user_message as a
                  clarification, then re-run the same stage. If ask count for
                  this stage >= _max_ask_user(), treat as ABORT instead.
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
        # Mint a pipeline_run_id so all events within this run share a correlation id.
        # Prefer identity already on the context (carries session/turn IDs from main.py);
        # fall back to the process-level identity for tests or headless contexts.
        base_identity = context.identity if context.identity is not None else get_runtime_identity()
        context.identity = base_identity.for_pipeline()

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
        import time as _time
        retry_count = 0
        ask_count = ask_counts.get(stage.name, 0)

        identity = context.identity
        bus = get_event_bus()
        if identity is not None:
            bus.emit(RuntimeEvent(
                "stage.started",
                identity,
                payload={"stage_name": stage.name},
                stage=stage.name,
            ))

        t0 = _time.monotonic()
        while True:
            # Cooperative yield point — gives InProcessAgentService a chance
            # to pause or cancel between stage invocations (including retries).
            if context._pause_check is not None:
                context._pause_check()  # may raise TurnCancelledError

            result = stage.run(context)
            context = result.updated_context

            if result.status in (StageStatus.OK, StageStatus.DONE, StageStatus.ABORT):
                duration_ms = int((_time.monotonic() - t0) * 1000)
                if identity is not None:
                    bus.emit(RuntimeEvent(
                        "stage.finished",
                        identity,
                        payload={
                            "stage_name": stage.name,
                            "status": result.status.value,
                            "duration_ms": duration_ms,
                        },
                        stage=stage.name,
                    ))
                return result

            if result.status == StageStatus.RETRY:
                retry_count += 1
                if retry_count > _max_retries():
                    logger.info(
                        f"  pipeline: '{stage.name}' exceeded max retries "
                        f"({_max_retries()}) — converting to ABORT"
                    )
                    return StageResult(
                        status=StageStatus.ABORT,
                        updated_context=context,
                        reason=f"max retries exceeded: {result.reason}",
                    )
                logger.info(
                    f"  pipeline: retrying '{stage.name}' "
                    f"({retry_count}/{_max_retries()}): {result.reason}"
                )
                context.retry_count = retry_count
                context.failure_reason = result.reason
                continue

            if result.status == StageStatus.ASK_USER:
                ask_count += 1
                ask_counts[stage.name] = ask_count
                if ask_count > _max_ask_user():
                    logger.info(
                        f"  pipeline: '{stage.name}' exceeded max user prompts "
                        f"({_max_ask_user()}) — converting to ABORT"
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
