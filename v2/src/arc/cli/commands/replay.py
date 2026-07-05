"""`arc replay` — replay a recorded session (modes 2, 3), plus batch (0019)
and the no-args replay menu."""
from __future__ import annotations

import sys
from pathlib import Path

import arc.cli as _cli
from arc.cli.commands.compare import t_short
from arc.cli.wiring import _load_dotenv_into_environ, build_session


def _cmd_replay(
    home_override: str | None,
    *,
    session_id: str | None,
    live_llm: bool,
    do_diff: bool,
    override_provider: str | None = None,
    override_model: str | None = None,
    max_cost_usd: float | None = None,
    against_spec: str | None = None,
) -> int:
    """Replay a recorded session.

    Modes:
      - no session_id  → drop into the 0019 TUI replay menu
      - --against      → batch replay against multiple targets (0019)
      - override flags → cross-provider single replay (0019)
      - --live-llm     → mode 3 same-provider
      - default        → mode 2 deterministic
    """
    _load_dotenv_into_environ(home_override)

    # ── No session id → TUI replay menu ───────────────────────────────────
    if session_id is None:
        return _cmd_replay_menu(home_override)

    from arc.bootstrap import paths_for, resolve_home
    from arc.config import load
    from arc.providers import build as build_provider
    from arc.replay import (
        MissingRecordingError,
        ReplayDivergenceError,
        ReplayingToolRegistry,
        ReplayProvider,
        diff_event_logs,
    )
    from arc.replay import (
        load as load_replay,
    )
    from arc.replay.override import OverrideError, apply_override
    from arc.runtime.subagents.registry import SubAgentRegistry

    home = resolve_home(home_override)
    paths = paths_for(home)
    source_dir = paths.sessions_dir / session_id

    # ── --against → batch fan-out via batch.py ─────────────────────────
    if against_spec is not None:
        return _cmd_replay_batch(
            home, paths.sessions_dir,
            source_id=session_id,
            against_spec=against_spec,
            max_cost_usd=max_cost_usd,
        )

    try:
        replay_data = load_replay(source_dir)
    except MissingRecordingError as e:
        print(f"replay: {e}", file=sys.stderr)
        return 1

    # Use the current config (so the user can edit it between recording
    # and replay to test changes). The snapshot is informational only.
    cfg = load(paths.config_file)

    # Apply provider/model override if requested (0019).  Override implies
    # --live-llm; otherwise the recorded ReplayProvider would just replay
    # the original LLM and the override would have no effect.
    if override_provider is not None:
        if not live_llm:
            live_llm = True  # silently upgrade — override is meaningless without --live-llm
        if not override_model:
            print("--override-provider requires --override-model", file=sys.stderr)
            return 2
        try:
            cfg = apply_override(cfg, provider=override_provider, model=override_model)
        except OverrideError as e:
            print(f"replay override: {e}", file=sys.stderr)
            return 2

    # Tool registry comes from the recording, not the config — the recording
    # tells us what tool names were called, with what inputs/outputs.
    mode = "by_call" if live_llm else "in_order"
    tools = ReplayingToolRegistry(replay_data, mode=mode)

    # Provider: stubbed (mode 2) or real (mode 3)
    if live_llm:
        provider = build_provider(cfg.provider)
    else:
        provider = ReplayProvider(replay_data.llm_responses)

    # Replay serves recorded tool results (incl. subagent_* tools) from the
    # ReplayingToolRegistry — do NOT re-merge live sub-agents (empty registry),
    # or their tool names collide with the recorded ones.
    built_session = build_session(
        cfg, paths, provider=provider, tools=tools,
        subagent_registry=SubAgentRegistry(builtins={}),
    )
    sess, bus, registry = built_session.session, built_session.bus, built_session.registry
    new_session_id_ = built_session.session_id

    # 0019: inject max_cost plugin if requested. Lives outside cfg.plugins
    # (the cap is a per-invocation flag) and needs the bus, so it's registered
    # after build_session but before start().
    max_cost_plugin = None
    if max_cost_usd is not None and max_cost_usd > 0:
        from arc.plugins.max_cost import MaxCostPlugin
        from arc.tui.pricing import PricingTable
        max_cost_plugin = MaxCostPlugin(
            max_cost_usd=float(max_cost_usd),
            pricing_table=PricingTable(cache_path=paths.home / "pricing_cache.json"),
        )
        max_cost_plugin.bind_bus(bus)
        registry.register(max_cost_plugin, hooks_order={})

    mode_label = "3 (live LLM)" if live_llm else "2 (strict)"
    if override_provider:
        mode_label += f" — override {override_provider}/{override_model}"
    if max_cost_usd is not None:
        mode_label += f" — max ${max_cost_usd:.2f}"
    print(f"replaying {session_id} → {new_session_id_}  (mode {mode_label})")

    diverged = False
    aborted = False
    try:
        sess.start()
        for user_input in replay_data.user_inputs:
            sess.run_turn(user_input)
    except ReplayDivergenceError as e:
        print(f"\nREPLAY DIVERGED:\n  {e}\n", file=sys.stderr)
        diverged = True
    except Exception as e:
        # max_cost or other plugin errors bubble here
        from arc.plugins.max_cost import MaxCostExceeded
        if isinstance(e, MaxCostExceeded):
            print(f"\nREPLAY ABORTED: {e}", file=sys.stderr)
            aborted = True
        else:
            raise
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

    if not do_diff or aborted:
        return 1 if (diverged or aborted) else 0

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


def _cmd_replay_batch(
    home: Path,
    sessions_dir: Path,
    *,
    source_id: str,
    against_spec: str,
    max_cost_usd: float | None,
) -> int:
    """Multi-target replay via arc.replay.batch.  Auto-launches `arc compare`
    at the end against the source + all completed targets (0019)."""
    from arc.replay.batch import BatchTarget, run_batch
    from arc.replay.compare import render_full_comparison
    from arc.replay.override import OverrideError, parse_target_list

    try:
        target_pairs = parse_target_list(against_spec)
    except OverrideError as e:
        print(f"replay --against: {e}", file=sys.stderr)
        return 2

    targets = [BatchTarget(provider=p, model=m) for p, m in target_pairs]
    print(f"replay batch: source={source_id}  targets={len(targets)}")
    for t in targets:
        print(f"  + {t.short()}")

    def _on_start(t):
        print(f"\n→ running {t.short()} …", file=sys.stderr)

    def _on_done(r):
        status = "ok" if r.succeeded else f"failed (rc={r.return_code})"
        sid = r.target_session_id or "—"
        print(f"  {t_short(r.target)}: {status}  session={sid}  {r.elapsed_seconds:.1f}s",
              file=sys.stderr)

    results = run_batch(
        source_session_id=source_id,
        targets=targets,
        arc_home=home,
        max_cost_usd=max_cost_usd,
        on_target_start=_on_start,
        on_target_done=_on_done,
    )

    successes = [r for r in results if r.succeeded]
    print(f"\nbatch complete: {len(successes)}/{len(results)} succeeded")

    if successes:
        # Auto-launch compare against source + successful targets
        dirs = [sessions_dir / source_id] + [
            sessions_dir / r.target_session_id for r in successes  # type: ignore[arg-type]
        ]
        from arc.tui.pricing import PricingTable
        table = PricingTable(cache_path=home / "pricing_cache.json")
        print()
        print(render_full_comparison(dirs, pricing_table=table))

    return 0 if all(r.succeeded for r in results) else 1


def _cmd_replay_menu(home_override: str | None) -> int:
    """`arc replay` with no args → opens the setup hub on the Replay section."""
    from arc.bootstrap import bootstrap, resolve_home
    from arc.setup.hub import run_hub

    home = resolve_home(home_override)
    bootstrap(home)
    result = run_hub(home, initial_section="replay")
    if result.launch_session:
        return _cli._cmd_interactive(home_override)
    return result.rc
