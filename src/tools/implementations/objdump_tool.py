import subprocess
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from app_config import config


class ObjdumpTool(BaseTool):
    name = "objdump"
    description = "Disassemble and analyze object files or binaries. Useful for reverse engineering."
    weight = ToolWeight.HEAVY

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "path": ToolProperty(type="string", description="Path to the binary or object file"),
                "flags": ToolProperty(type="string", description="objdump flags (e.g. '-d' for disassemble, '-x' for headers, '-d -M intel' for Intel syntax). Defaults to '-d'."),
            },
            required=["path"],
        )

    def execute(self, tool_input: dict) -> str:
        path = tool_input["path"]
        flags = tool_input.get("flags", "-d")
        try:
            result = subprocess.run(
                f"objdump {flags} {path}",
                shell=True, capture_output=True, text=True, timeout=config.timeouts.analysis
            )
            output = result.stdout
            if result.stderr:
                output += f"\nSTDERR: {result.stderr}"
            return output if output else "(no output)"
        except Exception as e:
            return f"Error: {e}"
