import shutil
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from runtime.policy import check_path_allowed


class CopyFileTool(BaseTool):
    name = "copy_file"
    description = "Copy a file from source to destination."
    weight = ToolWeight.LIGHTWEIGHT

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
        read_decision = check_path_allowed(source, "read")
        if not read_decision.allowed:
            return read_decision.error_message()
        write_decision = check_path_allowed(destination, "write")
        if not write_decision.allowed:
            return write_decision.error_message()
        try:
            shutil.copy2(source, destination)
            return f"Copied {source} to {destination}"
        except Exception as e:
            return f"Error: {e}"
