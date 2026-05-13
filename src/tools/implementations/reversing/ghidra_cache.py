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

# Cached startup failure reason — set by _ensure_started so we can report
# accurately instead of "GHIDRA_HOME not set" when the actual problem is
# something else (pyghidra missing, JVM crash, bad install path, etc.).
_startup_error: str | None = None


def ghidra_home() -> str | None:
    return settings.ghidra_home


def _ensure_started() -> bool:
    """Start PyGhidra JVM if not already running. Returns True if ready."""
    global _ghidra_started, _startup_error
    if _ghidra_started:
        return True
    home = ghidra_home()
    if not home:
        _startup_error = (
            "GHIDRA_HOME is not set. Add GHIDRA_HOME=/path/to/ghidra to .env. "
            "Download Ghidra from https://github.com/NationalSecurityAgency/ghidra/releases"
        )
        return False
    try:
        import pyghidra
    except ImportError:
        _startup_error = (
            "pyghidra is not installed. Install with: "
            "pip install pyghidra jpype1   (or: make install-reversing). "
            "GHIDRA_HOME is set correctly; only the Python bridge is missing."
        )
        logger.warning(f"  ghidra: {_startup_error}")
        return False
    try:
        pyghidra.start(install_dir=home, verbose=False)
        _ghidra_started = True
        _startup_error = None
        logger.info("  ghidra: PyGhidra JVM started")
        return True
    except Exception as e:
        _startup_error = f"PyGhidra failed to start: {e}. Verify GHIDRA_HOME points at a valid install."
        logger.warning(f"  ghidra: {_startup_error}")
        return False


def run_ghidra_function(binary_path: str, fn, *args):
    """Open binary in Ghidra and call fn(api, *args). Returns fn's return value or error string."""
    if not _ensure_started():
        return f"Error: {_startup_error or 'PyGhidra unavailable'}"
    try:
        import pyghidra
        with pyghidra.open_program(binary_path) as api:
            return fn(api, *args)
    except Exception as e:
        return f"Error: {e}"
