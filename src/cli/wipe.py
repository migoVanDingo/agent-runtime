"""arc wipe subcommand — delete generated runtime data directories."""
import argparse
import shutil
from pathlib import Path


def _measure(path: Path) -> tuple[int, float]:
    if not path.exists():
        return 0, 0.0
    if path.is_file():
        return 1, path.stat().st_size / 1_048_576
    files = list(path.rglob("*"))
    count = sum(1 for f in files if f.is_file())
    mb = sum(f.stat().st_size for f in files if f.is_file()) / 1_048_576
    return count, mb


def cmd_wipe(argv: list[str]) -> None:
    """arc wipe — delete generated runtime data directories."""
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
    project_root = Path(__file__).resolve().parent.parent.parent

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
