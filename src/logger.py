import logging
import sys
from pathlib import Path

LOGS_DIR = Path(__file__).resolve().parent.parent / "_logs"


def configure_logging(session_id: str, verbose: bool = False) -> None:
    LOGS_DIR.mkdir(exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    formatter = logging.Formatter(
        f"%(asctime)s [{session_id}] [%(levelname)s] %(name)s: %(message)s"
    )

    file_handler = logging.FileHandler(LOGS_DIR / f"{session_id}.log")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    if verbose:
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
