import subprocess
from tools.base import BaseTool, InputSchema, ToolProperty
from app_config import config


class BashExecTool(BaseTool):
    name = "bash_exec"
    description = "Execute a bash command and return stdout + stderr"

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "command": ToolProperty(
                    type="string", description="The bash command to run"
                )
            },
            required=["command"],
        )

    def execute(self, tool_input: dict) -> str:
        command = tool_input["command"]
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=config.timeouts.default
        )
        output = result.stdout
        if result.stderr:
            output += f"\nSTDERR: {result.stderr}"
        return output if output else "(no output)"
