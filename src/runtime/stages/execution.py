"""ExecutionStage — executes a validated plan step by step.

Direct lift of Agent._execute_plan() and Agent._run_step() from agent.py.
All runtime safeguards are preserved: guard checks, monitor assessments,
RETRY/REPLAN/DEFER/SKIP/ESCALATE decisions, loop detection, tool call cap,
max_tokens patching, importance scoring.

SynthesizerStage handles synthesis when plan.requires_synthesis is True.
This stage always returns OK — internal retry/replan handles step failures.
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
from runtime.utils import banner, fmt_input, fmt_result, has_error_indicator
from tools.implementations.web.read_url import INJECTION_WARNING_PREFIX
from app_config import config
from logger import get_logger

logger = get_logger(__name__)


def _step_system(plan: Plan, current_step: Step, agent_system: str) -> str:
    """Build the per-step system prompt showing plan progress."""
    lines = []
    for s in plan.steps:
        if s.status == StepStatus.COMPLETED:
            marker = "✓"
        elif s.step == current_step.step:
            marker = "→"
        else:
            marker = " "
        lines.append(f"  {marker} Step {s.step}: {s.description}")

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

    return (
        f"{agent_system}\n\n"
        f"You are executing one step of a multi-step plan:\n" + "\n".join(lines) + "\n\n"
        f"Currently executing Step {current_step.step} of {len(plan.steps)}: "
        f"{current_step.description}\n"
        f"{tool_note}\n"
        f"IMPORTANT: Execute ONLY this step. Do not perform work belonging to other steps. "
        f"Do not create files or produce outputs that are not explicitly required by this step's description. "
        f"When this step is complete, stop."
    )


def _step_utility_tools(step: Step) -> list[str]:
    """Return utility tool names to add alongside the step's declared tool."""
    utilities = []
    if step.tool == "write_file":
        utilities.append("make_directory")
    if step.tool == "bash_exec":
        utilities.append("read_file")
    return utilities


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
        self._spinner = spinner
        self._agent_system = agent_system

    def run(self, context: PipelineContext) -> StageResult:
        # No-op for direct mode (plan is None).
        if context.plan is None:
            return StageResult(status=StageStatus.OK, updated_context=context)

        response = self._execute_plan(context.plan)
        context.response = response

        # Return DONE when synthesis is not needed — this short-circuits the
        # pipeline so DirectExecutionStage (the final stage) is never reached
        # and cannot overwrite the response with a blank tool-loop result.
        # When requires_synthesis=True, return OK so SynthesizerStage runs next.
        if not context.plan.requires_synthesis:
            return StageResult(status=StageStatus.DONE, updated_context=context)
        return StageResult(status=StageStatus.OK, updated_context=context)

    # ── Internal execution logic (lifted from Agent._execute_plan) ────────

    def _execute_plan(self, plan: Plan) -> str:
        max_retries = config.runtime.execution_monitor.max_step_retries
        max_defers = config.runtime.execution_monitor.max_defers_per_step
        queue = list(plan.steps)
        idx = 0
        plan_start_index = len(self._messenger.get_messages())

        while idx < len(queue):
            step = queue[idx]
            n_total = len(queue)
            step.status = StepStatus.RUNNING
            desc_short = step.description[:40] + "..." if len(step.description) > 40 else step.description
            retry_label = f" RETRY ({step.flags.retry_count}/{max_retries})" if step.flags.retry_count > 0 else ""
            self._spinner.update(f"Step {step.step}/{n_total} — {desc_short}")
            logger.info(banner(f"Step {step.step}/{n_total} [{step.action_type.value}]{retry_label}"))
            logger.info(f"  {step.description}")

            if idx > 0 or step.flags.retry_count > 0:
                if step.flags.retry_count > 0:
                    self._messenger.add_user_message(
                        f"Retry step {step.step}: {step.description}\n"
                        f"Previous attempt failed. Try a different approach."
                    )
                else:
                    prev = queue[idx - 1]
                    prev_result = prev.result or "(no result captured)"
                    self._messenger.add_user_message(
                        f"Step {prev.step} complete. Result:\n{prev_result}\n\n"
                        f"Now execute step {step.step}: {step.description}"
                    )

            if step.action_type == ActionType.CONVERSATION:
                tools = []
            elif step.tool:
                tools = self._registry.get_tool_schema(step.tool)
                utility_tools = _step_utility_tools(step)
                for ut in utility_tools:
                    tools.extend(self._registry.get_tool_schema(ut))
                logger.info(f"  tool: {step.tool}" + (f" (+{utility_tools})" if utility_tools else ""))
            else:
                selected = self._router.select(step.description, self._messenger.get_messages())
                if step.action_type.value not in selected:
                    selected = list(set(selected + [step.action_type.value]))
                tools = self._registry.get_toolset_schema(selected)
                logger.info(f"  toolsets (fallback): {selected}")

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
                system = _step_system(plan, step, self._agent_system)
                if self._user_gate.prompt(escalation):
                    result = self._run_step(step, n_total, tools, system, query=plan.original_query, plan_start_index=plan_start_index)
                else:
                    result = f"Step denied by user: {step.description}"
                    step.error = "user denied escalation"
                self._spinner.start(f"Step {step.step}/{n_total}")
            else:
                system = _step_system(plan, step, self._agent_system)
                result = self._run_step(step, n_total, tools, system, query=plan.original_query, plan_start_index=plan_start_index)

            step.result = result[:1000] if result else None

            # Advisory check: if planner declared an expected artifact output,
            # verify it exists after step execution and surface a warning signal.
            if step.produces:
                try:
                    from runtime.artifact_store import get_artifact_store

                    expected_key = step.produces.strip()
                    if expected_key and get_artifact_store().meta(expected_key) is None:
                        warn = (
                            f"declared produces='{expected_key}' but artifact was not registered"
                        )
                        logger.warning(f"  ⚠ {warn}")
                        step.error = (step.error + "; " + warn) if step.error else warn
                except Exception as e:
                    logger.warning(f"  produces-check skipped: {e}")

            # ── LLM importance scoring ──
            if result and step.status != StepStatus.ERROR:
                msg_index = len(self._messenger.get_messages()) - 1
                importance = self._importance_scorer.score(
                    plan.original_query, step.description, result
                )
                self._context_mgr.set_importance(msg_index, importance)

            # ── Monitor assessment ──
            logger.info(banner(f"Monitor: Step {step.step}/{n_total}"))
            assessment = self._monitor.assess(step, plan, result or "")
            decision = assessment.decision

            if decision == StepDecision.CONTINUE:
                step.status = StepStatus.COMPLETED
                logger.info(banner(f"Step {step.step} complete"))
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
                new_steps = self._planner.replan(plan, step, assessment.reason)
                if new_steps:
                    queue = queue[:idx] + new_steps
                    plan.steps = list(queue)
                    logger.info(f"  replanned: {len(new_steps)} new step(s)")
                    for s in new_steps:
                        logger.info(f"    Step {s.step} [{s.action_type.value}]: {s.description}")
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

        self._spinner.stop()

        if plan.requires_synthesis:
            # Signal SynthesizerStage to run by NOT setting a final response here.
            # Return empty string — SynthesizerStage will overwrite context.response.
            logger.info(banner("Done (synthesis pending)"))
            return ""

        logger.info(banner("Done"))
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
    ) -> str:
        desc_short = step.description[:40] + "..." if len(step.description) > 40 else step.description
        step_tool_errors: list[str] = []
        step_tool_calls = 0
        force_end = False
        step.error = None
        _last_tool_sig: tuple | None = None
        # Set of tool names authorized for this step — enforces plan constraints.
        _authorized_tools = {t["name"] for t in tools}

        while True:
            packed = self._context_mgr.pack(
                self._messenger.get_messages(),
                query or step.description,
                plan_start_index=plan_start_index,
            )
            response = self._provider.chat(
                messages=packed,
                tools=[] if force_end else tools,
                system=system,
                label="ExecutionStage",
            )

            if response.stop_reason in ("end_turn", "max_tokens"):
                self._messenger.add_assistant_message(response.content)
                if response.stop_reason == "max_tokens":
                    logger.info("  [max_tokens] — stopping step early")
                    step.error = "max_tokens"
                    dangling = [b for b in response.content if isinstance(b, ToolUseBlock)]
                    if dangling:
                        logger.info(f"  [max_tokens] patching {len(dangling)} dangling tool_use block(s)")
                        self._messenger.add_tool_results([
                            {
                                "type": "tool_result",
                                "tool_use_id": b.id,
                                "content": "[interrupted: step ended at max_tokens before tool could execute]",
                            }
                            for b in dangling
                        ])
                if step_tool_errors:
                    step.error = (step.error or "") + "; tool errors: " + "; ".join(step_tool_errors)
                return next(
                    (b.text for b in response.content if isinstance(b, TextBlock)), ""
                )

            if response.stop_reason == "tool_use":
                self._messenger.add_assistant_message(response.content)
                tool_results = []
                for block in response.content:
                    if isinstance(block, ToolUseBlock):
                        logger.info(f"  → {block.name}  {fmt_input(block.name, block.input)}")

                        # Authorization gate: model must only call tools provided for this step.
                        if _authorized_tools and block.name not in _authorized_tools:
                            logger.info(f"  ✖ UNAUTHORIZED: '{block.name}' not in step tool list {sorted(_authorized_tools)}")
                            result = f"Tool call rejected: '{block.name}' is not authorized for this step. Use only the tools provided."
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": result,
                            })
                            step_tool_errors.append(f"{block.name}: unauthorized")
                            continue

                        guard_decision, guard_reason = self._guard.check_tool_call(block.name, block.input)

                        if guard_decision == GuardDecision.BLOCK:
                            logger.info(f"  ✖ BLOCKED: {guard_reason}")
                            result = f"Tool call blocked by safety policy: {guard_reason}"
                        elif guard_decision == GuardDecision.ESCALATE:
                            logger.info(f"  ⚠ ESCALATE: {guard_reason}")
                            escalation = Escalation(
                                reason=guard_reason,
                                source="guard",
                                tool_name=block.name,
                                tool_input=block.input,
                            )
                            self._spinner.stop()
                            if self._user_gate.prompt(escalation):
                                self._guard.record_approval(block.name, block.input)
                                self._spinner.start(f"Running {block.name}...")
                                try:
                                    tool = self._registry.get(block.name)
                                    result = tool.safe_execute(block.input)
                                except KeyError:
                                    result = f"Error: tool '{block.name}' does not exist."
                            else:
                                result = f"Tool call denied by user: {guard_reason}"
                            self._spinner.start(f"Step {step.step}/{n_total} — {desc_short}")
                        else:
                            self._spinner.update(f"Running {block.name}...")
                            try:
                                tool = self._registry.get(block.name)
                                result = tool.safe_execute(block.input)
                            except KeyError:
                                result = f"Error: tool '{block.name}' does not exist."

                        # ── Injection gate ──
                        if result.startswith(INJECTION_WARNING_PREFIX):
                            logger.info("  ⚠ INJECTION WARNING: read_url detected adversarial content — halting step")
                            self._spinner.stop()
                            print(f"\n{'─'*60}")
                            print("  SECURITY WARNING: Possible prompt injection detected")
                            print("  in fetched web content. The content has been quarantined")
                            print("  and has NOT entered context.")
                            print(f"{'─'*60}")
                            print(result.replace(INJECTION_WARNING_PREFIX + "\n", ""))
                            print(f"{'─'*60}")
                            user_choice = input("  Proceed with reading this content? [y/N]: ").strip().lower()
                            if user_choice == "y":
                                self._spinner.start(f"Step {step.step}/{n_total} — {desc_short}")
                                result = result.replace(INJECTION_WARNING_PREFIX + "\n", "[SECURITY REVIEW PASSED BY USER]\n")
                            else:
                                # Expel the artifact (removes from store + disk)
                                import re as _re
                                key_match = _re.search(r"Artifact-key: (\S+)", result)
                                if key_match:
                                    artifact_key = key_match.group(1)
                                    del_choice = input(f"  Delete quarantined artifact '{artifact_key}'? [Y/n]: ").strip().lower()
                                    if del_choice != "n":
                                        try:
                                            from runtime.artifact_store import get_artifact_store
                                            get_artifact_store().expel(artifact_key)
                                            print(f"  Expelled artifact '{artifact_key}'")
                                        except Exception:
                                            pass
                                result = "Tool call cancelled by user: potential prompt injection in fetched content."
                                tool_results.append({
                                    "type": "tool_result",
                                    "tool_use_id": block.id,
                                    "content": result,
                                })
                                self._messenger.add_tool_results(tool_results)
                                self._spinner.start(f"Step {step.step}/{n_total} — {desc_short}")
                                force_end = True
                                continue

                        logger.info(f"  ← {fmt_result(result)}")
                        step_tool_calls += 1

                        if has_error_indicator(result):
                            step_tool_errors.append(f"{block.name}: {result[:100]}")
                        elif step_tool_errors and config.runtime.execution_monitor.error_recovery_clears_step_error:
                            logger.info(f"  runtime: successful tool call after {len(step_tool_errors)} error(s) — clearing step errors")
                            step_tool_errors.clear()

                        cur_sig = (block.name, str(sorted(block.input.items())))
                        if cur_sig == _last_tool_sig and not has_error_indicator(result):
                            logger.info(f"  runtime: repeated identical tool call detected ({block.name}) — forcing wrap-up")
                            force_end = True
                        _last_tool_sig = cur_sig

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })

                self._spinner.update(f"Step {step.step}/{n_total} — {desc_short}")
                self._messenger.add_tool_results(tool_results)

                step_cap = config.runtime.execution_monitor.step_max_tool_calls
                if step_tool_calls >= step_cap:
                    logger.info(f"  runtime: step tool call cap ({step_cap}) reached — forcing wrap-up")
                    self._messenger.add_user_message(
                        "You have reached the tool call limit for this step. "
                        "Stop all tool calls and provide a final response for this step."
                    )
                    force_end = True
