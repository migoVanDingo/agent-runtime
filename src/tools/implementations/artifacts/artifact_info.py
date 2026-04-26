"""artifact_info - inspect metadata for an artifact without loading full value."""

from __future__ import annotations

import datetime as _dt

from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight


class ArtifactInfoTool(BaseTool):
    name = "artifact_info"
    description = "Show metadata for an artifact key without materializing full content."
    weight = ToolWeight.LIGHTWEIGHT

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
            return f"Artifact '{key}' was not found."

        created = _dt.datetime.fromtimestamp(meta.created_at).strftime("%Y-%m-%d %H:%M:%S")
        accessed = _dt.datetime.fromtimestamp(meta.last_accessed).strftime("%Y-%m-%d %H:%M:%S")

        lines = [
            f"key: {meta.key}",
            f"kind: {meta.kind}",
            f"source: {meta.source}",
            f"project: {store.get_tag(key, 'project') or ''}",
            f"session_id: {meta.session_id}",
            f"created_at: {created}",
            f"last_accessed: {accessed}",
            f"access_count: {meta.access_count}",
            f"decay_score: {meta.decay_score}",
            f"permanent: {meta.permanent}",
            f"has_value: {meta.has_value}",
            f"has_data_path: {meta.has_data_path}",
            f"data_path: {meta.data_path or ''}",
            f"summary: {meta.summary or ''}",
        ]
        return "\n".join(lines)

    def _store(self):
        try:
            from runtime.artifact_store import get_artifact_store

            return get_artifact_store()
        except Exception:
            return None
