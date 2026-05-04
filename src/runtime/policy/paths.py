"""Central path policy for file-oriented tools."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


PathOperation = Literal["read", "write", "delete"]


@dataclass(frozen=True)
class PathPolicyDecision:
    allowed: bool
    path: str
    operation: str
    reason: str = ""

    def error_message(self) -> str:
        return f"Error: path policy denied {self.operation} for '{self.path}': {self.reason}"


class PathPolicy:
    def __init__(
        self,
        *,
        workspace_root: str = ".",
        allowed_read_roots: list[str] | None = None,
        allowed_write_roots: list[str] | None = None,
    ) -> None:
        self._workspace_root = self._resolve(workspace_root)
        self._allowed_read_roots = [self._resolve(p) for p in (allowed_read_roots or [])]
        self._allowed_write_roots = [self._resolve(p) for p in (allowed_write_roots or [])]

    @classmethod
    def from_config(cls) -> "PathPolicy":
        from app_config import config

        cfg = config.runtime.sandbox
        return cls(
            workspace_root=cfg.workspace_root,
            allowed_read_roots=cfg.allowed_read_roots,
            allowed_write_roots=cfg.allowed_write_roots,
        )

    def check(self, path: str, operation: PathOperation) -> PathPolicyDecision:
        resolved = self._resolve(path)
        roots = [self._workspace_root]
        if operation == "read":
            roots.extend(self._allowed_read_roots)
        else:
            roots.extend(self._allowed_write_roots)

        if any(_is_relative_to(resolved, root) for root in roots):
            return PathPolicyDecision(True, str(resolved), operation)

        return PathPolicyDecision(
            False,
            str(resolved),
            operation,
            reason=f"path is outside allowed roots: {[str(r) for r in roots]}",
        )

    def _resolve(self, path: str) -> Path:
        return Path(path).expanduser().resolve(strict=False)


def check_path_allowed(path: str, operation: PathOperation) -> PathPolicyDecision:
    return PathPolicy.from_config().check(path, operation)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
