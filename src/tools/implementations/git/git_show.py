"""git_show — show a specific commit, tag, or tree object."""
import subprocess
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from logger import get_logger

logger = get_logger(__name__)

_MAX_SHOW_BYTES = 50_000


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
        return -1, "", "git show timed out"


class GitShowTool(BaseTool):
    name = "git_show"
    description = (
        "Show a specific git commit — metadata, changed files, and diff. "
        "Defaults to HEAD. Use stat=true for a summary without the full diff."
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
                "ref": ToolProperty(
                    type="string",
                    description="Commit hash, tag, or branch (default: HEAD)",
                ),
                "stat": ToolProperty(
                    type="boolean",
                    description="Show stat summary only, no full diff (default false)",
                ),
                "file": ToolProperty(
                    type="string",
                    description="Restrict output to changes in this file",
                ),
            },
            required=[],
        )

    def execute(self, tool_input: dict) -> str:
        cwd = tool_input.get("repo_path") or None
        ref = tool_input.get("ref", "HEAD")
        stat_only = tool_input.get("stat", False)
        file_path = tool_input.get("file", "")

        rc, _, _ = _run_git(["rev-parse", "--git-dir"], cwd)
        if rc != 0:
            return f"Error: not a git repository: {cwd or '(current directory)'}"

        args = ["show"]
        if stat_only:
            args += ["--stat", "--no-patch"]
        args.append(ref)
        if file_path:
            args += ["--", file_path]

        rc, out, stderr = _run_git(args, cwd)
        if rc != 0:
            return f"Error: git show failed: {stderr.strip()}"

        if not out.strip():
            return f"No output for ref: {ref}"

        if len(out) > _MAX_SHOW_BYTES:
            out = out[:_MAX_SHOW_BYTES]
            out += f"\n[truncated — output exceeded {_MAX_SHOW_BYTES:,} bytes]"

        return out.strip()
