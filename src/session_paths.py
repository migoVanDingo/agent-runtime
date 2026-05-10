"""Central source of truth for all session-scoped and analysis output paths.

Directory layout:
  _sessions/<session_id>/logs/session.log
  _sessions/<session_id>/metrics/council.jsonl
  _sessions/<session_id>/events/runtime.jsonl
  _analysis/<binary_name>/...

Old flat dirs (_logs/, _metrics/, _events/) are no longer written to.
They can be removed manually after verifying migration.
"""
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent


def session_dir(session_id: str) -> Path:
    return ROOT_DIR / "_sessions" / session_id


def log_path(session_id: str) -> Path:
    return session_dir(session_id) / "logs" / "session.log"


def metrics_path(session_id: str) -> Path:
    return session_dir(session_id) / "metrics" / "council.jsonl"


def events_dir(session_id: str) -> Path:
    return session_dir(session_id) / "events"


def rag_global_uri() -> str:
    """LanceDB URI for the Tier 1 global warehouse. Local path or gs:// based on config."""
    from app_config import config
    base = (config.storage.base_uri or "").rstrip("/")
    return f"{base}/rag/global" if base else str(ROOT_DIR / "_rag" / "global")


def rag_session_uri(session_id: str) -> str:
    """LanceDB URI for a session's Tier 2 chunk store."""
    from app_config import config
    base = (config.storage.base_uri or "").rstrip("/")
    return (
        f"{base}/rag/sessions/{session_id}" if base
        else str(ROOT_DIR / "_rag" / "sessions" / session_id)
    )


def analysis_dir(binary_path: str) -> Path:
    return ROOT_DIR / "_analysis" / Path(binary_path).name


def build_analysis_manifest() -> str:
    """Scan _analysis/ and return a short artifact manifest for injection into system prompts.

    Returns an empty string when no artifacts exist so callers can append it unconditionally.
    Capped at 20 entries to stay cheap in the context budget.
    """
    analysis_root = ROOT_DIR / "_analysis"
    if not analysis_root.exists():
        return ""

    entries = []
    for path in sorted(analysis_root.rglob("*")):
        if path.is_file():
            rel = path.relative_to(ROOT_DIR)
            size = path.stat().st_size
            entries.append(f"  {rel}  ({size:,} bytes)")

    if not entries:
        return ""

    lines = entries[:20]
    truncation = f"\n  ... ({len(entries) - 20} more)" if len(entries) > 20 else ""
    return (
        "\n--- Prior analysis artifacts ---\n"
        + "\n".join(lines)
        + truncation
        + "\nUse file-read tools to access them. Do not re-run the heavy tools.\n---"
    )
