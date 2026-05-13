"""YAML config loader — parses config.yml into AppConfig dataclasses."""
from pathlib import Path
import yaml

from config.llm import LLMConfig
from config.tools import ToolsConfig, Radare2Config, GhidraConfig, AngrConfig, ToolPolicyConfig
from config.routing import RoutingConfig
from config.agent import AgentConfig
from config.artifact_store import (
    ArtifactStoreConfig,
    ArtifactStoreDecayConfig,
    ArtifactStoreWorkflowDiscoveryConfig,
    ArtifactStoreSqliteVecConfig,
    ArtifactStoreProjectConfig,
    StorageConfig,
)
from config.rag import RagConfig
from config.runtime import (
    PlanningConfig,
    PlanValidatorConfig,
    PlanCriticConfig,
    ExecutionMonitorConfig,
    ContextManagerConfig,
    PipelineConfig,
    MonitorCouncilConfig,
    SynthesisQualityConfig,
    ImportanceCouncilConfig,
    EventsConfig,
    ContinuationConfig,
    SandboxConfig,
)
from config.council import CouncilConfig, CouncillorConfig, DebateConfig
from config.container import ContainerConfig, ContainerLimitsConfig, ContainerImagesConfig
from config.app import AppConfig, TimeoutsConfig, RuntimeConfig


_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config.yml"


def _load_tools_config(raw: dict) -> ToolsConfig:
    r2_raw = raw.pop("radare2", {}) or {}
    ghidra_raw = raw.pop("ghidra", {}) or {}
    angr_raw = raw.pop("angr", {}) or {}
    return ToolsConfig(
        **raw,
        radare2=Radare2Config(**r2_raw) if r2_raw else Radare2Config(),
        ghidra=GhidraConfig(**ghidra_raw) if ghidra_raw else GhidraConfig(),
        angr=AngrConfig(**angr_raw) if angr_raw else AngrConfig(),
    )


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
        sqlite_vec=ArtifactStoreSqliteVecConfig(
            enabled=sqlite_vec_raw.get("enabled", True),
            extension_path=sqlite_vec_raw.get("extension_path"),
        ),
        project=ArtifactStoreProjectConfig(
            enabled=project_raw.get("enabled", True),
            default=project_raw.get("default"),
        ),
    )

    storage_raw = raw.get("storage", {})
    storage = StorageConfig(base_uri=storage_raw.get("base_uri", ""))

    rag_raw = raw.get("rag", {})
    rag = RagConfig(
        enabled=rag_raw.get("enabled", False),
        mode=rag_raw.get("mode", "local"),
        http_base_url=rag_raw.get("http_base_url", "http://localhost:17433"),
        embedding_provider=rag_raw.get("embedding_provider", "sentence_transformers"),
        embedding_model=rag_raw.get("embedding_model", "all-MiniLM-L6-v2"),
        top_k=int(rag_raw.get("top_k", 5)),
        threshold=float(rag_raw.get("threshold", 0.65)),
        injection_budget_chars=int(rag_raw.get("injection_budget_chars", 2000)),
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
        tool_policy=ToolPolicyConfig(
            utility_tools=rt.get("tool_policy", {}).get("utility_tools", {}),
        ),
        continuation=ContinuationConfig(**rt.get("continuation", {})),
    )

    container_raw = raw.get("container", {})
    container_limits_raw = container_raw.get("limits", {})
    container_images_raw = container_raw.get("images", {})
    container = ContainerConfig(
        limits=ContainerLimitsConfig(
            timeout_seconds=float(container_limits_raw.get("timeout_seconds", 60.0)),
            memory=container_limits_raw.get("memory", "256m"),
            cpus=float(container_limits_raw.get("cpus", 1.0)),
            pids_limit=int(container_limits_raw.get("pids_limit", 64)),
            network=container_limits_raw.get("network", "none"),
        ),
        images=ContainerImagesConfig(
            native=container_images_raw.get("native", "gcc:12"),
            jvm=container_images_raw.get("jvm", "openjdk:17-slim"),
            python=container_images_raw.get("python", "python:3.11-slim"),
            base=container_images_raw.get("base", "ubuntu:22.04"),
        ),
    )

    return AppConfig(
        llm=LLMConfig(**raw["llm"]),
        timeouts=TimeoutsConfig(**raw["timeouts"]),
        tools=_load_tools_config(raw.get("tools", {})),
        routing=RoutingConfig(**raw["routing"]),
        agent=AgentConfig(**raw["agent"]),
        artifact_store=artifact_store,
        planning=planning,
        runtime=runtime,
        storage=storage,
        rag=rag,
        container=container,
    )
