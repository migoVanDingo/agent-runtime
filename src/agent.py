from messenger import Messenger
from tools.registry import ToolRegistry
from tools.toolsets import ALL_TOOLSETS
from routing.static_router import StaticRouter
from runtime.classifier import IntentClassifier
from runtime.validator import PlanValidator
from runtime.guard import ActionGuard, GuardDecision
from runtime.monitor import ExecutionMonitor
from runtime.context_manager import ContextManager
from planning.planner import Planner
from planning.synthesizer import Synthesizer
from planning.schema import Plan, Step, StepStatus, ActionType
from runtime.schema import ValidationStatus, StepDecision
from providers.factory import get_provider, get_runtime_provider
from providers.base import TextBlock, ToolUseBlock
from ui.spinner import Spinner
from app_config import config
from logger import get_logger

logger = get_logger(__name__)

_W = 56  # log banner width


def _banner(text: str) -> str:
    prefix = f"── {text} "
    return prefix + "─" * max(0, _W - len(prefix))


def _fmt_input(name: str, tool_input: dict) -> str:
    if name == "write_file":
        size = len(tool_input.get("content", ""))
        return f"{tool_input.get('path', '?')}  ({size} chars)"
    if "path" in tool_input:
        extras = {k: v for k, v in tool_input.items() if k != "path"}
        suffix = f"  {extras}" if extras else ""
        return f"{tool_input['path']}{suffix}"
    if "command" in tool_input:
        return tool_input["command"][:80]
    return str(tool_input)[:80]


def _fmt_result(result: str) -> str:
    first_line = result.strip().splitlines()[0] if result.strip() else "(empty)"
    return first_line[:120]


class Agent:

    def __init__(self, verbose: bool = False):
        self.provider = get_provider()
        self.messenger = Messenger()
        self.registry = ToolRegistry()
        self.spinner = Spinner(verbose=verbose)

        for toolset in ALL_TOOLSETS:
            self.registry.register_toolset(toolset)

        self.router = StaticRouter(self.registry)
        self.context_mgr = ContextManager(embedding_model=self.router._model)
        self.classifier = IntentClassifier(get_runtime_provider())
        self.validator = PlanValidator(set(self.registry.toolset_names()))
        self.guard = ActionGuard()
        self.monitor = ExecutionMonitor(get_runtime_provider())
        self.planner = Planner(self.provider)
        self.synthesizer = Synthesizer(self.provider)

    def call(self, user_message: str) -> str:
        logger.info(_banner("User"))
        logger.info(f"  {user_message[:200]}")
        self.messenger.add_user_message(user_message)

        self.spinner.start("Classifying...")
        logger.info(_banner("Intent classification"))
        history = self.messenger.get_messages()[:-1]  # exclude the message we just added
        mode = self.classifier.classify(user_message, history)

        response = None

        if mode == "plan":
            self.spinner.update("Planning...")
            logger.info(_banner("Planning"))
            plan = self.planner.plan(user_message)
            if plan is not None:
                for s in plan.steps:
                    logger.info(f"  Step {s.step} [{s.action_type.value}]: {s.description}")
                logger.info(_banner("Plan validation"))
                validation = self.validator.validate(plan)
                if validation.status == ValidationStatus.INVALID:
                    logger.info("  retrying planner with validation feedback")
                    plan = self.planner.plan(
                        user_message + "\n\nPrevious plan was invalid:\n" + validation.feedback
                    )
                    if plan is not None:
                        logger.info(_banner("Plan validation (retry)"))
                        validation = self.validator.validate(plan)
                if plan is not None and validation.status == ValidationStatus.VALID:
                    logger.info(_banner(f"Plan ready ({len(plan.steps)} steps)"))
                    response = self._execute_plan(plan)
            if response is None:
                if plan is None:
                    logger.info("  Planner returned None — falling back to direct execution")
                else:
                    logger.info("  Plan failed validation — falling back to direct execution")
                self.spinner.update("Thinking...")

        if response is None:
            if mode != "plan":
                self.spinner.update("Thinking...")
                logger.info(_banner("Direct execution"))
            response = self._run_loop(user_message, system=config.agent.system_prompt)

        logger.info(_banner("Assistant"))
        logger.info(f"  {response[:200]}")
        return response

    def _execute_plan(self, plan: Plan) -> str:
        max_retries = config.runtime.execution_monitor.max_step_retries
        max_defers = config.runtime.execution_monitor.max_defers_per_step
        queue = list(plan.steps)
        idx = 0

        while idx < len(queue):
            step = queue[idx]
            n_total = len(queue)
            step.status = StepStatus.RUNNING
            desc_short = step.description[:40] + "..." if len(step.description) > 40 else step.description
            retry_label = f" RETRY ({step.flags.retry_count}/{max_retries})" if step.flags.retry_count > 0 else ""
            self.spinner.update(f"Step {step.step}/{n_total} — {desc_short}")
            logger.info(_banner(f"Step {step.step}/{n_total} [{step.action_type.value}]{retry_label}"))
            logger.info(f"  {step.description}")

            if idx > 0 or step.flags.retry_count > 0:
                if step.flags.retry_count > 0:
                    self.messenger.add_user_message(
                        f"Retry step {step.step}: {step.description}\n"
                        f"Previous attempt failed. Try a different approach."
                    )
                else:
                    prev = queue[idx - 1]
                    self.messenger.add_user_message(
                        f"Step {prev.step} complete. Now execute step {step.step}: {step.description}"
                    )

            if step.action_type == ActionType.CONVERSATION:
                tools = []
            else:
                selected = self.router.select(step.description, self.messenger.get_messages())
                if step.action_type.value not in selected:
                    selected = list(set(selected + [step.action_type.value]))
                tools = self.registry.get_toolset_schema(selected)
                logger.info(f"  toolsets: {selected}")

            # ── Pre-execution guard (step level) ──
            step_guard = self.guard.check_step(step.description, step.action_type.value)
            if step_guard == GuardDecision.BLOCK:
                logger.info(f"  guard: BLOCKED step — {step.description[:60]}")
                result = f"Step blocked by safety policy: {step.description}"
            elif step_guard == GuardDecision.ESCALATE:
                logger.info(f"  guard: ESCALATE step — {step.description[:60]}")
                result = f"Step requires user approval (not yet implemented): {step.description}"
            else:
                system = self._step_system(plan, step)
                result = self._run_step(step, n_total, tools, system, query=plan.original_query)

            step.result = result[:500] if result else None

            # ── Monitor assessment ──
            logger.info(_banner(f"Monitor: Step {step.step}/{n_total}"))
            assessment = self.monitor.assess(step, plan, result or "")
            decision = assessment.decision

            if decision == StepDecision.CONTINUE:
                step.status = StepStatus.COMPLETED
                logger.info(_banner(f"Step {step.step} complete"))
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
                    # don't increment idx — will re-execute same step

            elif decision == StepDecision.REPLAN:
                logger.info(_banner("Replanning"))
                self.spinner.update("Replanning...")
                new_steps = self.planner.replan(plan, step, assessment.reason)
                if new_steps:
                    # Replace everything from current position onward
                    queue = queue[:idx] + new_steps
                    plan.steps = list(queue)
                    logger.info(f"  replanned: {len(new_steps)} new step(s)")
                    for s in new_steps:
                        logger.info(f"    Step {s.step} [{s.action_type.value}]: {s.description}")
                    # don't increment idx — will execute first new step
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
                    # don't increment idx — next step slides into current position

            elif decision == StepDecision.SKIP:
                step.flags.skipped = True
                step.status = StepStatus.COMPLETED
                logger.info(f"  skipped: {assessment.reason}")
                idx += 1

            elif decision == StepDecision.ESCALATE:
                # Future: surface to user. For now, treat as continue.
                logger.info("  ESCALATE requested — treating as continue (not yet implemented)")
                step.status = StepStatus.COMPLETED
                idx += 1

        self.spinner.stop()

        if plan.requires_synthesis:
            self.spinner.start("Synthesizing response...")
            logger.info(_banner("Synthesizing"))
            response = self.synthesizer.synthesize(plan)
            self.spinner.stop()
            logger.info(_banner("Done"))
            return response

        logger.info(_banner("Done"))
        last_completed = next(
            (s for s in reversed(queue) if s.status == StepStatus.COMPLETED and s.result),
            None,
        )
        return last_completed.result if last_completed else ""

    def _run_step(self, step: Step, n_total: int, tools: list[dict], system: str, query: str = "") -> str:
        desc_short = step.description[:40] + "..." if len(step.description) > 40 else step.description

        while True:
            packed = self.context_mgr.pack(self.messenger.get_messages(), query or step.description)
            response = self.provider.chat(
                messages=packed,
                tools=tools,
                system=system,
            )

            if response.stop_reason in ("end_turn", "max_tokens"):
                self.messenger.add_assistant_message(response.content)
                if response.stop_reason == "max_tokens":
                    logger.info(f"  [max_tokens] — stopping step early")
                return next(
                    (b.text for b in response.content if isinstance(b, TextBlock)), ""
                )

            if response.stop_reason == "tool_use":
                self.messenger.add_assistant_message(response.content)
                tool_results = []
                for block in response.content:
                    if isinstance(block, ToolUseBlock):
                        logger.info(f"  → {block.name}  {_fmt_input(block.name, block.input)}")

                        # ── Pre-execution guard (tool call level) ──
                        guard_decision, guard_reason = self.guard.check_tool_call(block.name, block.input)

                        if guard_decision == GuardDecision.BLOCK:
                            logger.info(f"  ✖ BLOCKED: {guard_reason}")
                            result = f"Tool call blocked by safety policy: {guard_reason}"
                        elif guard_decision == GuardDecision.ESCALATE:
                            logger.info(f"  ⚠ ESCALATE: {guard_reason}")
                            result = f"Tool call requires user approval: {guard_reason}. Action was not executed."
                        else:
                            self.spinner.update(f"Running {block.name}...")
                            tool = self.registry.get(block.name)
                            result = tool.execute(block.input)

                        logger.info(f"  ← {_fmt_result(result)}")
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })
                self.spinner.update(f"Step {step.step}/{n_total} — {desc_short}")
                self.messenger.add_tool_results(tool_results)

    def _run_loop(self, user_message: str, system: str) -> str:
        iteration = 0
        while True:
            iteration += 1
            selected = self.router.select(user_message, self.messenger.get_messages())
            tools = self.registry.get_toolset_schema(selected)
            logger.info(f"  [iteration {iteration}] toolsets: {selected}")

            packed = self.context_mgr.pack(self.messenger.get_messages(), user_message)
            response = self.provider.chat(
                messages=packed,
                tools=tools,
                system=system,
            )

            if response.stop_reason in ("end_turn", "max_tokens"):
                self.spinner.stop()
                self.messenger.add_assistant_message(response.content)
                if response.stop_reason == "max_tokens":
                    logger.info("  [max_tokens] — stopping")
                logger.info(_banner("Done"))
                return next(
                    (b.text for b in response.content if isinstance(b, TextBlock)), ""
                )

            if response.stop_reason == "tool_use":
                self.messenger.add_assistant_message(response.content)
                tool_results = []
                for block in response.content:
                    if isinstance(block, ToolUseBlock):
                        logger.info(f"  → {block.name}  {_fmt_input(block.name, block.input)}")

                        # ── Pre-execution guard (tool call level) ──
                        guard_decision, guard_reason = self.guard.check_tool_call(block.name, block.input)

                        if guard_decision == GuardDecision.BLOCK:
                            logger.info(f"  ✖ BLOCKED: {guard_reason}")
                            result = f"Tool call blocked by safety policy: {guard_reason}"
                        elif guard_decision == GuardDecision.ESCALATE:
                            logger.info(f"  ⚠ ESCALATE: {guard_reason}")
                            result = f"Tool call requires user approval: {guard_reason}. Action was not executed."
                        else:
                            self.spinner.update(f"Running {block.name}...")
                            tool = self.registry.get(block.name)
                            result = tool.execute(block.input)

                        logger.info(f"  ← {_fmt_result(result)}")
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })
                self.spinner.update("Thinking...")
                self.messenger.add_tool_results(tool_results)

    def _step_system(self, plan: Plan, current_step: Step) -> str:
        lines = []
        for s in plan.steps:
            if s.status == StepStatus.COMPLETED:
                marker = "✓"
            elif s.step == current_step.step:
                marker = "→"
            else:
                marker = " "
            lines.append(f"  {marker} Step {s.step}: {s.description}")

        return (
            f"{config.agent.system_prompt}\n\n"
            f"You are executing one step of a multi-step plan:\n" + "\n".join(lines) + "\n\n"
            f"Currently executing Step {current_step.step} of {len(plan.steps)}: "
            f"{current_step.description}\n\n"
            f"IMPORTANT: Execute ONLY this step. Do not perform work belonging to other steps. "
            f"Do not create files or produce outputs that are not explicitly required by this step's description. "
            f"When this step is complete, stop."
        )
