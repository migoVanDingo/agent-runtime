"""SynthesisQualityAdapter — council quality gate on synthesized responses.

Fires after synthesis when the plan had at least one failure (retry or replan).
Advisory only — always returns the original response, but logs gaps.
"""
from __future__ import annotations

from dataclasses import dataclass

from runtime.council import DeliberationAdapter, CouncilRound, CouncillorDecision
from runtime.json_extract import extract_json


_SYSTEM = """\
You are a quality reviewer for AI-generated responses. You will see the user's
original request and the agent's synthesized response. Determine whether the
response adequately addresses the request.

Return ONLY a JSON object:
  {"verdict": "pass"|"fail",
   "confidence": 0.0-1.0,
   "reason": "one sentence explaining the verdict",
   "gap": "what is missing or wrong (empty string if verdict=pass)"}

Be honest and specific. A response that hedges, hallucinates, or fails to
deliver what was asked should fail. A response that fully addresses the request
should pass even if it is brief.\
"""

_USER = """\
Original request: {original_query}

Synthesized response:
{response}

Context (what the plan did):
{plan_summary}

Does this response adequately address the original request?\
"""


@dataclass
class SynthesisVerdict:
    passed: bool
    confidence: float
    reason: str
    gap: str


class SynthesisQualityAdapter(DeliberationAdapter[SynthesisVerdict]):
    """Council adapter for synthesis quality gating.

    Input:  dict with original_query, response, plan_summary
    Output: SynthesisVerdict (majority vote; unanimous high-confidence fail blocks)
    """

    def system_prompt(self) -> str:
        return _SYSTEM

    def build_prompt(self, council_input: dict, prior_rounds: list[CouncilRound] | None = None) -> str:
        return _USER.format(**council_input)

    def parse_response(self, raw: str) -> SynthesisVerdict:
        data = extract_json(raw)
        if not isinstance(data, dict):
            return SynthesisVerdict(True, 1.0, "parse error", "")
        passed = data.get("verdict", "pass") == "pass"
        confidence = max(0.0, min(1.0, float(data.get("confidence", 1.0))))
        return SynthesisVerdict(
            passed=passed,
            confidence=confidence,
            reason=data.get("reason", ""),
            gap=data.get("gap", ""),
        )

    def synthesize(
        self, decisions: list[CouncillorDecision], consensus_threshold: float
    ) -> tuple[SynthesisVerdict, dict, list[str]]:
        N = len(decisions)
        failures = [d for d in decisions if not d.parsed.passed]
        fail_ratio = len(failures) / N
        avg_conf = sum(d.parsed.confidence for d in decisions) / N

        if fail_ratio >= consensus_threshold and avg_conf >= 0.7:
            gap = "; ".join(d.parsed.gap for d in failures if d.parsed.gap)
            reason = "; ".join(d.parsed.reason for d in failures if d.parsed.reason)
            final = SynthesisVerdict(False, avg_conf, reason[:200], gap[:300])
        else:
            reason = "majority passed" if fail_ratio < 0.5 else "below confidence threshold for fail"
            final = SynthesisVerdict(True, avg_conf, reason, "")

        agreement_map = {
            d.label: {"verdict": "pass" if d.parsed.passed else "fail", "confidence": d.parsed.confidence}
            for d in decisions
        }
        trace = [f"fail_ratio={fail_ratio:.0%} avg_conf={avg_conf:.2f} → {'fail' if not final.passed else 'pass'}"]
        return final, agreement_map, trace

    def decisions_converged(self, decisions: list[SynthesisVerdict]) -> bool:
        return len({d.passed for d in decisions}) == 1

    def summarize_decision(self, decision: SynthesisVerdict) -> dict:
        return {"verdict": "pass" if decision.passed else "fail", "confidence": decision.confidence}
