import os
import sys
import platform
from tools.base import BaseTool, InputSchema, ToolProperty


class EnvironmentInfoTool(BaseTool):
    name = "environment_info"
    description = "Return information about the current system environment: OS, Python version, shell, hostname, and user."

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(properties={})

    def execute(self, tool_input: dict) -> str:
        info = {
            "os": platform.system(),
            "os_version": platform.version(),
            "machine": platform.machine(),
            "hostname": platform.node(),
            "user": os.environ.get("USER", "unknown"),
            "shell": os.environ.get("SHELL", "unknown"),
            "python_version": sys.version,
            "cwd": os.getcwd(),
        }
        return "\n".join(f"{k}: {v}" for k, v in info.items())
