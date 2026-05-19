"""Tool factory.

Builds a ToolRegistry from `config.tools.enabled` + per-tool config.
Adding a new tool = add a class + a case in `_BUILDERS` below.
"""
from __future__ import annotations

from arc.config import ToolsConfig
from arc.tools.base import Tool, ToolRegistry
from arc.tools.bash_exec import BashExecTool
from arc.tools.ls import LSTool


# tool_name → callable that builds it from its per-tool config dict
_BUILDERS = {
    "ls": LSTool.from_config,
    "bash_exec": BashExecTool.from_config,
}


def build(cfg: ToolsConfig) -> ToolRegistry:
    """Construct registry from config. Unknown tool names raise at startup."""
    registry = ToolRegistry()
    for name in cfg.enabled:
        if name not in _BUILDERS:
            raise ValueError(
                f"unknown tool {name!r} in tools.enabled\n"
                f"  known: {sorted(_BUILDERS.keys())}\n"
                f"  (add a class + entry in arc/tools/__init__.py to support more)"
            )
        tool_cfg = cfg.config.get(name, {})
        registry.register(_BUILDERS[name](tool_cfg))
    return registry
