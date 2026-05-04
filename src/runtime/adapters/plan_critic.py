"""PlanCriticAdapter — adversarial plan review before execution.

Challenges tool selection, step proportionality, and ordering. Each councillor
independently reviews the plan and returns a verdict. The synthesis algorithm
uses threshold-based challenge downgrade: drop→replace→justify→discard.
"""
from __future__ import annotations

from collections import Counter

from planning.schema import Plan
from runtime.council import DeliberationAdapter, CouncilRound, CouncillorDecision
from runtime.json_extract import extract_json
from runtime.schema import CriticVerdict, CriticChallenge, CriticResult
from runtime.prompts import CRITIC_SYSTEM_PROMPT, CRITIC_USER_TEMPLATE
from tools.registry import ToolRegistry
from logger import get_logger

logger = get_logger(__name__)

_SUGGESTION_STRENGTH: dict[str, int] = {"drop": 3, "replace": 2, "justify": 1}
_DOWNGRADE: dict[str, str | None] = {"drop": "replace", "replace": "justify", "justify": None}


class PlanCriticAdapter(DeliberationAdapter[CriticResult]):
    """Adapts plan criticism to the Council interface.

    Input:  Plan object
    Output: CriticResult with threshold-downgraded challenges
    """

    def __init__(self, registry: ToolRegistry, plan: Plan):
        self._registry = registry
        self._plan = plan

    def system_prompt(self) -> str:
        return CRITIC_SYSTEM_PROMPT

    def build_prompt(self, council_input: Plan, prior_rounds: list[CouncilRound] | None = None) -> str:
        return CRITIC_USER_TEMPLATE.format(
            original_query=council_input.original_query,
            n_steps=len(council_input.steps),
            formatted_plan=self._format_plan(council_input),
            tool_descriptions=self._format_tool_descriptions(),
        )

    def parse_response(self, raw: str) -> CriticResult:
        data = extract_json(raw)
        if not isinstance(data, dict):
            logger.info(f"  critic: parse failed — no JSON found (len={len(raw)})")
            return CriticResult(verdict=CriticVerdict.APPROVED, reasoning="parse error")

        verdict_str = data.get("verdict", "approved")
        try:
            verdict = CriticVerdict(verdict_str)
        except ValueError:
            logger.info(f"  critic: unknown verdict '{verdict_str}' — defaulting to approved")
            return CriticResult(verdict=CriticVerdict.APPROVED, reasoning="unknown verdict")

        if verdict == CriticVerdict.APPROVED:
            return CriticResult(verdict=CriticVerdict.APPROVED, reasoning=data.get("reasoning", ""))

        challenges = []
        for c in data.get("challenges", []):
            step_val = c.get("step")
            if step_val is None:
                logger.info(f"  critic: skipping meta-challenge (step=null): {c.get('challenge', '')[:80]}")
                continue
            challenges.append(CriticChallenge(
                step=int(step_val),
                tool=c.get("tool"),
                challenge=c.get("challenge", ""),
                suggestion=c.get("suggestion") or "justify",
            ))
        return CriticResult(
            verdict=CriticVerdict.CHALLENGED,
            challenges=challenges if challenges else None,
        )

    def synthesize(
        self,
        decisions: list[CouncillorDecision],
        consensus_threshold: float,
    ) -> tuple[CriticResult, dict, list[str]]:
        """Threshold-based challenge downgrade: drop→replace→justify→discard."""
        N = len(decisions)
        step_entries: dict[int, list[tuple[str, CriticChallenge]]] = {}

        for d in decisions:
            result: CriticResult = d.parsed
            if result.verdict == CriticVerdict.CHALLENGED and result.challenges:
                for challenge in result.challenges:
                    step_entries.setdefault(challenge.step, []).append((d.label, challenge))

        agreement_map: dict = {}
        trace: list[str] = []
        final_challenges: list[CriticChallenge] = []

        for step_num in sorted(step_entries):
            entries = step_entries[step_num]
            challengers = [label for label, _ in entries]
            challenges_list = [c for _, c in entries]
            k = len(entries)
            ratio = k / N

            suggestion_counts = Counter(c.suggestion for c in challenges_list)
            majority_suggestion = suggestion_counts.most_common(1)[0][0]
            best = max(challenges_list, key=lambda c: _SUGGESTION_STRENGTH.get(c.suggestion, 0))

            approvers = [d.label for d in decisions if d.label not in challengers]
            agreement_map[f"step_{step_num}"] = {
                "challengers": challengers,
                "approvers": approvers,
                "ratio": round(ratio, 2),
            }

            if ratio >= consensus_threshold:
                final_suggestion = majority_suggestion
                trace.append(
                    f"step {step_num}: {k}/{N} challenged ({ratio:.0%} ≥ threshold) → keep {final_suggestion}"
                )
            elif k > 1:
                downgraded = _DOWNGRADE.get(majority_suggestion)
                if downgraded is None:
                    final_suggestion = "justify"
                    trace.append(
                        f"step {step_num}: {k}/{N} challenged ({ratio:.0%} < threshold) → floor at justify"
                    )
                else:
                    final_suggestion = downgraded
                    trace.append(
                        f"step {step_num}: {k}/{N} challenged ({ratio:.0%} < threshold) "
                        f"→ downgrade {majority_suggestion} → {final_suggestion}"
                    )
            else:
                if majority_suggestion in ("drop", "replace"):
                    final_suggestion = "justify"
                    lone_note = "(lone wolf)" if N > 2 else ""
                    trace.append(f"step {step_num}: 1/{N} challenged {lone_note} → floor at justify")
                elif N > 2:
                    trace.append(f"step {step_num}: 1/{N} justify (lone wolf, N>2) → discard")
                    continue
                else:
                    final_suggestion = "justify"
                    trace.append(f"step {step_num}: 1/{N} challenged → justify")

            final_challenges.append(CriticChallenge(
                step=step_num,
                tool=best.tool,
                challenge=best.challenge,
                suggestion=final_suggestion,
            ))

        if not final_challenges:
            all_approved = all(d.parsed.verdict == CriticVerdict.APPROVED for d in decisions)
            reasoning = "all councillors approved" if all_approved else "no challenges survived synthesis"
            return CriticResult(verdict=CriticVerdict.APPROVED, reasoning=reasoning), agreement_map, trace

        return CriticResult(verdict=CriticVerdict.CHALLENGED, challenges=final_challenges), agreement_map, trace

    def decisions_converged(self, decisions: list[CriticResult]) -> bool:
        verdicts = {d.verdict for d in decisions}
        if len(verdicts) > 1:
            return False
        if decisions[0].verdict == CriticVerdict.APPROVED:
            return True
        step_sets = [
            frozenset(c.step for c in d.challenges) if d.challenges else frozenset()
            for d in decisions
        ]
        return len(set(step_sets)) == 1

    def summarize_decision(self, decision: CriticResult) -> dict:
        if decision.verdict == CriticVerdict.APPROVED:
            return {"verdict": "approved", "reasoning": decision.reasoning or ""}
        challenges = [
            {"step": c.step, "tool": c.tool, "suggestion": c.suggestion}
            for c in (decision.challenges or [])
        ]
        return {"verdict": "challenged", "challenges": challenges}

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
