"""Container execution configuration dataclasses."""
from dataclasses import dataclass, field


@dataclass
class ContainerLimitsConfig:
    timeout_seconds: float = 60.0
    memory: str = "256m"
    cpus: float = 1.0
    pids_limit: int = 64
    network: str = "none"


@dataclass
class ContainerImagesConfig:
    native: str = "gcc:12"
    jvm: str = "openjdk:17-slim"
    python: str = "python:3.11-slim"
    base: str = "ubuntu:22.04"


@dataclass
class ContainerConfig:
    limits: ContainerLimitsConfig = field(default_factory=ContainerLimitsConfig)
    images: ContainerImagesConfig = field(default_factory=ContainerImagesConfig)
