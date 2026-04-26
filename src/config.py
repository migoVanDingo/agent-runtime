from dataclasses import dataclass, field
from pathlib import Path
import yaml


@dataclass
class LLMConfig:
    max_tokens: int


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
class IntentClassifierConfig:
    enabled: bool
    context_window: int


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
class PlanCriticConfig:
    enabled: bool
    skip_low_risk: bool = False
    consensus_on_high_risk: bool = True


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
    intent_classifier: IntentClassifierConfig
    plan_validator: PlanValidatorConfig
    plan_critic: PlanCriticConfig
    execution_monitor: ExecutionMonitorConfig
    context_manager: ContextManagerConfig
    council: CouncilConfig = field(default_factory=CouncilConfig)


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

    runtime = RuntimeConfig(
        intent_classifier=IntentClassifierConfig(**rt["intent_classifier"]),
        plan_validator=PlanValidatorConfig(**rt["plan_validator"]),
        plan_critic=PlanCriticConfig(**rt["plan_critic"]),
        execution_monitor=ExecutionMonitorConfig(**rt["execution_monitor"]),
        context_manager=ContextManagerConfig(**rt["context_manager"]),
        council=council,
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
