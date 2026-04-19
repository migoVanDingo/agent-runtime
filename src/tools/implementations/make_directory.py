import os
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight


class MakeDirectoryTool(BaseTool):
    name = "make_directory"
    description = "Create a directory and any missing parent directories."
    weight = ToolWeight.LIGHTWEIGHT

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "path": ToolProperty(type="string", description="Path of the directory to create"),
            },
            required=["path"],
        )

    def execute(self, tool_input: dict) -> str:
        path = tool_input["path"]
        try:
            if os.path.isdir(path):
                return f"Directory already exists: {path}"
            os.makedirs(path, exist_ok=True)
            return f"Created directory: {path}"
        except Exception as e:
            return f"Error: {e}"
