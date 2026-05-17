from dataclasses import dataclass
from typing import Callable


@dataclass
class RoutingRule:
    toolset: str
    condition: Callable[[str, list[dict]], bool]
