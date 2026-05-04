"""Council deliberation adapters.

Each adapter encapsulates one deliberation context — its system prompt,
user-turn format, response schema, synthesis algorithm, and convergence
check. The Council machinery is shared across all of them.

Adapters:
  monitor.py           — step-result assessment (replaces single-model monitor)
  synthesis_quality.py — synthesized response quality gate
  importance.py        — step-result importance tier classification
  plan_critic.py       — adversarial plan review (the original adapter)
"""
from runtime.adapters.plan_critic import PlanCriticAdapter
from runtime.adapters.monitor import MonitorAdapter, MonitorDecision
from runtime.adapters.synthesis_quality import SynthesisQualityAdapter, SynthesisVerdict
from runtime.adapters.importance import ImportanceAdapter, ImportanceDecision

__all__ = [
    "PlanCriticAdapter",
    "MonitorAdapter", "MonitorDecision",
    "SynthesisQualityAdapter", "SynthesisVerdict",
    "ImportanceAdapter", "ImportanceDecision",
]
