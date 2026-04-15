import subprocess
from tools.base import BaseTool, InputSchema, ToolProperty
from app_config import config


class ChecksecTool(BaseTool):
    name = "checksec"
    description = "Check binary security properties: NX, ASLR, stack canaries, PIE, RELRO. Requires checksec (brew install checksec on macOS)."

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "path": ToolProperty(type="string", description="Path to the binary to check"),
            },
            required=["path"],
        )

    def execute(self, tool_input: dict) -> str:
        path = tool_input["path"]
        try:
            result = subprocess.run(
                ["checksec", "--file", path],
                capture_output=True, text=True, timeout=config.timeouts.fast
            )
            output = result.stdout
            if result.stderr:
                output += f"\nSTDERR: {result.stderr}"
            return output if output else "(no output)"
        except FileNotFoundError:
            return "Error: 'checksec' not found. Install with: brew install checksec"
        except Exception as e:
            return f"Error: {e}"
