"""get_artifact - fetch and render an artifact value by key."""

from __future__ import annotations

import json

from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight


class GetArtifactTool(BaseTool):
    name = "get_artifact"
    description = "Get a stored artifact value by key."
    weight = ToolWeight.MODERATE

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "key": ToolProperty(type="string", description="Artifact key"),
            },
            required=["key"],
        )

    def execute(self, tool_input: dict) -> str:
        key = str(tool_input["key"])
        store = self._store()
        if store is None:
            return "Error: artifact store is not initialized."

        meta = store.meta(key)
        if meta is None:
            return f"Error: artifact '{key}' was not found."

        value = store.get(key)
        if value is None:
            return f"Error: artifact '{key}' exists but its value could not be loaded."

        rendered = _render_value(value)
        return f"Artifact '{key}' ({meta.kind}):\n{rendered}"

    def _store(self):
        try:
            from runtime.artifact_store import get_artifact_store

            return get_artifact_store()
        except Exception:
            return None


def _render_value(value) -> str:
    try:
        import pandas as pd

        if isinstance(value, pd.DataFrame):
            max_rows = 200
            clipped = value.head(max_rows)
            body = clipped.to_csv(index=False)
            if len(value) > max_rows:
                body += f"\n[truncated: showing first {max_rows} of {len(value)} rows]"
            return body
        if isinstance(value, pd.Series):
            return value.to_csv(index=True)
    except Exception:
        pass

    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, indent=2, ensure_ascii=False)
    return str(value)
