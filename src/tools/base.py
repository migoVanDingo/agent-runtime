from abc import ABC, abstractmethod
from pydantic import BaseModel


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

    @property
    @abstractmethod
    def input_schema(self) -> InputSchema:
        pass

    @abstractmethod
    def execute(self, tool_input: dict) -> str:
        pass

    def to_api_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema.model_dump(),
        }
