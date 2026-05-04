import os
import shutil
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from runtime.policy import check_path_allowed


class DeleteDirectoryTool(BaseTool):
    name = "delete_directory"
    description = "Recursively delete a directory and all its contents. Irreversible — use with caution."
    weight = ToolWeight.MODERATE

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "path": ToolProperty(type="string", description="Path to the directory to delete"),
            },
            required=["path"],
        )

    def execute(self, tool_input: dict) -> str:
        path = tool_input["path"].rstrip("/")
        decision = check_path_allowed(path, "delete")
        if not decision.allowed:
            return decision.error_message()

        # Basic safety: refuse to delete root or single-component paths
        parts = [p for p in path.split("/") if p]
        if len(parts) <= 1:
            return f"Error: refusing to delete top-level path '{path}'"

        if not os.path.exists(path):
            return f"Directory not found: {path}"
        if not os.path.isdir(path):
            return f"Error: '{path}' is not a directory (use delete_file for files)"

        try:
            shutil.rmtree(path)
            return f"Deleted directory: {path}"
        except Exception as e:
            return f"Error: {e}"
