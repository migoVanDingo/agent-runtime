"""list_artifacts - list artifact metadata for the current session store."""

from __future__ import annotations

import datetime as _dt

from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight


class ListArtifactsTool(BaseTool):
    name = "list_artifacts"
    description = "List stored artifacts, optionally filtered by kind."
    weight = ToolWeight.MODERATE

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "kind": ToolProperty(
                    type="string",
                    description="Optional artifact kind filter",
                )
            },
            required=[],
        )

    def execute(self, tool_input: dict) -> str:
        store = self._store()
        if store is None:
            return "Error: artifact store is not initialized."

        kind = tool_input.get("kind")
        artifacts = store.list(str(kind) if kind else None)
        if not artifacts:
            if kind:
                return f"No artifacts found with kind '{kind}'."
            return "No artifacts found."

        lines = []
        lines.append(f"Artifacts ({len(artifacts)}):")
        lines.append("key | kind | source | last_accessed | summary")
        lines.append("-" * 90)
        for m in artifacts:
            ts = _dt.datetime.fromtimestamp(m.last_accessed).strftime("%Y-%m-%d %H:%M:%S")
            summary = (m.summary or "").replace("\n", " ").strip()
            if len(summary) > 120:
                summary = summary[:117] + "..."
            source = (m.source or "")[:60]
            lines.append(f"{m.key} | {m.kind} | {source} | {ts} | {summary}")
        return "\n".join(lines)

    def _store(self):
        try:
            from runtime.artifact_store import get_artifact_store

            return get_artifact_store()
        except Exception:
            return None
