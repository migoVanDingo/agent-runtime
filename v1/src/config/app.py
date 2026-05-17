"""Top-level AppConfig dataclass and TimeoutsConfig."""
from dataclasses import dataclass, field

from config.llm import LLMConfig
from config.tools import ToolsConfig, ToolPolicyConfig
from config.routing import RoutingConfig
from config.agent import AgentConfig
from config.artifact_store import ArtifactStoreConfig, StorageConfig
from config.rag import RagConfig
from config.runtime import (
    PlanningConfig,
    PipelineConfig,
    PlanValidatorConfig,
    PlanCriticConfig,
    ExecutionMonitorConfig,
    ContextConfig,
    ContextManagerConfig,
    MonitorCouncilConfig,
    SynthesisQualityConfig,
    ImportanceCouncilConfig,
    EventsConfig,
    ContinuationConfig,
    SandboxConfig,
)
from config.council import CouncilConfig
from config.container import ContainerConfig
from config.subagents import SubAgentsConfig


@dataclass
class TimeoutsConfig:
    default: int
    analysis: int
    fast: int


@dataclass
class RuntimeConfig:
    events: EventsConfig
    sandbox: SandboxConfig
    pipeline: PipelineConfig
    plan_validator: PlanValidatorConfig
    plan_critic: PlanCriticConfig
    execution_monitor: ExecutionMonitorConfig
    context_manager: ContextManagerConfig
    # Pluggable context strategy — populated by the loader from either the
    # new ``runtime.context`` block or the legacy ``runtime.context_manager``.
    context: ContextConfig = field(default_factory=ContextConfig)
    council: CouncilConfig = field(default_factory=CouncilConfig)
    # Optional councils for specific decision points
    monitor_council: MonitorCouncilConfig = field(default_factory=MonitorCouncilConfig)
    synthesis_quality: SynthesisQualityConfig = field(default_factory=SynthesisQualityConfig)
    importance_council: ImportanceCouncilConfig = field(default_factory=ImportanceCouncilConfig)
    tool_policy: ToolPolicyConfig = field(default_factory=ToolPolicyConfig)
    continuation: ContinuationConfig = field(default_factory=ContinuationConfig)


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
    storage: StorageConfig = field(default_factory=StorageConfig)
    rag: RagConfig = field(default_factory=RagConfig)
    container: ContainerConfig = field(default_factory=ContainerConfig)
    # 0090e — per-sub-agent provider/model/timeout overrides loaded from
    # the top-level ``subagents:`` block in config.yml. Empty mapping
    # means every sub-agent uses its spec defaults.
    subagents: SubAgentsConfig = field(default_factory=SubAgentsConfig)
