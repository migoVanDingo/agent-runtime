"""`python -m arc.cli` — kept working after the cli.py → cli/ package split.

The TUI /replay menu, the replay menu's target runner, and batch replay all
spawn `[sys.executable, "-m", "arc.cli", ...]` subprocesses.
"""
import sys

from arc.cli import main

if __name__ == "__main__":
    sys.exit(main())
