"""git_branch — list local and remote branches."""
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
            timeout=10,
        )
        return result.returncode, result.stdout, result.stderr
    except FileNotFoundError:
        return -1, "", "git is not installed"
    except subprocess.TimeoutExpired:
        return -1, "", "git branch timed out"


class GitBranchTool(BaseTool):
    name = "git_branch"
    description = (
        "List git branches. Shows local branches by default; "
        "set all=true to include remote-tracking branches."
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
                "all": ToolProperty(
                    type="boolean",
                    description="Include remote-tracking branches (default false)",
                ),
                "verbose": ToolProperty(
                    type="boolean",
                    description="Show last commit hash and subject per branch (default false)",
                ),
            },
            required=[],
        )

    def execute(self, tool_input: dict) -> str:
        cwd = tool_input.get("repo_path") or None
        show_all = tool_input.get("all", False)
        verbose = tool_input.get("verbose", False)

        rc, _, _ = _run_git(["rev-parse", "--git-dir"], cwd)
        if rc != 0:
            return f"Error: not a git repository: {cwd or '(current directory)'}"

        args = ["branch"]
        if show_all:
            args.append("-a")
        if verbose:
            args.append("-vv")

        rc, out, stderr = _run_git(args, cwd)
        if rc != 0:
            return f"Error: git branch failed: {stderr.strip()}"

        if not out.strip():
            return "No branches found (empty repository?)."

        label = "git branch" + (" -a" if show_all else "") + (" -vv" if verbose else "")
        return f"{label}\n\n{out.rstrip()}"
