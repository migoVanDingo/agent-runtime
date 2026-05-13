"""CLI session helpers — resolve/finalize session IDs and build summaries."""
import sys

from utils import generate_id
from runtime.artifact_store import get_artifact_store
from runtime.token_tracker import get_tracker
from runtime.events import RuntimeEvent, get_event_bus, get_runtime_identity
from logger import get_logger

logger = get_logger(__name__)

_RESUME_PICK = "__resume_pick__"


def resolve_session_id(resume_arg: str | None, store_enabled: bool) -> tuple[str, bool]:
    if not store_enabled:
        if resume_arg is not None:
            raise RuntimeError("Cannot use --resume while artifact_store.enabled=false")
        return generate_id("session"), False

    store = get_artifact_store()
    if resume_arg is None:
        sid = generate_id("session")
        store.init_session(sid)
        return sid, False

    # Explicit ID path.
    if resume_arg != _RESUME_PICK:
        sid = store.load_session(resume_arg)
        return sid, True

    # Interactive or non-TTY fallback path.
    options = store.list_resumable_sessions(limit=20)
    if not options:
        sid = generate_id("session")
        store.init_session(sid)
        print("No resumable sessions found. Starting a new session.")
        return sid, False

    if sys.stdin.isatty():
        from cli.resume_picker import pick_resume_session
        selected = pick_resume_session(options)
        if selected is None:
            raise KeyboardInterrupt
        sid = store.load_session(selected)
        return sid, True

    sid = store.load_session(options[0].session_id)
    print(f"Resuming latest detached session (non-interactive): {sid}")
    return sid, True


def build_session_summary(agent: "Agent") -> str:  # type: ignore[name-defined]
    first_user = ""
    for msg in agent.messenger.get_messages():
        if msg.get("role") == "user" and isinstance(msg.get("content"), str):
            first_user = msg["content"].strip().replace("\n", " ")
            if first_user:
                break

    outcome = (agent.last_response or "").strip().replace("\n", " ")
    parts: list[str] = []
    if first_user:
        parts.append(f"Task: {first_user[:300]}")
    if outcome:
        parts.append(f"Outcome: {outcome[:900]}")
    summary = " | ".join(parts).strip()
    return summary[:1200]


def _shutdown_jvm_if_running() -> None:
    """Shut down the JPype/PyGhidra JVM cleanly to suppress semaphore leak warnings."""
    try:
        import jpype
        if jpype.isJVMStarted():
            jpype.shutdownJVM()
    except Exception:
        pass


def finalize_session(session_id: str, agent: "Agent | None", store_enabled: bool) -> None:  # type: ignore[name-defined]
    from rag import get_rag_service
    from app_config import config
    from logger import log_session_end

    if store_enabled and agent is not None:
        store = get_artifact_store()
        store.save_conversation(agent.messenger.get_messages())
        store.flush()
        store.mark_detached()

    if rag := get_rag_service():
        try:
            import time as _time
            summary = build_session_summary(agent) if agent else ""
            if summary:
                rag.index_session(session_id, summary, {
                    "project": config.artifact_store.project.default or "",
                    "timestamp": _time.time(),
                })
        except Exception as e:
            logger.warning(f"rag session indexing skipped: {e}")

    get_tracker().log_summary()
    get_event_bus().emit(
        RuntimeEvent(
            "session.ended",
            get_runtime_identity(),
            payload={"has_agent": agent is not None, "store_enabled": store_enabled},
            stage="main",
        )
    )
    _shutdown_jvm_if_running()
    # print_session_end is imported lazily to avoid circular dependency
    from main import print_session_end
    print_session_end(session_id)
    log_session_end(session_id)
