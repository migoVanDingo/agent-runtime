"""recall_sessions — semantic recall over prior sessions via the RAG service."""
from __future__ import annotations

from tools.base import BaseTool, InputSchema, ToolProperty, ToolWeight


class RecallSessionsTool(BaseTool):
    name = "recall_sessions"
    description = (
        "Recall relevant prior session summaries for a query. "
        "Returns the most semantically similar past sessions from the global RAG warehouse."
    )
    weight = ToolWeight.MODERATE

    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(
            properties={
                "query": ToolProperty(type="string", description="Recall query text"),
                "top_k": ToolProperty(type="string", description="Max results to return (default from config)"),
                "threshold": ToolProperty(type="string", description="Similarity threshold 0..1 (default from config)"),
            },
            required=["query"],
        )

    def execute(self, tool_input: dict) -> str:
        from rag import get_rag_service
        from app_config import config

        rag = get_rag_service()
        if rag is None:
            return "RAG service is not initialized (rag.enabled=false or lancedb not installed)."

        q = str(tool_input["query"]).strip()
        if not q:
            return "Error: query must be non-empty."

        try:
            top_k = int(tool_input.get("top_k") or config.rag.top_k)
        except Exception:
            top_k = config.rag.top_k
        try:
            threshold = float(tool_input.get("threshold") or config.rag.threshold)
        except Exception:
            threshold = config.rag.threshold

        sessions = rag.query_global(q, top_k=top_k, threshold=threshold)
        if not sessions:
            return "No matching prior sessions found."

        lines = [f"Recall query: {q}", f"Threshold: {threshold:.2f}  Top-k: {top_k}", ""]
        for i, s in enumerate(sessions, 1):
            summary = s.summary.replace("\n", " ").strip()
            if len(summary) > 180:
                summary = summary[:177] + "..."
            lines.append(f"{i}. [{s.score:.3f}] {s.session_id} | {summary}")
        return "\n".join(lines)
