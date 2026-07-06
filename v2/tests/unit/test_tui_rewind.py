"""Tests for interactive time travel (0026 phase a): /rewind + /retry.

Unlike test_tui.py these wire the full build_session path with a real
jsonl-recorder into a tmp ARC_HOME — branching truncates from the recording,
so a recorder is part of the feature under test.
"""
from __future__ import annotations

import io
import json
from collections import deque
from pathlib import Path

from rich.console import Console

from arc.bootstrap import paths_for
from arc.cli.wiring import build_session, stamp_session_meta
from arc.config import (
    BootstrapConfig,
    Config,
    PluginEntry,
    PluginsConfig,
    ProviderConfig,
    RetryConfig,
    RuntimeConfig,
    ToolsConfig,
    TUIConfig,
)
from arc.runtime.events import EventType
from arc.runtime.hooks import ContentBlock, LLMResponse
from arc.tools import build as build_tools
from arc.tui.app import TUIApp


def _cfg() -> Config:
    return Config(
        runtime=RuntimeConfig(
            workspace=".", max_iterations=10, max_tool_calls_per_turn=5,
            show_thinking=True, log_level="info",
            system_prompt="be concise",
            iteration_cap_message="wrap up", tool_call_cap_message="wrap up",
            cycle_detection_threshold=3, cycle_detected_message="cycle stop",
        ),
        provider=ProviderConfig(
            name="fake", model="fake-1", api_key_env="FAKE_KEY", base_url=None,
            timeout_seconds=10.0,
            retry=RetryConfig(max_attempts=1, backoff_base_seconds=0.01,
                              backoff_max_seconds=0.05),
            params={},
        ),
        tools=ToolsConfig(enabled=[], config={}),
        plugins=PluginsConfig(
            failure_threshold=3, exception_message_max_chars=500,
            enabled=[PluginEntry(name="jsonl-recorder", enabled=True,
                                 config={}, hooks_order={})],
        ),
        tui=TUIConfig(
            enabled=True, theme="default", inline_mode=True,
            spinner_style="dots", prompt_prefix="❯ ",
            show_token_counts=True, show_event_count=False,
            show_thinking=True, tool_output_max_lines=30,
            toolbar_enabled=False, input_history_enabled=False,
        ),
        bootstrap=BootstrapConfig(create_workspace_dir=False,
                                  write_example_session=False),
        source_path=None,  # type: ignore[arg-type]
    )


class FakeProvider:
    name = "fake"
    def __init__(self, responses):
        self._q = deque(responses)
    def chat(self, req):
        return self._q.popleft()


def _resp(text: str) -> LLMResponse:
    return LLMResponse(content=[ContentBlock(type="text", text=text)],
                       stop_reason="end_turn", input_tokens=5,
                       output_tokens=3, raw={})


def _build_app(tmp_path: Path, inputs: list[str], provider):
    """TUIApp over a real tmp ARC_HOME with the recorder enabled."""
    home = tmp_path / "arc_home"
    home.mkdir()
    # Real provider block: the /model snapshot transform rewrites it
    (home / "config.yml").write_text(
        "provider:\n"
        "  name: fake\n"
        "  model: fake-1\n"
        "  api_key_env: FAKE_KEY\n"
        "  base_url: null\n"
    )
    paths = paths_for(home)
    paths.sessions_dir.mkdir()

    cfg = _cfg()
    built = build_session(
        cfg, paths,
        provider=provider,
        tools=build_tools(cfg.tools),
        subagent_registry=None,
        gate=None,
    )

    out = io.StringIO()
    console = Console(file=out, force_terminal=False, width=120, color_system=None)
    queue = deque(inputs)

    def prompt_fn(prefix: str) -> str:
        if not queue:
            raise EOFError
        return queue.popleft()

    app = TUIApp(
        config=cfg, session=built.session, home_display=str(home),
        prompt_fn=prompt_fn, console=console, paths=paths,
    )
    return app, out, paths


def _session_metas(paths) -> dict[str, dict]:
    """sid → meta.json for every recorded session dir."""
    out = {}
    for d in paths.sessions_dir.iterdir():
        meta = d / "meta.json"
        if meta.is_file():
            out[d.name] = json.loads(meta.read_text())
    return out


def _events(paths, sid: str) -> list[dict]:
    lines = (paths.sessions_dir / sid / "events.jsonl").read_text().splitlines()
    return [json.loads(ln) for ln in lines if ln.strip()]


def _find_child(metas: dict[str, dict]) -> tuple[str, dict]:
    for sid, meta in metas.items():
        if "resumed_from" in meta:
            return sid, meta
    raise AssertionError(f"no branched session found in {list(metas)}")


# ── /rewind ───────────────────────────────────────────────────────────────


def test_rewind_branches_into_new_session(tmp_path):
    provider = FakeProvider([_resp("answer one"), _resp("answer two"),
                             _resp("branch answer")])
    app, out, paths = _build_app(
        tmp_path, ["one", "two", "/rewind 1", "branch prompt"], provider)
    app.run()

    metas = _session_metas(paths)
    assert len(metas) == 2
    child_sid, child_meta = _find_child(metas)
    parent_sid = child_meta["resumed_from"]

    assert child_meta["branched_at_turn"] == 1
    assert child_meta["restored_message_count"] == 2  # user + assistant of turn 1

    parent_events = _events(paths, parent_sid)
    assert sum(e["type"] == EventType.TURN_ENDED for e in parent_events) == 2

    child_events = _events(paths, child_sid)
    branched = [e for e in child_events if e["type"] == EventType.SESSION_BRANCHED]
    assert len(branched) == 1
    assert branched[0]["payload"]["source_session_id"] == parent_sid
    assert branched[0]["payload"]["branched_at_turn"] == 1

    child_turns = [e for e in child_events if e["type"] == EventType.TURN_STARTED]
    assert [t["content"]["user_input"] for t in child_turns] == ["branch prompt"]

    assert "branch answer" in out.getvalue()
    assert "branched" in out.getvalue()


def test_branch_lineage_stamped_eagerly_before_close(tmp_path):
    # Lineage must be on the child's meta the moment the branch is born —
    # not only when the tab closes — so a hard kill can't lose it. We assert
    # mid-run by snapshotting meta from a prompt_fn callback while the branch
    # tab is still open.
    provider = FakeProvider([_resp("a1"), _resp("a2"), _resp("a3")])
    snapshots = {}

    def make_app():
        app, out, paths = _build_app(tmp_path, [], provider)
        return app, out, paths

    app, out, paths = make_app()
    from collections import deque
    queue = deque(["one", "/rewind 1", "branch prompt", "SNAPSHOT"])

    def prompt_fn(prefix):
        if not queue:
            raise EOFError
        nxt = queue.popleft()
        if nxt == "SNAPSHOT":
            # branch tab is open, its session not yet ended
            child = [m for sid, m in _session_metas(paths).items()
                     if m.get("resumed_from")]
            snapshots["mid"] = child[0] if child else None
            raise EOFError
        return nxt

    app._prompt_fn = prompt_fn
    app.run()

    assert snapshots["mid"] is not None, "branch meta not stamped while tab open"
    assert snapshots["mid"]["branched_at_turn"] == 1
    assert snapshots["mid"]["ended_at"] is None  # proves it was stamped live


def test_rewind_empty_input_cancels_without_branching(tmp_path):
    provider = FakeProvider([_resp("a1"), _resp("a2")])
    app, out, paths = _build_app(
        tmp_path, ["one", "/rewind 0", "", "still here"], provider)
    app.run()

    metas = _session_metas(paths)
    assert len(metas) == 1  # no branch was created
    assert "rewind cancelled" in out.getvalue()
    (sid,) = metas
    events = _events(paths, sid)
    turns = [e["content"]["user_input"] for e in events
             if e["type"] == EventType.TURN_STARTED]
    assert turns == ["one", "still here"]  # second prompt ran in the SAME session


def test_rewind_armed_but_never_submitted_creates_nothing(tmp_path):
    provider = FakeProvider([_resp("a1")])
    app, out, paths = _build_app(tmp_path, ["one", "/rewind 9"], provider)
    app.run()

    assert "clamping" in out.getvalue()
    assert len(_session_metas(paths)) == 1  # branch-on-submit: EOF ≠ submit


def test_rewind_no_arg_prints_turn_map(tmp_path):
    # Multi-space input: the map collapses whitespace, the normal echo does
    # not — so finding the collapsed form proves the MAP rendered the text
    # (not just the echo). Guards the extract_turns payload/content split.
    provider = FakeProvider([_resp("the answer is 42")])
    app, out, paths = _build_app(
        tmp_path, ["what    is    the    answer", "/rewind"], provider)
    app.run()

    text = out.getvalue()
    assert "turn map" in text
    assert "what is the answer" in text   # collapsed → came from the map
    assert "the answer is 42" in text     # assistant summary line
    assert "/rewind N" in text
    assert len(_session_metas(paths)) == 1


def test_rewind_before_any_turn(tmp_path):
    provider = FakeProvider([])
    app, out, paths = _build_app(tmp_path, ["/rewind 1"], provider)
    app.run()
    assert "no completed turns" in out.getvalue()


def test_rewind_unavailable_without_paths():
    # test_tui.py-style wiring (no recorder/paths) must degrade gracefully
    from arc.runtime.bus import EventBus, HookRegistry
    from arc.runtime.loop import AgentSession
    from arc.tools.base import ToolRegistry

    out = io.StringIO()
    console = Console(file=out, force_terminal=False, width=120, color_system=None)
    registry = HookRegistry(failure_threshold=3, exception_message_max_chars=500)
    sess = AgentSession(config=_cfg(), provider=FakeProvider([]),
                        tools=ToolRegistry(), registry=registry,
                        bus=EventBus(registry), session_id="SES_test")
    queue = deque(["/rewind 1"])

    def prompt_fn(prefix):
        if not queue:
            raise EOFError
        return queue.popleft()

    app = TUIApp(config=_cfg(), session=sess, home_display="x",
                 prompt_fn=prompt_fn, console=console)
    app.run()
    assert "/rewind unavailable" in out.getvalue()


# ── rewind walker (phase b) ───────────────────────────────────────────────


def _build_app_with_walker(tmp_path, inputs, provider, walker):
    app, out, paths = _build_app(tmp_path, inputs, provider)
    app._turn_walker = walker
    return app, out, paths


def test_walker_selection_arms_the_branch(tmp_path):
    seen = {}

    def walker(turns):
        seen["turns"] = turns
        return 1

    provider = FakeProvider([_resp("a1"), _resp("a2"), _resp("a3")])
    app, out, paths = _build_app_with_walker(
        tmp_path, ["one", "two", "/rewind", "branch prompt"], provider, walker)
    app.run()

    assert [t.index for t in seen["turns"]] == [1, 2]
    assert seen["turns"][0].user_input == "one"

    metas = _session_metas(paths)
    assert len(metas) == 2
    _, child = _find_child(metas)
    assert child["branched_at_turn"] == 1


def test_walker_cancel_arms_nothing(tmp_path):
    provider = FakeProvider([_resp("a1"), _resp("a2")])
    app, out, paths = _build_app_with_walker(
        tmp_path, ["one", "/rewind", "still same session"], provider,
        lambda turns: None)
    app.run()

    metas = _session_metas(paths)
    assert len(metas) == 1
    (sid,) = metas
    turns = [e["content"]["user_input"] for e in _events(paths, sid)
             if e["type"] == EventType.TURN_STARTED]
    assert turns == ["one", "still same session"]


# ── /retry ────────────────────────────────────────────────────────────────


def test_retry_resends_last_prompt_on_a_branch(tmp_path):
    provider = FakeProvider([_resp("first roll"), _resp("second roll")])
    app, out, paths = _build_app(tmp_path, ["ask me something", "/retry"], provider)
    app.run()

    metas = _session_metas(paths)
    assert len(metas) == 2
    child_sid, child_meta = _find_child(metas)
    assert child_meta["branched_at_turn"] == 0
    assert child_meta["retry_of_turn"] == 1
    assert child_meta["restored_message_count"] == 0

    child_events = _events(paths, child_sid)
    turns = [e["content"]["user_input"] for e in child_events
             if e["type"] == EventType.TURN_STARTED]
    assert turns == ["ask me something"]  # verbatim resend
    assert "second roll" in out.getvalue()


def test_second_generation_branch_keeps_inherited_prefix(tmp_path):
    # /retry inside a branched session must not lose the messages the branch
    # itself inherited — the child's recording only holds its OWN turns.
    provider = FakeProvider([_resp("r1"), _resp("r2"), _resp("r3"), _resp("r4")])
    app, out, paths = _build_app(
        tmp_path, ["one", "two", "/rewind 1", "branch prompt", "/retry"], provider)
    app.run()

    metas = _session_metas(paths)
    assert len(metas) == 3
    grandchild_sid, grandchild = next(
        (sid, m) for sid, m in metas.items() if "retry_of_turn" in m)
    child = metas[grandchild["resumed_from"]]

    assert child["branched_at_turn"] == 1
    assert child["restored_message_count"] == 2       # turn 1 of the parent
    # retry rewound the child's own single turn, but kept its inherited prefix
    assert grandchild["branched_at_turn"] == 0
    assert grandchild["restored_message_count"] == 2  # inherited, not 0

    turns = [e["content"]["user_input"] for e in _events(paths, grandchild_sid)
             if e["type"] == EventType.TURN_STARTED]
    assert turns == ["branch prompt"]


def test_retry_with_no_turns(tmp_path):
    provider = FakeProvider([])
    app, out, paths = _build_app(tmp_path, ["/retry"], provider)
    app.run()
    assert "no completed turn to retry" in out.getvalue()


# ── tabs (phase d) ────────────────────────────────────────────────────────


def test_branch_opens_tab_and_exit_returns_to_parent(tmp_path):
    provider = FakeProvider([_resp("r1"), _resp("r2"), _resp("r3")])
    app, out, paths = _build_app(
        tmp_path,
        ["one", "/rewind 1", "branch prompt", "/exit", "back in parent"],
        provider)
    app.run()

    metas = _session_metas(paths)
    assert len(metas) == 2
    child_sid, child = _find_child(metas)
    parent_sid = child["resumed_from"]

    parent_turns = [e["content"]["user_input"] for e in _events(paths, parent_sid)
                    if e["type"] == EventType.TURN_STARTED]
    assert parent_turns == ["one", "back in parent"]  # parent survived the branch
    child_turns = [e["content"]["user_input"] for e in _events(paths, child_sid)
                   if e["type"] == EventType.TURN_STARTED]
    assert child_turns == ["branch prompt"]
    assert "tab closed" in out.getvalue()


def test_tab_switch_runs_turn_in_target_tab(tmp_path):
    provider = FakeProvider([_resp("r1"), _resp("r2"), _resp("r3")])
    app, out, paths = _build_app(
        tmp_path,
        ["one", "/rewind 1", "branch prompt", "/tab 1", "again in parent"],
        provider)
    app.run()

    metas = _session_metas(paths)
    child_sid, child = _find_child(metas)
    parent_sid = child["resumed_from"]
    parent_turns = [e["content"]["user_input"] for e in _events(paths, parent_sid)
                    if e["type"] == EventType.TURN_STARTED]
    assert parent_turns == ["one", "again in parent"]


def test_tab_cap_blocks_branch(tmp_path):
    from dataclasses import replace
    provider = FakeProvider([_resp("r1")])
    app, out, paths = _build_app(tmp_path, ["one", "/rewind 0", "x"], provider)
    app._cfg = replace(app._cfg, tui=replace(app._cfg.tui, tabs_max=1))
    app.run()

    assert "tab cap reached" in out.getvalue()
    assert len(_session_metas(paths)) == 1


# ── /model (phase c) ──────────────────────────────────────────────────────


def _patch_provider_build(monkeypatch, shared_provider):
    """Make arc.providers.build hand back a provider sharing the fake's
    response queue, so turns after a /model swap keep popping from it."""
    import arc.providers

    def fake_build(pcfg):
        p = FakeProvider([])
        p._q = shared_provider._q
        p.name = pcfg.name
        return p

    monkeypatch.setattr(arc.providers, "build", fake_build)


def test_model_swap_branches_with_effective_snapshot(tmp_path, monkeypatch):
    provider = FakeProvider([_resp("a1"), _resp("a2 on new model")])
    _patch_provider_build(monkeypatch, provider)
    app, out, paths = _build_app(
        tmp_path, ["one", "/model anthropic/claude-test", "after swap"], provider)
    app.run()

    metas = _session_metas(paths)
    assert len(metas) == 2
    child_sid, child = _find_child(metas)
    assert child["provider_override"] == {"name": "anthropic", "model": "claude-test"}
    assert child["branched_at_turn"] == 1
    assert child["restored_message_count"] == 2  # whole conversation carried

    snapshot = (paths.sessions_dir / child_sid / "config.snapshot.yml").read_text()
    assert "anthropic" in snapshot
    assert "claude-test" in snapshot
    assert "ANTHROPIC_API_KEY" in snapshot

    events = _events(paths, child_sid)
    swapped = [e for e in events if e["type"] == EventType.PROVIDER_SWAPPED]
    assert len(swapped) == 1
    assert swapped[0]["payload"] == {
        "from_provider": "fake", "from_model": "fake-1",
        "to_provider": "anthropic", "to_model": "claude-test",
    }
    turns = [e["content"]["user_input"] for e in events
             if e["type"] == EventType.TURN_STARTED]
    assert turns == ["after swap"]
    assert app._cfg.provider.model == "claude-test"  # toolbar follows


def test_model_bare_arg_keeps_provider(tmp_path, monkeypatch):
    provider = FakeProvider([_resp("a1"), _resp("a2")])
    _patch_provider_build(monkeypatch, provider)
    app, out, paths = _build_app(tmp_path, ["one", "/model fake-2", "go"], provider)
    app.run()

    _, child = _find_child(_session_metas(paths))
    assert child["provider_override"] == {"name": "fake", "model": "fake-2"}
    assert app._cfg.provider.name == "fake"
    assert app._cfg.provider.api_key_env == "FAKE_KEY"  # untouched on same provider


def test_model_unknown_provider_aborts_clean(tmp_path):
    provider = FakeProvider([_resp("a1"), _resp("a2")])
    app, out, paths = _build_app(
        tmp_path, ["one", "/model nope/xyz", "still here"], provider)
    app.run()

    assert "unknown provider" in out.getvalue()
    metas = _session_metas(paths)
    assert len(metas) == 1  # no branch; session survived and ran the next turn
    (sid,) = metas
    turns = [e["content"]["user_input"] for e in _events(paths, sid)
             if e["type"] == EventType.TURN_STARTED]
    assert turns == ["one", "still here"]


def test_model_no_arg_shows_current(tmp_path):
    provider = FakeProvider([])
    app, out, paths = _build_app(tmp_path, ["/model"], provider)
    app.run()
    assert "fake/fake-1" in out.getvalue()


# ── stamp helper ──────────────────────────────────────────────────────────


def test_stamp_session_meta_merges_not_clobbers(tmp_path):
    sdir = tmp_path / "sessions" / "SES_X"
    sdir.mkdir(parents=True)
    (sdir / "meta.json").write_text(json.dumps({"session_id": "SES_X", "ended_at": "t"}))

    stamp_session_meta(tmp_path / "sessions", "SES_X", {"resumed_from": "SES_Y"})

    meta = json.loads((sdir / "meta.json").read_text())
    assert meta == {"session_id": "SES_X", "ended_at": "t", "resumed_from": "SES_Y"}


def test_stamp_session_meta_noop_when_missing(tmp_path):
    (tmp_path / "sessions").mkdir()
    stamp_session_meta(tmp_path / "sessions", "SES_NOPE", {"x": 1})  # must not raise
