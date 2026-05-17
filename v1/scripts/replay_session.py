"""Replay a historical session's user inputs against a different model.

Reads ``~/.arc/sessions/<source_id>/events/runtime.jsonl``, extracts the
sequence of user messages from ``conversation.message.added`` events, then
runs them through a fresh ``agent.Agent`` configured for the target model.

The new session emits its own event log; analysts can join the two by
``model_run_id`` (set on the new session via ``set_model_run_id``).

Caveats:
- Tools execute for real. State-changing tools (file_io write, bash_exec)
  will run again. Point ``--workspace`` at a sandbox.
- Time-dependent tools (web search, news APIs) will produce different
  results than the source run.
- v1 source logs are supported via ``legacy_v1_to_v2_view``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from runtime.events import legacy_v1_to_v2_view  # noqa: E402
from runtime.identity import new_id  # noqa: E402
from session_paths import session_dir  # noqa: E402


def load_user_messages(source_session_id: str) -> list[str]:
    log = session_dir(source_session_id) / "events" / "runtime.jsonl"
    if not log.exists():
        raise FileNotFoundError(f"no event log at {log}")
    msgs: list[str] = []
    with open(log, "r", encoding="utf-8") as f:
        for line in f:
            try:
                ev = json.loads(line)
            except Exception:
                continue
            ev = legacy_v1_to_v2_view(ev)
            if ev.get("event_type") != "conversation.message.added":
                continue
            payload = ev.get("payload") or {}
            content = ev.get("content") or {}
            role = payload.get("role") or content.get("role")
            if role != "user":
                continue
            body = content.get("content")
            if isinstance(body, str) and body.strip():
                msgs.append(body)
    return msgs


def replay(
    source_session: str,
    target_model: str,
    target_provider: str,
    workspace: str | None = None,
) -> str:
    """Run a fresh agent on the source session's user inputs."""
    from app_config import config
    from agent import Agent
    from runtime.events import init_runtime_events, set_model_run_id
    from utils import generate_id

    config.llm.model = target_model
    config.llm.provider = target_provider
    if workspace:
        config.runtime.sandbox.workspace_root = workspace

    new_sid = generate_id("session")
    init_runtime_events(new_sid)
    set_model_run_id(new_id("MRUN"))

    user_msgs = load_user_messages(source_session)
    if not user_msgs:
        raise SystemExit(f"no user messages found in session {source_session}")

    agent = Agent()
    for msg in user_msgs:
        agent.call(msg)
    return new_sid


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", required=True, help="source session ID")
    ap.add_argument("--model", required=True, help="target model name")
    ap.add_argument("--provider", required=True, help="target provider name (anthropic/openai/...)")
    ap.add_argument("--workspace", default=None, help="sandbox workspace root (recommended)")
    args = ap.parse_args()
    new_sid = replay(args.source, args.model, args.provider, args.workspace)
    print(f"Replayed → session {new_sid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
