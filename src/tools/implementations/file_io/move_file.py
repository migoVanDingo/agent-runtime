import shutil
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight


class MoveFileTool(BaseTool):
    name = "move_file"
    description = "Move or rename a file or directory."
    weight = ToolWeight.LIGHTWEIGHT

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "source": ToolProperty(type="string", description="Path to the source file or directory"),
                "destination": ToolProperty(type="string", description="Destination path"),
            },
            required=["source", "destination"],
        )

    def execute(self, tool_input: dict) -> str:
        source = tool_input["source"]
        destination = tool_input["destination"]
        try:
            shutil.move(source, destination)
            return f"Moved {source} to {destination}"
        except Exception as e:
            return f"Error: {e}"
