"""diff_files - unified diff between two files or artifacts."""

from __future__ import annotations

import difflib
import json
from pathlib import Path

from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight


class DiffFilesTool(BaseTool):
    name = "diff_files"
    description = "Generate a unified diff between two files or artifact values."
    weight = ToolWeight.MODERATE

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "a": ToolProperty(type="string", description="Left file path or artifact key"),
                "b": ToolProperty(type="string", description="Right file path or artifact key"),
                "context_lines": ToolProperty(
                    type="number",
                    description="Number of context lines (default 3)",
                ),
                "output": ToolProperty(
                    type="string",
                    description="Optional file path to write the unified diff",
                ),
            },
            required=["a", "b"],
        )

    def execute(self, tool_input: dict) -> str:
        a = str(tool_input["a"])
        b = str(tool_input["b"])
        context_lines_raw = tool_input.get("context_lines", 3)
        output = tool_input.get("output")

        try:
            context_lines = int(context_lines_raw)
        except Exception:
            return "Error: context_lines must be an integer."

        text_a = self._resolve_text(a)
        text_b = self._resolve_text(b)
        if text_a is None:
            return f"Error: could not resolve '{a}' as file path or artifact key."
        if text_b is None:
            return f"Error: could not resolve '{b}' as file path or artifact key."

        diff_lines = list(
            difflib.unified_diff(
                text_a.splitlines(keepends=True),
                text_b.splitlines(keepends=True),
                fromfile=a,
                tofile=b,
                n=max(0, context_lines),
            )
        )

        if not diff_lines:
            return f"No differences found between '{a}' and '{b}'."

        diff_text = "".join(diff_lines)

        if output:
            out_path = Path(str(output))
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(diff_text, encoding="utf-8")
            return (
                f"Wrote unified diff to {out_path} ({len(diff_text)} chars).\n\n"
                f"{diff_text[:4000]}"
            )

        return diff_text

    def _resolve_text(self, source: str) -> str | None:
        p = Path(source)
        if p.exists() and p.is_file():
            return p.read_text(encoding="utf-8", errors="replace")

        store = self._store()
        if store is None:
            return None

        m = store.meta(source)
        if m is None:
            return None

        value = store.get(source)
        if value is None:
            return None

        return _to_text(value)

    def _store(self):
        try:
            from runtime.artifact_store import get_artifact_store

            return get_artifact_store()
        except Exception:
            return None


def _to_text(value) -> str:
    try:
        import pandas as pd

        if isinstance(value, pd.DataFrame):
            return value.to_csv(index=False)
        if isinstance(value, pd.Series):
            return value.to_csv(index=True)
    except Exception:
        pass

    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, indent=2, ensure_ascii=False)
    return str(value)
