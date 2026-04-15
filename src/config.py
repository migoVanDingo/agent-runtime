from dataclasses import dataclass
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
class PlanningGateConfig:
    min_message_length: int
    indicator_words: list[str]


@dataclass
class PlanningConfig:
    enabled: bool
    model: str | None
    max_steps: int
    retry_on_invalid: bool
    gate: PlanningGateConfig


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


@dataclass
class ContextManagerConfig:
    enabled: bool
    message_budget_tokens: int
    half_life_turns: int
    threshold_high: float
    threshold_mid: float
    compressed_max_chars: int


@dataclass
class RuntimeConfig:
    intent_classifier: IntentClassifierConfig
    plan_validator: PlanValidatorConfig
    execution_monitor: ExecutionMonitorConfig
    context_manager: ContextManagerConfig


@dataclass
class AppConfig:
    llm: LLMConfig
    timeouts: TimeoutsConfig
    tools: ToolsConfig
    routing: RoutingConfig
    agent: AgentConfig
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
        gate=PlanningGateConfig(**planning_raw["gate"]),
    )

    rt = raw["runtime"]
    runtime = RuntimeConfig(
        intent_classifier=IntentClassifierConfig(**rt["intent_classifier"]),
        plan_validator=PlanValidatorConfig(**rt["plan_validator"]),
        execution_monitor=ExecutionMonitorConfig(**rt["execution_monitor"]),
        context_manager=ContextManagerConfig(**rt["context_manager"]),
    )

    return AppConfig(
        llm=LLMConfig(**raw["llm"]),
        timeouts=TimeoutsConfig(**raw["timeouts"]),
        tools=ToolsConfig(**raw["tools"]),
        routing=RoutingConfig(**raw["routing"]),
        agent=AgentConfig(**raw["agent"]),
        planning=planning,
        runtime=runtime,
    )
