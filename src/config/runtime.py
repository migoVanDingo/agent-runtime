"""Runtime configuration dataclasses: pipeline, planning, monitoring, continuation, etc."""
from dataclasses import dataclass, field


@dataclass
class PlanningConfig:
    enabled: bool
    model: str | None
    max_steps: int
    retry_on_invalid: bool


@dataclass
class PlanValidatorConfig:
    enabled: bool


@dataclass
class ExecutionMonitorConfig:
    enabled: bool
    max_step_retries: int
    max_defers_per_step: int
    step_max_tool_calls: int = 10
    step_max_iterations: int = 3
    error_recovery_clears_step_error: bool = True


@dataclass
class ContextManagerConfig:
    enabled: bool
    message_budget_tokens: int
    half_life_turns: int
    threshold_high: float
    threshold_mid: float
    compressed_max_chars: int


@dataclass
class PipelineConfig:
    max_retries_per_stage: int = 2
    max_ask_user_per_stage: int = 1
    # Direct-mode / fallback tool loop caps
    max_tool_calls: int = 15
    max_iterations: int = 20
    max_consecutive_errors: int = 3
    tool_result_truncate_chars: int = 50_000


@dataclass
class PlanCriticConfig:
    enabled: bool
    skip_low_risk: bool = False
    consensus_on_high_risk: bool = True
    complexity_threshold: int = 8


@dataclass
class MonitorCouncilConfig:
    """Council vote replaces single-model monitor when confidence falls below threshold."""
    enabled: bool = False
    confidence_threshold: float = 0.65  # trigger council when single-model confidence < this
    n_councillors: int = 2              # how many councillors to use (taken from the top of the pool)


@dataclass
class SynthesisQualityConfig:
    """Council quality gate on synthesized responses after plans with failures."""
    enabled: bool = False
    only_after_failures: bool = True    # only gate when the plan had retries or replans
    n_councillors: int = 2


@dataclass
class ImportanceCouncilConfig:
    """Council vote on step-result importance when the single-model result is MEDIUM (ambiguous)."""
    enabled: bool = False
    only_on_medium: bool = True         # only deliberate when single-model says MEDIUM
    n_councillors: int = 2


@dataclass
class EventsConfig:
    enabled: bool = False
    jsonl_enabled: bool = False
    directory: str = "_events"   # legacy — no longer used; path derived from session_paths
    raw_payloads: bool = False
    redact_on_emit: bool = False     # scrub secrets before writing to JSONL
    redact_on_export: bool = True    # scrub secrets in all exported datasets


@dataclass
class ContinuationConfig:
    """Owns task-level completion decisions and continuation loops."""
    enabled: bool = True
    max_iterations: int = 8
    # LLM judge: off by default — only fires for skills with completion_criteria.
    # Enable when skill criteria coverage is comprehensive enough to trust LOOP decisions.
    use_llm_judge: bool = False
    trust_skill_criteria: bool = True
    llm_judge_label: str = "ContinuationStage"


@dataclass
class SandboxConfig:
    backend: str = "auto"
    allow_host_backend: bool = True
    docker_image: str = "python:3.11-slim"
    default_network: str = "disabled"
    command_timeout_seconds: int = 30
    max_output_chars: int = 50000
    workspace_root: str = "."
    allowed_read_roots: list[str] = field(default_factory=list)
    allowed_write_roots: list[str] = field(default_factory=list)
