import json
import re
from planning.schema import Plan, Step, StepStatus
from runtime.schema import StepDecision, StepAssessment
from runtime.prompts import MONITOR_SYSTEM_PROMPT, MONITOR_USER_TEMPLATE
from providers.base import BaseProvider, TextBlock
from app_config import config
from logger import get_logger

logger = get_logger(__name__)

# Matches tool-level failures — NOT content that happens to contain "error".
# Our tools return errors in specific formats; legitimate output (e.g. strings
# extracted from a binary) may contain words like "error" or "failed" as data.
#
# Rules:
#   ^Error:          — standard tool error prefix (Error: [Errno 2] ..., Error: ...)
#   ^STDERR:         — shell tools route stderr here
#   ^File not found: — file tools (delete_file etc.)
#   ^Tool call       — guard block/deny messages
#   command not found — shell: unknown command (safe: won't appear in binary strings)
#   Traceback (most recent call last): — unhandled Python exception in tool
#   I don't have / I cannot / I'm unable — LLM capability refusal leaking into result
_TOOL_ERROR_RE = re.compile(
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

# Tool-unavailable errors are structurally non-recoverable — retrying the same
# tool call will always fail. Skip the LLM and go straight to REPLAN.
_TOOL_UNAVAILABLE_RE = re.compile(r"command not found", re.I)


class ExecutionMonitor:

    def __init__(self, provider: BaseProvider):
        self._provider = provider

    def assess(self, step: Step, plan: Plan, result: str) -> StepAssessment:
        """Assess a step result. Heuristic-first, LLM only when flagged."""
        if not config.runtime.execution_monitor.enabled:
            return StepAssessment(decision=StepDecision.CONTINUE, reason="monitor disabled")

        flags = self._heuristic_triage(step, result)

        if not flags:
            logger.info("  monitor: heuristics PASS → auto-CONTINUE")
            return StepAssessment(decision=StepDecision.CONTINUE, reason="heuristics pass")

        logger.info(f"  monitor: heuristics FLAGGED — {flags}")

        # Short-circuit: "command not found" means the tool binary is missing from
        # the system. Retrying will always produce the same failure. Skip the LLM
        # and REPLAN immediately so the planner can substitute an available tool.
        if _TOOL_UNAVAILABLE_RE.search(result[:500]):
            logger.info("  monitor: tool unavailable (command not found) → REPLAN immediately")
            return StepAssessment(
                decision=StepDecision.REPLAN,
                reason=f"tool '{step.tool}' is not installed on this system — cannot retry",
                confidence=1.0,
            )

        return self._llm_assess(step, plan, result, flags)

    def _heuristic_triage(self, step: Step, result: str) -> list[str]:
        """Quick code-level checks. Returns list of flag descriptions, empty if clean."""
        flags = []

        if not result or not result.strip():
            flags.append("empty result")

        elif _TOOL_ERROR_RE.search(result[:500]):
            match = _TOOL_ERROR_RE.search(result[:500])
            flags.append(f"error indicator in result: '{match.group(0).strip()}'")

        if step.error:
            flags.append(f"step error field set: {step.error[:100]}")

        return flags

    def _llm_assess(self, step: Step, plan: Plan, result: str, flags: list[str]) -> StepAssessment:
        """LLM assessment — only called when heuristics flag a problem."""
        completed = []
        remaining = []
        for s in plan.steps:
            if s.status == StepStatus.COMPLETED:
                summary = s.result[:100] if s.result else "(no result)"
                completed.append(f"  Step {s.step}: {s.description} → {summary}")
            elif s.step > step.step:
                remaining.append(f"  Step {s.step}: {s.description}")

        user_turn = MONITOR_USER_TEMPLATE.format(
            original_query=plan.original_query,
            step_num=step.step,
            total_steps=len(plan.steps),
            step_description=step.description,
            action_type=step.action_type.value,
            step_result=result[:500] if result else "(empty)",
            completed_summary="\n".join(completed) if completed else "  (none)",
            remaining_summary="\n".join(remaining) if remaining else "  (none — this is the last step)",
            flags="; ".join(flags),
        )

        from messenger import Messenger
        messenger = Messenger()
        messenger.add_user_message(user_turn)

        response = self._provider.chat(
            messages=messenger.get_messages(),
            tools=[],
            system=MONITOR_SYSTEM_PROMPT,
            label="ExecutionMonitor",
        )

        raw = next(
            (b.text for b in response.content if isinstance(b, TextBlock)), ""
        )

        assessment = self._parse(raw)
        logger.info(f"  monitor LLM: {assessment.decision.value} (confidence={assessment.confidence:.2f}) — {assessment.reason}")

        # Low-confidence RETRY → skip instead (don't waste a retry on uncertainty)
        if assessment.decision == StepDecision.RETRY and assessment.confidence < 0.5:
            logger.info("  monitor: low confidence retry → skipping instead")
            assessment.decision = StepDecision.SKIP
            assessment.reason = f"low confidence retry ({assessment.confidence:.2f}) — skipping"

        return assessment

    def _parse(self, raw: str) -> StepAssessment:
        """Parse monitor LLM response. Defaults to CONTINUE on failure."""
        text = raw.strip()

        if text.startswith("```"):
            lines = text.splitlines()
            inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
            text = "\n".join(inner).strip()

        try:
            data = json.loads(text)
            decision_str = data.get("decision", "continue")
            try:
                decision = StepDecision(decision_str)
            except ValueError:
                logger.info(f"  monitor: invalid decision '{decision_str}' — defaulting to continue")
                decision = StepDecision.CONTINUE

            confidence = data.get("confidence", 1.0)
            try:
                confidence = float(confidence)
                confidence = max(0.0, min(1.0, confidence))
            except (TypeError, ValueError):
                confidence = 1.0

            return StepAssessment(
                decision=decision,
                reason=data.get("reason", ""),
                suggestion=data.get("suggestion"),
                confidence=confidence,
            )
        except (json.JSONDecodeError, AttributeError):
            logger.info("  monitor: parse failed — defaulting to continue")
            return StepAssessment(decision=StepDecision.CONTINUE, reason="parse error")
