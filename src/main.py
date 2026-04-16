import sys
import os
import argparse

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
from pathlib import Path
from utils import generate_id
from logger import configure_logging, log_session_end, LOGS_DIR, get_logger
from agent import Agent

logger = get_logger(__name__)


def print_session_banner(session_id: str) -> None:
    log_path = LOGS_DIR / f"{session_id}.log"
    width = 52
    print("\n" + "─" * width)
    print(f"  Agent Session Started")
    print(f"  Session ID : {session_id}")
    print(f"  Log file   : {log_path}")
    print("─" * width + "\n")


def print_session_end(session_id: str) -> None:
    print(f"\n{'─' * 52}")
    print(f"  Session ended  |  ID: {session_id}")
    print(f"{'─' * 52}\n")


def main():
    parser = argparse.ArgumentParser(description="Raw Tool Agent")
    parser.add_argument("--verbose", action="store_true", help="Stream logs to console")
    args = parser.parse_args()

    session_id = generate_id("session")
    configure_logging(session_id, verbose=args.verbose)

    print_session_banner(session_id)
    print("Type 'exit' or 'quit' to end the session.\n")

    agent = Agent(verbose=args.verbose)

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print_session_end(session_id)
            log_session_end(session_id)
            sys.exit(0)

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit"):
            print_session_end(session_id)
            log_session_end(session_id)
            sys.exit(0)

        response = agent.call(user_input)
        print(f"\nAgent: {response}\n")


if __name__ == "__main__":
    main()
