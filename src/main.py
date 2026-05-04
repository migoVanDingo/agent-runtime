import sys
import os
import argparse
from datetime import datetime

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
from pathlib import Path
from utils import generate_id
from logger import configure_logging, log_session_end, LOGS_DIR, get_logger
from runtime.token_tracker import get_tracker
from runtime.artifact_store import init_store, get_artifact_store, ResumableSession
from runtime.events import (
    RuntimeEvent,
    get_event_bus,
    get_runtime_identity,
    init_runtime_events,
    set_runtime_identity,
)
from app_config import config
from agent import Agent

logger = get_logger(__name__)


_RESUME_PICK = "__resume_pick__"


def print_session_banner(session_id: str, resumed: bool = False) -> None:
    log_path = LOGS_DIR / f"{session_id}.log"
    width = 52
    print("\n" + "─" * width)
    print(f"  Agent Session {'Resumed' if resumed else 'Started'}")
    print(f"  Session ID : {session_id}")
    print(f"  Log file   : {log_path}")
    print("─" * width + "\n")


def print_session_end(session_id: str) -> None:
    print(f"\n{'─' * 52}")
    print(f"  Session ended  |  ID: {session_id}")
    print(f"{'─' * 52}\n")


def _fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%b %d %H:%M")


def _pick_resume_session(options: list[ResumableSession]) -> str | None:
    if not options:
        print("No resumable sessions found.")
        return None

    print("\nResumable sessions:")
    for i, s in enumerate(options, start=1):
        print(
            f"{i}) {_fmt_ts(s.started_at)}  |  \"{s.preview}\"  | artifacts: {s.artifact_count}"
        )
    print("")

    retries = 3
    while retries > 0:
        raw = input(f"Select session to resume [1-{len(options)}] (Enter=1, q=cancel): ").strip().lower()
        if raw == "":
            return options[0].session_id
        if raw == "q":
            return None
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(options):
                return options[idx - 1].session_id
        retries -= 1
        print("Invalid selection.")

    print("Too many invalid selections.")
    return None


def _resolve_session_id(resume_arg: str | None, store_enabled: bool) -> tuple[str, bool]:
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
        selected = _pick_resume_session(options)
        if selected is None:
            raise KeyboardInterrupt
        sid = store.load_session(selected)
        return sid, True

    sid = store.load_session(options[0].session_id)
    print(f"Resuming latest detached session (non-interactive): {sid}")
    return sid, True


def _apply_decay_if_enabled() -> None:
    if not config.artifact_store.decay.enabled:
        return
    store = get_artifact_store()
    archived = store.apply_decay(
        factor=config.artifact_store.decay.factor,
        threshold=config.artifact_store.decay.archive_threshold,
    )
    if archived:
        logger.info(f"artifact decay: archived {len(archived)} artifact(s)")


def _maybe_prompt_workflow_candidates() -> None:
    cfg = config.artifact_store.workflow_discovery
    if not cfg.enabled:
        return

    store = get_artifact_store()
    store.discover_workflows(
        lookback_days=cfg.lookback_days,
        similarity_threshold=cfg.similarity_threshold,
        frequency_threshold=cfg.frequency_threshold,
        recency_decay=cfg.recency_decay,
    )

    if not sys.stdin.isatty():
        return

    pending = store.get_pending_workflow_candidates(limit=5)
    if not pending:
        return

    print("\nPotential workflow candidates detected:")
    for c in pending:
        print(f"\n[{c.id}] {c.description}")
        print(f"  frequency={c.frequency} recency_score={c.recency_score:.2f}")
        for ex in c.example_messages[:3]:
            print(f"  - {ex}")

        choice = input("  Approve this candidate? [y/N]: ").strip().lower()
        if choice == "y":
            store.approve_workflow_candidate(c.id)
            print("  Approved.")
        elif choice == "n" or choice == "":
            store.reject_workflow_candidate(c.id)
            print("  Rejected.")


def _build_session_summary(agent: Agent) -> str:
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


def _finalize_session(session_id: str, agent: Agent | None, store_enabled: bool) -> None:
    if store_enabled and agent is not None:
        store = get_artifact_store()
        store.save_conversation(agent.messenger.get_messages())
        if config.artifact_store.rag.enabled:
            try:
                summary = _build_session_summary(agent)
                if summary:
                    store.index_session_summary(session_id, summary)
            except Exception as e:
                logger.warning(f"session summary indexing skipped: {e}")
        store.flush()
        store.mark_detached()

    get_tracker().log_summary()
    get_event_bus().emit(
        RuntimeEvent(
            "session.ended",
            get_runtime_identity(),
            payload={"has_agent": agent is not None, "store_enabled": store_enabled},
            stage="main",
        )
    )
    print_session_end(session_id)
    log_session_end(session_id)


def main():
    parser = argparse.ArgumentParser(description="Raw Tool Agent")
    parser.add_argument("--verbose", action="store_true", help="Stream logs to console")
    parser.add_argument(
        "--resume",
        nargs="?",
        const=_RESUME_PICK,
        default=None,
        help="Resume a detached session (interactive picker if no ID is provided)",
    )
    args = parser.parse_args()

    store_enabled = config.artifact_store.enabled
    project_root = Path(__file__).resolve().parent.parent
    if store_enabled:
        init_store(
            db_path=project_root / "_store" / "artifacts.db",
            data_dir=project_root / "_store" / "data",
            inline_threshold=config.artifact_store.inline_threshold_bytes,
        )

    try:
        session_id, resumed = _resolve_session_id(args.resume, store_enabled)
    except KeyboardInterrupt:
        print("Resume cancelled.")
        sys.exit(0)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

    configure_logging(session_id, verbose=args.verbose)
    init_runtime_events(session_id, project_id=project_root.name)
    get_event_bus().emit(
        RuntimeEvent(
            "session.resumed" if resumed else "session.started",
            get_runtime_identity(),
            payload={"resumed": resumed, "store_enabled": store_enabled},
            stage="main",
        )
    )

    if store_enabled:
        _apply_decay_if_enabled()

    restored_messages: list[dict] = []
    if store_enabled and resumed:
        restored_messages = get_artifact_store().load_conversation(session_id)

    if store_enabled and config.artifact_store.project.enabled:
        store = get_artifact_store()
        active_project = (config.artifact_store.project.default or project_root.name or "").strip()
        if active_project:
            store.set_active_project(active_project)
            print(f"Project memory scope: {active_project}")

    print_session_banner(session_id, resumed=resumed)
    print("Type 'exit' or 'quit' to end the session.\n")

    agent = Agent(verbose=args.verbose, initial_messages=restored_messages)

    if store_enabled:
        try:
            _maybe_prompt_workflow_candidates()
        except Exception as e:
            logger.warning(f"workflow candidate prompt skipped: {e}")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            _finalize_session(session_id, agent, store_enabled)
            sys.exit(0)

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit"):
            _finalize_session(session_id, agent, store_enabled)
            sys.exit(0)

        agent.spinner.begin_turn()
        turn_identity = get_runtime_identity().for_turn()
        set_runtime_identity(turn_identity)
        get_event_bus().emit(
            RuntimeEvent(
                "turn.started",
                turn_identity,
                payload={"message_preview": user_input[:300]},
                stage="main",
            )
        )
        try:
            response = agent.call(user_input)
        except Exception as exc:
            agent.spinner.stop()
            logger.exception("Unhandled error during agent.call")
            get_event_bus().emit(
                RuntimeEvent(
                    "turn.failed",
                    turn_identity,
                    payload={"error": str(exc)[:500]},
                    stage="main",
                )
            )
            print(f"\nAgent: Sorry, something went wrong: {exc}\n")
            continue
        get_event_bus().emit(
            RuntimeEvent(
                "turn.completed",
                turn_identity,
                payload={"response_preview": response[:500]},
                stage="main",
            )
        )
        elapsed = agent.spinner.elapsed_display()
        print(f"\nAgent: {response}\n")
        if elapsed:
            print(f"  ⏱  {elapsed}\n")


if __name__ == "__main__":
    main()
