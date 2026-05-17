# arc — agent runtime v2

A minimal, pluggable, fully-observable LLM agent runtime.

## Quick start

```bash
make dev                          # install package + dev deps
cp .env.example .env              # add GEMINI_API_KEY
arc bootstrap                     # create ~/.arc-v2/ + default config
arc                               # interactive session
```

## Design

All design decisions live in [`_design/`](_design/). Start with [`0001-foundation-phase0-design.md`](_design/0001-foundation-phase0-design.md) — that's the spec everything else is built against.

Architecture overviews go in [`_architecture/`](_architecture/). Integration tests live in [`_tests/`](_tests/).

## Principles

1. **Runtime as mediator, not director.** The runtime sees and can intercept every LLM call, tool call, and event. The model drives; the runtime mediates.
2. **Observability is king.** Every event is recorded canonically. Sessions are replayable byte-identical. You can pause, branch, and re-run.
3. **Pluggable everything.** The minimal core is `model + tools + ReAct loop + telemetry`. Every other capability is a plugin, toggleable in `config.yml`.
4. **No hardcoded defaults.** If a value is user-tunable, it lives in `config.yml`. If you can't grep for the key, the knob doesn't exist yet.

## Status

**Phase 0** — design complete (see `_design/0001-foundation-phase0-design.md`).
**Phase 1** — minimal core in progress.

## Project layout

```
src/arc/          # package
  cli.py          # `arc` entry point
  bootstrap.py    # `arc bootstrap`
  config.py       # config loading
  runtime/        # event bus, hooks, loop
  providers/      # LLM providers
  tools/          # built-in tools
  plugins/        # built-in plugins
  tui/            # interactive UI
tests/            # unit + integration
_design/          # design docs
_architecture/    # architecture overviews
_tests/           # integration scenarios
```
