import platform
from runtime.json_extract import extract_json
from messenger import Messenger
from providers.base import BaseProvider, TextBlock
from planning.schema import Plan, Step, StepStatus, ActionType, PLAN_JSON_SCHEMA
from planning.prompts import PLANNING_SYSTEM_PROMPT, PLANNING_USER_TURN, build_tool_list
from tools.toolsets import ALL_TOOLSETS
from app_config import config
from logger import get_logger

logger = get_logger(__name__)


def _platform_note() -> str:
    """Return a platform-specific tool availability note for plan revision/replan prompts."""
    system = platform.system()
    if system == "Darwin":
        return (
            "\nPlatform: macOS (darwin). "
            "GNU/ELF tools are NOT available (no readelf, no GNU objdump, no strace, no ltrace). "
            "Use these macOS equivalents instead:\n"
            "  - otool        → disassembly (otool -tv), headers (otool -l), linked libs (otool -L)\n"
            "  - llvm-objdump → drop-in objdump replacement if installed\n"
            "  - nm           → symbol table (BSD variant, compatible flags)\n"
            "  - file_info    → file type and architecture detection\n"
            "  - strings      → printable string extraction\n"
            "  - hexdump      → hex dump\n"
            "  - bash_exec    → run any shell command (otool, nm, file, etc.)\n"
            "Do NOT suggest readelf, strace, ltrace, or GNU objdump on macOS.\n"
        )
    return ""


class Planner:

    def __init__(self, provider: BaseProvider):
        self._provider = provider

    def plan(self, user_message: str, context: str | None = None, messages: list[dict] | None = None) -> Plan | None:
        messenger = Messenger()

        system = PLANNING_SYSTEM_PROMPT.format(max_steps=config.planning.max_steps, tool_list=build_tool_list(ALL_TOOLSETS))

        # If packed conversation messages are provided, seed the messenger with them
        # so the planner sees the full compressed history. Messages are already
        # serialized dicts from the context manager — inject directly rather than
        # routing through add_assistant_message() which expects dataclass instances.
        if messages:
            messenger.get_messages().extend(messages)
            context_block = ""
        elif context:
            context_block = (
                "Recent conversation (use this to resolve references like "
                "'the same file', 'that binary', 'the previous output', etc.):\n"
                f"{context}\n\n"
            )
        else:
            context_block = ""

        user_turn = PLANNING_USER_TURN.format(
            user_message=user_message,
            context_block=context_block,
        )

        messenger.add_user_message(user_turn)

        response = self._safe_chat(
            messages=messenger.get_messages(),
            tools=[],
            system=system,
            json_schema=PLAN_JSON_SCHEMA,
            label="Planner",
            context="plan",
        )
        if response is None:
            logger.info("Planner: provider call failed — falling back to direct execution")
            return None

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
            response = self._safe_chat(
                messages=messenger.get_messages(),
                tools=[],
                system=system,
                json_schema=PLAN_JSON_SCHEMA,
                label="Planner",
                context="plan retry",
            )
            if response is None:
                logger.info("Planner: retry provider call failed — falling back to direct execution")
                return None
            raw = next(
                (b.text for b in response.content if isinstance(b, TextBlock)), ""
            )
            plan = self._parse(raw)

        if plan is None:
            logger.info("Planner: falling back to direct execution")
            return None

        plan.original_query = user_message
        return plan

    def revise(self, plan: Plan, challenges_text: str) -> Plan | None:
        """Revise a plan in response to critic challenges. Returns revised plan or None."""
        messenger = Messenger()
        system = PLANNING_SYSTEM_PROMPT.format(max_steps=config.planning.max_steps, tool_list=build_tool_list(ALL_TOOLSETS))

        # Format current plan for context
        plan_lines = []
        for s in plan.steps:
            tool_label = s.tool or "none"
            plan_lines.append(f"  Step {s.step} [{s.action_type.value}] tool={tool_label}: {s.description}")

        user_turn = (
            f"Your plan was reviewed by an adversarial critic. Address each challenge below.\n\n"
            f"Original request: {plan.original_query}\n\n"
            f"Your original plan:\n" + "\n".join(plan_lines) + "\n\n"
            f"Critic challenges:\n{challenges_text}\n\n"
            f"Each challenge has a suggestion — follow it exactly:\n"
            f"  DROP    — remove this step; the critic determined it adds no value\n"
            f"  REPLACE — substitute a lighter tool that achieves the same result\n"
            f"  JUSTIFY — KEEP this step as-is; the critic just wants it to be clearly used\n\n"
            f"IMPORTANT: JUSTIFY means keep the step. Do NOT remove or replace JUSTIFY steps. "
            f"Just make sure their output is explicitly referenced in a later step.\n"
            + _platform_note() +
            f"\nReturn a revised plan as JSON. Same format as before."
        )

        messenger.add_user_message(user_turn)

        response = self._safe_chat(
            messages=messenger.get_messages(),
            tools=[],
            system=system,
            json_schema=PLAN_JSON_SCHEMA,
            label="Planner",
            context="revise",
        )
        if response is None:
            logger.info("Planner.revise: provider call failed")
            return None

        raw = next(
            (b.text for b in response.content if isinstance(b, TextBlock)), ""
        )

        revised = self._parse(raw)
        if revised is None:
            # Retry once with feedback
            logger.info("Planner.revise: invalid response — retrying once")
            messenger.add_assistant_message(response.content)
            messenger.add_user_message(
                "Your response was not valid JSON or was missing required fields. "
                "Return ONLY the raw JSON plan object, nothing else."
            )
            response = self._safe_chat(
                messages=messenger.get_messages(),
                tools=[],
                system=system,
                json_schema=PLAN_JSON_SCHEMA,
                label="Planner",
                context="revise retry",
            )
            if response is None:
                logger.info("Planner.revise: retry provider call failed")
                return None
            raw = next(
                (b.text for b in response.content if isinstance(b, TextBlock)), ""
            )
            revised = self._parse(raw)

        if revised is None:
            logger.info("Planner.revise: failed to produce valid revised plan after retry")
            return None

        revised.original_query = plan.original_query
        logger.info(f"Planner.revise: revised plan has {len(revised.steps)} steps")
        return revised

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
        system = PLANNING_SYSTEM_PROMPT.format(max_steps=max_remaining, tool_list=build_tool_list(ALL_TOOLSETS))

        user_turn = (
            f"You are RE-PLANNING the remaining steps of a task.\n\n"
            f"Original request: {plan.original_query}\n\n"
            f"Completed steps:\n" + ("\n".join(completed) or "  (none)") + "\n\n"
            f"Step {failed_step.step} failed: {failed_step.description}\n"
            f"Reason: {reason}\n\n"
            f"Original remaining steps (now invalidated):\n"
            + ("\n".join(remaining) or "  (none)") + "\n\n"
            + _platform_note() +
            f"\nProduce a revised plan for the REMAINING work only. "
            f"Number steps starting at {next_num}. Maximum {max_remaining} steps.\n\n"
            f"Return the same JSON structure as a normal plan."
        )

        messenger.add_user_message(user_turn)

        response = self._safe_chat(
            messages=messenger.get_messages(),
            tools=[],
            system=system,
            json_schema=PLAN_JSON_SCHEMA,
            label="Planner",
            context="replan",
        )
        if response is None:
            logger.info("Planner.replan: provider call failed")
            return None

        raw = next(
            (b.text for b in response.content if isinstance(b, TextBlock)), ""
        )

        replan = self._parse(raw)
        if replan is None:
            logger.info("Planner.replan: failed to produce valid plan")
            return None

        return replan.steps

    def _parse(self, raw: str) -> Plan | None:
        data = extract_json(raw)
        if data is None:
            logger.info("Planner: JSON parse error — no JSON found")
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

    def _safe_chat(self, *, context: str, **kwargs):
        try:
            return self._provider.chat(**kwargs)
        except Exception as e:
            logger.info(f"Planner.{context}: provider error — {type(e).__name__}: {e}")
            return None
