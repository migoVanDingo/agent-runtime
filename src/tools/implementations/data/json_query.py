"""json_query - JSONPath extraction from file, artifact, or inline JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight


class JsonQueryTool(BaseTool):
    name = "json_query"
    description = "Extract values from JSON using a JSONPath expression."
    weight = ToolWeight.MODERATE

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "source": ToolProperty(
                    type="string",
                    description="JSON file path, artifact key, or inline JSON string",
                ),
                "path": ToolProperty(
                    type="string",
                    description="JSONPath expression, e.g. $.store.books[*].title",
                ),
            },
            required=["source", "path"],
        )

    def execute(self, tool_input: dict) -> str:
        try:
            from jsonpath_ng import parse as parse_jsonpath
        except Exception:
            return "Error: jsonpath-ng is not installed."

        source = str(tool_input["source"])
        json_path = str(tool_input["path"])

        try:
            data = self._resolve_json(source)
        except ValueError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error: failed to load JSON source: {e}"

        try:
            expr = parse_jsonpath(json_path)
        except Exception as e:
            return f"Error: invalid JSONPath expression: {e}"

        try:
            matches = expr.find(data)
        except Exception as e:
            return f"Error: JSONPath evaluation failed: {e}"

        if not matches:
            return f"No matches for JSONPath '{json_path}'."

        max_items = 200
        lines = []
        for idx, m in enumerate(matches[:max_items], start=1):
            rendered = _render_json_value(m.value)
            lines.append(f"[{idx}] {rendered}")

        header = f"Matched {len(matches)} value(s) for '{json_path}'"
        if len(matches) > max_items:
            header += f" (showing first {max_items})"
        return header + "\n" + "\n".join(lines)

    def _resolve_json(self, source: str) -> Any:
        p = Path(source)
        if p.exists() and p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))

        store = self._store()
        if store is not None:
            m = store.meta(source)
            if m is not None:
                value = store.get(source)
                return _coerce_to_json(value)

        # Inline JSON fallback.
        return json.loads(source)

    def _store(self):
        try:
            from runtime.artifact_store import get_artifact_store

            return get_artifact_store()
        except Exception:
            return None


def _coerce_to_json(value: Any) -> Any:
    try:
        import pandas as pd

        if isinstance(value, pd.DataFrame):
            return value.to_dict(orient="records")
        if isinstance(value, pd.Series):
            return value.to_dict()
    except Exception:
        pass

    if isinstance(value, (dict, list, int, float, bool)) or value is None:
        return value

    if isinstance(value, str):
        return json.loads(value)

    raise ValueError("artifact value is not JSON-decodable")


def _render_json_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)
