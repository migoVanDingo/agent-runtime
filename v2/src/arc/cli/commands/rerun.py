"""`arc rerun` — re-run a recorded session's user inputs against a fresh agent (mode 5)."""
from __future__ import annotations

import sys

from arc.cli.wiring import _load_dotenv_into_environ, _make_subagent_registry, build_session


def _cmd_rerun(
    home_override: str | None,
    *,
    session_id: str,
    stop_on_error: bool,
) -> int:
    """Rerun (mode 5): replay just the user inputs against a fresh agent.

    Live LLM + live tools — actually does the work again. New session is
    marked `rerun_of: <original>` in meta. Useful as a scenario regression
    test ("does this still pass with my current config?").
    """
    _load_dotenv_into_environ(home_override)

    from arc.bootstrap import paths_for, resolve_home
    from arc.config import load
    from arc.providers import build as build_provider
    from arc.rerun import user_inputs_from_session
    from arc.tools import build as build_tools
    from arc.user_gate import NoOpGate

    home = resolve_home(home_override)
    paths = paths_for(home)
    source_dir = paths.sessions_dir / session_id

    if not source_dir.is_dir():
        print(f"rerun: session not found: {session_id}", file=sys.stderr)
        return 1

    try:
        inputs = user_inputs_from_session(source_dir)
    except FileNotFoundError as e:
        print(f"rerun: {e}", file=sys.stderr)
        return 1

    if not inputs:
        print("rerun: source session has no user inputs", file=sys.stderr)
        return 1

    cfg = load(paths.config_file)
    built_session = build_session(
        cfg, paths,
        provider=build_provider(cfg.provider),
        tools=build_tools(cfg.tools),
        subagent_registry=_make_subagent_registry(cfg, home),
        gate=NoOpGate(),
    )
    sess = built_session.session
    new_sid = built_session.session_id

    print(f"rerunning {session_id} → {new_sid}  "
          f"({len(inputs)} user input(s))")

    n_ok = 0
    n_failed = 0
    try:
        sess.start()
        for i, user_text in enumerate(inputs, start=1):
            print(f"\n[rerun turn {i}/{len(inputs)}]  {user_text[:80]}"
                  f"{'...' if len(user_text) > 80 else ''}")
            outcome = sess.run_turn(user_text)
            if outcome.success:
                n_ok += 1
                if outcome.final_response:
                    print(outcome.final_response)
            else:
                n_failed += 1
                print(f"[turn failed: {outcome.error}]", file=sys.stderr)
                if stop_on_error:
                    print("rerun: stopping after first failure (--stop-on-error)",
                          file=sys.stderr)
                    break
    finally:
        sess.end()
        # Mark rerun_of on meta AFTER end() (same race fix as resume)
        meta_path = paths.sessions_dir / new_sid / "meta.json"
        if meta_path.exists():
            import json
            meta = json.loads(meta_path.read_text())
            meta["rerun_of"] = session_id
            meta["rerun_turns_attempted"] = n_ok + n_failed
            meta["rerun_turns_succeeded"] = n_ok
            meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False))

    print(f"\nrerun complete: {n_ok} ok, {n_failed} failed, "
          f"{len(inputs) - n_ok - n_failed} skipped")
    return 0 if n_failed == 0 else 1
