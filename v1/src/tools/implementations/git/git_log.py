"""git_log — commit history."""
import subprocess
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from logger import get_logger

logger = get_logger(__name__)

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 200


def _run_git(args: list[str], cwd: str | None) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.returncode, result.stdout, result.stderr
    except FileNotFoundError:
        return -1, "", "git is not installed"
    except subprocess.TimeoutExpired:
        return -1, "", "git command timed out"


class GitLogTool(BaseTool):
    name = "git_log"
    description = (
        "Show the commit history of a git repository. "
        "Can be filtered by branch, file, or number of commits."
    )
    weight = ToolWeight.LIGHTWEIGHT

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "repo_path": ToolProperty(
                    type="string",
                    description="Path to the git repository (default: cwd)",
                ),
                "limit": ToolProperty(
                    type="number",
                    description=f"Max commits to show (default {_DEFAULT_LIMIT}, max {_MAX_LIMIT})",
                ),
                "branch": ToolProperty(
                    type="string",
                    description="Branch or ref to log (default: current branch)",
                ),
                "file": ToolProperty(
                    type="string",
                    description="Restrict log to commits that touched this file/path",
                ),
                "oneline": ToolProperty(
                    type="boolean",
                    description="Compact one-line format (default false)",
                ),
            },
            required=[],
        )

    def execute(self, tool_input: dict) -> str:
        cwd = tool_input.get("repo_path") or None
        limit = min(int(tool_input.get("limit", _DEFAULT_LIMIT)), _MAX_LIMIT)
        branch = tool_input.get("branch", "")
        file_path = tool_input.get("file", "")
        oneline = tool_input.get("oneline", False)

        # Check repo
        rc, _, _ = _run_git(["rev-parse", "--git-dir"], cwd)
        if rc != 0:
            return f"Error: not a git repository: {cwd or '(current directory)'}"

        if oneline:
            fmt_args = ["--oneline"]
        else:
            fmt_args = ["--pretty=format:%C(auto)%h  %<(12,trunc)%an  %ar  %s"]

        args = ["log", f"-{limit}"] + fmt_args
        if branch:
            args.append(branch)
        if file_path:
            args += ["--", file_path]

        rc, out, stderr = _run_git(args, cwd)
        if rc != 0:
            return f"Error: git log failed: {stderr.strip()}"

        if not out.strip():
            return "No commits found."

        header = f"git log  (limit={limit}"
        if branch:
            header += f"  branch={branch}"
        if file_path:
            header += f"  file={file_path}"
        header += ")\n"
        return header + out.strip()
