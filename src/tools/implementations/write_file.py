from tools.base import BaseTool, InputSchema, ToolProperty


class WriteFileTool(BaseTool):
    name = "write_file"
    description = "Write content to a file. Creates the file if it does not exist, overwrites if it does."

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "path": ToolProperty(type="string", description="Path to the file to write"),
                "content": ToolProperty(type="string", description="Content to write to the file"),
            },
            required=["path", "content"],
        )

    def execute(self, tool_input: dict) -> str:
        path = tool_input["path"]
        content = tool_input["content"]
        try:
            with open(path, "w") as f:
                f.write(content)
            return f"Successfully wrote {len(content)} bytes to {path}"
        except Exception as e:
            return f"Error: {e}"
