import json
from messenger import Messenger
from providers.base import BaseProvider, TextBlock
from planning.schema import Plan, Step, StepStatus, ActionType
from planning.prompts import PLANNING_SYSTEM_PROMPT, PLANNING_USER_TURN
from app_config import config
from logger import get_logger

logger = get_logger(__name__)


class Planner:

    def __init__(self, provider: BaseProvider):
        self._provider = provider

    def plan(self, user_message: str) -> Plan | None:
        messenger = Messenger()

        system = PLANNING_SYSTEM_PROMPT.format(max_steps=config.planning.max_steps)
        user_turn = PLANNING_USER_TURN.format(user_message=user_message)

        messenger.add_user_message(user_turn)

        response = self._provider.chat(
            messages=messenger.get_messages(),
            tools=[],
            system=system,
        )

        raw = next(
            (b.text for b in response.content if isinstance(b, TextBlock)), ""
        )
        plan = self._parse(raw)

        if plan is None and config.planning.retry_on_invalid:
            logger.info("Planner: invalid response — retrying once")
            messenger.add_assistant_message(response.content)
            messenger.add_user_message(
                "Your response was not valid JSON or did not match the required schema. "
                "Try again. Return ONLY the raw JSON object, nothing else."
            )
            response = self._provider.chat(
                messages=messenger.get_messages(),
                tools=[],
                system=system,
            )
            raw = next(
                (b.text for b in response.content if isinstance(b, TextBlock)), ""
            )
            plan = self._parse(raw)

        if plan is None:
            logger.info("Planner: falling back to direct execution")
            return None

        plan.original_query = user_message
        return plan

    def replan(self, plan: Plan, failed_step: Step, reason: str) -> list[Step] | None:
        """Re-plan remaining steps after a failure. Returns new steps or None."""
        completed = []
        remaining = []
        for s in plan.steps:
            if s.status == StepStatus.COMPLETED:
                result_summary = s.result[:100] if s.result else "(no result)"
                completed.append(f"Step {s.step}: {s.description} → {result_summary}")
            elif s.step > failed_step.step:
                remaining.append(f"Step {s.step}: {s.description}")

        next_num = failed_step.step
        max_remaining = config.planning.max_steps - (next_num - 1)

        messenger = Messenger()
        system = PLANNING_SYSTEM_PROMPT.format(max_steps=max_remaining)

        user_turn = (
            f"You are RE-PLANNING the remaining steps of a task.\n\n"
            f"Original request: {plan.original_query}\n\n"
            f"Completed steps:\n" + ("\n".join(completed) or "  (none)") + "\n\n"
            f"Step {failed_step.step} failed: {failed_step.description}\n"
            f"Reason: {reason}\n\n"
            f"Original remaining steps (now invalidated):\n"
            + ("\n".join(remaining) or "  (none)") + "\n\n"
            f"Produce a revised plan for the REMAINING work only. "
            f"Number steps starting at {next_num}. Maximum {max_remaining} steps.\n\n"
            f"Return the same JSON structure as a normal plan."
        )

        messenger.add_user_message(user_turn)

        response = self._provider.chat(
            messages=messenger.get_messages(),
            tools=[],
            system=system,
        )

        raw = next(
            (b.text for b in response.content if isinstance(b, TextBlock)), ""
        )

        replan = self._parse(raw)
        if replan is None:
            logger.info("Planner.replan: failed to produce valid plan")
            return None

        return replan.steps

    def _parse(self, raw: str) -> Plan | None:
        text = raw.strip()

        if text.startswith("```"):
            lines = text.splitlines()
            # drop opening fence (```json or ```) and closing fence
            inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
            text = "\n".join(inner).strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.info(f"Planner: JSON parse error — {e}")
            return None

        if not isinstance(data.get("steps"), list) or len(data["steps"]) == 0:
            logger.info("Planner: missing or empty steps")
            return None

        valid_action_types = {a.value for a in ActionType}
        for step in data["steps"]:
            for field in ("step", "description", "action_type"):
                if field not in step:
                    logger.info(f"Planner: step missing field '{field}'")
                    return None
            if step["action_type"] not in valid_action_types:
                logger.info(f"Planner: invalid action_type '{step['action_type']}'")
                return None

        try:
            return Plan.from_dict(data)
        except Exception as e:
            logger.info(f"Planner: failed to build Plan — {e}")
            return None
