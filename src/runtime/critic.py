"""PlanCritic — orchestrates council-based adversarial plan review.

PlanCriticAdapter has moved to runtime/adapters/plan_critic.py.
This module owns only PlanCritic, which wires the adapter into the
Council and exposes the review() API used by CouncilStage.
"""
from __future__ import annotations

from planning.schema import Plan
from runtime.adapters.plan_critic import PlanCriticAdapter
from runtime.council import Council
from runtime.schema import CriticVerdict, CriticResult
from tools.registry import ToolRegistry
from app_config import config
from logger import get_logger

logger = get_logger(__name__)


class PlanCritic:

    def __init__(self, registry: ToolRegistry):
        self._registry = registry

    def review(self, plan: Plan, active_councillors: list | None = None,
               identity=None) -> CriticResult:
        """Review a plan using a council of N councillors."""
        if not config.runtime.plan_critic.enabled:
            return CriticResult(verdict=CriticVerdict.APPROVED, reasoning="critic disabled")

        council_cfg = config.runtime.council
        councillors = active_councillors if active_councillors is not None else council_cfg.councillors
        if not councillors:
            logger.info("  critic: no councillors active — skipping")
            return CriticResult(verdict=CriticVerdict.APPROVED, reasoning="no active councillors")

        import dataclasses
        effective_cfg = dataclasses.replace(council_cfg, councillors=councillors)

        adapter = PlanCriticAdapter(self._registry, plan)
        council = Council(adapter=adapter, config=effective_cfg)

        result = council.deliberate(
            council_input=plan,
            context="plan_critic",
            query=plan.original_query,
            identity=identity,
        )

        final: CriticResult = result.final
        final.council_run_id = result.metrics.run_id

        if final.verdict == CriticVerdict.APPROVED:
            logger.info(f"  critic: APPROVED — {final.reasoning}")
        else:
            n = len(final.challenges) if final.challenges else 0
            logger.info(f"  critic: CHALLENGED ({n} challenge(s))")
            if final.challenges:
                for c in final.challenges:
                    logger.info(f"    step {c.step} [{c.tool}]: {c.suggestion} — {c.challenge}")

        return final

    def format_challenges(self, result: CriticResult) -> str:
        """Format critic challenges for the planner revision prompt."""
        if not result.challenges:
            return ""
        lines = []
        for c in result.challenges:
            lines.append(
                f"Step {c.step} (tool: {c.tool}) — {(c.suggestion or 'justify').upper()}\n"
                f"  Challenge: {c.challenge}"
            )
        return "\n\n".join(lines)
