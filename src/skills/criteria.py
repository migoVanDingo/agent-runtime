"""CompletionCriteria — how a skill declares 'done'.

ContinuationStage evaluates these. Structural criteria are pure
predicates over plan execution state and are cheap. LLM-judged criteria
make one focused chat call.
"""
from __future__ import annotations
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from planning.schema import Plan, Step
from runtime.schema import ContinuationDecision


class CriteriaOutcome(str, Enum):
    MET          = "met"
    NOT_MET      = "not_met"
    INCONCLUSIVE = "inconclusive"


@dataclass
class CriteriaContext:
    plan: Plan
    user_message: str


class CompletionCriteria(ABC):
    """Base class. on_met says what to do when the criteria are satisfied."""
    on_met: ContinuationDecision = ContinuationDecision.SYNTHESIZE

    @abstractmethod
    def evaluate(self, ctx: CriteriaContext) -> CriteriaOutcome:
        ...


@dataclass
class StructuralCriteria(CompletionCriteria):
    """Pass if the last step using tool_name satisfies predicate.

    predicate receives the step's result string and returns True/False/None.
    None ⇒ inconclusive (e.g., couldn't parse JSON).
    """
    tool_name: str
    predicate: Callable[[str], bool | None]
    on_met: ContinuationDecision = ContinuationDecision.SYNTHESIZE

    def evaluate(self, ctx: CriteriaContext) -> CriteriaOutcome:
        target = next(
            (s for s in reversed(ctx.plan.steps) if s.tool == self.tool_name),
            None,
        )
        if target is None or not target.result:
            return CriteriaOutcome.INCONCLUSIVE
        verdict = self.predicate(target.result)
        if verdict is None:
            return CriteriaOutcome.INCONCLUSIVE
        return CriteriaOutcome.MET if verdict else CriteriaOutcome.NOT_MET


@dataclass
class LLMJudgedCriteria(CompletionCriteria):
    """The skill provides a focused yes/no prompt; ContinuationStage runs it."""
    prompt: str
    on_met: ContinuationDecision = ContinuationDecision.SYNTHESIZE

    def evaluate(self, ctx: CriteriaContext) -> CriteriaOutcome:
        # Actual evaluation is done by ContinuationStage which has the provider.
        return CriteriaOutcome.INCONCLUSIVE


# ── Common predicates ─────────────────────────────────────────────────────

def diff_behavior_all_match(result: str) -> bool | None:
    """Predicate for diff_behavior: True iff DiffReport.all_match is True.

    Returns False (NOT_MET) for error results so ContinuationStage loops
    rather than synthesizing a false "success" response.
    """
    if not result:
        return None
    # Error responses → test didn't run → NOT_MET (should retry/fix)
    stripped = result.strip()
    if stripped.startswith('{"error"') or '"error":' in stripped[:50]:
        return False
    try:
        data = json.loads(result)
    except (ValueError, TypeError):
        if '"all_match"' in result:
            low = result.lower()
            if '"all_match": true' in low:
                return True
            if '"all_match": false' in low:
                return False
        return None
    if isinstance(data, dict):
        # Explicit error key → test failed to run
        if "error" in data:
            return False
        return bool(data.get("all_match", False)) if "all_match" in data else None
    return None


def file_written(path: str) -> Callable[[str], bool | None]:
    """Predicate factory: True iff a write_file step succeeded for path."""
    def predicate(result: str) -> bool | None:
        if not result:
            return None
        lowered = result.lower()
        if "error" in lowered or "permission denied" in lowered:
            return False
        return path in result or "wrote" in lowered
    return predicate
