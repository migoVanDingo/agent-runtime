"""expel_artifact - remove an artifact and its backing data."""

from __future__ import annotations

from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight


class ExpelArtifactTool(BaseTool):
    name = "expel_artifact"
    description = "Delete a stored artifact and any backing file from the artifact store."
    weight = ToolWeight.MODERATE

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "key": ToolProperty(type="string", description="Artifact key to delete"),
            },
            required=["key"],
        )

    def execute(self, tool_input: dict) -> str:
        key = str(tool_input["key"])
        store = self._store()
        if store is None:
            return "Error: artifact store is not initialized."

        removed = store.expel(key)
        if not removed:
            return f"Artifact '{key}' was not found."
        return f"Expelled artifact '{key}'."

    def _store(self):
        try:
            from runtime.artifact_store import get_artifact_store

            return get_artifact_store()
        except Exception:
            return None
