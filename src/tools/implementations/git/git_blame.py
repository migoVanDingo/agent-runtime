"""git_blame — annotate file lines with commit and author info."""
import subprocess
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from logger import get_logger

logger = get_logger(__name__)

_MAX_BLAME_LINES = 500


def _run_git(args: list[str], cwd: str | None) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=20,
        )
        return result.returncode, result.stdout, result.stderr
    except FileNotFoundError:
        return -1, "", "git is not installed"
    except subprocess.TimeoutExpired:
        return -1, "", "git blame timed out"


class GitBlameTool(BaseTool):
    name = "git_blame"
    description = (
        "Annotate file lines with the commit hash, author, and date of last change. "
        "Optionally restrict to a line range."
    )
    weight = ToolWeight.LIGHTWEIGHT

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "file": ToolProperty(
                    type="string",
                    description="Path to the file to blame (relative to repo root)",
                ),
                "repo_path": ToolProperty(
                    type="string",
                    description="Path to the git repository (default: cwd)",
                ),
                "start_line": ToolProperty(
                    type="number",
                    description="First line to show (1-indexed)",
                ),
                "end_line": ToolProperty(
                    type="number",
                    description="Last line to show (1-indexed, inclusive)",
                ),
                "ref": ToolProperty(
                    type="string",
                    description="Blame at a specific commit or branch (default: HEAD)",
                ),
            },
            required=["file"],
        )

    def execute(self, tool_input: dict) -> str:
        file_path = tool_input["file"]
        cwd = tool_input.get("repo_path") or None
        start = tool_input.get("start_line")
        end = tool_input.get("end_line")
        ref = tool_input.get("ref", "")

        rc, _, _ = _run_git(["rev-parse", "--git-dir"], cwd)
        if rc != 0:
            return f"Error: not a git repository: {cwd or '(current directory)'}"

        args = ["blame", "--abbrev=8", "-w"]
        if start and end:
            args += [f"-L{int(start)},{int(end)}"]
        elif start:
            args += [f"-L{int(start)},+{_MAX_BLAME_LINES}"]
        if ref:
            args.append(ref)
        args += ["--", file_path]

        rc, out, stderr = _run_git(args, cwd)
        if rc != 0:
            return f"Error: git blame failed: {stderr.strip()}"

        lines = out.splitlines()
        if len(lines) > _MAX_BLAME_LINES:
            lines = lines[:_MAX_BLAME_LINES]
            lines.append(f"[truncated at {_MAX_BLAME_LINES} lines]")

        header = f"git blame {file_path}"
        if ref:
            header += f" @ {ref}"
        if start or end:
            header += f" (lines {start or 1}–{end or '?'})"

        return header + "\n\n" + "\n".join(lines)
