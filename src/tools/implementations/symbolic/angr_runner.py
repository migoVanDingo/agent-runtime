"""Shared angr subprocess runner with binary-complexity-aware timeouts."""
from __future__ import annotations
import json
import os
import subprocess
import sys
import tempfile
from app_config import config
from logger import get_logger

logger = get_logger(__name__)

_ANGR_CHECK = None  # cached availability result


def angr_available() -> bool:
    global _ANGR_CHECK
    if _ANGR_CHECK is None:
        try:
            result = subprocess.run(
                [sys.executable, "-c", "import angr"],
                capture_output=True, timeout=10,
            )
            _ANGR_CHECK = result.returncode == 0
        except Exception:
            _ANGR_CHECK = False
    return _ANGR_CHECK


def _function_count(binary: str) -> int:
    """Quick function count via nm — used to scale angr timeout."""
    try:
        result = subprocess.run(
            ["nm", "-f", "posix", binary],
            capture_output=True, text=True, timeout=10,
        )
        return max(1, result.stdout.count("\n"))
    except Exception:
        return 1


def scaled_timeout(base_seconds: int, binary: str) -> int:
    """Scale timeout by binary complexity (function count)."""
    cfg = config.tools.angr
    n = _function_count(binary)
    if n >= cfg.complexity_large_threshold:
        multiplier = 2.5
    elif n >= cfg.complexity_medium_threshold:
        multiplier = 1.5
    else:
        multiplier = 1.0
    timeout = int(base_seconds * multiplier)
    logger.info(f"  angr: binary has ~{n} symbols → timeout {timeout}s (base {base_seconds}s × {multiplier}×)")
    return timeout


def run_angr_script(script_path: str, timeout: int, env_vars: dict | None = None) -> dict:
    """Run an angr Python script and return its JSON output dict.

    The script reads its inputs from environment variables and writes a JSON
    dict to the path in ANGR_OUTPUT. Returns {'ok': bool, 'result': ..., 'error': ...}.
    """
    if not angr_available():
        return {"ok": False, "result": None, "error": "angr not installed. Run: pip install angr"}

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        output_path = tmp.name

    env = {**os.environ, "ANGR_OUTPUT": output_path, **(env_vars or {})}
    try:
        result = subprocess.run(
            [sys.executable, script_path],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            with open(output_path) as f:
                return json.load(f)
        stderr = result.stderr[-500:] if result.stderr else "(no stderr)"
        return {"ok": False, "result": None, "error": f"Script produced no output.\nSTDERR: {stderr}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "result": None, "error": f"angr timed out after {timeout}s — binary may be too complex or path unreachable"}
    except Exception as e:
        return {"ok": False, "result": None, "error": str(e)}
    finally:
        if os.path.exists(output_path):
            os.unlink(output_path)
