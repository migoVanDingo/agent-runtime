import logging
import os
import re
import sys
from pathlib import Path

LOGS_DIR = Path(__file__).resolve().parent.parent / "_logs"


_NOISY_LOGGERS = [
    "httpx",
    "httpcore",
    "sentence_transformers",
    "huggingface_hub",
    "transformers",
    "torch",
]


# ── ANSI color codes ─────────────────────────────────────────────────────────

_RESET      = "\033[0m"
_BOLD       = "\033[1m"
_DIM        = "\033[2m"

# Source-class colors
_COLOR_USER       = "\033[96m"   # bright cyan
_COLOR_ASSISTANT  = "\033[92m"   # bright green
_COLOR_RUNTIME    = "\033[2m"    # dim (system noise)
_COLOR_ERROR      = "\033[91m"   # bright red
_COLOR_COUNCIL    = "\033[93m"   # bright yellow  (council banners/headers)
_COLOR_SYNTHESIS  = "\033[1m"    # bold            (final consensus)
_COLOR_ESCALATE   = "\033[33m"   # yellow

# Per-councillor palette — assigned by label, consistent for the process lifetime
_COUNCILLOR_PALETTE = [
    "\033[34m",   # blue
    "\033[35m",   # magenta
    "\033[33m",   # yellow
    "\033[36m",   # cyan
    "\033[32m",   # green
]
_councillor_color_map: dict[str, str] = {}


def get_councillor_color(label: str) -> str:
    """Return a consistent ANSI color for a councillor label.

    Colors are assigned by order of first encounter and persist for the
    process lifetime. Returns empty string if stdout is not a TTY.
    """
    if not _is_tty():
        return ""
    if label not in _councillor_color_map:
        idx = len(_councillor_color_map) % len(_COUNCILLOR_PALETTE)
        _councillor_color_map[label] = _COUNCILLOR_PALETTE[idx]
    return _councillor_color_map[label]


def _is_tty() -> bool:
    """Return True if stdout is a real terminal and NO_COLOR is not set."""
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def council_tag(label: str) -> str:
    """Return a colored [council][label] prefix for log messages."""
    if not _is_tty():
        return f"[council][{label}]"
    color = get_councillor_color(label)
    return f"{_COLOR_COUNCIL}[council]{_RESET}{color}[{label}]{_RESET}"


def council_header_tag() -> str:
    """Return a colored [council] prefix for council-level log messages."""
    if not _is_tty():
        return "[council]"
    return f"{_COLOR_COUNCIL}[council]{_RESET}"


def synth_tag() -> str:
    if not _is_tty():
        return "[synth]"
    return f"{_COLOR_SYNTHESIS}[synth]{_RESET}"


def user_tag() -> str:
    if not _is_tty():
        return "[user]"
    return f"{_COLOR_USER}[user]{_RESET}"


def assistant_tag() -> str:
    if not _is_tty():
        return "[assistant]"
    return f"{_COLOR_ASSISTANT}[assistant]{_RESET}"


def escalate_tag() -> str:
    if not _is_tty():
        return "[escalate]"
    return f"{_COLOR_ESCALATE}[escalate]{_RESET}"


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
    LOGS_DIR.mkdir(exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    plain_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    file_handler = logging.FileHandler(LOGS_DIR / f"{session_id}.log")
    file_handler.setFormatter(plain_formatter)
    file_handler.addFilter(_StripANSIFilter())
    root.addHandler(file_handler)

    if verbose:
        stream_handler = logging.StreamHandler(sys.stdout)
        if _is_tty():
            stream_handler.setFormatter(ColoredFormatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
            ))
        else:
            stream_handler.setFormatter(plain_formatter)
        root.addHandler(stream_handler)

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    # Initialize council metrics writer for this session
    init_metrics_writer(session_id)

    # Build provider info for the banner
    from settings import settings
    provider = settings.llm_provider
    if provider == "ollama":
        provider_line = f"Provider   : ollama ({settings.ollama_model})"
    else:
        rt_provider = settings.runtime_provider or provider
        rt_model = settings.runtime_model or "(default)"
        provider_line = f"Provider   : {provider}  |  Runtime: {rt_provider} ({rt_model})"

    _log_session_banner(logging.getLogger("main"), session_id, "Session Started", [provider_line])


def log_session_end(session_id: str) -> None:
    """Write the session end banner to the log."""
    _log_session_banner(logging.getLogger("main"), session_id, "Session Ended")


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
