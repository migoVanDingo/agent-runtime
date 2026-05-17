"""git_diff — show changes between working tree, index, and commits."""
import subprocess
from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from logger import get_logger

logger = get_logger(__name__)

_MAX_DIFF_BYTES = 50_000


def _run_git(args: list[str], cwd: str | None) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode, result.stdout, result.stderr
    except FileNotFoundError:
        return -1, "", "git is not installed"
    except subprocess.TimeoutExpired:
        return -1, "", "git diff timed out (large repo?)"


class GitDiffTool(BaseTool):
    name = "git_diff"
    description = (
        "Show changes between working tree, index, or commits. "
        "Use ref for commit ranges (e.g. 'HEAD~3..HEAD' or 'main..feature-branch'). "
        "Use staged=true for changes already added to the index."
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
                    description="Commit, branch, or range to diff (e.g. 'HEAD', 'main..feature', 'abc123')",
                ),
                "staged": ToolProperty(
                    type="boolean",
                    description="Show staged changes (git diff --staged). Default false.",
                ),
                "file": ToolProperty(
                    type="string",
                    description="Restrict diff to a specific file or path",
                ),
                "stat": ToolProperty(
                    type="boolean",
                    description="Show summary stats only (--stat), no full diff. Default false.",
                ),
            },
            required=[],
        )

    def execute(self, tool_input: dict) -> str:
        cwd = tool_input.get("repo_path") or None
        ref = tool_input.get("ref", "")
        staged = tool_input.get("staged", False)
        file_path = tool_input.get("file", "")
        stat_only = tool_input.get("stat", False)

        # Check repo
        rc, _, _ = _run_git(["rev-parse", "--git-dir"], cwd)
        if rc != 0:
            return f"Error: not a git repository: {cwd or '(current directory)'}"

        args = ["diff"]
        if staged:
            args.append("--staged")
        if stat_only:
            args.append("--stat")
        if ref:
            args.append(ref)
        if file_path:
            args += ["--", file_path]

        rc, out, stderr = _run_git(args, cwd)
        if rc != 0:
            return f"Error: git diff failed: {stderr.strip()}"

        if not out.strip():
            return "No differences found."

        if len(out) > _MAX_DIFF_BYTES:
            out = out[:_MAX_DIFF_BYTES]
            out += f"\n[truncated — diff exceeded {_MAX_DIFF_BYTES:,} bytes. Use stat=true for a summary or narrow with file=<path>]"

        label_parts = ["git diff"]
        if staged:
            label_parts.append("--staged")
        if ref:
            label_parts.append(ref)
        if file_path:
            label_parts.append(f"-- {file_path}")

        return f"{'  '.join(label_parts)}\n\n{out.strip()}"
