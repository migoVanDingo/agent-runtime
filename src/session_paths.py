"""Central source of truth for all session-scoped and analysis output paths.

All runtime data is stored under ARC_HOME (default: ~/.arc/). The project
directory stays clean. Override the location by setting ARC_HOME in .env.

Directory layout under ARC_HOME:
  sessions/<session_id>/logs/session.log
  sessions/<session_id>/metrics/council.jsonl
  sessions/<session_id>/events/runtime.jsonl
  rag/global/                           — Tier 1 RAG warehouse
  rag/sessions/<session_id>/            — Tier 2 RAG chunk store
  store/artifacts.db                    — artifact store DB
  store/data/                           — artifact store payload data
  ghidra/projects/                      — cached Ghidra projects
  analysis/<binary_name>/               — paged tool outputs

The agent still uses logical paths like `_analysis/<binary>/<file>` when calling
write_file / read_file — those are transparently rewritten by runtime.path_resolver.
"""
from __future__ import annotations

from pathlib import Path

# Project root is still needed for plan docs, tests, and other static project content.
ROOT_DIR = Path(__file__).resolve().parent.parent


def arc_home() -> Path:
    """Return the centralized data directory for all arc runtime data.

    Resolves once per call from settings, expanding ~. Creates the directory
    if it doesn't exist (parents=True so missing intermediate dirs are fine).

    Default:    ~/.arc/
    Override:   set ARC_HOME=/path in .env
    """
    from app_config import settings
    custom = getattr(settings, "arc_home", None) if settings else None
    if custom:
        p = Path(custom).expanduser()
    else:
        p = Path.home() / ".arc"
    p.mkdir(parents=True, exist_ok=True)
    return p


def session_dir(session_id: str) -> Path:
    return arc_home() / "sessions" / session_id


def log_path(session_id: str) -> Path:
    return session_dir(session_id) / "logs" / "session.log"


def metrics_path(session_id: str) -> Path:
    return session_dir(session_id) / "metrics" / "council.jsonl"


def events_dir(session_id: str) -> Path:
    return session_dir(session_id) / "events"


def rag_global_uri() -> str:
    """LanceDB URI for the Tier 1 global warehouse. Remote (gs://...) or local."""
    from app_config import config
    base = (config.storage.base_uri or "").rstrip("/")
    return f"{base}/rag/global" if base else str(arc_home() / "rag" / "global")


def rag_session_uri(session_id: str) -> str:
    """LanceDB URI for a session's Tier 2 chunk store."""
    from app_config import config
    base = (config.storage.base_uri or "").rstrip("/")
    return (
        f"{base}/rag/sessions/{session_id}" if base
        else str(arc_home() / "rag" / "sessions" / session_id)
    )


def analysis_dir(binary_path: str) -> Path:
    """Return the on-disk directory where paged tool output for a binary lives.

    Agent-facing path is `_analysis/<binary>/...` — those get rewritten to this
    location by runtime.path_resolver.
    """
    return arc_home() / "analysis" / Path(binary_path).name


def virtual_analysis_path(binary_path: str, filename: str) -> str:
    """Return the agent-facing logical path for a paged tool output.

    Always returns `_analysis/<basename>/<filename>` regardless of where ARC_HOME
    actually points. Skills should use this when constructing step descriptions
    that reference paged tool outputs — the path resolver in runtime.path_resolver
    translates the virtual prefix at read/write time.

    Example:
        virtual_analysis_path("/abs/path/proc", "ghidra_decompile.txt")
        → "_analysis/proc/ghidra_decompile.txt"
    """
    return f"_analysis/{Path(binary_path).name}/{filename}"


def store_db_path() -> Path:
    """Path to the artifact store SQLite database."""
    return arc_home() / "store" / "artifacts.db"


def store_data_dir() -> Path:
    """Directory for artifact store payload blobs."""
    return arc_home() / "store" / "data"


def ghidra_projects_dir() -> Path:
    """Directory where cached Ghidra project files live."""
    return arc_home() / "ghidra" / "projects"


# Subdirectories created on `arc bootstrap` / `make install-python` so they
# exist before any code tries to write into them.
_ESSENTIAL_SUBDIRS = (
    "sessions",
    "rag/global",
    "rag/sessions",
    "store/data",
    "ghidra/projects",
    "analysis",
)


def ensure_data_layout() -> Path:
    """Create the full arc data directory tree under ARC_HOME.

    Idempotent — existing directories are left alone. Returns the root path.
    Called on install (`arc bootstrap`) and lazily by callers that need a path.
    """
    home = arc_home()
    for sub in _ESSENTIAL_SUBDIRS:
        (home / sub).mkdir(parents=True, exist_ok=True)
    return home


def build_analysis_manifest() -> str:
    """Scan analysis output and return a short artifact manifest for system prompts.

    Returns paths in the agent-facing form (`_analysis/<binary>/<file>`) regardless
    of where the files actually live on disk. Capped at 20 entries.
    """
    analysis_root = arc_home() / "analysis"
    if not analysis_root.exists():
        return ""

    entries = []
    for path in sorted(analysis_root.rglob("*")):
        if path.is_file():
            rel = path.relative_to(analysis_root)
            size = path.stat().st_size
            # Show the agent-facing logical path, not the on-disk arc_home path.
            entries.append(f"  _analysis/{rel}  ({size:,} bytes)")

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
