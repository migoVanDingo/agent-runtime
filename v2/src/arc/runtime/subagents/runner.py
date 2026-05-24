"""SubAgentRunner — sync dispatch of a child AgentSession.

One runner per parent session. Owns the per-spec DispatchGuard state.
Each dispatch:
  1. Checks the tripwire (recursion prohibition layer 2)
  2. Asks the guard whether to allow this attempt
  3. Builds a child Config + AgentSession (real, full)
  4. Runs the child in a watchdog thread with timeout
  5. Retries transient errors per spec.max_transient_retries
  6. Collects metrics from the child's bus
  7. Updates the guard with the outcome
  8. Emits subagent.* events on the PARENT's bus
  9. Returns a SubAgentResult

The child has its own bus, its own session dir, its own everything.
Cross-bus event propagation is forbidden — telemetry that needs both
queries each events.jsonl separately.
"""
from __future__ import annotations

import threading
import time
from dataclasses import replace as dc_replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from arc.config import Config, ProviderConfig, RetryConfig, RuntimeConfig
from arc.runtime.bus import EventBus, HookRegistry
from arc.runtime.events import EventType, RuntimeEvent, Severity
from arc.runtime.hooks import Cancelled, Message
from arc.runtime.ids import new_session_id
from arc.runtime.scope import scoped
from arc.runtime.subagents.errors import (
    SubAgentError,
    SubAgentTimeoutError,
)
from arc.runtime.subagents.guards import DispatchGuard, classify_error
from arc.runtime.subagents.registry import SubAgentRegistry
from arc.runtime.subagents.result import SubAgentResult
from arc.runtime.subagents.spec import SubAgentSpec
from arc.runtime.subagents.tripwire import inside_subagent, subagent_scope
from arc.tools.base import ToolRegistry


# Default API key env vars per provider — used when spec doesn't override.
_DEFAULT_API_KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "ollama": "OLLAMA_API_KEY",
    "llama_cpp": "LLAMA_CPP_API_KEY",
    "openai": "OPENAI_API_KEY",
}


# Backoff schedule for transient-error retries (seconds).
# Three slots so we can retry up to spec.max_transient_retries (default 2).
_BACKOFF_SCHEDULE = (0.5, 2.0, 8.0)


class SubAgentRunner:
    """One per parent session. Owns guard state."""

    def __init__(
        self,
        *,
        registry: SubAgentRegistry,
        parent_bus: EventBus,
        parent_tools: ToolRegistry,
        parent_config: Config,
        arc_home: Path,
        sessions_dir: Path,
    ) -> None:
        self._registry = registry
        self._parent_bus = parent_bus
        self._parent_tools = parent_tools
        self._parent_config = parent_config
        self._arc_home = arc_home
        self._sessions_dir = sessions_dir
        self._guard = DispatchGuard()

    @property
    def guard(self) -> DispatchGuard:
        """Exposed for tests + future telemetry."""
        return self._guard

    # ── Public dispatch ────────────────────────────────────────────────────

    def dispatch(
        self,
        spec_name: str,
        task: str,
        *,
        context_bundle: str | None = None,
        parent_session_id: str,
        parent_turn_id: str | None = None,
        count_against_quota: bool = True,
    ) -> SubAgentResult:
        """Run one sub-agent dispatch end-to-end.

        Sync — blocks until the child finishes or times out. The result is
        a SubAgentResult; callers in the tool-adapter path convert error
        statuses to ToolError so the parent agent can recover.
        """
        from arc.runtime.subagents.errors import SubAgentRecursionError

        if inside_subagent():
            # Tripwire — recursion prohibition layer 2.
            raise SubAgentRecursionError(
                f"sub-agent {spec_name!r} attempted to dispatch from inside another sub-agent"
            )

        spec = self._registry.get(spec_name)

        # ── Guard pre-check ─────────────────────────────────────────────
        if count_against_quota:
            outcome = self._guard.try_acquire(
                spec_name,
                max_dispatches=spec.max_dispatches_per_session,
                max_consecutive_failures=spec.max_consecutive_failures,
            )
            if not outcome.allowed:
                if outcome.fired_event_type:
                    self._parent_bus.emit(RuntimeEvent(
                        type=outcome.fired_event_type,  # already canonical event string
                        stage="SubAgentRunner",
                        severity=Severity.WARN,
                        payload={
                            "spec_name": spec_name,
                            "cap": spec.max_dispatches_per_session,
                            "denied_task_chars": len(task),
                        },
                    ))
                # Denial surfaces as an error result. The tool adapter
                # converts to ToolError.
                return SubAgentResult(
                    status="error",
                    output="",
                    error_message=outcome.reason,
                    child_session_id="",
                    cost_usd=0.0,
                    turns=0,
                    tool_calls=0,
                    wallclock_s=0.0,
                )
            self._guard.record_attempt(spec_name)

        # ── Transient-retry loop ────────────────────────────────────────
        retries_attempted = 0
        last_error: BaseException | None = None
        for attempt in range(spec.max_transient_retries + 1):
            try:
                result = self._dispatch_once(
                    spec=spec,
                    task=task,
                    context_bundle=context_bundle,
                    parent_session_id=parent_session_id,
                    parent_turn_id=parent_turn_id,
                    retries_so_far=retries_attempted,
                )
                # Update guard based on outcome status. Don't re-trip if
                # already tripped.
                tripped = self._guard.record_outcome(
                    spec_name,
                    status=result.status,
                    max_consecutive_failures=spec.max_consecutive_failures,
                )
                if tripped:
                    self._parent_bus.emit(RuntimeEvent(
                        type=EventType.SUBAGENT_CIRCUIT_TRIPPED,
                        stage="SubAgentRunner",
                        severity=Severity.WARN,
                        payload={
                            "spec_name": spec_name,
                            "consecutive_failures": self._guard.consecutive_failures(spec_name),
                            "triggering_child_session_id": result.child_session_id,
                        },
                    ))
                return result
            except _TransientError as te:
                last_error = te.original
                retries_attempted += 1
                if attempt >= spec.max_transient_retries:
                    break
                backoff = _BACKOFF_SCHEDULE[min(attempt, len(_BACKOFF_SCHEDULE) - 1)]
                self._parent_bus.emit(RuntimeEvent(
                    type=EventType.SUBAGENT_RETRY_ATTEMPTED,
                    stage="SubAgentRunner",
                    severity=Severity.WARN,
                    payload={
                        "spec_name": spec_name,
                        "attempt": attempt + 1,
                        "error_class": type(te.original).__name__,
                        "backoff_s": backoff,
                    },
                ))
                time.sleep(backoff)

        # All transient retries exhausted.
        msg = f"{type(last_error).__name__}: {last_error}" if last_error else "transient error"
        result = SubAgentResult(
            status="error",
            output="",
            error_message=msg,
            child_session_id="",
            cost_usd=0.0,
            turns=0,
            tool_calls=0,
            wallclock_s=0.0,
            retries_attempted=retries_attempted,
        )
        tripped = self._guard.record_outcome(
            spec_name,
            status="error",
            max_consecutive_failures=spec.max_consecutive_failures,
        )
        if tripped:
            self._parent_bus.emit(RuntimeEvent(
                type=EventType.SUBAGENT_CIRCUIT_TRIPPED,
                stage="SubAgentRunner",
                severity=Severity.WARN,
                payload={
                    "spec_name": spec_name,
                    "consecutive_failures": self._guard.consecutive_failures(spec_name),
                    "triggering_child_session_id": "",
                },
            ))
        return result

    # ── Internal: one attempt ──────────────────────────────────────────────

    def _dispatch_once(
        self,
        *,
        spec: SubAgentSpec,
        task: str,
        context_bundle: str | None,
        parent_session_id: str,
        parent_turn_id: str | None,
        retries_so_far: int,
    ) -> SubAgentResult:
        """One dispatch attempt. Raises _TransientError for retryable failures.

        Logical failures (timeout, tool error, malformed model output) are
        returned as SubAgentResult(error/timeout/cancelled).
        """
        child_session_id = new_session_id()
        wall_start = time.monotonic()

        # Resolve the child's tool list from the spec's allowlist intersected
        # with the parent's registry. Missing tools are a hard error.
        child_tools = self._build_child_tool_registry(spec)

        # Build the child config.
        child_config = self._build_child_config(spec)

        # Emit subagent.dispatched on parent's bus BEFORE building child
        # infrastructure so the event chain is clean.
        self._parent_bus.emit(RuntimeEvent(
            type=EventType.SUBAGENT_DISPATCHED,
            stage="SubAgentRunner",
            payload={
                "spec_name": spec.name,
                "provider": spec.provider,
                "model": spec.model,
                "child_session_id": child_session_id,
                "parent_turn": parent_turn_id,
                "task_chars": len(task),
                "retry_attempt": retries_so_far,
            },
        ))

        # Build the child's provider. Late import to avoid circular at
        # module load time.
        from arc.providers import build as build_provider
        try:
            child_provider = build_provider(child_config.provider)
        except Exception as exc:
            reason = classify_error(exc)
            if reason:
                raise _TransientError(exc) from exc
            # Logical failure — surface immediately.
            self._parent_bus.emit(RuntimeEvent(
                type=EventType.SUBAGENT_ABORTED,
                stage="SubAgentRunner",
                severity=Severity.ERROR,
                payload={
                    "spec_name": spec.name,
                    "child_session_id": child_session_id,
                    "reason": "provider_error",
                    "turns": 0,
                    "wallclock_s": time.monotonic() - wall_start,
                },
                content={"error_message": f"{type(exc).__name__}: {exc}"},
            ))
            return SubAgentResult(
                status="error",
                output="",
                error_message=f"{type(exc).__name__}: {exc}",
                child_session_id=child_session_id,
                cost_usd=0.0,
                turns=0,
                tool_calls=0,
                wallclock_s=time.monotonic() - wall_start,
                retries_attempted=retries_so_far,
            )

        # Construct a fresh registry + bus for the child. CRITICAL: this is
        # NOT the parent's bus — events are isolated.
        child_registry = HookRegistry(
            failure_threshold=child_config.plugins.failure_threshold,
            exception_message_max_chars=child_config.plugins.exception_message_max_chars,
        )
        child_bus = EventBus(child_registry)

        # Watchdog cancellation flag — set by the timer when timeout expires.
        # The child's pause_check observer (registered below) checks it
        # between turns and raises Cancelled.
        cancel_flag = threading.Event()
        timeout_flag = threading.Event()  # distinguishes timeout vs. external cancel

        # Single observer that handles both pause_check (cancellation) and
        # on_event (metrics). The HookRegistry tracks instances by `name`,
        # so combining them keeps the registration clean.
        class _ChildObserver:
            name = "_subagent_runner_observer"

            def __init__(self, metrics_collector):
                self._metrics = metrics_collector

            def pause_check(self, ctx) -> None:
                if cancel_flag.is_set():
                    raise Cancelled()

            def on_event(self, ctx, event: RuntimeEvent) -> None:
                self._metrics.on_event(ctx, event)

        metrics = _ChildMetricsObserver()
        child_registry.register(
            _ChildObserver(metrics),
            hooks_order={"pause_check": 1, "on_event": 100},
        )

        # Build the child AgentSession.
        from arc.runtime.loop import AgentSession
        child_session = AgentSession(
            config=child_config,
            provider=child_provider,
            tools=child_tools,
            registry=child_registry,
            bus=child_bus,
            session_id=child_session_id,
        )

        # Watchdog timer — fires once after timeout_s.
        def _watchdog():
            timeout_flag.set()
            cancel_flag.set()

        timer = threading.Timer(spec.timeout_s, _watchdog)
        timer.daemon = True
        timer.start()

        full_task = task if not context_bundle else f"{context_bundle}\n\n{task}"

        outcome_status = "ok"
        error_message: str | None = None
        final_text = ""
        try:
            with subagent_scope(), scoped(f"subagent:{spec.name}"):
                child_session.start()
                turn_outcome = child_session.run_turn(full_task)
                final_text = turn_outcome.final_response
                if turn_outcome.error == "cancelled" and timeout_flag.is_set():
                    outcome_status = "timeout"
                    error_message = f"sub-agent timed out after {spec.timeout_s:.0f}s"
                elif turn_outcome.error == "cancelled":
                    outcome_status = "cancelled"
                    error_message = "sub-agent cancelled"
                elif not turn_outcome.success:
                    outcome_status = "error"
                    error_message = turn_outcome.error or "sub-agent reported failure"
                child_session.end()
        except Exception as exc:
            reason = classify_error(exc)
            if reason:
                timer.cancel()
                raise _TransientError(exc) from exc
            outcome_status = "error"
            error_message = f"{type(exc).__name__}: {exc}"
        finally:
            timer.cancel()

        wallclock = time.monotonic() - wall_start

        # Emit subagent.returned or subagent.aborted on parent's bus.
        if outcome_status == "ok":
            self._parent_bus.emit(RuntimeEvent(
                type=EventType.SUBAGENT_RETURNED,
                stage="SubAgentRunner",
                payload={
                    "spec_name": spec.name,
                    "child_session_id": child_session_id,
                    "status": "ok",
                    "cost_usd": metrics.cost_usd,
                    "turns": metrics.turns,
                    "tool_calls": metrics.tool_calls,
                    "wallclock_s": wallclock,
                    "output_chars": len(final_text),
                },
            ))
        else:
            self._parent_bus.emit(RuntimeEvent(
                type=EventType.SUBAGENT_ABORTED,
                stage="SubAgentRunner",
                severity=Severity.WARN,
                payload={
                    "spec_name": spec.name,
                    "child_session_id": child_session_id,
                    "reason": outcome_status,
                    "turns": metrics.turns,
                    "wallclock_s": wallclock,
                },
                content={"error_message": error_message},
            ))

        return SubAgentResult(
            status=outcome_status,  # type: ignore[arg-type]
            output=final_text,
            error_message=error_message,
            child_session_id=child_session_id,
            cost_usd=metrics.cost_usd,
            turns=metrics.turns,
            tool_calls=metrics.tool_calls,
            wallclock_s=wallclock,
            retries_attempted=retries_so_far,
        )

    # ── Child config / tool construction ───────────────────────────────────

    def _build_child_tool_registry(self, spec: SubAgentSpec) -> ToolRegistry:
        """Intersect spec.tools with the parent's registry. Missing = error."""
        child = ToolRegistry()
        for name in spec.tools:
            try:
                tool = self._parent_tools.get(name)
            except KeyError as exc:
                raise SubAgentError(
                    f"sub-agent {spec.name!r} declares tool {name!r} but it is "
                    f"not available in the parent's registry"
                ) from exc
            child.register(tool)
        return child

    def _build_child_config(self, spec: SubAgentSpec) -> Config:
        """Construct a child Config by swapping fields off the parent's."""
        parent = self._parent_config

        # Provider — pin to spec's provider/model. Inherit retry/timeout
        # from parent (reasonable defaults; spec can override env/url).
        api_key_env = spec.api_key_env or _DEFAULT_API_KEY_ENV.get(spec.provider, "")
        child_provider = ProviderConfig(
            name=spec.provider,
            model=spec.model,
            api_key_env=api_key_env,
            base_url=spec.base_url,
            timeout_seconds=parent.provider.timeout_seconds,
            retry=parent.provider.retry,
            params=dict(parent.provider.params),
        )

        # Runtime — override system prompt + cap iterations to spec.max_turns.
        sys_prompt = spec.system_prompt
        if spec.expected_output:
            sys_prompt = (
                f"{sys_prompt}\n\n"
                f"Your final message MUST follow this output shape: {spec.expected_output}"
            )

        child_runtime = RuntimeConfig(
            workspace=parent.runtime.workspace,
            max_iterations=min(parent.runtime.max_iterations, spec.max_turns),
            max_tool_calls_per_turn=parent.runtime.max_tool_calls_per_turn,
            show_thinking=False,
            log_level=parent.runtime.log_level,
            system_prompt=sys_prompt,
            iteration_cap_message=parent.runtime.iteration_cap_message,
            tool_call_cap_message=parent.runtime.tool_call_cap_message,
            cycle_detection_threshold=parent.runtime.cycle_detection_threshold,
            cycle_detected_message=parent.runtime.cycle_detected_message,
        )

        # Plugins — empty for v0.1. Child runs with no recorder/log_writer.
        # Future: spec can opt in via a `plugins:` field.
        from arc.config import PluginsConfig
        child_plugins = PluginsConfig(
            failure_threshold=parent.plugins.failure_threshold,
            exception_message_max_chars=parent.plugins.exception_message_max_chars,
            enabled=[],
        )

        # Keep tui/bootstrap as the parent's; child won't render.
        return Config(
            runtime=child_runtime,
            provider=child_provider,
            tools=parent.tools,
            plugins=child_plugins,
            tui=parent.tui,
            bootstrap=parent.bootstrap,
            source_path=parent.source_path,
        )


# ── Internal helpers ───────────────────────────────────────────────────────


class _TransientError(Exception):
    """Wraps an underlying transient exception so the retry loop can catch it."""
    def __init__(self, original: BaseException) -> None:
        self.original = original
        super().__init__(repr(original))


class _ChildMetricsObserver:
    """Subscribes to the child's bus + on_event hook; sums tokens/cost/turns."""

    def __init__(self) -> None:
        self.turns = 0
        self.tool_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost_usd = 0.0  # left at 0.0 for v0.1; future: PricingTable lookup

    def on_bus_event(self, event: RuntimeEvent) -> None:
        """Bus subscription — catches every emitted event on the child's bus."""
        t = event.type
        if t == EventType.TURN_ENDED:
            self.turns += 1
        elif t == EventType.TOOL_CALL_COMPLETED:
            self.tool_calls += 1
        elif t == EventType.LLM_CALL_COMPLETED:
            self.input_tokens += int(event.payload.get("input_tokens", 0) or 0)
            self.output_tokens += int(event.payload.get("output_tokens", 0) or 0)

    def on_event(self, ctx, event: RuntimeEvent) -> None:
        """Hook contract — same as on_bus_event but signature matches OnEvent protocol."""
        self.on_bus_event(event)
