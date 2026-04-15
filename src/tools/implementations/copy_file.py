import shutil
from tools.base import BaseTool, InputSchema, ToolProperty


class CopyFileTool(BaseTool):
    name = "copy_file"
    description = "Copy a file from source to destination."

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "source": ToolProperty(type="string", description="Path to the source file"),
                "destination": ToolProperty(type="string", description="Path to the destination"),
            },
            required=["source", "destination"],
        )

    def execute(self, tool_input: dict) -> str:
        source = tool_input["source"]
        destination = tool_input["destination"]
        try:
            shutil.copy2(source, destination)
            return f"Copied {source} to {destination}"
        except Exception as e:
            return f"Error: {e}"
