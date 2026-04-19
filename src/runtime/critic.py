"""Adversarial plan critic — challenges tool selection before execution.

The critic is now backed by a Council of N independent (or debating) agents.
Each councillor independently reviews the plan and returns a CriticResult.
The Council's synthesis algorithm resolves disagreements using a configurable
consensus threshold: challenges require N*threshold councillors to survive at
full strength; minority challenges are downgraded or discarded.

This replaces the old single-critic + optional-consensus-provider pattern.
"""

from __future__ import annotations

import json
import re
from collections import Counter

from planning.schema import Plan
from runtime.council import Council, DeliberationAdapter, CouncilRound, CouncillorDecision
from runtime.schema import CriticVerdict, CriticChallenge, CriticResult
from runtime.prompts import CRITIC_SYSTEM_PROMPT, CRITIC_USER_TEMPLATE
from tools.registry import ToolRegistry
from app_config import config
from logger import get_logger

logger = get_logger(__name__)

_SUGGESTION_STRENGTH: dict[str, int] = {"drop": 3, "replace": 2, "justify": 1}
_DOWNGRADE: dict[str, str | None] = {"drop": "replace", "replace": "justify", "justify": None}

# Matches a fenced JSON block (```json ... ``` or ``` ... ```) anywhere in text
_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _extract_json(text: str) -> dict | None:
    """Extract the first JSON object from a response that may contain reasoning prose.

    Tries in order:
    1. Fenced code block: ```json { ... } ```
    2. First bare { ... } span in the text
    3. The whole text as-is
    """
    # 1. Fenced block anywhere in the response
    m = _FENCE_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 2. Find the first '{' and try progressively shorter spans ending at each '}'
    start = text.find("{")
    if start != -1:
        # Walk from the last '}' backwards until we get a valid parse
        end = len(text)
        while True:
            end = text.rfind("}", start, end)
            if end == -1:
                break
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                end -= 1  # try a shorter span

    # 3. Whole text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


# ── Adapter ──────────────────────────────────────────────────────────────────

class PlanCriticAdapter(DeliberationAdapter[CriticResult]):
    """Adapts CriticResult deliberation to the generic Council interface."""

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
        text = raw.strip()
        data = _extract_json(text)
        if data is None:
            logger.info(f"  critic: parse failed — no JSON found in response (len={len(raw)})")
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

        challenges = [
            CriticChallenge(
                step=c.get("step", 0),
                tool=c.get("tool"),
                challenge=c.get("challenge", ""),
                suggestion=c.get("suggestion") or "justify",
            )
            for c in data.get("challenges", [])
        ]
        return CriticResult(
            verdict=CriticVerdict.CHALLENGED,
            challenges=challenges if challenges else None,
        )

    def synthesize(
        self,
        decisions: list[CouncillorDecision],
        consensus_threshold: float,
    ) -> tuple[CriticResult, dict, list[str]]:
        """Consensus synthesis with threshold-based challenge downgrade.

        Algorithm:
          ratio = k / N  (fraction of councillors that challenged a step)
          ratio >= threshold  → keep suggestion at full strength
          ratio > 1/N         → downgrade one level (drop→replace, replace→justify, justify→discard)
          lone wolf (k==1, N>2) → floor at justify
        """
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
                    f"step {step_num}: {k}/{N} challenged"
                    f" ({ratio:.0%} ≥ threshold {consensus_threshold:.0%})"
                    f" → keep {final_suggestion}"
                )
            elif k > 1:
                downgraded = _DOWNGRADE.get(majority_suggestion)
                if downgraded is None:
                    # "justify" is already the floor — keep it rather than discard.
                    # 2/3 councillors wanting the planner to defend a step is meaningful signal.
                    final_suggestion = "justify"
                    trace.append(
                        f"step {step_num}: {k}/{N} challenged ({ratio:.0%} < threshold)"
                        f" → floor at justify (majority opinion, below threshold)"
                    )
                else:
                    final_suggestion = downgraded
                    trace.append(
                        f"step {step_num}: {k}/{N} challenged ({ratio:.0%} < threshold)"
                        f" → downgrade {majority_suggestion} → {final_suggestion}"
                    )
            else:
                # Lone wolf (k==1):
                # - "drop"/"replace" → floor at "justify" regardless of N
                # - "justify" with N>2 → discard (2 others approved, this is real minority)
                # - "justify" with N==2 → keep (genuine 50/50 split)
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
        # All challenged — check same set of steps
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

    # ── Helpers ──────────────────────────────────────────────────────────────

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


# ── PlanCritic ───────────────────────────────────────────────────────────────

class PlanCritic:

    def __init__(self, registry: ToolRegistry):
        self._registry = registry

    def review(self, plan: Plan) -> CriticResult:
        """Review a plan using a council of N councillors.

        Returns a CriticResult with council_run_id set so callers can
        correlate with _metrics/ records.
        """
        if not config.runtime.plan_critic.enabled:
            return CriticResult(verdict=CriticVerdict.APPROVED, reasoning="critic disabled")

        council_cfg = config.runtime.council
        if not council_cfg.councillors:
            logger.info("  critic: no councillors configured — skipping")
            return CriticResult(verdict=CriticVerdict.APPROVED, reasoning="no councillors configured")

        adapter = PlanCriticAdapter(self._registry, plan)
        council = Council(adapter=adapter, config=council_cfg)

        result = council.deliberate(
            council_input=plan,
            context="plan_critic",
            query=plan.original_query,
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
