"""store_artifact - store a value in the artifact store."""

from __future__ import annotations

from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight


class StoreArtifactTool(BaseTool):
    name = "store_artifact"
    description = "Store a named artifact value for reuse in later steps."
    weight = ToolWeight.MODERATE

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "key": ToolProperty(type="string", description="Artifact key"),
                "value": ToolProperty(type="string", description="Value to store"),
                "kind": ToolProperty(
                    type="string",
                    description="Artifact kind (default: string)",
                ),
                "source": ToolProperty(
                    type="string",
                    description="Optional source label",
                ),
                "project": ToolProperty(
                    type="string",
                    description="Optional project tag override",
                ),
            },
            required=["key", "value"],
        )

    def execute(self, tool_input: dict) -> str:
        key = str(tool_input["key"])
        value = tool_input["value"]
        kind = str(tool_input.get("kind", "string") or "string")
        source = str(tool_input.get("source", "") or "")
        project = tool_input.get("project")
        project = str(project).strip() if project is not None else None

        store = self._store()
        if store is None:
            return "Error: artifact store is not initialized."

        try:
            meta = store.set(key, value, kind=kind, source=source)
            if project:
                store.set_tag(key, "project", project)
        except Exception as e:
            return f"Error: failed to store artifact '{key}': {e}"

        return (
            f"Stored artifact '{key}' (kind={meta.kind}).\n"
            f"Summary: {meta.summary or '(none)'}"
        )

    def _store(self):
        try:
            from runtime.artifact_store import get_artifact_store

            return get_artifact_store()
        except Exception:
            return None
