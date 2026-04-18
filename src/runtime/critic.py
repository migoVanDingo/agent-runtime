"""Adversarial plan critic — challenges tool selection before execution."""

import json
from planning.schema import Plan
from runtime.schema import CriticVerdict, CriticChallenge, CriticResult
from runtime.prompts import CRITIC_SYSTEM_PROMPT, CRITIC_USER_TEMPLATE
from providers.base import BaseProvider, TextBlock
from tools.registry import ToolRegistry
from app_config import config
from logger import get_logger

logger = get_logger(__name__)


class PlanCritic:

    def __init__(self, provider: BaseProvider, registry: ToolRegistry, consensus_provider: BaseProvider | None = None):
        self._provider = provider
        self._registry = registry
        self._consensus_provider = consensus_provider

    def review(self, plan: Plan) -> CriticResult:
        """Review a plan and return challenges or approval.

        For high-risk plans, a second critic (consensus_provider) is consulted
        and challenges from both are merged.
        """
        if not config.runtime.plan_critic.enabled:
            return CriticResult(verdict=CriticVerdict.APPROVED, reasoning="critic disabled")

        user_turn = self._build_user_turn(plan)

        # Primary critic (runtime provider)
        result = self._single_review(user_turn, self._provider, label="critic-1")

        # Consensus: second critic on high-risk plans
        if (plan.risk == "high"
                and self._consensus_provider is not None
                and config.runtime.plan_critic.consensus_on_high_risk):
            logger.info("  consensus: high-risk plan — consulting second critic")
            result2 = self._single_review(user_turn, self._consensus_provider, label="critic-2")
            result = self._merge_results(result, result2)

        if result.verdict == CriticVerdict.APPROVED:
            logger.info(f"  critic: APPROVED — {result.reasoning}")
        else:
            n = len(result.challenges) if result.challenges else 0
            logger.info(f"  critic: CHALLENGED ({n} challenge(s))")
            if result.challenges:
                for c in result.challenges:
                    logger.info(f"    step {c.step} [{c.tool}]: {c.suggestion} — {c.challenge}")

        return result

    def _build_user_turn(self, plan: Plan) -> str:
        formatted_plan = self._format_plan(plan)
        tool_descriptions = self._format_tool_descriptions()
        return CRITIC_USER_TEMPLATE.format(
            original_query=plan.original_query,
            n_steps=len(plan.steps),
            formatted_plan=formatted_plan,
            tool_descriptions=tool_descriptions,
        )

    def _single_review(self, user_turn: str, provider: BaseProvider, label: str = "critic") -> CriticResult:
        """Run a single critic review with the given provider."""
        from messenger import Messenger
        messenger = Messenger()
        messenger.add_user_message(user_turn)

        response = provider.chat(
            messages=messenger.get_messages(),
            tools=[],
            system=CRITIC_SYSTEM_PROMPT,
        )

        raw = next(
            (b.text for b in response.content if isinstance(b, TextBlock)), ""
        )
        logger.info(f"  [{label}] raw response:\n{raw}")
        return self._parse(raw)

    def _merge_results(self, r1: CriticResult, r2: CriticResult) -> CriticResult:
        """Merge two critic results — if either challenges a step, it's challenged."""
        if r1.verdict == CriticVerdict.APPROVED and r2.verdict == CriticVerdict.APPROVED:
            return CriticResult(verdict=CriticVerdict.APPROVED, reasoning="both critics approved")

        # Merge challenges, deduplicating by step number (keep the stronger suggestion)
        all_challenges = {}
        suggestion_strength = {"drop": 3, "replace": 2, "justify": 1}

        for c in (r1.challenges or []) + (r2.challenges or []):
            existing = all_challenges.get(c.step)
            if existing is None:
                all_challenges[c.step] = c
            else:
                # Keep the stronger suggestion
                if suggestion_strength.get(c.suggestion, 0) > suggestion_strength.get(existing.suggestion, 0):
                    all_challenges[c.step] = c

        merged = sorted(all_challenges.values(), key=lambda c: c.step)
        return CriticResult(
            verdict=CriticVerdict.CHALLENGED,
            challenges=merged if merged else None,
        )

    def format_challenges(self, result: CriticResult) -> str:
        """Format critic challenges for the planner revision prompt."""
        if not result.challenges:
            return ""
        lines = []
        for c in result.challenges:
            lines.append(
                f"Step {c.step} (tool: {c.tool}) — {c.suggestion.upper()}\n"
                f"  Challenge: {c.challenge}"
            )
        return "\n\n".join(lines)

    def _format_plan(self, plan: Plan) -> str:
        lines = []
        for s in plan.steps:
            tool_label = s.tool or "none"
            lines.append(f"  Step {s.step} [{s.action_type.value}] tool={tool_label}: {s.description}")
        return "\n".join(lines)

    def _format_tool_descriptions(self) -> str:
        lines = []
        for name in sorted(self._registry.tool_names()):
            desc = self._registry.get_tool_description(name)
            lines.append(f"  {name}: {desc}")
        return "\n".join(lines)

    def _parse(self, raw: str) -> CriticResult:
        """Parse critic response. Defaults to APPROVED on parse failure."""
        text = raw.strip()

        if text.startswith("```"):
            lines = text.splitlines()
            inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
            text = "\n".join(inner).strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.info("  critic: parse failed — defaulting to approved")
            return CriticResult(verdict=CriticVerdict.APPROVED, reasoning="parse error")

        verdict_str = data.get("verdict", "approved")
        try:
            verdict = CriticVerdict(verdict_str)
        except ValueError:
            logger.info(f"  critic: unknown verdict '{verdict_str}' — defaulting to approved")
            return CriticResult(verdict=CriticVerdict.APPROVED, reasoning="unknown verdict")

        if verdict == CriticVerdict.APPROVED:
            return CriticResult(
                verdict=CriticVerdict.APPROVED,
                reasoning=data.get("reasoning", ""),
            )

        challenges = []
        for c in data.get("challenges", []):
            challenges.append(CriticChallenge(
                step=c.get("step", 0),
                tool=c.get("tool"),
                challenge=c.get("challenge", ""),
                suggestion=c.get("suggestion", "justify"),
            ))

        return CriticResult(
            verdict=CriticVerdict.CHALLENGED,
            challenges=challenges if challenges else None,
        )
