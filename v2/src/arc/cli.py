"""`arc` CLI entry point.

Subcommands (per design §9.1):

  arc                  start interactive TUI session (added in phase 1 task #60)
  arc bootstrap        create ARC_HOME + default config
  arc bootstrap --force  overwrite existing config
  arc run "<prompt>"   one-shot, non-interactive turn; prints final reply
  arc replay <id>      (phase 2.0.5)
  arc sessions         list known sessions
  arc show <id>        pretty-print a recorded session
  arc config show      print resolved config
  arc config path      print config file path
  arc --home <path>    override ARC_HOME resolution (works with all subcommands)
  arc --version
  arc --help

Phase 1 implements all of these EXCEPT `replay` and the interactive TUI
(which lands in task #60). `arc` with no subcommand currently runs a one-shot
turn from stdin if stdin is not a TTY, or prints a message directing users to
`arc run` until the TUI is wired in.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from arc import __version__


# ── Top-level entry point ──────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # --home applies to every subcommand
    home_override: str | None = getattr(args, "home", None)

    # Load .env BEFORE any subcommand runs, so things like ARC_HOME in
    # the user's .env are picked up by resolve_home() consistently.
    # Individual subcommands used to call this themselves; centralizing
    # here means even read-only commands (config, sessions, show) get
    # consistent env resolution.
    _load_dotenv_into_environ(home_override)

    # Dispatch
    if args.command == "bootstrap":
        return _cmd_bootstrap(home_override, force=args.force)
    if args.command == "run":
        return _cmd_run(home_override, prompt=args.prompt)
    if args.command == "sessions":
        return _cmd_sessions(home_override)
    if args.command == "show":
        return _cmd_show(home_override, session_id=args.session_id)
    if args.command == "replay":
        return _cmd_replay(
            home_override,
            session_id=args.session_id,
            live_llm=args.live_llm,
            do_diff=not args.no_diff,
        )
    if args.command == "resume":
        return _cmd_resume(
            home_override,
            session_id=args.session_id,
            prompt=args.prompt,
            no_tui=args.no_tui,
            at_turn=args.at_turn,
        )
    if args.command == "rerun":
        return _cmd_rerun(
            home_override,
            session_id=args.session_id,
            stop_on_error=args.stop_on_error,
        )
    if args.command == "config":
        if args.config_action == "show":
            return _cmd_config_show(home_override)
        if args.config_action == "path":
            return _cmd_config_path(home_override)
        parser.error(f"unknown config action: {args.config_action}")
    if args.command is None:
        return _cmd_interactive(home_override)

    parser.error(f"unknown command: {args.command}")
    return 2  # unreachable; parser.error exits


# ── Parser ─────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="arc", description="arc — agent runtime")
    p.add_argument("--version", action="version", version=f"arc {__version__}")
    p.add_argument(
        "--home",
        metavar="PATH",
        help="override ARC_HOME resolution with an explicit path",
    )

    sub = p.add_subparsers(dest="command")

    boot = sub.add_parser("bootstrap", help="create ARC_HOME + default config")
    boot.add_argument(
        "--force", action="store_true",
        help="overwrite an existing config.yml (sessions are untouched)",
    )

    run = sub.add_parser("run", help="one-shot, non-interactive turn")
    run.add_argument("prompt", help="the user message to send (in quotes)")

    sub.add_parser("sessions", help="list known sessions")

    show = sub.add_parser("show", help="pretty-print a recorded session")
    show.add_argument("session_id", help="session id (e.g., SES01HXYZ...)")

    replay = sub.add_parser("replay", help="replay a recorded session")
    replay.add_argument("session_id", help="session id to replay")
    replay.add_argument(
        "--live-llm", action="store_true",
        help="mode 3: call the LLM live, stub only the tools "
             "(use to test prompt/model changes against a recorded scenario)",
    )
    replay.add_argument(
        "--no-diff", action="store_true",
        help="don't compare against the original; just run the replay",
    )

    resume = sub.add_parser(
        "resume",
        help="continue a recorded (paused or completed) session in a new session",
    )
    resume.add_argument("session_id", help="session id to resume from")
    resume.add_argument(
        "--prompt", default=None,
        help="next user turn to run immediately (headless). "
             "Omit to drop into interactive mode.",
    )
    resume.add_argument(
        "--no-tui", action="store_true",
        help="if --prompt is omitted, exit after restore instead of starting TUI",
    )
    resume.add_argument(
        "--at-turn", type=int, default=None, metavar="N",
        help="branch: restore only the first N turns instead of all of them "
             "(mode 4 from the replay catalog)",
    )

    rerun = sub.add_parser(
        "rerun",
        help="re-run a recorded session's user inputs against a fresh agent "
             "(live LLM + live tools — mode 5)",
    )
    rerun.add_argument("session_id", help="session id whose user inputs to replay")
    rerun.add_argument(
        "--stop-on-error", action="store_true",
        help="bail on the first turn that fails (default: continue through all)",
    )

    cfg = sub.add_parser("config", help="inspect resolved configuration")
    cfg_sub = cfg.add_subparsers(dest="config_action", required=True)
    cfg_sub.add_parser("show", help="print resolved config")
    cfg_sub.add_parser("path", help="print resolved config file path")

    return p


# ── Subcommand impls ───────────────────────────────────────────────────────


def _cmd_bootstrap(home_override: str | None, *, force: bool) -> int:
    from arc.bootstrap import bootstrap, format_bootstrap_summary, resolve_home
    home = resolve_home(home_override)
    result = bootstrap(home, force_config=force)
    print(format_bootstrap_summary(result))
    return 0


def _cmd_config_path(home_override: str | None) -> int:
    from arc.bootstrap import paths_for, resolve_home
    p = paths_for(resolve_home(home_override))
    print(p.config_file)
    return 0 if p.config_file.exists() else 1


def _cmd_config_show(home_override: str | None) -> int:
    """Print the resolved config (as YAML) for debugging."""
    from arc.bootstrap import paths_for, resolve_home
    from arc.config import load
    p = paths_for(resolve_home(home_override))
    if not p.config_file.exists():
        print(f"no config at {p.config_file}", file=sys.stderr)
        print(f"run `arc bootstrap` to create one", file=sys.stderr)
        return 1
    # Just print the raw file contents — it's the source of truth
    print(p.config_file.read_text(), end="")
    return 0


def _cmd_sessions(home_override: str | None) -> int:
    """List recorded sessions from sessions/index.jsonl."""
    from arc.bootstrap import paths_for, resolve_home
    p = paths_for(resolve_home(home_override))
    if not p.sessions_index.exists():
        print("no sessions recorded yet", file=sys.stderr)
        return 0

    rows = []
    for line in p.sessions_index.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if not rows:
        print("no sessions recorded yet", file=sys.stderr)
        return 0

    # Simple aligned columns
    print(f"{'session_id':32}  {'started_at':28}  {'provider':10}  {'model':40}")
    for r in rows:
        print(f"{r.get('session_id', '?'):32}  "
              f"{r.get('started_at', '?')[:26]:28}  "
              f"{r.get('provider', '?'):10}  "
              f"{r.get('model', '?'):40}")
    return 0


def _cmd_show(home_override: str | None, *, session_id: str) -> int:
    """Render a recorded session as human-readable text (from canonical events)."""
    from arc.bootstrap import paths_for, resolve_home
    p = paths_for(resolve_home(home_override))
    session_dir = p.sessions_dir / session_id
    events_file = session_dir / "events.jsonl"
    if not events_file.exists():
        print(f"no events for session {session_id!r} at {events_file}", file=sys.stderr)
        return 1

    for line in events_file.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = e.get("ts", "")[11:23]  # HH:MM:SS.mmm
        typ = e.get("type", "?")
        stage = e.get("stage", "")
        scope = e.get("scope", "main")
        scope_tag = "" if scope == "main" else f" [{scope}]"
        print(f"{ts}  {typ:30}  {stage}{scope_tag}")
    return 0


def _cmd_run(home_override: str | None, *, prompt: str) -> int:
    """One-shot turn. Bootstraps if needed, loads config, runs, prints reply."""
    _load_dotenv_into_environ(home_override)

    from arc.bootstrap import bootstrap, paths_for, resolve_home
    from arc.config import load
    from arc.plugins import PluginBuildContext, build as build_plugins
    from arc.providers import build as build_provider
    from arc.runtime.bus import EventBus, HookRegistry
    from arc.runtime.ids import new_session_id
    from arc.runtime.loop import AgentSession
    from arc.tools import build as build_tools

    home = resolve_home(home_override)
    bootstrap(home)  # idempotent — creates layout on first run
    paths = paths_for(home)

    cfg = load(paths.config_file)

    # Wire everything together
    provider = build_provider(cfg.provider)
    tools = build_tools(cfg.tools)
    registry = HookRegistry(
        failure_threshold=cfg.plugins.failure_threshold,
        exception_message_max_chars=cfg.plugins.exception_message_max_chars,
    )
    bus = EventBus(registry)

    session_id = new_session_id()
    config_snapshot_yaml = paths.config_file.read_text()

    # Headless mode: any guard escalation auto-denies. The NoOpGate logs
    # to stderr so the user can see why a tool was blocked.
    from arc.user_gate import NoOpGate
    gate = NoOpGate()

    plugins = build_plugins(cfg.plugins, PluginBuildContext(
        sessions_dir=paths.sessions_dir,
        session_id=session_id,
        config_snapshot_yaml=config_snapshot_yaml,
        user_gate=gate,
    ))
    for built in plugins:
        registry.register(built.instance, hooks_order=built.hooks_order)

    sess = AgentSession(
        config=cfg, provider=provider, tools=tools,
        registry=registry, bus=bus, session_id=session_id,
    )

    try:
        sess.start()
        outcome = sess.run_turn(prompt)
        print(outcome.final_response)
        return 0 if outcome.success else 1
    finally:
        sess.end()


def _cmd_replay(
    home_override: str | None,
    *,
    session_id: str,
    live_llm: bool,
    do_diff: bool,
) -> int:
    """Replay a recorded session. Mode 2 (default) or mode 3 (--live-llm).

    Writes a NEW session dir for the replay (so the original is untouched
    and the diff layer has something to compare against). Returns 0 on
    match (or on success-without-diff), 1 on divergence or error.
    """
    _load_dotenv_into_environ(home_override)

    from arc.bootstrap import bootstrap, paths_for, resolve_home
    from arc.config import load
    from arc.plugins import PluginBuildContext, build as build_plugins
    from arc.providers import build as build_provider
    from arc.replay import (
        MissingRecordingError,
        ReplayDivergenceError,
        ReplayProvider,
        ReplayingToolRegistry,
        diff_event_logs,
        load as load_replay,
    )
    from arc.runtime.bus import EventBus, HookRegistry
    from arc.runtime.ids import new_session_id
    from arc.runtime.loop import AgentSession

    home = resolve_home(home_override)
    paths = paths_for(home)
    source_dir = paths.sessions_dir / session_id

    try:
        replay_data = load_replay(source_dir)
    except MissingRecordingError as e:
        print(f"replay: {e}", file=sys.stderr)
        return 1

    # Use the current config (so the user can edit it between recording
    # and replay to test changes). The snapshot is informational only.
    cfg = load(paths.config_file)

    # Tool registry comes from the recording, not the config — the recording
    # tells us what tool names were called, with what inputs/outputs.
    mode = "by_call" if live_llm else "in_order"
    tools = ReplayingToolRegistry(replay_data, mode=mode)

    # Provider: stubbed (mode 2) or real (mode 3)
    if live_llm:
        provider = build_provider(cfg.provider)
    else:
        provider = ReplayProvider(replay_data.llm_responses)

    registry = HookRegistry(
        failure_threshold=cfg.plugins.failure_threshold,
        exception_message_max_chars=cfg.plugins.exception_message_max_chars,
    )
    bus = EventBus(registry)

    new_session_id_ = new_session_id()
    plugins = build_plugins(cfg.plugins, PluginBuildContext(
        sessions_dir=paths.sessions_dir,
        session_id=new_session_id_,
        config_snapshot_yaml=paths.config_file.read_text(),
    ))
    for built in plugins:
        registry.register(built.instance, hooks_order=built.hooks_order)

    sess = AgentSession(
        config=cfg, provider=provider, tools=tools,
        registry=registry, bus=bus, session_id=new_session_id_,
    )

    print(f"replaying {session_id} → {new_session_id_}  "
          f"(mode {'3 (live LLM)' if live_llm else '2 (strict)'})")

    diverged = False
    try:
        sess.start()
        for user_input in replay_data.user_inputs:
            sess.run_turn(user_input)
    except ReplayDivergenceError as e:
        print(f"\nREPLAY DIVERGED:\n  {e}\n", file=sys.stderr)
        diverged = True
    finally:
        sess.end()

    # Mark the new session as a replay
    new_meta_path = paths.sessions_dir / new_session_id_ / "meta.json"
    if new_meta_path.exists():
        import json
        meta = json.loads(new_meta_path.read_text())
        meta["replay_of"] = session_id
        meta["replay_mode"] = "by_call" if live_llm else "in_order"
        new_meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False))

    if not do_diff:
        return 1 if diverged else 0

    # Run the diff
    result = diff_event_logs(
        original=source_dir / "events.jsonl",
        replayed=paths.sessions_dir / new_session_id_ / "events.jsonl",
    )

    if result.matched and not diverged:
        print(f"replay matches ({result.n_events_a} events)")
        return 0

    print(f"\nreplay DIVERGED ({result.n_events_a} original vs "
          f"{result.n_events_b} replayed events)", file=sys.stderr)
    if result.first_divergence_index is not None:
        print(f"first divergence at event index "
              f"{result.first_divergence_index}", file=sys.stderr)
    print(file=sys.stderr)
    print(result.unified_diff, file=sys.stderr)
    return 1


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

    from arc.bootstrap import bootstrap, paths_for, resolve_home
    from arc.config import load
    from arc.plugins import PluginBuildContext, build as build_plugins
    from arc.providers import build as build_provider
    from arc.resume import count_completed_turns, messages_from_session
    from arc.runtime.bus import EventBus, HookRegistry
    from arc.runtime.ids import new_session_id
    from arc.runtime.loop import AgentSession
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
            print(f"resume: --at-turn must be >= 0", file=sys.stderr)
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

    provider = build_provider(cfg.provider)
    tools = build_tools(cfg.tools)
    registry = HookRegistry(
        failure_threshold=cfg.plugins.failure_threshold,
        exception_message_max_chars=cfg.plugins.exception_message_max_chars,
    )
    bus = EventBus(registry)

    new_sid = new_session_id()
    from arc.user_gate import NoOpGate
    gate = NoOpGate()
    plugins = build_plugins(cfg.plugins, PluginBuildContext(
        sessions_dir=paths.sessions_dir,
        session_id=new_sid,
        config_snapshot_yaml=paths.config_file.read_text(),
        user_gate=gate,
    ))
    for built in plugins:
        registry.register(built.instance, hooks_order=built.hooks_order)

    sess = AgentSession(
        config=cfg, provider=provider, tools=tools,
        registry=registry, bus=bus, session_id=new_sid,
        initial_messages=prior_messages,
    )

    branch_note = ""
    if at_turn is not None:
        branch_note = f"  (branch @ turn {effective_at_turn})"
    print(f"resuming {session_id} → {new_sid}{branch_note}  "
          f"({len(prior_messages)} messages restored)")

    def _mark_resume_meta():
        """Stamp resumed_from + restored_message_count on the new session's meta.

        Run AFTER sess.end() so the recorder's own on_session_end write doesn't
        clobber our additions. The recorder writes meta from its own dict and
        doesn't merge what's on disk.
        """
        meta_path = paths.sessions_dir / new_sid / "meta.json"
        if meta_path.exists():
            import json
            meta = json.loads(meta_path.read_text())
            meta["resumed_from"] = session_id
            meta["restored_message_count"] = len(prior_messages)
            if at_turn is not None:
                meta["branched_at_turn"] = effective_at_turn
            meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False))

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
    app = TUIApp(cfg, sess, home_display=str(home), console=console)
    try:
        return app.run()
    finally:
        _mark_resume_meta()


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

    from arc.bootstrap import bootstrap, paths_for, resolve_home
    from arc.config import load
    from arc.plugins import PluginBuildContext, build as build_plugins
    from arc.providers import build as build_provider
    from arc.rerun import user_inputs_from_session
    from arc.runtime.bus import EventBus, HookRegistry
    from arc.runtime.ids import new_session_id
    from arc.runtime.loop import AgentSession
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
        print(f"rerun: source session has no user inputs", file=sys.stderr)
        return 1

    cfg = load(paths.config_file)
    provider = build_provider(cfg.provider)
    tools = build_tools(cfg.tools)
    registry = HookRegistry(
        failure_threshold=cfg.plugins.failure_threshold,
        exception_message_max_chars=cfg.plugins.exception_message_max_chars,
    )
    bus = EventBus(registry)

    new_sid = new_session_id()
    plugins = build_plugins(cfg.plugins, PluginBuildContext(
        sessions_dir=paths.sessions_dir,
        session_id=new_sid,
        config_snapshot_yaml=paths.config_file.read_text(),
        user_gate=NoOpGate(),
    ))
    for built in plugins:
        registry.register(built.instance, hooks_order=built.hooks_order)

    sess = AgentSession(
        config=cfg, provider=provider, tools=tools,
        registry=registry, bus=bus, session_id=new_sid,
    )

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
                    print(f"rerun: stopping after first failure (--stop-on-error)",
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


def _cmd_interactive(home_override: str | None) -> int:
    """Interactive session — the inline TUI."""
    _load_dotenv_into_environ(home_override)

    from arc.bootstrap import bootstrap, paths_for, resolve_home
    from arc.config import load
    from arc.plugins import PluginBuildContext, build as build_plugins
    from arc.providers import build as build_provider
    from arc.runtime.bus import EventBus, HookRegistry
    from arc.runtime.ids import new_session_id
    from arc.runtime.loop import AgentSession
    from arc.tools import build as build_tools
    from arc.tui.app import TUIApp

    home = resolve_home(home_override)
    bootstrap(home)
    paths = paths_for(home)
    cfg = load(paths.config_file)

    provider = build_provider(cfg.provider)
    tools = build_tools(cfg.tools)
    registry = HookRegistry(
        failure_threshold=cfg.plugins.failure_threshold,
        exception_message_max_chars=cfg.plugins.exception_message_max_chars,
    )
    bus = EventBus(registry)

    session_id = new_session_id()

    # Interactive mode: TUIGate prompts the user via prompt_toolkit when
    # a tool trips an escalation pattern. Construct the gate with a shared
    # console so escalation prompts use the same render pipeline as the rest.
    from rich.console import Console
    from arc.user_gate import TUIGate
    console = Console()
    gate = TUIGate(console=console)

    plugins = build_plugins(cfg.plugins, PluginBuildContext(
        sessions_dir=paths.sessions_dir,
        session_id=session_id,
        config_snapshot_yaml=paths.config_file.read_text(),
        user_gate=gate,
    ))
    for built in plugins:
        registry.register(built.instance, hooks_order=built.hooks_order)

    sess = AgentSession(
        config=cfg, provider=provider, tools=tools,
        registry=registry, bus=bus, session_id=session_id,
    )
    app = TUIApp(cfg, sess, home_display=str(home), console=console)
    return app.run()


# ── .env loading ───────────────────────────────────────────────────────────


def _load_dotenv_into_environ(home_override: str | None) -> None:
    """Load .env if one exists.

    Looks first at $CWD/.env, then at ARC_HOME/.env. Existing env vars win
    (we don't clobber). Minimal impl to avoid hard dep on python-dotenv at
    startup time — that lib is in pyproject for plugin convenience but not
    required here.
    """
    candidates = [Path.cwd() / ".env"]
    try:
        from arc.bootstrap import resolve_home
        candidates.append(resolve_home(home_override) / ".env")
    except Exception:
        pass

    for path in candidates:
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


if __name__ == "__main__":
    sys.exit(main())
