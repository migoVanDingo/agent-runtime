import subprocess
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from app_config import config


class ReadElfTool(BaseTool):
    name = "readelf"
    description = "Display information about ELF binaries: headers, sections, symbols, dynamic dependencies. Requires binutils (Linux native, macOS via brew install binutils)."
    weight = ToolWeight.HEAVY

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "path": ToolProperty(type="string", description="Path to the ELF binary"),
                "flags": ToolProperty(type="string", description="readelf flags (e.g. '-h' headers, '-S' sections, '-d' dynamic, '-s' symbols, '-a' all). Defaults to '-h'."),
            },
            required=["path"],
        )

    def execute(self, tool_input: dict) -> str:
        path = tool_input["path"]
        flags = tool_input.get("flags", "-h")
        try:
            result = subprocess.run(
                f"readelf {flags} {path}",
                shell=True, capture_output=True, text=True, timeout=config.timeouts.default
            )
            output = result.stdout
            if result.stderr:
                output += f"\nSTDERR: {result.stderr}"
            return output if output else "(no output)"
        except FileNotFoundError:
            return "Error: 'readelf' not found. Install binutils (brew install binutils on macOS)."
        except Exception as e:
            return f"Error: {e}"
