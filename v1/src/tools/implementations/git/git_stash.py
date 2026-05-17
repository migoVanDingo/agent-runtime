"""git_stash — inspect stash entries."""
import subprocess
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from logger import get_logger

logger = get_logger(__name__)

_MAX_SHOW_BYTES = 30_000


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
        return -1, "", "git stash timed out"


class GitStashTool(BaseTool):
    name = "git_stash"
    description = (
        "Inspect git stash entries. "
        "Use action='list' (default) to see all stashes, "
        "action='show' to see the diff of a specific stash entry."
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
                "action": ToolProperty(
                    type="string",
                    description="'list' (default) or 'show'",
                ),
                "index": ToolProperty(
                    type="number",
                    description="Stash index for 'show' (0 = most recent, default 0)",
                ),
            },
            required=[],
        )

    def execute(self, tool_input: dict) -> str:
        cwd = tool_input.get("repo_path") or None
        action = tool_input.get("action", "list").lower()
        index = int(tool_input.get("index", 0))

        rc, _, _ = _run_git(["rev-parse", "--git-dir"], cwd)
        if rc != 0:
            return f"Error: not a git repository: {cwd or '(current directory)'}"

        if action == "list":
            rc, out, stderr = _run_git(["stash", "list"], cwd)
            if rc != 0:
                return f"Error: git stash list failed: {stderr.strip()}"
            return out.strip() or "No stash entries."

        elif action == "show":
            stash_ref = f"stash@{{{index}}}"
            rc, out, stderr = _run_git(["stash", "show", "-p", stash_ref], cwd)
            if rc != 0:
                return f"Error: git stash show failed: {stderr.strip()}"
            if not out.strip():
                return f"Empty stash at index {index}."
            if len(out) > _MAX_SHOW_BYTES:
                out = out[:_MAX_SHOW_BYTES] + f"\n[truncated at {_MAX_SHOW_BYTES:,} bytes]"
            return f"{stash_ref}\n\n{out.strip()}"

        else:
            return f"Error: unknown action '{action}'. Use 'list' or 'show'."
