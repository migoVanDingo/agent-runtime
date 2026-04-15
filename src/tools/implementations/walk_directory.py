import os
from tools.base import BaseTool, InputSchema, ToolProperty


class WalkDirectoryTool(BaseTool):
    name = "walk_directory"
    description = "Recursively walk a directory tree and return all file paths."

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
        try:
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
