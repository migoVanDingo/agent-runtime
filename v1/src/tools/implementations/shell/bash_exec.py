from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from runtime.sandbox import SandboxManager


class BashExecTool(BaseTool):
    name = "bash_exec"
    description = "Execute a bash command and return stdout + stderr"
    weight = ToolWeight.MODERATE

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
        result = SandboxManager().run_shell(command)
        return result.to_tool_output()
