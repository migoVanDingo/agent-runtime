from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from runtime.policy import check_path_allowed


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
        path = tool_input["path"]
        decision = check_path_allowed(path, "read")
        if not decision.allowed:
            return decision.error_message()
        try:
            with open(path, "r") as f:
                return f.read()
        except FileNotFoundError:
            return "File not found"
        except Exception as e:
            return f"Error: {e}"
