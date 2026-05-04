import os
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from runtime.policy import check_path_allowed


class DeleteFileTool(BaseTool):
    name = "delete_file"
    description = "Delete a file. This is irreversible — use with caution."
    weight = ToolWeight.LIGHTWEIGHT

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "path": ToolProperty(type="string", description="Path to the file to delete"),
            },
            required=["path"],
        )

    def execute(self, tool_input: dict) -> str:
        path = tool_input["path"]
        decision = check_path_allowed(path, "delete")
        if not decision.allowed:
            return decision.error_message()
        try:
            os.remove(path)
            return f"Deleted {path}"
        except FileNotFoundError:
            return f"File not found: {path}"
        except Exception as e:
            return f"Error: {e}"
