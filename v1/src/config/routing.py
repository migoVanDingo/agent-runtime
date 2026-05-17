"""Routing configuration dataclass."""
from dataclasses import dataclass


@dataclass
class RoutingConfig:
    embedding_model: str
    embedding_threshold: float
    default_toolsets: list[str]
    toolset_descriptions: dict[str, str]
