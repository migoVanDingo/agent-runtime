"""recall_sessions - semantic recall over prior sessions and artifact summaries."""

from __future__ import annotations

from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight
from app_config import config


def _as_bool(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"1", "true", "yes", "y"}:
            return True
        if v in {"0", "false", "no", "n"}:
            return False
    return default


class RecallSessionsTool(BaseTool):
    name = "recall_sessions"
    description = (
        "Recall relevant prior session summaries and artifact hits for a query. "
        "Returns sessions + artifact hits by default."
    )
    weight = ToolWeight.MODERATE

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "query": ToolProperty(type="string", description="Recall query text"),
                "top_k": ToolProperty(type="string", description="Top results per category (default from config)"),
                "include_sessions": ToolProperty(type="string", description="Whether to include session hits (default true)"),
                "include_artifacts": ToolProperty(type="string", description="Whether to include artifact hits (default true)"),
                "project": ToolProperty(
                    type="string",
                    description="Optional project filter. Use '*' to search all projects.",
                ),
                "threshold": ToolProperty(
                    type="string",
                    description="Optional cosine similarity threshold (0..1).",
                ),
            },
            required=["query"],
        )

    def execute(self, tool_input: dict) -> str:
        store = self._store()
        if store is None:
            return "Error: artifact store is not initialized."

        q = str(tool_input["query"]).strip()
        if not q:
            return "Error: query must be non-empty."

        rag_cfg = config.artifact_store.rag
        try:
            top_k = int(tool_input.get("top_k", rag_cfg.top_k) or rag_cfg.top_k)
        except Exception:
            top_k = int(rag_cfg.top_k)
        try:
            threshold = float(tool_input.get("threshold", rag_cfg.similarity_threshold) or rag_cfg.similarity_threshold)
        except Exception:
            threshold = float(rag_cfg.similarity_threshold)
        project = tool_input.get("project")
        if project is not None:
            project = str(project)

        include_sessions = _as_bool(tool_input.get("include_sessions"), True)
        include_artifacts = _as_bool(tool_input.get("include_artifacts"), True)

        sessions = []
        artifacts = []
        if include_sessions:
            sessions = store.recall_sessions(q, top_k=top_k, threshold=threshold, project=project)
        if include_artifacts:
            artifacts = store.recall_artifacts(q, top_k=top_k, threshold=threshold, project=project)

        if not sessions and not artifacts:
            return "No matching prior sessions or artifact hits found."

        lines: list[str] = []
        lines.append(f"Recall query: {q}")
        if project:
            lines.append(f"Project filter: {project}")
        lines.append(f"Threshold: {threshold:.2f}  Top-k: {top_k}")

        if sessions:
            lines.append("")
            lines.append(f"Sessions ({len(sessions)}):")
            for i, s in enumerate(sessions, start=1):
                summary = s.summary.replace("\n", " ").strip()
                if len(summary) > 180:
                    summary = summary[:177] + "..."
                lines.append(f"{i}. [{s.score:.3f}] {s.session_id} | {summary}")

        if artifacts:
            lines.append("")
            lines.append(f"Artifacts ({len(artifacts)}):")
            for i, a in enumerate(artifacts, start=1):
                summary = a.summary.replace("\n", " ").strip()
                if len(summary) > 160:
                    summary = summary[:157] + "..."
                proj = f", project={a.project}" if a.project else ""
                lines.append(
                    f"{i}. [{a.score:.3f}] {a.key} ({a.kind}{proj}, session={a.session_id}) | {summary}"
                )

        return "\n".join(lines)

    def _store(self):
        try:
            from runtime.artifact_store import get_artifact_store

            return get_artifact_store()
        except Exception:
            return None
