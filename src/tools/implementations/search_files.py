import subprocess
from tools.base import BaseTool, InputSchema, ToolProperty
from app_config import config


class SearchFilesTool(BaseTool):
    name = "search_files"
    description = "Search for a pattern across files in a directory. Returns matching lines with file names and line numbers."

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "pattern": ToolProperty(type="string", description="The search pattern (regex supported)"),
                "path": ToolProperty(type="string", description="Directory to search in"),
                "file_glob": ToolProperty(type="string", description="File pattern to restrict search (e.g. '*.py', '*.c'). Defaults to all files."),
                "case_sensitive": ToolProperty(type="string", description="'true' or 'false'. Defaults to 'true'."),
            },
            required=["pattern", "path"],
        )

    def execute(self, tool_input: dict) -> str:
        pattern = tool_input["pattern"]
        path = tool_input["path"]
        file_glob = tool_input.get("file_glob", "")
        case_sensitive = tool_input.get("case_sensitive", "true").lower() == "true"

        cmd = ["grep", "-rn"]
        if not case_sensitive:
            cmd.append("-i")
        if file_glob:
            cmd.extend(["--include", file_glob])
        cmd.extend([pattern, path])

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=config.timeouts.default)
            output = result.stdout
            if result.stderr:
                output += f"\nSTDERR: {result.stderr}"
            return output if output else "(no matches found)"
        except Exception as e:
            return f"Error: {e}"
