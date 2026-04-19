from messenger import Messenger
from tools.registry import ToolRegistry
from tools.toolsets import ALL_TOOLSETS
from routing.static_router import StaticRouter
from runtime.classifier import IntentClassifier
from runtime.validator import PlanValidator
from runtime.critic import PlanCritic
from runtime.entity_critic import EntityCritic
from runtime.guard import ActionGuard, GuardDecision
from runtime.escalation import Escalation, CLIUserGate
from runtime.monitor import ExecutionMonitor
from runtime.context_manager import ContextManager
from runtime.importance import ImportanceScorer
from planning.planner import Planner
from planning.synthesizer import Synthesizer
from workflows.matcher import WorkflowMatcher
from planning.schema import Plan, Step, StepStatus, ActionType
from runtime.schema import ValidationStatus, StepDecision, CriticVerdict
from providers.factory import get_provider, get_runtime_provider
from providers.base import TextBlock, ToolUseBlock
from ui.spinner import Spinner
from app_config import config
from logger import get_logger

logger = get_logger(__name__)

_W = 56  # log banner width

import re

# Same logic as monitor._TOOL_ERROR_RE — match tool failure formats, not content.
_ERROR_INDICATORS = re.compile(
    r"(?im)("
    r"^Error[:\s]|"
    r"^STDERR:|"
    r"^File not found:|"
    r"^Tool call (?:blocked|denied)|"
    r"command not found|"
    r"Traceback \(most recent call last\)|"
    r"I don't have|I cannot|I'm unable"
    r")"
)


def _has_error_indicator(text: str) -> bool:
    """Check if a tool result contains a tool-level error (not content that mentions errors)."""
    return bool(_ERROR_INDICATORS.search(text[:500]))


def _banner(text: str) -> str:
    prefix = f"── {text} "
    return prefix + "─" * max(0, _W - len(prefix))


def _build_planner_context(history: list[dict], max_turns: int = 4) -> str | None:
    """Format the last N user/assistant turns into a compact context block for the planner."""
    lines = []
    for msg in history:
        role = msg.get("role", "")
        if role not in ("user", "assistant"):
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            # extract text blocks only; skip tool_use / tool_result noise
            parts = [
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            text = " ".join(p for p in parts if p).strip()
        else:
            text = str(content).strip()
        if not text:
            continue
        if len(text) > 250:
            text = text[:250] + "..."
        lines.append(f"{role.capitalize()}: {text}")

    if not lines:
        return None

    # keep only the last max_turns * 2 lines (each turn = user + assistant)
    lines = lines[-(max_turns * 2):]
    return "\n".join(lines)


def _fmt_input(name: str, tool_input: dict) -> str:
    if name == "write_file":
        size = len(tool_input.get("content", ""))
        return f"{tool_input.get('path', '?')}  ({size} chars)"
    if "path" in tool_input:
        extras = {k: v for k, v in tool_input.items() if k != "path"}
        suffix = f"  {extras}" if extras else ""
        return f"{tool_input['path']}{suffix}"
    if "command" in tool_input:
        return tool_input["command"]
    return str(tool_input)


def _fmt_result(result: str) -> str:
    stripped = result.strip()
    if not stripped:
        return "(empty)"
    lines = stripped.splitlines()
    if len(lines) == 1:
        return lines[0]
    return f"{lines[0]}  … ({len(lines)} lines)"


class Agent:

    def __init__(self, verbose: bool = False, user_gate=None):
        self.provider = get_provider()
        self.messenger = Messenger()
        self.registry = ToolRegistry()
        self.spinner = Spinner(verbose=verbose)

        for toolset in ALL_TOOLSETS:
            self.registry.register_toolset(toolset)

        self.router = StaticRouter(self.registry)
        self.context_mgr = ContextManager()
        self.context_mgr.set_summarizer(get_runtime_provider())
        self.classifier = IntentClassifier(get_runtime_provider())
        self.validator = PlanValidator(set(self.registry.toolset_names()), self.registry.tool_names())
        self.critic = PlanCritic(self.registry)
        self.guard = ActionGuard()
        self.user_gate = user_gate or CLIUserGate()
        self.monitor = ExecutionMonitor(get_runtime_provider())
        self.importance_scorer = ImportanceScorer(get_runtime_provider())
        self.planner = Planner(self.provider)
        self.synthesizer = Synthesizer(self.provider)
        self.workflow_matcher = WorkflowMatcher()
        self.entity_critic = EntityCritic()

    def call(self, user_message: str) -> str:
        logger.info(_banner("User"))
        logger.info(f"  {user_message}")
        self.messenger.add_user_message(user_message)

        self.spinner.start("Classifying...")
        logger.info(_banner("Intent classification"))
        history = self.messenger.get_messages()[:-1]  # exclude the message we just added
        mode, risk = self.classifier.classify(user_message, history)

        response = None

        if mode == "plan":
            # Try workflow templates first (zero LLM calls)
            logger.info(_banner("Workflow match"))
            planner_context = _build_planner_context(history)
            plan = self.workflow_matcher.match(user_message)
            if plan is not None:
                logger.info("  using workflow template — skipping LLM planner")
            else:
                logger.info("  no workflow match — using LLM planner")
                self.spinner.update("Planning...")
                logger.info(_banner("Planning"))
                plan = self.planner.plan(user_message, context=planner_context)
            if plan is not None:
                # Entity correction pass — fix hallucinated paths/filenames before validation
                if planner_context:
                    logger.info(_banner("Entity critic"))
                    plan, entity_corrections = self.entity_critic.correct(plan, planner_context, user_message=user_message)
                    if entity_corrections:
                        for msg in entity_corrections:
                            logger.info(f"  corrected: {msg}")
                    else:
                        logger.info("  no corrections needed")
                logger.info(_banner(f"Plan ({len(plan.steps)} steps)"))
                for s in plan.steps:
                    logger.info(f"  Step {s.step} [{s.action_type.value}] tool={s.tool}: {s.description}")
                logger.info(_banner("Plan validation"))
                validation = self.validator.validate(plan)
                if validation.status == ValidationStatus.INVALID:
                    logger.info("  retrying planner with validation feedback")
                    plan = self.planner.plan(
                        user_message + "\n\nPrevious plan was invalid:\n" + validation.feedback,
                        context=planner_context,
                    )
                    if plan is not None:
                        logger.info(_banner("Plan validation (retry)"))
                        validation = self.validator.validate(plan)
                if plan is not None and validation.status == ValidationStatus.VALID:
                    plan.risk = risk

                    # ── Adversarial critic review ──
                    # Skip critic for low-risk plans (configurable)
                    if risk == "low" and config.runtime.plan_critic.skip_low_risk:
                        logger.info(_banner("Plan critic"))
                        logger.info("  critic: skipped (low-risk plan)")
                        critic_result = None
                    else:
                        self.spinner.update("Reviewing plan...")
                        logger.info(_banner("Plan critic"))
                        critic_result = self.critic.review(plan)
                    if critic_result is not None and critic_result.verdict == CriticVerdict.CHALLENGED:
                        logger.info("  sending challenges to planner for revision")
                        challenges_text = self.critic.format_challenges(critic_result)
                        revised = self.planner.revise(plan, challenges_text)
                        if revised is not None:
                            for s in revised.steps:
                                logger.info(f"  Step {s.step} [{s.action_type.value}] tool={s.tool}: {s.description}")
                            logger.info(_banner("Plan validation (post-critic)"))
                            validation = self.validator.validate(revised)
                            if validation.status == ValidationStatus.VALID:
                                plan = revised
                            else:
                                logger.info("  revised plan failed validation — stripping challenged steps")
                                plan = self._strip_challenged_steps(plan, critic_result)
                        else:
                            logger.info("  planner revision returned None — stripping challenged steps")
                            plan = self._strip_challenged_steps(plan, critic_result)
                    if plan is not None:
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
        logger.info(f"  {response}")
        return response

    def _execute_plan(self, plan: Plan) -> str:
        max_retries = config.runtime.execution_monitor.max_step_retries
        max_defers = config.runtime.execution_monitor.max_defers_per_step
        queue = list(plan.steps)
        idx = 0
        plan_start_index = len(self.messenger.get_messages())

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
                    prev_result = prev.result or "(no result captured)"
                    self.messenger.add_user_message(
                        f"Step {prev.step} complete. Result:\n{prev_result}\n\n"
                        f"Now execute step {step.step}: {step.description}"
                    )

            if step.action_type == ActionType.CONVERSATION:
                tools = []
            elif step.tool:
                # Tool-per-step enforcement: provide only the declared tool
                # plus utility tools needed for common operations
                tools = self.registry.get_tool_schema(step.tool)
                utility_tools = self._step_utility_tools(step)
                for ut in utility_tools:
                    tools.extend(self.registry.get_tool_schema(ut))
                logger.info(f"  tool: {step.tool}" + (f" (+{utility_tools})" if utility_tools else ""))
            else:
                # Fallback for steps without tool field (shouldn't happen after critic)
                selected = self.router.select(step.description, self.messenger.get_messages())
                if step.action_type.value not in selected:
                    selected = list(set(selected + [step.action_type.value]))
                tools = self.registry.get_toolset_schema(selected)
                logger.info(f"  toolsets (fallback): {selected}")

            # ── Pre-execution guard (step level) ──
            step_guard = self.guard.check_step(step.description, step.action_type.value)
            if step_guard == GuardDecision.BLOCK:
                logger.info(f"  guard: BLOCKED step — {step.description[:60]}")
                result = f"Step blocked by safety policy: {step.description}"
            elif step_guard == GuardDecision.ESCALATE:
                logger.info(f"  guard: ESCALATE step — {step.description[:60]}")
                escalation = Escalation(
                    reason=f"Step contains potentially destructive operation: {step.description}",
                    source="guard",
                )
                self.spinner.stop()
                if self.user_gate.prompt(escalation):
                    system = self._step_system(plan, step)
                    result = self._run_step(step, n_total, tools, system, query=plan.original_query, plan_start_index=plan_start_index)
                else:
                    result = f"Step denied by user: {step.description}"
                    step.error = "user denied escalation"
                self.spinner.start(f"Step {step.step}/{n_total}")
            else:
                system = self._step_system(plan, step)
                result = self._run_step(step, n_total, tools, system, query=plan.original_query, plan_start_index=plan_start_index)

            step.result = result[:1000] if result else None

            # ── LLM importance scoring for context management ──
            if result and step.status != StepStatus.ERROR:
                # Score the tool result message (most recent user message with tool_results)
                msg_index = len(self.messenger.get_messages()) - 1
                importance = self.importance_scorer.score(
                    plan.original_query, step.description, result
                )
                self.context_mgr.set_importance(msg_index, importance)

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
                logger.info(f"  ESCALATE requested by monitor: {assessment.reason}")
                escalation = Escalation(
                    reason=f"Monitor flagged step {step.step}: {assessment.reason}",
                    source="monitor",
                )
                self.spinner.stop()
                if self.user_gate.prompt(escalation):
                    logger.info("  user approved — continuing")
                    step.status = StepStatus.COMPLETED
                else:
                    logger.info("  user denied — skipping step")
                    step.flags.skipped = True
                    step.status = StepStatus.COMPLETED
                    step.error = "user denied escalation"
                self.spinner.start(f"Continuing...")
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

    def _run_step(self, step: Step, n_total: int, tools: list[dict], system: str, query: str = "", plan_start_index: int | None = None) -> str:
        desc_short = step.description[:40] + "..." if len(step.description) > 40 else step.description
        step_tool_errors: list[str] = []
        step_tool_calls = 0
        force_end = False
        step.error = None  # reset per-attempt; prevents stale errors from prior retries misleading the monitor
        _last_tool_sig: tuple | None = None  # (tool_name, frozen_input) of previous call

        while True:
            packed = self.context_mgr.pack(self.messenger.get_messages(), query or step.description, plan_start_index=plan_start_index)
            response = self.provider.chat(
                messages=packed,
                tools=[] if force_end else tools,
                system=system,
            )

            if response.stop_reason in ("end_turn", "max_tokens"):
                self.messenger.add_assistant_message(response.content)
                if response.stop_reason == "max_tokens":
                    logger.info(f"  [max_tokens] — stopping step early")
                    step.error = "max_tokens"
                    # Patch any dangling tool_use blocks so the next provider.chat()
                    # call doesn't get a 400 from unmatched tool_use_id pairs.
                    dangling = [b for b in response.content if isinstance(b, ToolUseBlock)]
                    if dangling:
                        logger.info(f"  [max_tokens] patching {len(dangling)} dangling tool_use block(s)")
                        self.messenger.add_tool_results([
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
                            escalation = Escalation(
                                reason=guard_reason,
                                source="guard",
                                tool_name=block.name,
                                tool_input=block.input,
                            )
                            self.spinner.stop()
                            if self.user_gate.prompt(escalation):
                                self.guard.record_approval(block.name, block.input)
                                self.spinner.start(f"Running {block.name}...")
                                try:
                                    tool = self.registry.get(block.name)
                                    result = tool.safe_execute(block.input)
                                except KeyError:
                                    result = f"Error: tool '{block.name}' does not exist."
                            else:
                                result = f"Tool call denied by user: {guard_reason}"
                            self.spinner.start(f"Step {step.step}/{n_total} — {desc_short}")
                        else:
                            self.spinner.update(f"Running {block.name}...")
                            try:
                                tool = self.registry.get(block.name)
                                result = tool.safe_execute(block.input)
                            except KeyError:
                                result = f"Error: tool '{block.name}' does not exist."

                        logger.info(f"  ← {_fmt_result(result)}")
                        step_tool_calls += 1

                        # Bug 1: track errors; clear on recovery so step.error is not
                        # set if the model successfully recovered from earlier failures.
                        if _has_error_indicator(result):
                            step_tool_errors.append(f"{block.name}: {result[:100]}")
                        elif step_tool_errors and config.runtime.execution_monitor.error_recovery_clears_step_error:
                            logger.info(f"  runtime: successful tool call after {len(step_tool_errors)} error(s) — clearing step errors")
                            step_tool_errors.clear()

                        # Loop detection: same tool + same input called twice in a row
                        # with a successful result → model is stuck; force wrap-up.
                        cur_sig = (block.name, str(sorted(block.input.items())))
                        if cur_sig == _last_tool_sig and not _has_error_indicator(result):
                            logger.info(f"  runtime: repeated identical tool call detected ({block.name}) — forcing wrap-up")
                            force_end = True
                        _last_tool_sig = cur_sig

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })

                self.spinner.update(f"Step {step.step}/{n_total} — {desc_short}")
                self.messenger.add_tool_results(tool_results)

                # Bug 2: per-step tool call cap — force a text-only wrap-up response.
                step_cap = config.runtime.execution_monitor.step_max_tool_calls
                if step_tool_calls >= step_cap:
                    logger.info(f"  runtime: step tool call cap ({step_cap}) reached — forcing wrap-up")
                    self.messenger.add_user_message(
                        "You have reached the tool call limit for this step. "
                        "Stop all tool calls and provide a final response for this step."
                    )
                    force_end = True

    _DIRECT_MAX_TOOL_RESULT_CHARS = 50000
    _DIRECT_MAX_TOOL_CALLS = 15
    _DIRECT_MAX_CONSECUTIVE_ERRORS = 3
    _DIRECT_MAX_ITERATIONS = 20

    def _run_loop(self, user_message: str, system: str) -> str:
        iteration = 0
        last_had_errors = False
        error_correction_sent = False
        total_tool_calls = 0
        consecutive_errors = 0
        while True:
            iteration += 1
            if iteration > self._DIRECT_MAX_ITERATIONS:
                logger.info(f"  runtime: iteration cap ({self._DIRECT_MAX_ITERATIONS}) reached — forcing wrap-up")
                self.messenger.add_user_message(
                    "You have exceeded the maximum number of iterations. "
                    "Stop all tool calls immediately and give the user a final response "
                    "summarizing what you were able to accomplish and what failed."
                )
                self.spinner.stop()
                packed = self.context_mgr.pack(self.messenger.get_messages(), user_message)
                response = self.provider.chat(
                    messages=packed,
                    tools=[],  # no tools — force text response
                    system=system,
                )
                self.messenger.add_assistant_message(response.content)
                logger.info(_banner("Done"))
                return next(
                    (b.text for b in response.content if isinstance(b, TextBlock)), ""
                )
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
                # ── Runtime check: did previous tool calls error? ──
                if last_had_errors and not error_correction_sent:
                    logger.info("  runtime: model ended turn after tool errors — injecting correction")
                    self.messenger.add_assistant_message(response.content)
                    self.messenger.add_user_message(
                        "One or more of your previous tool calls returned errors. "
                        "Do not claim success if the operation failed. "
                        "Review the errors and either retry with corrected parameters or "
                        "acknowledge the failure to the user."
                    )
                    error_correction_sent = True
                    last_had_errors = False
                    continue

                self.spinner.stop()
                self.messenger.add_assistant_message(response.content)
                if response.stop_reason == "max_tokens":
                    logger.info("  [max_tokens] — stopping")
                    dangling = [b for b in response.content if isinstance(b, ToolUseBlock)]
                    if dangling:
                        logger.info(f"  [max_tokens] patching {len(dangling)} dangling tool_use block(s)")
                        self.messenger.add_tool_results([
                            {
                                "type": "tool_result",
                                "tool_use_id": b.id,
                                "content": "[interrupted: response ended at max_tokens before tool could execute]",
                            }
                            for b in dangling
                        ])
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
                            escalation = Escalation(
                                reason=guard_reason,
                                source="guard",
                                tool_name=block.name,
                                tool_input=block.input,
                            )
                            self.spinner.stop()
                            if self.user_gate.prompt(escalation):
                                self.guard.record_approval(block.name, block.input)
                                self.spinner.start(f"Running {block.name}...")
                                try:
                                    tool = self.registry.get(block.name)
                                    result = tool.safe_execute(block.input)
                                except KeyError:
                                    result = f"Error: tool '{block.name}' does not exist."
                            else:
                                result = f"Tool call denied by user: {guard_reason}"
                            self.spinner.start("Thinking...")
                        else:
                            self.spinner.update(f"Running {block.name}...")
                            try:
                                tool = self.registry.get(block.name)
                                result = tool.safe_execute(block.input)
                            except KeyError:
                                result = f"Error: tool '{block.name}' does not exist."

                        # Truncate oversized tool results
                        if len(result) > self._DIRECT_MAX_TOOL_RESULT_CHARS:
                            original_len = len(result)
                            result = result[:self._DIRECT_MAX_TOOL_RESULT_CHARS] + \
                                f"\n[truncated — output was {original_len} chars, showing first {self._DIRECT_MAX_TOOL_RESULT_CHARS}]"
                            logger.info(f"  [truncated tool result from {original_len} to {self._DIRECT_MAX_TOOL_RESULT_CHARS} chars]")

                        logger.info(f"  ← {_fmt_result(result)}")
                        total_tool_calls += 1

                        # Track consecutive errors
                        if _has_error_indicator(result):
                            consecutive_errors += 1
                        else:
                            consecutive_errors = 0

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })

                # Check for tool errors — flag them so the model can't ignore
                last_had_errors = any(
                    _has_error_indicator(r["content"]) for r in tool_results
                )
                if last_had_errors:
                    logger.info("  ⚠ tool error(s) detected in results")
                    error_correction_sent = False

                self.messenger.add_tool_results(tool_results)

                # ── Direct mode safety limits ──
                if consecutive_errors >= self._DIRECT_MAX_CONSECUTIVE_ERRORS:
                    logger.info(f"  runtime: {consecutive_errors} consecutive errors — injecting stop")
                    self.messenger.add_user_message(
                        "Multiple consecutive tool calls have failed. "
                        "Stop retrying and report the issue to the user."
                    )
                    consecutive_errors = 0

                if total_tool_calls >= self._DIRECT_MAX_TOOL_CALLS:
                    logger.info(f"  runtime: {total_tool_calls} total tool calls — injecting wrap-up")
                    self.messenger.add_user_message(
                        "You have made many tool calls. Wrap up and respond to the user."
                    )
                    total_tool_calls = 0  # reset to allow one more round

                self.spinner.update("Thinking...")

    def _strip_challenged_steps(self, plan: Plan, critic_result) -> Plan | None:
        """Remove steps the critic suggested dropping. Keep 'justify' steps (benefit of the doubt).
        Returns the stripped plan, or None if no steps remain."""
        if not critic_result.challenges:
            return plan

        drop_steps = set()
        for c in critic_result.challenges:
            if c.suggestion == "drop":
                drop_steps.add(c.step)
            # "replace" without a valid revision — drop as well (we can't auto-replace)
            elif c.suggestion == "replace":
                drop_steps.add(c.step)
            # "justify" — keep (benefit of the doubt)

        if not drop_steps:
            return plan

        kept = [s for s in plan.steps if s.step not in drop_steps]
        if not kept:
            logger.info("  all steps stripped by critic — falling back to direct execution")
            return None

        # Re-number steps
        for i, s in enumerate(kept, 1):
            s.step = i

        logger.info(f"  stripped {len(drop_steps)} challenged step(s), {len(kept)} remaining")
        plan.steps = kept
        return plan

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

        tool_note = ""
        if current_step.tool:
            tool_note = f"\nYou have been given ONLY the '{current_step.tool}' tool for this step. Use it and stop.\n"
            if current_step.tool == "write_file":
                tool_note += (
                    "\nWhen writing a report or analysis file: include your complete interpretation "
                    "and insights — not just raw tool output. The file should be self-contained "
                    "and tell the full story of what was found.\n"
                )

        return (
            f"{config.agent.system_prompt}\n\n"
            f"You are executing one step of a multi-step plan:\n" + "\n".join(lines) + "\n\n"
            f"Currently executing Step {current_step.step} of {len(plan.steps)}: "
            f"{current_step.description}\n"
            f"{tool_note}\n"
            f"IMPORTANT: Execute ONLY this step. Do not perform work belonging to other steps. "
            f"Do not create files or produce outputs that are not explicitly required by this step's description. "
            f"When this step is complete, stop."
        )

    def _step_utility_tools(self, step: Step) -> list[str]:
        """Return utility tools that should be available alongside the step's declared tool."""
        utilities = []
        # Write steps may need to create directories first
        if step.tool == "write_file":
            utilities.append("make_directory")
        # Shell analysis tools might need to read the file first
        if step.tool == "bash_exec":
            utilities.append("read_file")
        return utilities
