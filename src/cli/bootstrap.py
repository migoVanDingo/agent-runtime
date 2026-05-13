"""arc bootstrap subcommand — create/migrate the centralized ARC_HOME data layout."""
import argparse
import shutil
from pathlib import Path


_LEGACY_DIRS = {
    "_sessions": "sessions",
    "_rag":      "rag",
    "_store":    "store",
    "_analysis": "analysis",
}


def cmd_bootstrap(argv: list[str]) -> None:
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

    project_root = Path(__file__).resolve().parent.parent.parent

    print()
    home = ensure_data_layout()
    print(f"✓ Data layout created at {home}")
    for sub in ("sessions", "rag/global", "rag/sessions", "store/data",
                "ghidra/projects", "analysis"):
        print(f"    {sub}")

    if args.migrate:
        print()
        print("Migrating legacy project-dir data…")
        moved = 0
        for old_name, new_name in _LEGACY_DIRS.items():
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
