"""`ls` tool — list directory contents.

The simplest possible useful tool. Implements the Tool protocol; reads its
config from `tools.config.ls.*`.
"""
from __future__ import annotations

import os
from pathlib import Path

from arc.tools.base import Tool, ToolError, ToolInputSchema


class LSTool:
    """List files and directories at a path."""

    name = "ls"
    description = (
        "List files and directories at the given path. "
        "Returns one entry per line. Optionally recursive up to a depth limit."
    )

    def __init__(self, *, max_depth: int, show_hidden: bool) -> None:
        # All tunables come from config — no hardcoded defaults
        self._max_depth = max_depth
        self._show_hidden = show_hidden

    @classmethod
    def from_config(cls, cfg: dict) -> "LSTool":
        """Build from `tools.config.ls`. Required keys: max_depth, show_hidden."""
        try:
            return cls(
                max_depth=int(cfg["max_depth"]),
                show_hidden=bool(cfg["show_hidden"]),
            )
        except KeyError as e:
            raise ValueError(f"tools.config.ls missing required key: {e.args[0]!r}")

    @property
    def input_schema(self) -> ToolInputSchema:
        return ToolInputSchema(
            properties={
                "path": {
                    "type": "string",
                    "description": "Path to list. Defaults to current working directory if omitted.",
                },
                "depth": {
                    "type": "integer",
                    "description": (
                        f"Recursion depth (1=flat, 2+=recursive). Capped at the "
                        f"configured max_depth={self._max_depth}. Omit for flat listing."
                    ),
                },
            },
            required=[],
        )

    def execute(self, input: dict) -> str:
        raw_path = input.get("path", ".")
        depth = int(input.get("depth", 1))
        depth = max(1, min(depth, self._max_depth))

        target = Path(os.path.expanduser(raw_path)).resolve()
        if not target.exists():
            raise ToolError(f"path does not exist: {target}")
        if not target.is_dir():
            raise ToolError(f"path is not a directory: {target}")

        lines = list(self._walk(target, target, depth))
        if not lines:
            return f"{target}: (empty)"
        return "\n".join(lines)

    def _walk(self, root: Path, base: Path, remaining_depth: int):
        """Yield indented relative-path lines, depth-first, alphabetical."""
        try:
            entries = sorted(root.iterdir(), key=lambda p: p.name)
        except PermissionError:
            yield f"{root.relative_to(base)}/  (permission denied)"
            return

        for entry in entries:
            if not self._show_hidden and entry.name.startswith("."):
                continue
            rel = entry.relative_to(base)
            suffix = "/" if entry.is_dir() else ""
            yield f"{rel}{suffix}"
            if entry.is_dir() and remaining_depth > 1:
                yield from self._walk(entry, base, remaining_depth - 1)
