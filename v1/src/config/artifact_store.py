"""Artifact store configuration dataclasses."""
from dataclasses import dataclass, field


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
    sqlite_vec: ArtifactStoreSqliteVecConfig = field(default_factory=ArtifactStoreSqliteVecConfig)
    project: ArtifactStoreProjectConfig = field(default_factory=ArtifactStoreProjectConfig)


@dataclass
class StorageConfig:
    base_uri: str = ""   # "" = local filesystem; "gs://bucket" = GCS
