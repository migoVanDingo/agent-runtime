"""Plugin system — user-installable tools and skills.

Two discovery paths:

1. Python entry points (canonical for distributed plugins on PyPI):
       [project.entry-points."arc.tools"]
       my_tool = "my_pkg:MyTool"

2. Filesystem (no-friction local plugins):
       ~/.arc/plugins/tools/my_tool.py
       ~/.arc/plugins/skills/my_skill/plugin.toml

Plugins are passive participants — they expose `BaseTool` / `Skill` subclasses,
never make control-flow decisions. The runtime stays in charge of retries,
escalations, and pause/cancel (see _plans/0079-runtime-as-god.md).
"""
from plugins.loader import LoadReport, discover_plugins, load_into
from plugins.manifest import Manifest, ManifestError, parse_dict_manifest, parse_toml_manifest

__all__ = [
    "LoadReport",
    "Manifest",
    "ManifestError",
    "discover_plugins",
    "load_into",
    "parse_dict_manifest",
    "parse_toml_manifest",
]
