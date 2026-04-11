from tools.base import BaseTool
from logger import get_logger

logger = get_logger(__name__)


class ToolRegistry:

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

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
