import re

from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight

# File extensions that should contain raw code — strip markdown code fences if present
_CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".sh", ".bash", ".rb", ".pl", ".go",
    ".rs", ".c", ".cpp", ".h", ".java", ".cs", ".swift", ".kt",
}

# Matches an entire response that is a single fenced code block (with optional language tag)
_SINGLE_FENCE_RE = re.compile(r"^\s*```[\w]*\n(.*?)\n?```\s*$", re.DOTALL)


def _strip_code_fences(path: str, content: str) -> tuple[str, bool]:
    """If the file is a code file and the content is wrapped in a single markdown
    code fence, strip the fence and return the inner code.

    Returns (cleaned_content, was_stripped).
    """
    ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if ext not in _CODE_EXTENSIONS:
        return content, False
    m = _SINGLE_FENCE_RE.match(content)
    if m:
        return m.group(1), True
    return content, False


class WriteFileTool(BaseTool):
    name = "write_file"
    description = "Write content to a file. Creates the file if it does not exist, overwrites if it does."
    weight = ToolWeight.LIGHTWEIGHT

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
        from logger import get_logger
        logger = get_logger(__name__)

        path = tool_input["path"]
        content = tool_input["content"]

        cleaned, stripped = _strip_code_fences(path, content)
        if stripped:
            logger.info(f"  write_file: stripped markdown code fence from {path}")
            content = cleaned

        try:
            with open(path, "w") as f:
                f.write(content)
            return f"Successfully wrote {len(content)} bytes to {path}"
        except Exception as e:
            return f"Error: {e}"
