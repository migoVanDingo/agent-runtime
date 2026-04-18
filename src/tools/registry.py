from tools.base import BaseTool
from tools.toolset import Toolset
from shared_types import RoutingRule
from logger import get_logger

logger = get_logger(__name__)


class ToolRegistry:

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}
        self._toolsets: dict[str, Toolset] = {}

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
        seen = set()
        schemas = []
        for name in names:
            for tool in self.get_toolset_tools(name):
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

    def get_all_rules(self) -> list[RoutingRule]:
        rules = []
        for toolset in self._toolsets.values():
            rules.extend(toolset.rules)
        return rules
