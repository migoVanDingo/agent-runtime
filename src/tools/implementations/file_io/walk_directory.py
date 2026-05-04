import os
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from runtime.policy import check_path_allowed


class WalkDirectoryTool(BaseTool):
    name = "walk_directory"
    description = "Recursively walk a directory tree and return all file paths."
    weight = ToolWeight.MODERATE

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "path": ToolProperty(type="string", description="Root directory to walk"),
            },
            required=["path"],
        )

    def execute(self, tool_input: dict) -> str:
        path = tool_input["path"]
        decision = check_path_allowed(path, "read")
        if not decision.allowed:
            return decision.error_message()
        try:
            if os.path.isfile(path):
                return f"Error: '{path}' is a file, not a directory. Use file_info or read_file to inspect it."
            if not os.path.exists(path):
                return f"Error: '{path}' does not exist."
            lines = []
            for root, dirs, files in os.walk(path):
                # Skip hidden directories
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                level = root.replace(path, "").count(os.sep)
                indent = "  " * level
                lines.append(f"{indent}{os.path.basename(root)}/")
                for file in files:
                    lines.append(f"{indent}  {file}")
            return "\n".join(lines) if lines else "(empty directory)"
        except Exception as e:
            return f"Error: {e}"
