import logging
import os
import re
import sys
from pathlib import Path

from session_paths import log_path as _log_path

_NOISY_LOGGERS = [
    "httpx",
    "httpcore",
    "sentence_transformers",
    "huggingface_hub",
    "transformers",
    "torch",
]

# ── Tag helpers — delegated to runtime.log_formatting ────────────────────────
# Imported here for backward compatibility; callers can import from either place.

from runtime.log_formatting import (  # noqa: E402
    council_tag,
    council_header_tag,
    synth_tag,
    user_tag,
    assistant_tag,
    escalate_tag,
    get_councillor_color,
)

def _is_tty() -> bool:
    """Return True if stdout is a real terminal and NO_COLOR is not set."""
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


# ── Formatters ───────────────────────────────────────────────────────────────

_LEVEL_COLORS = {
    "DEBUG":    "\033[37m",    # white
    "INFO":     "\033[0m",     # default
    "WARNING":  "\033[33m",    # yellow
    "ERROR":    "\033[91m",    # bright red
    "CRITICAL": "\033[1;91m",  # bold bright red
}


class _StripANSIFilter(logging.Filter):
    """Strip ANSI escape codes from log records before writing to file.

    Keeps file logs clean when message content contains color tags from
    council_tag(), synth_tag(), etc.
    """
    _ansi_re = re.compile(r"\x1b\[[0-9;]*[mGKHF]")

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._ansi_re.sub("", str(record.msg))
        return True


class _ScopeTagFilter(logging.Filter):
    """Prefix each log record's message with the active ``runtime.scope`` tag.

    Result in ``session.log``:

        2026-05-17 12:00:00,000 [INFO] runtime.stages.routing: [runtime] mode=plan ...
        2026-05-17 12:00:01,000 [INFO] runtime.stages.execution: [main] step 3/12 ...
        2026-05-17 12:00:02,000 [INFO] runtime.tool_loop:     [subagent:ghidra_analyst] → ghidra_decompile ...

    The ``[main]`` tag is omitted so the default scope doesn't add visual
    noise to routine logs — the absence of a tag IS the main-agent indicator.
    Only ``[runtime]`` and ``[subagent:*]`` are emitted explicitly.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            from runtime.scope import current_scope, MAIN
        except Exception:
            return True
        scope = current_scope()
        if scope and scope != MAIN:
            record.msg = f"[{scope}] {record.msg}"
        return True


class ColoredFormatter(logging.Formatter):
    """ANSI-colored formatter for TTY output.

    Colorizes the level name. Message content is left to callers
    who can use the tag helpers (council_tag, user_tag, etc.) to
    colorize specific parts of their log lines.
    """

    def format(self, record: logging.LogRecord) -> str:
        color = _LEVEL_COLORS.get(record.levelname, "")
        record = logging.makeLogRecord(record.__dict__)
        record.levelname = f"{color}{record.levelname}{_RESET}"
        return super().format(record)


# ── Session banner ────────────────────────────────────────────────────────────

def _log_session_banner(logger_instance: logging.Logger, session_id: str, label: str, extra_lines: list[str] | None = None) -> None:
    """Write a formatted session banner to the log."""
    w = 56
    logger_instance.info("=" * w)
    logger_instance.info(f"  {label}")
    logger_instance.info(f"  Session ID : {session_id}")
    if extra_lines:
        for line in extra_lines:
            logger_instance.info(f"  {line}")
    logger_instance.info("=" * w)


# ── Configuration ─────────────────────────────────────────────────────────────

def configure_logging(session_id: str, verbose: bool = False) -> None:
    from runtime.council_metrics import init_metrics_writer
    path = _log_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    plain_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    # Open with buffering=1 (line-buffered) so each log line is immediately
    # visible to file watchers without an explicit flush or fsync.
    log_stream = open(path, "a", buffering=1, encoding="utf-8")
    file_handler = logging.StreamHandler(log_stream)
    file_handler.setFormatter(plain_formatter)
    file_handler.addFilter(_StripANSIFilter())
    # 0090c — prefix log records with the active scope tag so main/runtime/
    # subagent work is visually distinguishable in session.log.
    file_handler.addFilter(_ScopeTagFilter())
    root.addHandler(file_handler)

    if verbose:
        stream_handler = logging.StreamHandler(sys.stdout)
        if _is_tty():
            stream_handler.setFormatter(ColoredFormatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
            ))
        else:
            stream_handler.setFormatter(plain_formatter)
        stream_handler.addFilter(_ScopeTagFilter())
        root.addHandler(stream_handler)

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    # Initialize council metrics writer for this session
    init_metrics_writer(session_id)

    # Build provider info for the banner — read from config.yml (app_config), not env vars.
    from app_config import config as _cfg
    provider = _cfg.llm.provider
    if provider == "ollama":
        from settings import settings as _s
        provider_line = f"Provider   : ollama ({_s.ollama_model})"
    else:
        rt_provider = _cfg.llm.runtime_provider or provider
        rt_model = _cfg.llm.runtime_model or "(default)"
        provider_line = f"Provider   : {provider} / {_cfg.llm.model}  |  Runtime: {rt_provider} / {rt_model}"

    _log_session_banner(logging.getLogger("main"), session_id, "Session Started", [provider_line])


def log_session_end(session_id: str) -> None:
    """Write the session end banner to the log."""
    _log_session_banner(logging.getLogger("main"), session_id, "Session Ended")


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
