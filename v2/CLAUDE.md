# arc v2 — agent runtime

A minimal, pluggable, fully-observable LLM agent runtime. Ground-up rewrite
of v1 (`../v1/`) that drops v1's brittle multi-stage orchestration in favor
of: **the runtime mediates, the model drives, plugins extend.**

| | |
|---|---|
| Source | ~6,900 lines Python |
| Tests | 386 passing (unit + real-API integration) |
| Providers | Gemini (`google-genai`), Anthropic (`anthropic`) |
| TUI | prompt_toolkit + Rich, inline mode (scrollback works) |
| Persistence | None — each session is a self-contained dir under `$ARC_HOME/sessions/<sid>/` |

## Read first

- **`README.md`** — long-form architecture, design principles, replay catalog.
- **`_architecture/`** — authoring guides + reference:
  - `plugin-authoring.md` — 12-hook protocol catalog, builder pattern
  - `provider-authoring.md` — Provider Protocol, byte-fidelity contract
  - `tool-authoring.md` — Tool Protocol, ToolError, output conventions
  - `config-reference.md` — every config key, type, default
  - `cli-reference.md` — every subcommand, sessions dir layout, event taxonomy
- **`_design/`** — phase-by-phase design docs (00xx, chronological). Start
  with `0001-foundation-phase0-design.md` for the contract everything else
  is built against.

## Three-layer architecture

```
src/arc/
  runtime/                  Layer 1 — minimal core (always present)
    loop.py                   ReAct loop
    events.py                 RuntimeEvent + EventType catalog
    hooks.py                  12 Protocol definitions
    bus.py                    HookRegistry + EventBus
    scope.py                  session/turn/scope contextvars
    ids.py                    self-contained ULID generator
  plugins/                  Layer 2 — built-in plugins (all optional)
    jsonl_recorder/           byte-faithful events.jsonl writer
    guard/                    tool-call policy (allow/block/escalate)
    safety_gate/              destructive-action confirmation (0012)
    pause_resume/             pause checkpoint + signal file
    log_writer/               human-readable session.log
    sliding_window_context/   pack_context — drops oldest user-turn fragments
  providers/                Layer 3 — supporting code
    base.py / gemini.py / anthropic.py
  tools/                    ls, bash_exec
  tui/                      app.py, render.py, pricing.py (LiteLLM cost lookup)
  replay/ resume/ rerun/    replay-mode engines
  cli.py bootstrap.py config.py defaults.py user_gate.py
```

## Design principles (don't violate without discussion)

1. **Runtime mediates, model drives, plugins extend.** Don't put policy in
   the runtime. Put it in a plugin.
2. **Observability is king.** Every observable moment emits an event. Events
   are the source of truth — replay, resume, branch, rerun, the human log,
   and meta files all rebuild from `events.jsonl`.
3. **No hardcoded user-tunables.** If a value is user-tunable, it lives in
   `config.yml` (via `defaults.py`). If you can't grep for the key in
   `defaults.py`, the knob doesn't exist.
4. **Byte-faithful replay.** Every `LLMResponse` must include `.raw` (the
   provider's full response as a JSON-faithful dict). Replay reconstructs
   from it without re-calling the API.
5. **Plugin failure ≠ session crash.** Plugins are quarantined after
   `plugins.failure_threshold` exceptions (default 3). Don't catch exceptions
   defensively in your plugin — the runtime handles it.

## CLI surface

```
arc                          interactive TUI
arc bootstrap [--force]      create $ARC_HOME + default config
arc run "<prompt>"           one-shot non-interactive turn
arc sessions                 list recorded sessions
arc show <id>                pretty-print events
arc log <id> [--tail N]      human-readable session.log
arc config show / path       inspect resolved config
arc replay <id> [--live-llm] mode 2 (deterministic) / mode 3 (live LLM)
arc resume <id> [--at-turn N --prompt "..."]   mode 1 (time-travel) / mode 4 (branch)
arc rerun <id>               mode 5 (rerun user inputs vs fresh agent)
arc --home <path> <cmd>      override ARC_HOME for one invocation
```

## ARC_HOME resolution

1. `--home <path>` flag
2. `ARC_HOME` env var
3. `./.arc/` (cwd, if exists — for per-project configs)
4. `~/.arc/` (default)

## Conventions when working in this tree

- **Use Edit/Write, not bash heredocs.** The harness has dedicated tools.
- **Run `python3 -m pytest tests/ -q`** after non-trivial changes. Tests
  are fast (~90s for the full unit + integration suite with API keys).
- **Tests structure:**
  - `tests/unit/` — fast, no network
  - `tests/integration/` — real Gemini/Anthropic; auto-skip without API key
- **Don't break replay.** If you change provider translation or event shape,
  run replay tests specifically and update fixtures if needed (intentional)
  or fix the regression (not intentional).
- **New plugin = builder + `_BUILDERS` entry + `defaults.py` entry + tests.**
  See `_architecture/plugin-authoring.md`.
- **New event type = `events.py` constant + `log_writer/formatter.py`
  dispatch entry.** Don't skip the formatter — session.log loses fidelity.
- **Comments: minimal.** No multi-paragraph docstrings, no obvious comments.
  WHY-only when non-obvious; let names carry the WHAT.

## Common gotchas

- **Existing user `.arc/config.yml` files don't auto-pick up new plugins.**
  The loader is strict by design — adding a new plugin to `defaults.py`
  doesn't silently mutate users' configs. They need `arc bootstrap --force`
  or to paste the new block in manually.
- **New optional config keys must default-on-missing in `_parse_*` functions
  in `config.py`** so older configs keep loading. See the `_parse_tui` pattern
  for how new fields were added.
- **macOS SSL caveat for pricing.** Stock macOS Python often lacks a CA
  bundle, so `PricingTable._fetch_upstream()` fails. Cost segment in the
  TUI toolbar simply disappears in that case. Fix at the user level:
  `pip install --upgrade certifi`.
- **Headless mode auto-denies safety_gate + guard escalations.** `arc run`
  uses `NoOpGate` by design. Batch jobs that need destructive ops should
  set `bypass_mode: true` on safety_gate in a scratch config.
- **Sub-agents must inherit the parent's spinner.** (Carried over from v1.)
  A fresh `Spinner` under the TUI corrupts the alt-screen render. Pass
  `None` or hand the parent's spinner down.
- **prompt_toolkit's bottom_toolbar updates only between prompts.** It does
  not push updates mid-input. This is fine for our use case; don't try to
  work around it.
- **Don't escalate to the user mid-task** when working autonomously. The
  user's standing instruction is "knock it out, I'll review later." Make
  defensible judgment calls and document them in the design doc.

## User preferences (carried across sessions)

- **Senior engineer.** Terse responses. Skip the "as you can see" framing.
- **Pragmatic over pure.** Bias toward "knock it out and test" rather than
  long design conversations.
- **Reverse-engineering is the primary use case.** Tools for binary analysis,
  long shell outputs, persistence of state across sessions all matter.
- **`/loop` / `arc` is `arc` here.** When user says "the agent" they mean
  this thing. When they say "v1" or "v2" they mean these directories.

## Phase status (most recent first)

| Phase | Doc | What landed |
|---|---|---|
| 3.4 | `_design/0012-destructive-action-gate.md` | `safety_gate` plugin, 12 default patterns, per-session remember cache |
| 3.3 | (this CLAUDE.md + `_architecture/`) | Doc pass — 5 authoring/reference guides |
| 3.2 | `_design/0011-tui-polish.md` | Slash commands, tab complete, history, bottom toolbar with cost, thinking blocks |
| 3.1 | `_design/0010-anthropic-provider.md` | Anthropic provider, thinking-block translation |
| 3.0 | `_design/0009-context-manager-sliding-window.md` | `sliding_window_context` plugin |
| 2.3 | `_design/0008-foundation-logging.md` | log_writer plugin, `arc log` |
| 2.2 | `_design/0007-foundation-phase2.2-branch-and-rerun.md` | Branch + rerun (modes 4, 5) |
| 2.1.5 | `_design/0006-foundation-phase2.1.5-pause-and-resume.md` | Pause + resume (mode 1) |
| 2.1 | `_design/0005-foundation-phase2.1-bash-and-guards.md` | bash_exec + guard plugin |
| 2.0.5 | `_design/0004-foundation-phase2.0.5-replay.md` | Replay engine (modes 2, 3) |
| 1 | `_design/0003-foundation-phase1-implementation.md` | Minimal core, recorder, TUI |
| 0 | `_design/0001-foundation-phase0-design.md` | Hook catalog, plugin protocol, the contract |

## When NOT in this directory

The user often has another Claude session open in v1 next door. If something
they say doesn't match what you see (e.g. "platform-common", "the service
deploy", "the SQLModel migration"), they may be referring to that one. Ask
before inventing context.
