from tools.base import BaseTool
from tools.toolset import Toolset
from shared_types import RoutingRule
from logger import get_logger

logger = get_logger(__name__)


class ToolRegistry:

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}
        self._toolsets: dict[str, Toolset] = {}
        # Plugin tools record their manifest here so the guard / introspection
        # tools can consult permissions blocks without round-tripping disk.
        self._plugin_manifests: dict[str, object] = {}

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool
        logger.info(f"Registered tool: {tool.name}")

    def get(self, name: str) -> BaseTool:
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"Tool not found: {name}")
        return tool

    def to_api_schema(self) -> list[dict]:
        return [tool.to_api_schema() for tool in self._tools.values()]

    def register_toolset(self, toolset: Toolset) -> None:
        self._toolsets[toolset.name] = toolset
        for tool in toolset.tools:
            self.register(tool)
        logger.info(f"Registered toolset: {toolset.name} ({len(toolset.tools)} tools)")

    def get_toolset_tools(self, name: str) -> list[BaseTool]:
        toolset = self._toolsets.get(name)
        if toolset is None:
            raise KeyError(f"Toolset not found: {name}")
        return toolset.tools

    def get_toolset_schema(self, names: list[str]) -> list[dict]:
        """Collect API schemas for the named toolsets. Unknown names are skipped.

        Skip-and-warn instead of raising because callers commonly pass the
        output of ``StaticRouter.select(...)``, which can return toolset
        names that exist in ``config.routing.toolset_descriptions`` but
        aren't in THIS registry — most often a narrowed sub-agent registry.
        Raising here would crash the agent on an upstream routing decision
        the registry has no say in. Verified failure mode in session
        SES01KRTZG0R4BN105HB2M8J17XTE: the GhidraAnalyst sub-agent's child
        registry didn't have ``analysis``; the router returned it anyway;
        get_toolset_schema raised KeyError → the entire sub-agent crashed.
        """
        seen = set()
        schemas = []
        for name in names:
            toolset = self._toolsets.get(name)
            if toolset is None:
                logger.warning(
                    f"  get_toolset_schema: toolset {name!r} not in this registry — "
                    f"skipping (have: {sorted(self._toolsets)})"
                )
                continue
            for tool in toolset.tools:
                if tool.name not in seen:
                    seen.add(tool.name)
                    schemas.append(tool.to_api_schema())
        return schemas

    def tool_names(self) -> set[str]:
        return set(self._tools.keys())

    def toolset_names(self) -> list[str]:
        return list(self._toolsets.keys())

    def get_tool_schema(self, tool_name: str) -> list[dict]:
        """Get API schema for a single tool. Returns a list for API compatibility."""
        tool = self._tools.get(tool_name)
        if tool is None:
            return []
        return [tool.to_api_schema()]

    def get_tool_description(self, tool_name: str) -> str:
        """Get a tool's description with weight annotation."""
        tool = self._tools.get(tool_name)
        if not tool:
            return ""
        return f"[{tool.weight.value}] {tool.description}"

    def toolset_tool_names(self, toolset_name: str) -> list[str]:
        """Return the names of tools registered under a toolset."""
        toolset = self._toolsets.get(toolset_name)
        if toolset is None:
            return []
        return [t.name for t in toolset.tools]

    def get_all_rules(self) -> list[RoutingRule]:
        rules = []
        for toolset in self._toolsets.values():
            rules.extend(toolset.rules)
        return rules

    # ── Plugin support ─────────────────────────────────────────────────

    def attach_tool_to_toolset(self, toolset_name: str, tool: BaseTool) -> None:
        """Append a plugin tool to an existing toolset's `tools` list.

        Used by the plugin loader when a single tool declares
        ``extends_toolset = "<name>"``. No-op if the toolset doesn't exist
        or the tool is already a member.
        """
        toolset = self._toolsets.get(toolset_name)
        if toolset is None:
            return
        if any(t.name == tool.name for t in toolset.tools):
            return
        toolset.tools.append(tool)
        logger.info(f"  plugin tool {tool.name} attached to toolset {toolset_name}")

    def record_plugin_manifest(self, tool_name: str, manifest) -> None:
        """Associate a plugin manifest with a tool name (consulted by ActionGuard)."""
        self._plugin_manifests[tool_name] = manifest

    def get_plugin_manifest(self, tool_name: str):
        """Return the plugin manifest backing ``tool_name``, or None for built-ins."""
        return self._plugin_manifests.get(tool_name)
