from __future__ import annotations
from dataclasses import dataclass
from enum import Enum


# ── Execution Monitor ────────────────────────────────────────────────

class StepDecision(str, Enum):
    CONTINUE = "continue"
    RETRY    = "retry"
    REPLAN   = "replan"
    DEFER    = "defer"
    SKIP     = "skip"
    ESCALATE = "escalate"


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
