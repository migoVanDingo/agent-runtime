import os
import re

from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight

# File extensions that should contain raw code — strip markdown code fences if present
_CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".sh", ".bash", ".rb", ".pl", ".go",
    ".rs", ".c", ".cpp", ".h", ".java", ".cs", ".swift", ".kt",
}

# Matches an entire response that is a single fenced code block (with optional language tag)
_SINGLE_FENCE_RE = re.compile(r"^\s*```[\w]*\n(.*?)\n?```\s*$", re.DOTALL)

# Markdown signals that indicate prose content rather than code.
# Checked against the first 20 lines of content for code-extension files.
_MARKDOWN_RE = re.compile(
    r"(?m)"
    r"^#{1,6}\s+\S|"       # ATX heading: # Title, ## Section, etc.
    r"^\*\*\w|"            # bold text starting a line
    r"^>\s|"               # blockquote
    r"^\|\s*\w.*\|\s*$",   # table row
)


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


def _is_markdown_prose(path: str, content: str) -> bool:
    """Return True if a code-extension file appears to contain markdown prose.

    Checks the first 20 lines for markdown structural markers. A single
    heading or table row is enough to flag the content as wrong.
    """
    ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if ext not in _CODE_EXTENSIONS:
        return False
    head = "\n".join(content.splitlines()[:20])
    return bool(_MARKDOWN_RE.search(head))


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

        if _is_markdown_prose(path, content):
            ext = "." + path.rsplit(".", 1)[-1].lower()
            msg = (
                f"Error: content written to '{path}' appears to be markdown prose, not valid {ext} code. "
                f"Rewrite the content as proper {ext} source code without markdown headings, "
                f"bullet points, or prose paragraphs."
            )
            logger.info(f"  write_file: rejected markdown prose in code file {path}")
            return msg

        try:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(path, "w") as f:
                f.write(content)
            return f"Successfully wrote {len(content)} bytes to {path}"
        except Exception as e:
            return f"Error: {e}"
