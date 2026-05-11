from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from runtime.policy import check_path_allowed
from runtime.path_resolver import resolve_path


class ReadFileTool(BaseTool):
    name = "read_file"
    description = "Read the contents of a file"
    weight = ToolWeight.MODERATE

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "path": ToolProperty(
                    type="string", description="Path to the file to read"
                )
            },
            required=["path"],
        )

    def execute(self, tool_input: dict) -> str:
        # Agent passes logical paths like `_analysis/proc/foo.c`; the resolver
        # rewrites those to the real on-disk location under ARC_HOME.
        path = tool_input["path"]
        decision = check_path_allowed(path, "read")
        if not decision.allowed:
            return decision.error_message()
        real_path = resolve_path(path)
        try:
            with open(real_path, "r") as f:
                return f.read()
        except FileNotFoundError:
            return "File not found"
        except Exception as e:
            return f"Error: {e}"
