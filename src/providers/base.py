from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict
    type: str = "tool_use"


@dataclass
class ProviderResponse:
    stop_reason: str
    content: list[TextBlock | ToolUseBlock] = field(default_factory=list)


class BaseProvider(ABC):

    @abstractmethod
    def chat(
        self,
        messages: list[dict],
        tools: list[dict],
        system: str,
        json_schema: dict | None = None,
    ) -> ProviderResponse:
        pass
