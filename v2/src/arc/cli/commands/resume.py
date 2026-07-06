"""`arc resume` — continue a recorded session in a new session (modes 1, 4)."""
from __future__ import annotations

import sys

from arc.cli.wiring import _load_dotenv_into_environ, _make_subagent_registry, build_session


def _cmd_resume(
    home_override: str | None,
    *,
    session_id: str,
    prompt: str | None,
    no_tui: bool,
    at_turn: int | None = None,
) -> int:
    """Resume a session — load conversation, start a new session, continue.

    The new session is marked `resumed_from: <original>` in meta. Sessions
    can be resumed regardless of whether they were paused or completed —
    this doubles as "continue an old conversation."

    `at_turn` is the mode 4 (branch) knob: restore only the first N turns.
    None = restore everything (regular resume).
    """
    _load_dotenv_into_environ(home_override)

    from arc.bootstrap import paths_for, resolve_home
    from arc.config import load
    from arc.providers import build as build_provider
    from arc.resume import count_completed_turns, messages_from_session
    from arc.tools import build as build_tools

    home = resolve_home(home_override)
    paths = paths_for(home)
    source_dir = paths.sessions_dir / session_id

    if not source_dir.is_dir():
        print(f"resume: session not found: {session_id}", file=sys.stderr)
        print(f"  expected: {source_dir}", file=sys.stderr)
        return 1

    # Validate / clamp --at-turn
    effective_at_turn: int | None = at_turn
    if at_turn is not None:
        total = count_completed_turns(source_dir)
        if at_turn < 0:
            print("resume: --at-turn must be >= 0", file=sys.stderr)
            return 1
        if at_turn > total:
            print(
                f"resume: --at-turn {at_turn} > available turns ({total}); "
                f"clamping to {total}",
                file=sys.stderr,
            )
            effective_at_turn = total
        if at_turn == 0:
            print(
                "resume: --at-turn 0 → no messages restored (fresh session)",
                file=sys.stderr,
            )

    try:
        prior_messages = messages_from_session(
            source_dir, max_turns=effective_at_turn,
        )
    except FileNotFoundError as e:
        print(f"resume: {e}", file=sys.stderr)
        return 1

    cfg = load(paths.config_file)

    from arc.user_gate import NoOpGate
    built_session = build_session(
        cfg, paths,
        provider=build_provider(cfg.provider),
        tools=build_tools(cfg.tools),
        subagent_registry=_make_subagent_registry(cfg, home),
        gate=NoOpGate(),
        initial_messages=prior_messages,
    )
    sess = built_session.session
    plugins = built_session.plugins
    new_sid = built_session.session_id

    branch_note = ""
    if at_turn is not None:
        branch_note = f"  (branch @ turn {effective_at_turn})"
    print(f"resuming {session_id} → {new_sid}{branch_note}  "
          f"({len(prior_messages)} messages restored)")

    def _mark_resume_meta():
        """Stamp lineage on the new session's meta — after sess.end(), see
        stamp_session_meta."""
        from arc.cli.wiring import stamp_session_meta
        fields = {
            "resumed_from": session_id,
            "restored_message_count": len(prior_messages),
        }
        if at_turn is not None:
            fields["branched_at_turn"] = effective_at_turn
        stamp_session_meta(paths.sessions_dir, new_sid, fields)

    # Headless one-shot
    if prompt is not None:
        try:
            sess.start()
            outcome = sess.run_turn(prompt)
        finally:
            sess.end()
            _mark_resume_meta()
        print(outcome.final_response)
        return 0 if outcome.success else 1

    # No prompt, --no-tui: restore-only
    if no_tui:
        try:
            sess.start()
        finally:
            sess.end()
            _mark_resume_meta()
        print("session restored. exit (--no-tui set, no --prompt given).")
        return 0

    # Interactive continuation via TUI (TUIApp.run handles start/end itself)
    from rich.console import Console

    from arc.tui.app import TUIApp
    from arc.user_gate import TUIGate
    console = Console()
    # Upgrade the guard's gate from NoOp to TUI now that we know it's interactive
    for built in plugins:
        if built.name == "guard":
            built.instance._gate = TUIGate(console=console)
    app = TUIApp(cfg, sess, home_display=str(home), console=console, paths=paths)
    try:
        return app.run()
    finally:
        _mark_resume_meta()
