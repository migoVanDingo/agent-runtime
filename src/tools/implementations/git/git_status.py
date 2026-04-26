"""git_status — working tree and staging area status."""
import subprocess
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from logger import get_logger

logger = get_logger(__name__)


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


def _check_repo(cwd: str | None) -> str | None:
    """Returns an error string if cwd is not a git repo, else None."""
    rc, _, _ = _run_git(["rev-parse", "--git-dir"], cwd)
    if rc != 0:
        return f"Error: not a git repository: {cwd or '(current directory)'}"
    return None


class GitStatusTool(BaseTool):
    name = "git_status"
    description = (
        "Show the working tree status of a git repository — "
        "current branch, staged changes, unstaged changes, and untracked files."
    )
    weight = ToolWeight.LIGHTWEIGHT

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "repo_path": ToolProperty(
                    type="string",
                    description="Path to the git repository (default: current working directory)",
                ),
            },
            required=[],
        )

    def execute(self, tool_input: dict) -> str:
        cwd = tool_input.get("repo_path") or None

        err = _check_repo(cwd)
        if err:
            return err

        rc, out, stderr = _run_git(["status"], cwd)
        if rc != 0:
            return f"Error: git status failed: {stderr.strip()}"

        return out.strip() or "(nothing to report — working tree clean)"
