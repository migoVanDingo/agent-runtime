import os
from tools.base import BaseTool, InputSchema, ToolProperty


class ListFilesTool(BaseTool):
    name = "list_files"
    description = "List files in a directory"

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "path": ToolProperty(
                    type="string", description="Path to the directory to list"
                )
            },
            required=["path"],
        )

    def execute(self, tool_input: dict) -> str:
        path = tool_input["path"]
        try:
            files = os.listdir(path)
            return "\n".join(files)
        except Exception as e:
            return f"Error: {e}"
