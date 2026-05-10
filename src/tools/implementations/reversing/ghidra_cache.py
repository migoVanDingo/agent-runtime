"""Ghidra integration via PyGhidra — opens programs directly in-process.

PyGhidra embeds the Ghidra JVM in the current Python process via JPype.
start() is called once; subsequent open_program() calls reuse the running JVM.
"""
from __future__ import annotations
import json
from pathlib import Path
from app_config import settings
from logger import get_logger

logger = get_logger(__name__)

_ghidra_started = False


def ghidra_home() -> str | None:
    return settings.ghidra_home


def _ensure_started() -> bool:
    """Start PyGhidra JVM if not already running. Returns True if ready."""
    global _ghidra_started
    if _ghidra_started:
        return True
    home = ghidra_home()
    if not home:
        return False
    try:
        import pyghidra
        pyghidra.start(install_dir=home, verbose=False)
        _ghidra_started = True
        logger.info("  ghidra: PyGhidra JVM started")
        return True
    except Exception as e:
        logger.warning(f"  ghidra: failed to start PyGhidra: {e}")
        return False


def run_ghidra_function(binary_path: str, fn, *args):
    """Open binary in Ghidra and call fn(api, *args). Returns fn's return value or error string."""
    if not _ensure_started():
        return "Error: GHIDRA_HOME not set or PyGhidra failed to start. Add GHIDRA_HOME=/path/to/ghidra to .env"
    try:
        import pyghidra
        with pyghidra.open_program(binary_path) as api:
            return fn(api, *args)
    except Exception as e:
        return f"Error: {e}"
