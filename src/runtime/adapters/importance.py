"""ImportanceAdapter — council vote on step-result importance tier.

Fires when the single-model ImportanceScorer returns MEDIUM (the ambiguous
middle tier where a second opinion has the most value). Uses median vote.
"""
from __future__ import annotations

from dataclasses import dataclass

from runtime.council import DeliberationAdapter, CouncilRound, CouncillorDecision
from runtime.json_extract import extract_json
from runtime.schema import Importance


_SYSTEM = """\
You classify the importance of a tool result for an AI assistant's working memory.

Given the original request and a tool result, classify how important this result
is for completing the task. You are one of several independent reviewers — give
your honest assessment.

Return ONLY a JSON object:
  {"importance": "critical"|"high"|"medium"|"low",
   "reason": "one sentence"}

Guidelines:
- "critical": the result contains the primary answer or a key fact the user asked for.
- "high": useful information that will influence the final output.
- "medium": helpful context but not essential.
- "low": boilerplate, confirmation messages, or redundant with other results.\
"""

_USER = """\
Original request: {original_query}

Step: {step_description}

Tool result (first 500 chars):
{result}

Classify the importance of this result.\
"""

_TIER_ORDER = [Importance.LOW, Importance.MEDIUM, Importance.HIGH, Importance.CRITICAL]


@dataclass
class ImportanceDecision:
    importance: Importance
    reason: str


class ImportanceAdapter(DeliberationAdapter[ImportanceDecision]):
    """Council adapter for importance classification.

    Input:  dict with original_query, step_description, result
    Output: ImportanceDecision using median importance tier
    """

    def system_prompt(self) -> str:
        return _SYSTEM

    def build_prompt(self, council_input: dict, prior_rounds: list[CouncilRound] | None = None) -> str:
        return _USER.format(**council_input)

    def parse_response(self, raw: str) -> ImportanceDecision:
        data = extract_json(raw)
        if not isinstance(data, dict):
            return ImportanceDecision(Importance.MEDIUM, "parse error")
        try:
            importance = Importance(data.get("importance", "medium").lower())
        except ValueError:
            importance = Importance.MEDIUM
        return ImportanceDecision(importance, data.get("reason", ""))

    def synthesize(
        self, decisions: list[CouncillorDecision], consensus_threshold: float
    ) -> tuple[ImportanceDecision, dict, list[str]]:
        tiers = [d.parsed.importance for d in decisions]
        tier_indices = sorted(_TIER_ORDER.index(t) for t in tiers)
        median_idx = tier_indices[len(tier_indices) // 2]
        final_tier = _TIER_ORDER[median_idx]

        agreement_map = {d.label: {"importance": d.parsed.importance.value} for d in decisions}
        trace = [f"tiers={[t.value for t in tiers]} → median={final_tier.value}"]
        reason = "; ".join(d.parsed.reason for d in decisions if d.parsed.reason)[:200]
        return ImportanceDecision(final_tier, reason), agreement_map, trace

    def decisions_converged(self, decisions: list[ImportanceDecision]) -> bool:
        return len({d.importance for d in decisions}) == 1

    def summarize_decision(self, decision: ImportanceDecision) -> dict:
        return {"importance": decision.importance.value}
