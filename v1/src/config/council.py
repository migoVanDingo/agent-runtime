"""Council configuration dataclasses (councillors, debate, master config)."""
from dataclasses import dataclass, field


@dataclass
class CouncillorConfig:
    provider: str
    label: str
    model: str | None = None


@dataclass
class DebateConfig:
    max_rounds: int = 3
    early_exit_on_consensus: bool = True


@dataclass
class CouncilConfig:
    # same-provider N times → variance/noise reduction (self-consistency)
    # different providers   → epistemic independence (different training, priors)
    # mixed N+M            → both; labels distinguish councillors in logs/metrics
    councillors: list[CouncillorConfig] = field(default_factory=list)
    mode: str = "independent"           # independent | debate
    debate: DebateConfig = field(default_factory=DebateConfig)
    consensus_threshold: float = 0.60
    max_workers: int | None = None      # None = len(councillors); 1 = sequential (debug)
    dynamic_scaling: dict[str, int] = field(default_factory=lambda: {"low": 0, "moderate": 1, "high": 3})
