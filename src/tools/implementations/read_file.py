from tools.base import BaseTool, InputSchema, ToolProperty


class ReadFileTool(BaseTool):
    name = "read_file"
    description = "Read the contents of a file"

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
        try:
            with open(path, "r") as f:
                return f.read()
        except FileNotFoundError:
            return "File not found"
        except Exception as e:
            return f"Error: {e}"
