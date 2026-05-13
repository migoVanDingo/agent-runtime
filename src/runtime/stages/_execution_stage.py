"""ExecutionStage — executes a validated plan step by step.

All runtime safeguards are preserved: guard checks, monitor assessments,
RETRY/REPLAN/DEFER/SKIP/ESCALATE/GOAL_ACHIEVED decisions, loop detection,
tool call cap, max_tokens patching, importance scoring.

The per-step ReAct loop is delegated to runtime.tool_loop.ToolLoop.
ContinuationStage (not ExecutionStage) decides whether synthesis runs.
This stage always returns OK.

Submodule layout:
  execution/step_prompt.py  — step_system() builder
  execution/step_runner.py  — run_step() (tool loop construction + hooks)
  execution/step_loop.py    — _StepLoopState + apply_decision()
"""
from __future__ import annotations

import time as _time

from planning.planner import Planner
from planning.schema import Plan, Step, StepStatus, ActionType
from providers.base import BaseProvider
from routing.static_router import StaticRouter
from runtime.context_manager import ContextManager
from runtime.escalation import Escalation
from runtime.guard import ActionGuard, GuardDecision
from runtime.importance import ImportanceScorer
from runtime.monitor import ExecutionMonitor
from runtime.pipeline_context import PipelineContext
from runtime.schema import StepDecision
from runtime.stage_base import Stage
from runtime.stage_result import StageResult, StageStatus
from runtime.tool_executor import ToolCallExecutor
from runtime.utils import banner
from app_config import config
from logger import get_logger

from runtime.stages.execution.step_prompt import step_system as _step_system
from runtime.stages.execution.step_runner import run_step as _run_step_fn
from runtime.stages.execution.step_loop import _StepLoopState, apply_decision

logger = get_logger(__name__)


class ExecutionStage(Stage):
    """Executes all plan steps and stores the final result in context.response.

    Reads:  context.plan
    Writes: context.response

    Always returns OK — internal retry/replan/skip loops handle step-level
    failures without surfacing them to the pipeline runner.
    """

    name = "ExecutionStage"

    def __init__(
        self,
        provider: BaseProvider,
        registry,
        router: StaticRouter,
        context_mgr: ContextManager,
        messenger,
        monitor: ExecutionMonitor,
        guard: ActionGuard,
        user_gate,
        importance_scorer: ImportanceScorer,
        planner: Planner,
        agent_system: str,
        skill_expansion=None,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._router = router
        self._context_mgr = context_mgr
        self._messenger = messenger
        self._monitor = monitor
        self._guard = guard
        self._user_gate = user_gate
        self._importance_scorer = importance_scorer
        self._planner = planner
        self._skill_expansion = skill_expansion  # may be None if not wired
        self._agent_system = agent_system
        self._tool_executor = ToolCallExecutor(registry, guard, user_gate)

    def _resolve_step_tools(self, step: Step) -> list[dict]:
        """Single point of tool resolution for a step.

        1. CONVERSATION → []
        2. Base set: step.tool (if set) else router-selected toolsets
        3. Augment with config.runtime.tool_policy.utility_tools
        4. Resolve names → schemas via registry
        """
        if step.action_type == ActionType.CONVERSATION:
            logger.info("  tools: none (CONVERSATION step)")
            return []

        if step.tool:
            base_names: list[str] = [step.tool]
            base_source = "step.tool"
        elif step.action_type == ActionType.FILE_IO:
            # FILE_IO without an explicit tool means "produce output" — default to
            # write_file so the agent doesn't waste calls on inspection tools.
            # Planner should use ActionType.ANALYSIS + tool="read_file" for reads.
            base_names = ["write_file"]
            base_source = "action_type.FILE_IO(write default)"
        else:
            selected_sets = self._router.select(step.description, self._messenger.get_messages())
            if step.action_type.value not in selected_sets:
                selected_sets = list(set(selected_sets + [step.action_type.value]))
            base_names = []
            for ts in selected_sets:
                base_names.extend(self._registry.toolset_tool_names(ts))
            base_source = f"router(toolsets={selected_sets})"

        utility_map = config.runtime.tool_policy.utility_tools
        utilities: list[str] = []
        for name in list(base_names):
            for u in utility_map.get(name, []):
                if u not in base_names and u not in utilities:
                    utilities.append(u)

        final_names = base_names + utilities
        logger.info(
            f"  tool selection: base={base_names} ({base_source})"
            + (f" utilities={utilities}" if utilities else "")
        )

        schemas: list[dict] = []
        for name in final_names:
            schemas.extend(self._registry.get_tool_schema(name))
        return schemas

    def run(self, context: PipelineContext) -> StageResult:
        # No-op for direct mode (plan is None).
        if context.plan is None:
            return StageResult(status=StageStatus.OK, updated_context=context)

        # Mint plan_run_id so execution events join to this plan instance.
        if context.identity is not None:
            context.identity = context.identity.for_plan_run()
        self._identity = context.identity

        self._active_skill_name = context.active_skill_name
        self._rag_context = context.rag_context
        self._checkpoint = context._pause_check  # stored for _run_step
        response = self._execute_plan(context.plan, db_session_id=context.db_session_id)
        context.response = response

        # ContinuationStage owns the next-step decision.
        return StageResult(status=StageStatus.OK, updated_context=context)

    def _execute_plan(self, plan: Plan, *, db_session_id: str | None = None) -> str:
        from runtime.persistence import PersistenceWriter
        from runtime.events import RuntimeEvent, get_event_bus

        _identity = getattr(self, "_identity", None)
        _bus = get_event_bus()
        if _identity is not None:
            _bus.emit(RuntimeEvent(
                "plan.created",
                _identity,
                payload={"n_steps": len(plan.steps),
                         "action_types": list({s.action_type.value for s in plan.steps})},
                stage="ExecutionStage",
            ))

        state = _StepLoopState.from_plan(plan, self._messenger)

        # Persistence: record plan
        state.db_plan_id = PersistenceWriter.record_plan(
            db_session_id or "",
            plan_index=state.replan_count,
            original_query=plan.original_query,
            steps=[
                {"step": s.step, "action_type": s.action_type.value, "description": s.description}
                for s in plan.steps
            ],
        )

        max_retries = config.runtime.execution_monitor.max_step_retries
        max_defers = config.runtime.execution_monitor.max_defers_per_step

        while state.has_more():
            step = state.current()
            n_total = len(state.queue)
            step_display = state.step_display()
            step.status = StepStatus.RUNNING
            _step_t0 = _time.monotonic()
            retry_label = f" RETRY ({step.flags.retry_count}/{max_retries})" if step.flags.retry_count > 0 else ""
            logger.info(banner(f"Step {step_display}/{n_total} [{step.action_type.value}]{retry_label}"))
            logger.info(f"  {step.description}")
            if _identity is not None:
                _bus.emit(RuntimeEvent(
                    "step.started",
                    _identity,
                    payload={"step_index": step.step, "action_type": step.action_type.value,
                             "tool": step.tool, "description_preview": step.description[:120]},
                    stage="ExecutionStage",
                ))

            if state.idx > 0 or step.flags.retry_count > 0:
                if step.flags.retry_count > 0:
                    self._messenger.add_user_message(
                        f"Retry step {step_display}: {step.description}\n"
                        f"Previous attempt failed. Try a different approach."
                    )
                else:
                    prev = state.queue[state.idx - 1]
                    prev_result = prev.result or "(no result captured)"
                    self._messenger.add_user_message(
                        f"Step {step_display - 1} complete. Result:\n{prev_result}\n\n"
                        f"Now execute step {step_display}: {step.description}"
                    )

            tools = self._resolve_step_tools(step)

            # Pre-execution guard (step level)
            step_guard = self._guard.check_step(step.description, step.action_type.value)
            if step_guard == GuardDecision.BLOCK:
                logger.info(f"  guard: BLOCKED step — {step.description[:60]}")
                result = f"Step blocked by safety policy: {step.description}"
            elif step_guard == GuardDecision.ESCALATE:
                logger.info(f"  guard: ESCALATE step — {step.description[:60]}")
                escalation = Escalation(
                    reason=f"Step contains potentially destructive operation: {step.description}",
                    source="guard",
                )
                system = _step_system(plan, step, self._agent_system, self._rag_context, step_display)
                if self._user_gate.prompt(escalation):
                    result = self._run_step(step, n_total, tools, system, query=plan.original_query, plan_start_index=state.plan_start_index, step_display=step_display)
                else:
                    result = f"Step denied by user: {step.description}"
                    step.error = "user denied escalation"
            else:
                system = _step_system(plan, step, self._agent_system, self._rag_context, step_display)
                result = self._run_step(step, n_total, tools, system, query=plan.original_query, plan_start_index=state.plan_start_index, step_display=step_display)

            step.result = result[:1000] if result else None

            # Advisory produces check
            if step.produces:
                try:
                    from runtime.artifact_store import get_artifact_store
                    expected_key = step.produces.strip()
                    if expected_key and get_artifact_store().meta(expected_key) is None:
                        logger.info(
                            f"  produces='{expected_key}' declared but artifact not registered "
                            f"(step used {step.tool!r} — only store_artifact registers artifacts)"
                        )
                except Exception as e:
                    logger.info(f"  produces-check skipped: {e}")

            # LLM importance scoring
            if result and step.status != StepStatus.ERROR:
                msg_index = len(self._messenger.get_messages()) - 1
                importance = self._importance_scorer.score(
                    plan.original_query, step.description, result
                )
                self._context_mgr.set_importance(msg_index, importance)

            # Persistence: record step result
            if db_session_id and state.db_plan_id:
                _importance = (
                    self._context_mgr.get_importance(len(self._messenger.get_messages()) - 1)
                    if result and step.status != StepStatus.ERROR else None
                )
                PersistenceWriter.record_step(
                    db_session_id=db_session_id,
                    db_plan_id=state.db_plan_id,
                    step_index=step.step,
                    action_type=step.action_type.value,
                    description=step.description,
                    tool=step.tool if hasattr(step, "tool") else None,
                    status=step.status.value if hasattr(step.status, "value") else str(step.status),
                    result=result,
                    error=step.error if hasattr(step, "error") else None,
                    retry_count=step.flags.retry_count if hasattr(step, "flags") else 0,
                    importance_score=_importance.value if _importance is not None else None,
                )

            # Monitor assessment
            logger.info(banner(f"Monitor: Step {step_display}/{n_total}"))
            assessment = self._monitor.assess(
                step, plan, result or "",
                active_skill_name=getattr(self, "_active_skill_name", None),
            )
            prev_idx = state.idx

            apply_decision(
                state, plan, step, assessment,
                planner=self._planner,
                skill_expansion=self._skill_expansion,
                db_session_id=db_session_id,
                identity=_identity,
                user_gate=self._user_gate,
                max_retries=max_retries,
            )

            # Emit step.completed with timing for CONTINUE decisions
            if assessment.decision == StepDecision.CONTINUE and _identity is not None:
                _step_dur = int((_time.monotonic() - _step_t0) * 1000)
                _step_imp = self._context_mgr.get_importance(
                    len(self._messenger.get_messages()) - 1
                ) if result else None
                _bus.emit(RuntimeEvent(
                    "step.completed",
                    _identity,
                    payload={"step_index": step.step, "status": "completed",
                             "duration_ms": _step_dur,
                             "importance_score": _step_imp.value if _step_imp else None},
                    stage="ExecutionStage",
                ))

        logger.info(banner("Execution complete"))
        last_completed = next(
            (s for s in reversed(state.queue) if s.status == StepStatus.COMPLETED and s.result),
            None,
        )
        return last_completed.result if last_completed else ""

    def _run_step(
        self,
        step: Step,
        n_total: int,
        tools: list[dict],
        system: str,
        query: str = "",
        plan_start_index: int | None = None,
        step_display: int = 0,
    ) -> str:
        """Delegate to run_step() in execution/step_runner.py."""
        return _run_step_fn(
            step=step,
            n_total=n_total,
            tools=tools,
            system=system,
            provider=self._provider,
            messenger=self._messenger,
            context_mgr=self._context_mgr,
            tool_executor=self._tool_executor,
            user_gate=self._user_gate,
            query=query,
            plan_start_index=plan_start_index,
            step_display=step_display,
            checkpoint=getattr(self, "_checkpoint", None),
            parent_identity=getattr(self, "_identity", None),
        )
