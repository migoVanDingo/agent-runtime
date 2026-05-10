from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum


# ── Intent Classifier ───────────────────────────────────────────────

@dataclass
class ClassifierResult:
    mode: str            # "plan" | "direct"
    risk: str            # "low" | "moderate" | "high"
    skill_hint: str | None = None    # skill name suggested by classifier, or None

    @property
    def workflow_hint(self) -> str | None:
        """Legacy alias — kept for any remaining callers."""
        return self.skill_hint


# ── Execution Monitor ────────────────────────────────────────────────

class StepDecision(str, Enum):
    CONTINUE      = "continue"
    RETRY         = "retry"
    REPLAN        = "replan"
    DEFER         = "defer"
    SKIP          = "skip"
    ESCALATE      = "escalate"
    GOAL_ACHIEVED = "goal_achieved"


@dataclass
class StepAssessment:
    decision: StepDecision
    reason: str
    suggestion: str | None = None
    confidence: float = 1.0  # 0.0-1.0, how confident the monitor is in this decision


# ── Plan Validator ───────────────────────────────────────────────────

class ValidationStatus(str, Enum):
    VALID   = "valid"
    INVALID = "invalid"


@dataclass
class ValidationResult:
    status: ValidationStatus
    feedback: str | None = None


# ── Plan Critic ─────────────────────────────────────────────────────

class CriticVerdict(str, Enum):
    APPROVED   = "approved"
    CHALLENGED = "challenged"


@dataclass
class CriticChallenge:
    step: int
    tool: str | None
    challenge: str
    suggestion: str  # "drop", "replace", "justify"


@dataclass
class CriticResult:
    verdict: CriticVerdict
    reasoning: str | None = None
    challenges: list[CriticChallenge] | None = None
    council_run_id: str | None = None   # set when result came from a council deliberation


# ── Context Manager (AFM-inspired) ──────────────────────────────────

class FidelityLevel(str, Enum):
    FULL        = "full"
    COMPRESSED  = "compressed"
    PLACEHOLDER = "placeholder"


class Importance(str, Enum):
    CRITICAL = "critical"
    HIGH     = "high"
    MEDIUM   = "medium"
    LOW      = "low"


@dataclass
class ScoredMessage:
    index: int
    message: dict
    score: float
    importance: Importance
    fidelity: FidelityLevel
    token_estimate: int


# ── Continuation Stage ────────────────────────────────────────────

class ContinuationDecision(str, Enum):
    SYNTHESIZE = "synthesize"
    DONE       = "done"
    LOOP       = "loop"


@dataclass
class ContinuationState:
    iteration_count: int = 0
    last_decision: str | None = None
    artifacts_carried: list[str] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)
