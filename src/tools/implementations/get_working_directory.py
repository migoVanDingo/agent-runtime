import os
from tools.base import BaseTool, InputSchema, ToolProperty


class GetWorkingDirectoryTool(BaseTool):
    name = "get_working_directory"
    description = "Return the current working directory."

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(properties={})

    def execute(self, tool_input: dict) -> str:
        return os.getcwd()
