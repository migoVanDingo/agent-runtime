from dataclasses import dataclass, field
from tools.base import BaseTool
from shared_types import RoutingRule


@dataclass
class Toolset:
    name: str
    description: str
    tools: list[BaseTool] = field(default_factory=list)
    rules: list[RoutingRule] = field(default_factory=list)
