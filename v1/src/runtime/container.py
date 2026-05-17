"""Dependency container.

Holds all shared service instances for one agent session. `main.py` builds
one container and passes it to `Agent`. Tests build fake containers without
touching module-level globals.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app_config import AppConfig
    from providers.base import BaseProvider
    from routing.static_router import StaticRouter
    from runtime.artifact_store import ArtifactStore
    from runtime.events import EventBus
    from runtime.sandbox.manager import SandboxManager
    from tools.registry import ToolRegistry


@dataclass
class Container:
    """All shared service instances for one agent session.

    Fields are optional where the feature may be disabled.
    """
    config: "AppConfig"
    provider: "BaseProvider"
    runtime_provider: "BaseProvider"
    registry: "ToolRegistry"
    router: "StaticRouter"
    event_bus: "EventBus"
    sandbox: "SandboxManager"
    artifact_store: "ArtifactStore | None" = None

    @classmethod
    def build(cls) -> "Container":
        """Build a container from the global app config + env settings.

        This is the single place that reads module-level singletons.
        All other code should receive a Container and read from it.
        """
        from app_config import config
        from providers.factory import get_provider, get_runtime_provider
        from routing.static_router import StaticRouter
        from runtime.events import get_event_bus
        from runtime.sandbox.manager import SandboxManager
        from tools.registry import ToolRegistry
        from tools.toolsets import ALL_TOOLSETS

        registry = ToolRegistry()
        for toolset in ALL_TOOLSETS:
            registry.register_toolset(toolset)

        return cls(
            config=config,
            provider=get_provider(),
            runtime_provider=get_runtime_provider(),
            registry=registry,
            router=StaticRouter(registry),
            event_bus=get_event_bus(),
            sandbox=SandboxManager(),
            artifact_store=None,  # set by main.py after init_store
        )
