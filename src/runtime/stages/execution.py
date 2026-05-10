"""ExecutionStage — executes a validated plan step by step.

All runtime safeguards are preserved: guard checks, monitor assessments,
RETRY/REPLAN/DEFER/SKIP/ESCALATE/GOAL_ACHIEVED decisions, loop detection,
tool call cap, max_tokens patching, importance scoring.

The per-step ReAct loop is delegated to runtime.tool_loop.ToolLoop.
ContinuationStage (not ExecutionStage) decides whether synthesis runs.
This stage always returns OK.
"""
from __future__ import annotations
from planning.planner import Planner
from planning.schema import Plan, Step, StepStatus, ActionType
from providers.base import BaseProvider, TextBlock, ToolUseBlock
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
from runtime.tool_loop import ToolLoop, ToolLoopConfig
from runtime.utils import banner, fmt_input, fmt_result, has_error_indicator
from app_config import config
from logger import get_logger
from session_paths import build_analysis_manifest

logger = get_logger(__name__)


def _step_system(plan: Plan, current_step: Step, agent_system: str, rag_context: str = "", step_display: int = 0) -> str:
    """Build the per-step system prompt showing plan progress."""
    lines = []
    for display_num, s in enumerate(plan.steps, start=1):
        if s.status == StepStatus.COMPLETED:
            marker = "✓"
        elif s.step == current_step.step:
            marker = "→"
        else:
            marker = " "
        lines.append(f"  {marker} Step {display_num}: {s.description}")

    tool_note = ""
    if current_step.tool:
        tool_note = f"\nYou have been given ONLY the '{current_step.tool}' tool for this step. Call it ONCE on the target specified in this step's description, then stop.\n"
        if current_step.tool == "write_file":
            tool_note += (
                "\nWhen writing a report or analysis file: include your complete interpretation "
                "and insights — not just raw tool output. The file should be self-contained "
                "and tell the full story of what was found. "
                "Do NOT attempt to read the output file before writing it — it may not exist yet.\n"
            )
        elif current_step.tool == "read_file":
            tool_note += (
                "\nRead ONLY the single file named in this step's description. "
                "Do not read any other files — other steps in this plan handle those.\n"
            )

    manifest = build_analysis_manifest()
    _disp = step_display or next(
        (i + 1 for i, s in enumerate(plan.steps) if s.step == current_step.step), 1
    )
    return (
        f"{agent_system}{rag_context}{manifest}\n\n"
        f"You are executing one step of a multi-step plan:\n" + "\n".join(lines) + "\n\n"
        f"Currently executing Step {_disp} of {len(plan.steps)}: "
        f"{current_step.description}\n"
        f"{tool_note}\n"
        f"IMPORTANT: Execute ONLY this step. Do not perform work belonging to other steps. "
        f"Do not create files or produce outputs that are not explicitly required by this step's description. "
        f"When this step is complete, stop."
    )


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
        spinner,
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
        self._spinner = spinner
        self._agent_system = agent_system
        self._tool_executor = ToolCallExecutor(registry, guard, user_gate, spinner)

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
        response = self._execute_plan(context.plan, db_session_id=context.db_session_id)
        context.response = response

        # ContinuationStage owns the next-step decision.
        return StageResult(status=StageStatus.OK, updated_context=context)

    # ── Internal execution logic (lifted from Agent._execute_plan) ────────

    def _execute_plan(self, plan: Plan, *, db_session_id: str | None = None) -> str:
        from runtime.persistence import PersistenceWriter
        from runtime.events import RuntimeEvent, get_event_bus

        replan_count = 0
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

        # ── Persistence: record plan ───────────────────────────────────
        db_plan_id = PersistenceWriter.record_plan(
            db_session_id or "",
            plan_index=replan_count,
            original_query=plan.original_query,
            steps=[
                {"step": s.step, "action_type": s.action_type.value, "description": s.description}
                for s in plan.steps
            ],
        )

        max_retries = config.runtime.execution_monitor.max_step_retries
        max_defers = config.runtime.execution_monitor.max_defers_per_step
        queue = list(plan.steps)
        idx = 0
        plan_start_index = len(self._messenger.get_messages())

        while idx < len(queue):
            step = queue[idx]
            n_total = len(queue)
            step_display = idx + 1  # position in current queue, always 1-based
            step.status = StepStatus.RUNNING
            desc_short = step.description[:40] + "..." if len(step.description) > 40 else step.description
            import time as _step_time
            _step_t0 = _step_time.monotonic()
            retry_label = f" RETRY ({step.flags.retry_count}/{max_retries})" if step.flags.retry_count > 0 else ""
            self._spinner.update(f"Step {step_display}/{n_total} — {desc_short}")
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

            if idx > 0 or step.flags.retry_count > 0:
                if step.flags.retry_count > 0:
                    self._messenger.add_user_message(
                        f"Retry step {step_display}: {step.description}\n"
                        f"Previous attempt failed. Try a different approach."
                    )
                else:
                    prev = queue[idx - 1]
                    prev_result = prev.result or "(no result captured)"
                    self._messenger.add_user_message(
                        f"Step {step_display - 1} complete. Result:\n{prev_result}\n\n"
                        f"Now execute step {step_display}: {step.description}"
                    )

            tools = self._resolve_step_tools(step)

            # ── Pre-execution guard (step level) ──
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
                self._spinner.stop()
                system = _step_system(plan, step, self._agent_system, self._rag_context, step_display)
                if self._user_gate.prompt(escalation):
                    result = self._run_step(step, n_total, tools, system, query=plan.original_query, plan_start_index=plan_start_index, step_display=step_display)
                else:
                    result = f"Step denied by user: {step.description}"
                    step.error = "user denied escalation"
                self._spinner.start(f"Step {step_display}/{n_total}")
            else:
                system = _step_system(plan, step, self._agent_system, self._rag_context, step_display)
                result = self._run_step(step, n_total, tools, system, query=plan.original_query, plan_start_index=plan_start_index, step_display=step_display)
                # Spinner keeps running — ToolLoop no longer stops it mid-step.
                self._spinner.update(f"Step {step_display}/{n_total} — done")

            step.result = result[:1000] if result else None

            # Advisory check: if planner declared an expected artifact output,
            # verify it exists after step execution and surface a signal.
            if step.produces:
                try:
                    from runtime.artifact_store import get_artifact_store

                    expected_key = step.produces.strip()
                    if expected_key and get_artifact_store().meta(expected_key) is None:
                        # INFO not WARNING — this is advisory; the planner sometimes
                        # declares produces on steps that don't call store_artifact.
                        logger.info(
                            f"  produces='{expected_key}' declared but artifact not registered "
                            f"(step used {step.tool!r} — only store_artifact registers artifacts)"
                        )
                except Exception as e:
                    logger.info(f"  produces-check skipped: {e}")

            # ── LLM importance scoring ──
            if result and step.status != StepStatus.ERROR:
                msg_index = len(self._messenger.get_messages()) - 1
                importance = self._importance_scorer.score(
                    plan.original_query, step.description, result
                )
                self._context_mgr.set_importance(msg_index, importance)

            # ── Persistence: record step result ────────────────────────
            if db_session_id and db_plan_id:
                _importance = (
                    self._context_mgr.get_importance(len(self._messenger.get_messages()) - 1)
                    if result and step.status != StepStatus.ERROR else None
                )
                PersistenceWriter.record_step(
                    db_session_id=db_session_id,
                    db_plan_id=db_plan_id,
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

            # ── Monitor assessment ──
            logger.info(banner(f"Monitor: Step {step_display}/{n_total}"))
            assessment = self._monitor.assess(
                step, plan, result or "",
                active_skill_name=getattr(self, "_active_skill_name", None),
            )
            decision = assessment.decision

            if decision == StepDecision.CONTINUE:
                step.status = StepStatus.COMPLETED
                logger.info(banner(f"Step {step_display}/{n_total} complete"))
                if _identity is not None:
                    _step_dur = int((_step_time.monotonic() - _step_t0) * 1000)
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
                idx += 1

            elif decision == StepDecision.RETRY:
                if step.flags.retry_count >= max_retries:
                    logger.info(f"  max retries ({max_retries}) reached — continuing anyway")
                    step.status = StepStatus.COMPLETED
                    idx += 1
                else:
                    step.flags.retry_count += 1
                    step.status = StepStatus.PENDING
                    logger.info(f"  retrying step ({step.flags.retry_count}/{max_retries})")

            elif decision == StepDecision.REPLAN:
                logger.info(banner("Replanning"))
                self._spinner.update("Replanning...")
                if _identity is not None:
                    _bus.emit(RuntimeEvent(
                        "replan.triggered",
                        _identity,
                        payload={"failed_step": step.step, "reason": assessment.reason},
                        stage="ExecutionStage",
                    ))
                new_steps = self._planner.replan(plan, step, assessment.reason)
                if new_steps:
                    # Runtime owns skill expansion — expand any skill: references
                    # in replanned steps the same way SkillExpansionStage does at
                    # pipeline start. Skill expansion is an infrastructure decision,
                    # not a planner responsibility.
                    if self._skill_expansion is not None:
                        new_steps = self._skill_expansion.expand_steps(
                            new_steps, plan.original_query
                        )
                    replan_count += 1
                    queue = queue[:idx] + new_steps
                    plan.steps = list(queue)
                    logger.info(f"  replanned: {len(new_steps)} new step(s)")
                    for s in new_steps:
                        logger.info(f"    Step {s.step} [{s.action_type.value}]: {s.description}")
                    db_plan_id = PersistenceWriter.record_plan(
                        db_session_id or "",
                        plan_index=replan_count,
                        original_query=plan.original_query,
                        steps=[
                            {"step": s.step, "action_type": s.action_type.value, "description": s.description}
                            for s in new_steps
                        ],
                        replan_reason=assessment.reason,
                    )
                else:
                    logger.info("  replan failed — marking step complete and continuing")
                    step.status = StepStatus.COMPLETED
                    idx += 1

            elif decision == StepDecision.DEFER:
                if step.flags.deferred or step.flags.retry_count > 0:
                    logger.info("  already deferred once — continuing anyway")
                    step.status = StepStatus.COMPLETED
                    idx += 1
                else:
                    step.flags.deferred = True
                    step.status = StepStatus.PENDING
                    queue.pop(idx)
                    queue.append(step)
                    plan.steps = list(queue)
                    logger.info(f"  deferred to end of queue (now position {len(queue)})")

            elif decision == StepDecision.SKIP:
                step.flags.skipped = True
                step.status = StepStatus.COMPLETED
                logger.info(f"  skipped: {assessment.reason}")
                idx += 1

            elif decision == StepDecision.GOAL_ACHIEVED:
                step.status = StepStatus.COMPLETED
                logger.info(banner(f"Goal achieved at step {step_display}/{n_total}"))
                if _identity is not None:
                    _bus.emit(RuntimeEvent(
                        "goal.achieved",
                        _identity,
                        payload={"at_step": step.step, "remaining": n_total - step.step},
                        stage="ExecutionStage",
                    ))
                for remaining in queue[idx + 1:]:
                    remaining.status = StepStatus.COMPLETED
                    remaining.flags.skipped = True
                    remaining.result = "(skipped — goal achieved earlier)"
                idx = len(queue)
                continue

            elif decision == StepDecision.ESCALATE:
                logger.info(f"  ESCALATE requested by monitor: {assessment.reason}")
                escalation = Escalation(
                    reason=f"Monitor flagged step {step.step}: {assessment.reason}",
                    source="monitor",
                )
                self._spinner.stop()
                if self._user_gate.prompt(escalation):
                    logger.info("  user approved — continuing")
                    step.status = StepStatus.COMPLETED
                else:
                    logger.info("  user denied — skipping step")
                    step.flags.skipped = True
                    step.status = StepStatus.COMPLETED
                    step.error = "user denied escalation"
                self._spinner.start("Continuing...")
                idx += 1

        # Spinner lifecycle belongs to agent.call() — only update, don't stop.
        # ContinuationStage and SynthesizerStage will use the same thread.
        self._spinner.update("Analyzing...")

        logger.info(banner("Execution complete"))
        last_completed = next(
            (s for s in reversed(queue) if s.status == StepStatus.COMPLETED and s.result),
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
        """Execute one plan step via ToolLoop. Updates step.error on failure."""
        step.error = None
        desc_short = step.description[:40] + "..." if len(step.description) > 40 else step.description
        authorized = frozenset(t["name"] for t in tools)

        step_identity = getattr(self, "_identity", None)
        if step_identity is not None:
            step_identity = step_identity.for_step_run()

        loop_cfg = ToolLoopConfig(
            max_iterations=config.runtime.execution_monitor.step_max_iterations,
            max_tool_calls=config.runtime.execution_monitor.step_max_tool_calls,
            max_consecutive_errors=3,
            authorized_tool_names=authorized,
            label="ExecutionStage",
        )

        class _StepHooks:
            def __init__(self, s: Step):
                self._step = s
                self.tool_errors: list[str] = []

            def on_tool_complete(self, tool_name: str, result: str) -> None:
                pass

            def on_max_tokens(self) -> None:
                self._step.error = "max_tokens"

            def on_error_cleared(self, n: int) -> None:
                if config.runtime.execution_monitor.error_recovery_clears_step_error:
                    logger.info(f"  runtime: successful tool call after {n} error(s) — clearing step errors")
                    self._step.error = None

        hooks = _StepHooks(step)
        loop = ToolLoop(
            provider=self._provider,
            messenger=self._messenger,
            context_mgr=self._context_mgr,
            tool_executor=self._tool_executor,
            spinner=self._spinner,
            user_gate=self._user_gate,
            config=loop_cfg,
            parent_identity=step_identity,
        )

        result = loop.run(
            system=system,
            tools=tools,
            query=query or step.description,
            plan_start_index=plan_start_index,
            hooks=hooks,
            resume_message=f"Step {step_display or step.step}/{n_total} — {desc_short}",
        )

        # Only propagate tool_errors to step.error when step.error is still set.
        # If step.error is None, the errors were already cleared by a subsequent
        # successful tool call (on_error_cleared fired) — don't re-flag the step.
        if result.tool_errors and step.error is not None:
            existing = step.error or ""
            step.error = (existing + "; tool errors: " + "; ".join(result.tool_errors)).lstrip("; ")

        # Return the raw tool output when available so StructuralCriteria can
        # inspect structured results (e.g. diff_behavior JSON with all_match).
        # For CONVERSATION steps (no tools), return the model's prose response.
        if result.last_tool_output:
            return result.last_tool_output
        return result.response_text
