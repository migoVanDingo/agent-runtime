from abc import ABC, abstractmethod
from enum import Enum
from pydantic import BaseModel


class ToolWeight(str, Enum):
    LIGHTWEIGHT = "lightweight"  # ~100-1000 chars output, fast
    MODERATE    = "moderate"     # ~1K-50K chars output
    HEAVY       = "heavy"       # ~50K+ chars output, or requires installation


class ToolProperty(BaseModel):
    type: str
    description: str


class InputSchema(BaseModel):
    type: str = "object"
    properties: dict[str, ToolProperty]
    required: list[str] = []


class BaseTool(ABC):
    name: str
    description: str
    weight: ToolWeight = ToolWeight.MODERATE  # default; tools override as needed

    @property
    @abstractmethod
    def input_schema(self) -> InputSchema:
        pass

    @abstractmethod
    def execute(self, tool_input: dict) -> str:
        pass

    def safe_execute(self, tool_input: dict) -> str:
        """Execute with input validation. Returns error string instead of crashing."""
        missing = [f for f in self.input_schema.required if f not in tool_input]
        if missing:
            return f"Error: missing required field(s): {', '.join(missing)}"
        try:
            return self.execute(tool_input)
        except Exception as e:
            return f"Error: {type(e).__name__}: {e}"

    def to_api_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema.model_dump(),
        }
