import logging
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


def configure_logging(session_id: str, verbose: bool = False) -> None:
    LOGS_DIR.mkdir(exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    file_handler = logging.FileHandler(LOGS_DIR / f"{session_id}.log")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    if verbose:
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

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
