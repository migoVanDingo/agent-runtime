"""MonitorAdapter — council vote on step-result assessment.

Replaces the single-model ExecutionMonitor LLM call when confidence falls
below the configured threshold. Uses majority vote across N councillors.
"""
from __future__ import annotations

from dataclasses import dataclass

from runtime.council import DeliberationAdapter, CouncilRound, CouncillorDecision
from runtime.json_extract import extract_json
from runtime.schema import StepDecision, StepAssessment


_SYSTEM = """\
You assess whether a step in an AI agent's multi-step plan succeeded or needs
intervention. You are one of several independent reviewers. Give your honest
assessment — do not try to agree with others.

Return ONLY a JSON object:
  {"decision": "continue"|"retry"|"replan"|"defer"|"skip",
   "confidence": 0.0-1.0,
   "reason": "one sentence"}

Decision guide:
- "continue": the step produced a meaningful result consistent with its description.
- "retry": the step failed but the error is recoverable (wrong path, transient failure).
- "replan": the step result reveals the remaining plan is invalid or needs restructuring.
- "defer": the step cannot complete yet — it depends on something not yet produced.
- "skip": the step is redundant; its objective was already achieved by a previous step.

Be concise. Confidence reflects how certain you are given the available evidence.\
"""

_USER = """\
Original request: {original_query}
Step {step_num}/{total_steps}: {step_description}
Action type: {action_type}

Step result (first 400 chars):
{step_result}

Completed steps:
{completed_summary}

Remaining steps:
{remaining_summary}

Flags: {flags}

Assess this step result.\
"""


@dataclass
class MonitorDecision:
    decision: StepDecision
    confidence: float
    reason: str


class MonitorAdapter(DeliberationAdapter[MonitorDecision]):
    """Council adapter for the execution monitor.

    Input:  dict with keys matching the _USER template
    Output: MonitorDecision (majority-vote synthesis)
    """

    def system_prompt(self) -> str:
        return _SYSTEM

    def build_prompt(self, council_input: dict, prior_rounds: list[CouncilRound] | None = None) -> str:
        return _USER.format(**council_input)

    def parse_response(self, raw: str) -> MonitorDecision:
        data = extract_json(raw)
        if not isinstance(data, dict):
            return MonitorDecision(StepDecision.CONTINUE, 1.0, "parse error")
        decision_str = data.get("decision", "continue")
        try:
            decision = StepDecision(decision_str)
        except ValueError:
            decision = StepDecision.CONTINUE
        confidence = max(0.0, min(1.0, float(data.get("confidence", 1.0))))
        return MonitorDecision(decision, confidence, data.get("reason", ""))

    def synthesize(
        self, decisions: list[CouncillorDecision], consensus_threshold: float
    ) -> tuple[MonitorDecision, dict, list[str]]:
        from collections import Counter
        N = len(decisions)
        votes = [d.parsed.decision for d in decisions]
        counts = Counter(votes)
        majority_decision, majority_count = counts.most_common(1)[0]
        ratio = majority_count / N
        avg_confidence = sum(d.parsed.confidence for d in decisions) / N

        agreement_map = {
            d.label: {"decision": d.parsed.decision.value, "confidence": d.parsed.confidence}
            for d in decisions
        }
        trace = [
            f"{majority_decision.value}: {majority_count}/{N} votes ({ratio:.0%}) "
            f"avg_confidence={avg_confidence:.2f}"
        ]
        reason = "; ".join(d.parsed.reason for d in decisions if d.parsed.reason)[:200]
        return MonitorDecision(majority_decision, avg_confidence, reason), agreement_map, trace

    def decisions_converged(self, decisions: list[MonitorDecision]) -> bool:
        return len({d.decision for d in decisions}) == 1

    def summarize_decision(self, decision: MonitorDecision) -> dict:
        return {"decision": decision.decision.value, "confidence": decision.confidence}
