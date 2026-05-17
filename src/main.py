"""Arc agent entry point.

Provides the legacy interactive CLI (main()), the top-level argv router (dispatch()),
and session banner helpers shared across CLI and ui modules.

Subcommands (wipe, bootstrap) live in src/cli/.
Session lifecycle helpers live in src/cli/session.py.
"""
import sys
import os
import argparse

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from pathlib import Path
from logger import configure_logging, log_session_end, get_logger
from runtime.artifact_store import init_store, get_artifact_store
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
    from session_paths import session_dir
    sdir = session_dir(session_id)
    width = 52
    print("\n" + "─" * width)
    print(f"  Agent Session {'Resumed' if resumed else 'Started'}")
    print(f"  Session ID : {session_id}")
    print(f"  Session dir: {sdir}")
    print("─" * width + "\n")


def print_session_end(session_id: str) -> None:
    print(f"\n{'─' * 52}")
    print(f"  Session ended  |  ID: {session_id}")
    print(f"{'─' * 52}\n")


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


def _finalize_session(session_id: str, agent: Agent | None, store_enabled: bool) -> None:
    from cli.session import finalize_session
    finalize_session(session_id, agent, store_enabled)


def main() -> None:
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
        from session_paths import store_db_path, store_data_dir
        init_store(
            db_path=store_db_path(),
            data_dir=store_data_dir(),
            inline_threshold=config.artifact_store.inline_threshold_bytes,
        )

    from cli.session import resolve_session_id
    try:
        session_id, resumed = resolve_session_id(args.resume, store_enabled)
    except KeyboardInterrupt:
        print("Resume cancelled.")
        sys.exit(0)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

    from rag import init_rag_service
    configure_logging(session_id, verbose=args.verbose)
    init_runtime_events(session_id, project_id=project_root.name)
    init_rag_service(session_id)
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
        # Cap resumed context to the most recent turns to prevent token blowout.
        # Older turns are dropped; the manifest and RAG surface prior artifacts.
        # 30 messages ≈ 15 turns — enough for immediate task continuity.
        _RESUME_MSG_CAP = 30
        if len(restored_messages) > _RESUME_MSG_CAP:
            n_dropped = len(restored_messages) - _RESUME_MSG_CAP
            logger.info(
                f"resume: dropped {n_dropped} older message(s) to cap context "
                f"({_RESUME_MSG_CAP} most recent kept)"
            )
            restored_messages = restored_messages[-_RESUME_MSG_CAP:]

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

        # Streaming state — shared between on_token callback and main thread.
        _streaming_started = False

        def _on_token(chunk: str) -> None:
            nonlocal _streaming_started
            if not _streaming_started:
                print("\nAgent: ", end="", flush=True)
                _streaming_started = True
            print(chunk, end="", flush=True)

        try:
            response = agent.call(user_input, on_token=_on_token)
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
        if _streaming_started:
            # Response was streamed — just print newline and timing
            print("\n")
        else:
            # Non-streaming path (direct mode, or provider doesn't support streaming)
            print(f"\nAgent: {response}\n")
        if elapsed:
            print(f"  ⏱  {elapsed}\n")


def dispatch() -> None:
    """Top-level `arc` entry point.

    Default: launch the Textual-UI replacement (`arc-tui`).
    Override: `arc --cli` or `arc -t` runs the legacy text CLI.
    Subcommands `arc wipe …` and `arc bootstrap …` always go to CLI.

    Forwards all other arguments through unchanged.
    """
    argv = sys.argv[1:]

    # Subcommands that are CLI-only — route to dedicated handlers.
    if argv and argv[0] == "wipe":
        from cli.wipe import cmd_wipe
        cmd_wipe(argv[1:])
        return

    if argv and argv[0] == "bootstrap":
        from cli.bootstrap import cmd_bootstrap
        cmd_bootstrap(argv[1:])
        return

    if argv and argv[0] == "plugin":
        from plugins.cli import cmd_plugin
        cmd_plugin(argv[1:])
        return

    if argv and argv[0] == "subagent":
        from cli.subagent import cmd_subagent
        cmd_subagent(argv[1:])
        return

    # Explicit legacy CLI flag — strip it from argv, then call legacy main().
    if "--cli" in argv or "-t" in argv:
        cleaned = [a for a in argv if a not in ("--cli", "-t")]
        sys.argv[1:] = cleaned
        main()
        return

    # Default: TUI.
    from ui.app import run as tui_run
    tui_run()


if __name__ == "__main__":
    dispatch()
