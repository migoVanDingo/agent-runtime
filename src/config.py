from dataclasses import dataclass, field
from pathlib import Path
import yaml


@dataclass
class LLMConfig:
    max_tokens: int
    # Main agent LLM
    provider: str = "anthropic"
    model: str | None = None
    # Runtime LLM (classifier, monitor, importance, council)
    runtime_provider: str | None = None
    runtime_model: str | None = None


@dataclass
class TimeoutsConfig:
    default: int
    analysis: int
    fast: int


@dataclass
class ToolsConfig:
    strings_min_length: str
    hexdump_default_bytes: str


@dataclass
class RoutingConfig:
    embedding_model: str
    embedding_threshold: float
    default_toolsets: list[str]
    toolset_descriptions: dict[str, str]


@dataclass
class AgentConfig:
    system_prompt: str


@dataclass
class ArtifactStoreDecayConfig:
    enabled: bool = True
    factor: float = 0.85
    archive_threshold: float = 0.1


@dataclass
class ArtifactStoreWorkflowDiscoveryConfig:
    enabled: bool = True
    lookback_days: int = 30
    similarity_threshold: float = 0.82
    frequency_threshold: int = 5
    recency_decay: float = 0.95


@dataclass
class ArtifactStoreRagConfig:
    enabled: bool = False
    top_k: int = 3
    similarity_threshold: float = 0.6
    inject_on_start: bool = True
    max_injected_chars: int = 3000


@dataclass
class ArtifactStoreSqliteVecConfig:
    enabled: bool = True
    extension_path: str | None = None


@dataclass
class ArtifactStoreProjectConfig:
    enabled: bool = True
    default: str | None = None


@dataclass
class ArtifactStoreConfig:
    enabled: bool = True
    inline_threshold_bytes: int = 4096
    decay: ArtifactStoreDecayConfig = field(default_factory=ArtifactStoreDecayConfig)
    workflow_discovery: ArtifactStoreWorkflowDiscoveryConfig = field(
        default_factory=ArtifactStoreWorkflowDiscoveryConfig
    )
    rag: ArtifactStoreRagConfig = field(default_factory=ArtifactStoreRagConfig)
    sqlite_vec: ArtifactStoreSqliteVecConfig = field(default_factory=ArtifactStoreSqliteVecConfig)
    project: ArtifactStoreProjectConfig = field(default_factory=ArtifactStoreProjectConfig)


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
    directory: str = "_events"
    raw_payloads: bool = False
    redact_on_emit: bool = False     # scrub secrets before writing to JSONL
    redact_on_export: bool = True    # scrub secrets in all exported datasets


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


# ── Council config ──────────────────────────────────────────────────────────

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


@dataclass
class RuntimeConfig:
    events: EventsConfig
    sandbox: SandboxConfig
    pipeline: PipelineConfig
    plan_validator: PlanValidatorConfig
    plan_critic: PlanCriticConfig
    execution_monitor: ExecutionMonitorConfig
    context_manager: ContextManagerConfig
    council: CouncilConfig = field(default_factory=CouncilConfig)
    # Optional councils for specific decision points
    monitor_council: MonitorCouncilConfig = field(default_factory=MonitorCouncilConfig)
    synthesis_quality: SynthesisQualityConfig = field(default_factory=SynthesisQualityConfig)
    importance_council: ImportanceCouncilConfig = field(default_factory=ImportanceCouncilConfig)


@dataclass
class AppConfig:
    llm: LLMConfig
    timeouts: TimeoutsConfig
    tools: ToolsConfig
    routing: RoutingConfig
    agent: AgentConfig
    artifact_store: ArtifactStoreConfig
    planning: PlanningConfig
    runtime: RuntimeConfig


_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yml"


def load_config() -> AppConfig:
    with open(_CONFIG_PATH) as f:
        raw = yaml.safe_load(f)

    planning_raw = raw["planning"]
    planning = PlanningConfig(
        enabled=planning_raw["enabled"],
        model=planning_raw["model"],
        max_steps=planning_raw["max_steps"],
        retry_on_invalid=planning_raw["retry_on_invalid"],
    )
    artifact_store_raw = raw.get("artifact_store", {})
    decay_raw = artifact_store_raw.get("decay", {})
    workflow_raw = artifact_store_raw.get("workflow_discovery", {})
    rag_raw = artifact_store_raw.get("rag", {})
    sqlite_vec_raw = artifact_store_raw.get("sqlite_vec", {})
    project_raw = artifact_store_raw.get("project", {})
    artifact_store = ArtifactStoreConfig(
        enabled=artifact_store_raw.get("enabled", True),
        inline_threshold_bytes=int(artifact_store_raw.get("inline_threshold_bytes", 4096)),
        decay=ArtifactStoreDecayConfig(
            enabled=decay_raw.get("enabled", True),
            factor=float(decay_raw.get("factor", 0.85)),
            archive_threshold=float(decay_raw.get("archive_threshold", 0.1)),
        ),
        workflow_discovery=ArtifactStoreWorkflowDiscoveryConfig(
            enabled=workflow_raw.get("enabled", True),
            lookback_days=int(workflow_raw.get("lookback_days", 30)),
            similarity_threshold=float(workflow_raw.get("similarity_threshold", 0.82)),
            frequency_threshold=int(workflow_raw.get("frequency_threshold", 5)),
            recency_decay=float(workflow_raw.get("recency_decay", 0.95)),
        ),
        rag=ArtifactStoreRagConfig(
            enabled=rag_raw.get("enabled", False),
            top_k=int(rag_raw.get("top_k", 3)),
            similarity_threshold=float(rag_raw.get("similarity_threshold", 0.6)),
            inject_on_start=rag_raw.get("inject_on_start", True),
            max_injected_chars=int(rag_raw.get("max_injected_chars", 3000)),
        ),
        sqlite_vec=ArtifactStoreSqliteVecConfig(
            enabled=sqlite_vec_raw.get("enabled", True),
            extension_path=sqlite_vec_raw.get("extension_path"),
        ),
        project=ArtifactStoreProjectConfig(
            enabled=project_raw.get("enabled", True),
            default=project_raw.get("default"),
        ),
    )

    rt = raw["runtime"]

    council_raw = rt.get("council", {})
    councillors = [
        CouncillorConfig(
            provider=c["provider"],
            label=c["label"],
            model=c.get("model"),
        )
        for c in council_raw.get("councillors", [])
    ]
    debate_raw = council_raw.get("debate", {})
    scaling_raw = council_raw.get("dynamic_scaling", {})
    default_scaling = {"low": 0, "moderate": 1, "high": 3}
    dynamic_scaling = {**default_scaling, **scaling_raw}

    council = CouncilConfig(
        councillors=councillors,
        mode=council_raw.get("mode", "independent"),
        debate=DebateConfig(**debate_raw) if debate_raw else DebateConfig(),
        consensus_threshold=council_raw.get("consensus_threshold", 0.60),
        max_workers=council_raw.get("max_workers"),
        dynamic_scaling=dynamic_scaling,
    )

    events_raw = rt.get("events", {})
    sandbox_raw = rt.get("sandbox", {})

    runtime = RuntimeConfig(
        events=EventsConfig(
            enabled=events_raw.get("enabled", False),
            jsonl_enabled=events_raw.get("jsonl_enabled", False),
            directory=events_raw.get("directory", "_events"),
            raw_payloads=events_raw.get("raw_payloads", False),
            redact_on_emit=events_raw.get("redact_on_emit", False),
            redact_on_export=events_raw.get("redact_on_export", True),
        ),
        sandbox=SandboxConfig(
            backend=sandbox_raw.get("backend", "host"),
            allow_host_backend=sandbox_raw.get("allow_host_backend", True),
            docker_image=sandbox_raw.get("docker_image", "python:3.11-slim"),
            default_network=sandbox_raw.get("default_network", "disabled"),
            command_timeout_seconds=int(sandbox_raw.get("command_timeout_seconds", 30)),
            max_output_chars=int(sandbox_raw.get("max_output_chars", 50000)),
            workspace_root=sandbox_raw.get("workspace_root", "."),
            allowed_read_roots=list(sandbox_raw.get("allowed_read_roots", []) or []),
            allowed_write_roots=list(sandbox_raw.get("allowed_write_roots", []) or []),
        ),
        pipeline=PipelineConfig(**rt.get("pipeline", {})),
        plan_validator=PlanValidatorConfig(**rt["plan_validator"]),
        plan_critic=PlanCriticConfig(**rt["plan_critic"]),
        execution_monitor=ExecutionMonitorConfig(**rt["execution_monitor"]),
        context_manager=ContextManagerConfig(**rt["context_manager"]),
        council=council,
        monitor_council=MonitorCouncilConfig(**rt.get("monitor_council", {})),
        synthesis_quality=SynthesisQualityConfig(**rt.get("synthesis_quality", {})),
        importance_council=ImportanceCouncilConfig(**rt.get("importance_council", {})),
    )

    return AppConfig(
        llm=LLMConfig(**raw["llm"]),
        timeouts=TimeoutsConfig(**raw["timeouts"]),
        tools=ToolsConfig(**raw["tools"]),
        routing=RoutingConfig(**raw["routing"]),
        agent=AgentConfig(**raw["agent"]),
        artifact_store=artifact_store,
        planning=planning,
        runtime=runtime,
    )
