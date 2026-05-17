"""Shared radare2 subprocess runner used by all r2_* tools."""
from __future__ import annotations
import subprocess
import shutil
from app_config import config


def r2_available() -> bool:
    return shutil.which("r2") is not None


def r2_run(binary: str, command: str, *, analyze: bool = True, timeout: int | None = None) -> str:
    """Run one r2 command against a binary and return stdout.

    analyze=True runs `aaa` (full analysis) before the command.
    timeout defaults to config.timeouts.analysis.
    """
    if not r2_available():
        return "Error: radare2 (r2) not found in PATH. Install with: brew install radare2"

    t = timeout or config.timeouts.analysis
    flags = ["-q", "-e", "scr.color=0"]
    if analyze:
        flags.append("-A")  # full analysis (aaa)

    cmd = ["r2"] + flags + ["-c", f"{command}; q", binary]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=t,
        )
        out = result.stdout.strip()
        # Filter r2's internal diagnostic noise — only surface real errors
        err_lines = [
            ln for ln in result.stderr.splitlines()
            if ln and not ln.startswith(("WARN:", "INFO:", "-- "))
        ]
        err = "\n".join(err_lines).strip()
        if not out and err:
            return f"(no output)\nSTDERR: {err}"
        if err:
            out = f"{out}\nSTDERR: {err}" if out else f"STDERR: {err}"
        return out or "(no output)"
    except subprocess.TimeoutExpired:
        return f"Error: r2 timed out after {t}s"
    except Exception as e:
        return f"Error: {e}"
