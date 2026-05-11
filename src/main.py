import sys
import os
import argparse
from datetime import datetime

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
from pathlib import Path
from utils import generate_id
from logger import configure_logging, log_session_end, get_logger
from session_paths import session_dir
from rag import init_rag_service, get_rag_service
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


def _shutdown_jvm_if_running() -> None:
    """Shut down the JPype/PyGhidra JVM cleanly to suppress semaphore leak warnings."""
    try:
        import jpype
        if jpype.isJVMStarted():
            jpype.shutdownJVM()
    except Exception:
        pass


def _finalize_session(session_id: str, agent: Agent | None, store_enabled: bool) -> None:
    if store_enabled and agent is not None:
        store = get_artifact_store()
        store.save_conversation(agent.messenger.get_messages())
        store.flush()
        store.mark_detached()

    if rag := get_rag_service():
        try:
            import time as _time
            summary = _build_session_summary(agent) if agent else ""
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
    print_session_end(session_id)
    log_session_end(session_id)


def _cmd_wipe(argv: list[str]) -> None:
    """arc wipe — delete generated runtime data directories."""
    import shutil

    p = argparse.ArgumentParser(
        prog="arc wipe",
        description="Delete generated runtime data. Prompts for confirmation unless --yes is set.",
    )
    p.add_argument("--all", "-a", action="store_true",
                   help="Wipe all current data under ARC_HOME (~/.arc/)")
    p.add_argument("--sessions", "-s", action="store_true",
                   help="Wipe ARC_HOME/sessions/ (logs, metrics, events)")
    p.add_argument("--rag", "-r", action="store_true",
                   help="Wipe ARC_HOME/rag/ (LanceDB chunk stores + global warehouse)")
    p.add_argument("--analysis", "-n", action="store_true",
                   help="Wipe ARC_HOME/analysis/ (paged tool artifacts)")
    p.add_argument("--store", action="store_true",
                   help="Wipe ARC_HOME/store/ (artifact store DB + payload data)")
    p.add_argument("--legacy", "-L", action="store_true",
                   help="Wipe legacy project-dir data (_sessions, _rag, _store, _analysis, _logs, _metrics, _events, data/agent.db)")
    p.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")

    args = p.parse_args(argv)

    # All runtime data lives under ARC_HOME (default ~/.arc/). The project
    # directory itself is no longer touched.
    from session_paths import arc_home
    root = arc_home()
    project_root = Path(__file__).resolve().parent.parent

    # Build list of (label, path) targets based on flags
    targets: list[tuple[str, Path]] = []

    def _add(label: str, path: Path) -> None:
        targets.append((label, path))

    # ── Current data under ARC_HOME ───────────────────────────────────────────
    if args.all or args.sessions:
        _add("sessions", root / "sessions")
    if args.all or args.rag:
        _add("rag", root / "rag")
    if args.all or args.analysis:
        _add("analysis", root / "analysis")
    if args.all or args.store:
        _add("store/artifacts.db", root / "store" / "artifacts.db")
        _add("store/data", root / "store" / "data")

    # ── Legacy project-dir data (pre-centralization layout) ──────────────────
    if args.legacy:
        _add("_sessions    (legacy)", project_root / "_sessions")
        _add("_rag         (legacy)", project_root / "_rag")
        _add("_store       (legacy)", project_root / "_store")
        _add("_analysis    (legacy)", project_root / "_analysis")
        _add("_logs        (legacy)", project_root / "_logs")
        _add("_metrics     (legacy)", project_root / "_metrics")
        _add("_events      (legacy)", project_root / "_events")
        _add("data/        (legacy SQLModel DB)", project_root / "data")

    if not targets:
        p.print_help()
        return

    # Measure and display what will be deleted
    def _measure(path: Path) -> tuple[int, float]:
        if not path.exists():
            return 0, 0.0
        if path.is_file():
            return 1, path.stat().st_size / 1_048_576
        files = list(path.rglob("*"))
        count = sum(1 for f in files if f.is_file())
        mb = sum(f.stat().st_size for f in files if f.is_file()) / 1_048_576
        return count, mb

    print()
    any_exists = False
    for label, path in targets:
        rel = path.relative_to(root)
        if path.exists():
            count, mb = _measure(path)
            print(f"  {label:<22}  {rel}  ({count} files, {mb:.1f} MB)")
            any_exists = True
        else:
            print(f"  {label:<22}  {rel}  (not found)")

    if not any_exists:
        print("\nNothing to delete.")
        return

    print()
    if not args.yes:
        confirm = input("Delete all of the above? [y/N]: ").strip().lower()
        if confirm != "y":
            print("Cancelled.")
            return

    deleted = 0
    for label, path in targets:
        if not path.exists():
            continue
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            print(f"  deleted  {path.relative_to(root)}")
            deleted += 1
        except Exception as e:
            print(f"  FAILED   {path.relative_to(root)}: {e}")

    print(f"\nDone — {deleted} item(s) removed.")


def _cmd_bootstrap(argv: list[str]) -> None:
    """arc bootstrap — create the centralized ARC_HOME data layout.

    Run this once after installing. Idempotent — safe to re-run.
    Also migrates legacy project-dir data (_sessions/, _rag/, _store/, _analysis/)
    into ARC_HOME if --migrate is passed.
    """
    p = argparse.ArgumentParser(
        prog="arc bootstrap",
        description="Initialize the arc data directory layout.",
    )
    p.add_argument("--migrate", "-m", action="store_true",
                   help="Move legacy project-dir data into ARC_HOME")
    args = p.parse_args(argv)

    from session_paths import arc_home, ensure_data_layout
    import shutil

    project_root = Path(__file__).resolve().parent.parent

    print()
    home = ensure_data_layout()
    print(f"✓ Data layout created at {home}")
    for sub in ("sessions", "rag/global", "rag/sessions", "store/data",
                "ghidra/projects", "analysis"):
        print(f"    {sub}")

    if args.migrate:
        print()
        print("Migrating legacy project-dir data…")
        # Directory-to-directory migrations
        legacy_map = {
            "_sessions": "sessions",
            "_rag":      "rag",
            "_store":    "store",
            "_analysis": "analysis",
        }
        moved = 0
        for old_name, new_name in legacy_map.items():
            old = project_root / old_name
            new = home / new_name
            if not old.exists():
                continue
            if new.exists() and any(new.iterdir()):
                print(f"  ⚠ skip   {old_name}  (target {new_name} non-empty)")
                continue
            try:
                if new.exists():
                    new.rmdir()
                shutil.move(str(old), str(new))
                print(f"  ✓ moved  {old_name}  →  {new_name}")
                moved += 1
            except Exception as e:
                print(f"  ✗ FAILED {old_name}: {e}")

        # Single-file migration: data/agent.db → ~/.arc/agent.db
        old_db = project_root / "data" / "agent.db"
        new_db = home / "agent.db"
        if old_db.exists():
            if new_db.exists() and new_db.stat().st_size > 0:
                print(f"  ⚠ skip   data/agent.db  (target agent.db non-empty)")
            else:
                try:
                    shutil.move(str(old_db), str(new_db))
                    print(f"  ✓ moved  data/agent.db  →  agent.db")
                    moved += 1
                    # Clean up the empty data/ dir if nothing else is in it.
                    data_dir = project_root / "data"
                    if data_dir.exists() and not any(data_dir.iterdir()):
                        data_dir.rmdir()
                        print(f"  ✓ removed empty data/ dir")
                except Exception as e:
                    print(f"  ✗ FAILED data/agent.db: {e}")

        print(f"\nMigrated {moved} item(s).")

    print()
    print(f"To override the data location, add ARC_HOME=/path to .env")


def main():
    # Intercept wipe / bootstrap subcommands before the agent argparse so
    # existing behaviour is completely unchanged for normal `arc` invocations.
    if len(sys.argv) > 1 and sys.argv[1] == "wipe":
        _cmd_wipe(sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "bootstrap":
        _cmd_bootstrap(sys.argv[2:])
        return

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


def dispatch():
    """Top-level `arc` entry point.

    Default: launch the Textual-UI replacement (`arc-tui`).
    Override: `arc --cli` or `arc -t` runs the legacy text CLI.
    Subcommands `arc wipe …` and `arc bootstrap …` always go to legacy
    (they are CLI-only operations).

    Forwards all other arguments through unchanged.
    """
    argv = sys.argv[1:]

    # Subcommands that are CLI-only — always go through legacy main().
    if argv and argv[0] in ("wipe", "bootstrap"):
        main()
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
