"""CLI resume picker — interactive session selection for --resume."""
from datetime import datetime

from runtime.artifact_store import ResumableSession


def fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%b %d %H:%M")


def pick_resume_session(options: list[ResumableSession]) -> str | None:
    if not options:
        print("No resumable sessions found.")
        return None

    print("\nResumable sessions:")
    for i, s in enumerate(options, start=1):
        print(
            f"{i}) {fmt_ts(s.started_at)}  |  \"{s.preview}\"  | artifacts: {s.artifact_count}"
        )
    print("")

    retries = 3
    while retries > 0:
        raw = input(f"Select session to resume [1-{len(options)}] (Enter=1, q=cancel): ").strip().lower()
        if raw == "":
            return options[0].session_id
        if raw == "q":
            return None
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(options):
                return options[idx - 1].session_id
        retries -= 1
        print("Invalid selection.")

    print("Too many invalid selections.")
    return None
