from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight


class ReadFileLinesTool(BaseTool):
    name = "read_file_lines"
    description = "Read a specific range of lines from a file. Useful for inspecting large files without loading the entire contents."
    weight = ToolWeight.MODERATE

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "path": ToolProperty(type="string", description="Path to the file"),
                "start": ToolProperty(type="string", description="Starting line number (1-indexed)"),
                "end": ToolProperty(type="string", description="Ending line number (inclusive). Omit to read to end of file."),
            },
            required=["path", "start"],
        )

    def execute(self, tool_input: dict) -> str:
        path = tool_input["path"]
        try:
            start = int(tool_input["start"])
            end = int(tool_input["end"]) if "end" in tool_input else None

            with open(path, "r") as f:
                lines = f.readlines()

            total = len(lines)
            start_idx = max(0, start - 1)
            end_idx = min(total, end) if end is not None else total

            selected = lines[start_idx:end_idx]
            numbered = [f"{start_idx + i + 1}: {line}" for i, line in enumerate(selected)]
            return "".join(numbered) if numbered else "(no lines in range)"
        except FileNotFoundError:
            return f"File not found: {path}"
        except ValueError as e:
            return f"Invalid line number: {e}"
        except Exception as e:
            return f"Error: {e}"
