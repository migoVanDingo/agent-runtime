"""SubAgentRunner — spawns a scoped child Agent for one task and returns the result.

The runner is the only legitimate entry point for sub-agent dispatch. It owns:

- The recursion tripwire (a contextvar that rejects re-entry).
- The child Agent construction (narrowed registry, optional provider/model
  overrides, parent's user_gate so escalations reach the user, custom system
  prompt).
- The scope contextvar (``subagent:<name>``) for the duration of the child's
  execution, so AFM picks the right budget and telemetry / logs tag the work.
- Lifecycle telemetry (``subagent.spawned/completed/failed`` runtime bus
  events with parent_turn_id linkage).
- Timeout enforcement via a wall-clock watcher thread (the child can't be
  hard-killed mid-Python-call without subprocess isolation, but ``pause_check``
  IS propagated so the child can be cooperatively cancelled at checkpoints).
- Structured response parsing when ``spec.response_format == "json"``.

Runtime-as-god alignment: the runner owns lifecycle; the child is a passive
executor. The child cannot decide whether the parent retries/replans on its
output — it just returns ``SubAgentResult`` and dies.
"""
from __future__ import annotations

import contextvars
import json
import threading
import time
from typing import Any, Callable

from logger import get_logger
from runtime.subagents.spec import (
    SubAgentRecursionError,
    SubAgentResult,
    SubAgentSpec,
    SubAgentTimeoutError,
)

logger = get_logger(__name__)


# Recursion tripwire. True while a sub-agent is currently executing on this
# call stack. A second SubAgentRunner.run on the same stack raises
# SubAgentRecursionError. v1 hard-prohibits sub-sub-agents (see plan 0090c).
_inside_subagent: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "arc_inside_subagent", default=False
)


def _emit(event_type: str, *, severity: str = "info", **payload: Any) -> None:
    """Emit a lifecycle event on the runtime bus. Best-effort; never raises."""
    try:
        from runtime.events import RuntimeEvent, get_event_bus, get_runtime_identity
        get_event_bus().emit(RuntimeEvent(
            event_type,
            get_runtime_identity(),
            payload=payload,
            stage="SubAgentRunner",
            severity=severity,
        ))
    except Exception:
        pass


class SubAgentRunner:
    """Owns the construction and execution of one child Agent per task.

    Synchronous: ``run`` blocks the caller until the child returns or its
    timeout fires.
    """

    def run(
        self,
        spec: SubAgentSpec,
        task: str,
        *,
        parent: Any,
        pause_check: Callable[[], None] | None = None,
        parent_turn_id: str | None = None,
    ) -> SubAgentResult:
        """Dispatch a child Agent. Returns a SubAgentResult.

        Args:
            spec: the sub-agent profile (toolsets, provider, prompt, …).
            task: the user-message-equivalent the child receives as its
                only input.
            parent: the calling Agent instance. The runner reads its
                ``user_gate``, ``registry`` (to narrow), ``provider`` (to
                inherit when spec.provider is None), and ``container``
                (passed if non-None for shared services like the event bus).
            pause_check: parent's checkpoint callable. Propagated to the
                child's pipeline so ``/cancel`` mid-subagent works.
            parent_turn_id: parent's current turn ID. Stamped on
                lifecycle telemetry events for parent/child linkage.

        Returns:
            ``SubAgentResult`` with text + optional structured + cost/timing.
            ``ok=False`` on timeout / execution error.

        Raises:
            SubAgentRecursionError: if called while another sub-agent is on
                the call stack.
        """
        if _inside_subagent.get():
            raise SubAgentRecursionError(
                "sub-agents may not spawn further sub-agents (recursion not permitted in v1). "
                "If you need this, file plan 0094."
            )

        # 0090e — merge per-spec config overrides (provider/model/timeout/
        # max_iterations) before dispatch. Spec defaults survive when no
        # override exists for this name.
        spec = self._merge_overrides(spec)

        from runtime.scope import scoped

        scope_tag = f"subagent:{spec.name}"
        t0 = time.monotonic()

        _emit(
            "subagent.spawned",
            name=spec.name,
            provider=spec.provider,
            model=spec.model,
            toolset_names=list(spec.toolset_names),
            skill_names=list(spec.skill_names),
            response_format=spec.response_format,
            parent_turn_id=parent_turn_id,
            timeout_seconds=spec.timeout_seconds,
        )

        token = _inside_subagent.set(True)
        try:
            with scoped(scope_tag):
                result = self._execute(spec, task, parent, pause_check)
        except SubAgentTimeoutError as exc:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            _emit(
                "subagent.failed",
                severity="error",
                name=spec.name,
                error_type="SubAgentTimeoutError",
                error_message=str(exc),
                elapsed_ms=elapsed_ms,
                parent_turn_id=parent_turn_id,
            )
            return SubAgentResult(
                ok=False,
                text="",
                elapsed_ms=elapsed_ms,
                error=str(exc),
            )
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            _emit(
                "subagent.failed",
                severity="error",
                name=spec.name,
                error_type=type(exc).__name__,
                error_message=str(exc)[:500],
                elapsed_ms=elapsed_ms,
                parent_turn_id=parent_turn_id,
            )
            return SubAgentResult(
                ok=False,
                text="",
                elapsed_ms=elapsed_ms,
                error=f"{type(exc).__name__}: {exc}",
            )
        finally:
            _inside_subagent.reset(token)

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        result.elapsed_ms = elapsed_ms
        _emit(
            "subagent.completed",
            name=spec.name,
            elapsed_ms=elapsed_ms,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            cost_usd=result.cost_usd,
            response_chars=len(result.text or ""),
            structured=(result.structured is not None),
            parent_turn_id=parent_turn_id,
        )
        return result

    # ── Internal ──────────────────────────────────────────────────────────

    @staticmethod
    def _merge_overrides(spec: SubAgentSpec) -> SubAgentSpec:
        """Apply per-spec config overrides from config.yml's ``subagents:`` block.

        Reads ``app_config.config.subagents.get(spec.name)``. When the
        override exists, returns a NEW SubAgentSpec (frozen dataclasses
        can't be mutated) with the relevant fields swapped in. Returns
        the original when no override applies.
        """
        try:
            from app_config import config
            override = config.subagents.get(spec.name)
        except Exception:
            return spec
        if override is None:
            return spec
        from dataclasses import replace
        kwargs: dict[str, Any] = {}
        if override.provider is not None:
            kwargs["provider"] = override.provider
        if override.model is not None:
            kwargs["model"] = override.model
        if override.timeout_seconds is not None:
            kwargs["timeout_seconds"] = override.timeout_seconds
        if override.max_iterations is not None:
            kwargs["max_iterations"] = override.max_iterations
        if not kwargs:
            return spec
        logger.info(
            f"  subagent {spec.name!r}: applying config overrides {list(kwargs)}"
        )
        return replace(spec, **kwargs)

    def _execute(
        self,
        spec: SubAgentSpec,
        task: str,
        parent: Any,
        pause_check: Callable[[], None] | None,
    ) -> SubAgentResult:
        """Build child Agent, run it, return SubAgentResult. Inside the scope."""
        # Token snapshot for cost tracking.
        try:
            from runtime.token_tracker import get_tracker
            tracker = get_tracker()
            tokens_in_before = tracker._session_input
            tokens_out_before = tracker._session_output
        except Exception:
            tracker = None
            tokens_in_before = tokens_out_before = 0

        child = self._build_child_agent(spec, parent)

        # Augment the task message with response-format guidance when JSON is required.
        if spec.response_format == "json" and spec.response_schema is not None:
            schema_str = json.dumps(spec.response_schema, indent=2)
            framed_task = (
                f"{task}\n\n"
                f"--- Response format ---\n"
                f"Your final response MUST be valid JSON conforming to this schema:\n"
                f"```json\n{schema_str}\n```\n"
                f"Return ONLY the JSON object — no prose, no markdown fences in your final message."
            )
        else:
            framed_task = task

        result_holder: dict[str, Any] = {}

        def _run_child() -> None:
            try:
                response = child.call(framed_task, checkpoint_fn=pause_check)
                result_holder["text"] = response
            except BaseException as exc:
                result_holder["error"] = exc

        # Wall-clock timeout via a worker thread. The child runs synchronously
        # on this thread; the worker checks elapsed time and abandons if cap
        # exceeded. We CAN'T hard-kill a Python thread mid-call — pause_check
        # is the cooperative interrupt path. Worst case: the orphaned thread
        # finishes after we've already returned. Pragmatic acceptable tradeoff
        # at v1 scale (single sub-agent per parent turn, parent's overall
        # timeout caps the total session).
        worker = threading.Thread(target=_run_child, daemon=True, name=f"subagent-{spec.name}")
        worker.start()
        worker.join(timeout=spec.timeout_seconds)
        if worker.is_alive():
            raise SubAgentTimeoutError(
                f"sub-agent {spec.name!r} exceeded {spec.timeout_seconds}s timeout"
            )
        if "error" in result_holder:
            raise result_holder["error"]

        text = str(result_holder.get("text", ""))

        # Token deltas
        tokens_in = tokens_out = 0
        cost_usd: float | None = None
        if tracker is not None:
            tokens_in = tracker._session_input - tokens_in_before
            tokens_out = tracker._session_output - tokens_out_before
        try:
            from runtime.cost import compute_cost
            model = spec.model or getattr(parent.provider, "model", None)
            cost_usd = compute_cost(model, tokens_in, tokens_out)
        except Exception:
            cost_usd = None

        # Structured response parsing
        structured: dict[str, Any] | None = None
        if spec.response_format == "json":
            structured = self._parse_json_response(text)
            if structured is None:
                logger.warning(
                    f"  subagent {spec.name!r}: response_format=json requested but "
                    f"response wasn't parseable JSON; returning raw text"
                )

        return SubAgentResult(
            ok=True,
            text=text,
            structured=structured,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
        )

    def _build_child_agent(self, spec: SubAgentSpec, parent: Any) -> Any:
        """Construct a scoped child Agent inheriting parent infrastructure."""
        # Lazy import to avoid circular: agent.py imports subagent stuff (eventually).
        from agent import Agent
        from providers.factory import get_provider
        from routing.static_router import StaticRouter
        from skills.registry import SkillRegistry
        from tools.registry import ToolRegistry

        # Provider: spec override or inherit from parent.
        if spec.provider is not None:
            provider = get_provider(spec.provider, spec.model)
        else:
            provider = parent.provider

        # Narrowed ToolRegistry. Filter out SubAgentTool instances even if a
        # parent toolset contains one — recursion-prevention layer 1.
        child_registry = self._build_narrowed_registry(parent.registry, spec.toolset_names)
        child_router = StaticRouter(child_registry)

        # Narrowed SkillRegistry.
        child_skill_registry = self._build_narrowed_skill_registry(
            parent.skill_registry, spec.skill_names
        )

        # Reuse the parent's runtime container if available so the child
        # shares event bus, sandbox, artifact store, etc.
        parent_container = getattr(parent, "_container", None)

        # Build a child container. Agent's __init__ accepts container=... to
        # skip auto-construction; we feed it the narrowed bits.
        from runtime.container import Container
        from app_config import config as _cfg
        if parent_container is not None:
            child_container = Container(
                config=parent_container.config,
                provider=provider,
                runtime_provider=parent_container.runtime_provider,
                registry=child_registry,
                router=child_router,
                event_bus=parent_container.event_bus,
                sandbox=parent_container.sandbox,
                artifact_store=parent_container.artifact_store,
            )
        else:
            # Parent didn't use a container (legacy path) — build a minimal one.
            from providers.factory import get_runtime_provider
            from runtime.events import get_event_bus
            from runtime.sandbox.manager import SandboxManager
            child_container = Container(
                config=_cfg,
                provider=provider,
                runtime_provider=get_runtime_provider(),
                registry=child_registry,
                router=child_router,
                event_bus=get_event_bus(),
                sandbox=SandboxManager(),
                artifact_store=None,
            )

        # Inject parent's spinner at construction time. Critical when the
        # parent runs under the TUI: the parent's spinner is a NoopSpinner so
        # nothing writes to stdout, but if we don't propagate it here the
        # child's `Agent.__init__` builds a fresh real `ui.spinner.Spinner`
        # which writes BRAILLE-dot frames directly to stdout via `\033[2K\r`
        # carriage-return overprinting, corrupting the TUI's alt-screen render:
        # cursor jumps, duplicated/partial spinner lines, the escalation panel
        # shifts down to the footer position. Verified in session
        # SES01KRV1XJ7WK4177X1KHDYEWQ4B. The child can't be in a "more capable"
        # stdout situation than its parent, so inheriting is always safe.
        child = Agent(
            verbose=False,
            user_gate=parent.user_gate,  # share gate — escalations reach the same user
            initial_messages=None,
            container=child_container,
            spinner=parent.spinner,
        )
        # Override the child's skill registry with the narrowed one.
        child.skill_registry = child_skill_registry
        # Re-build planner skill registry binding and validator to match.
        child.planner.set_skill_registry(child_skill_registry)
        child.validator = type(child.validator)(
            set(child_registry.toolset_names()),
            child_registry.tool_names(),
            registered_skills=set(child_skill_registry.names()),
        )

        # Override system prompt if spec provides one. The child still uses
        # config.agent.system_prompt as a fallback if spec didn't override.
        if spec.system_prompt:
            # Stash on the agent for build_routing_system / step_prompt callers.
            # The cleanest way is to monkey-patch config.agent.system_prompt
            # for the child's pipeline lifetime — but that's process-global.
            # Instead, store the override on the agent and have stages prefer it.
            # For v1 we set it as an attribute the stages may read; if a stage
            # reads config.agent.system_prompt directly the override is ignored.
            child._system_prompt_override = spec.system_prompt
        return child

    @staticmethod
    def _build_narrowed_registry(parent_registry: Any, toolset_names: tuple[str, ...]) -> Any:
        """Make a fresh ToolRegistry with only the named toolsets, sub-agent tools filtered."""
        from tools.registry import ToolRegistry
        from tools.implementations.subagents.tool import SubAgentTool  # noqa: F401

        child = ToolRegistry()
        for name in toolset_names:
            if name not in parent_registry.toolset_names():
                logger.warning(f"  subagent: requested toolset {name!r} is not registered, skipping")
                continue
            # Get the original Toolset object so we can filter its tools.
            tools = parent_registry.get_toolset_tools(name)
            from tools.toolset import Toolset
            filtered_tools = [t for t in tools if not isinstance(t, SubAgentTool)]
            if not filtered_tools:
                continue
            # Pull rules from the parent toolset for routing inheritance.
            parent_toolset = parent_registry._toolsets.get(name)
            rules = parent_toolset.rules if parent_toolset is not None else []
            child.register_toolset(Toolset(
                name=name,
                description=parent_toolset.description if parent_toolset else "",
                tools=filtered_tools,
                rules=rules,
                planning_note=getattr(parent_toolset, "planning_note", "") or "",
            ))
        return child

    @staticmethod
    def _build_narrowed_skill_registry(parent_skill_registry: Any, skill_names: tuple[str, ...]) -> Any:
        """Make a fresh SkillRegistry with only the named skills."""
        from skills.registry import SkillRegistry
        skills = []
        for name in skill_names:
            sk = parent_skill_registry.get(name)
            if sk is None:
                logger.warning(f"  subagent: requested skill {name!r} is not registered, skipping")
                continue
            skills.append(sk)
        return SkillRegistry(skills=skills)

    @staticmethod
    def _parse_json_response(text: str) -> dict[str, Any] | None:
        """Parse JSON out of the child's response. Returns None on failure."""
        from runtime.json_extract import extract_json
        data = extract_json(text)
        return data if isinstance(data, dict) else None
