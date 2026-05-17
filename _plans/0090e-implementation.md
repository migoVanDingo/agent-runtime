# 0090e — Implementation notes

> Companion to `_plans/0090-context-discipline-and-subagents.md` §6 0090e.

## What landed

Per-spec provider/model/timeout/max-iterations overrides loaded from
`config.yml` and merged into the registered `SubAgentSpec` at dispatch
time. New `arc subagent {list,info}` CLI for introspection.

## Files added

- `src/config/subagents.py` — `SubAgentOverride`, `SubAgentsConfig`.
- `src/cli/subagent.py` — `cmd_subagent(argv)` implementing `list` and
  `info` subcommands.
- `tests/unit/test_subagent_overrides.py` — 5 tests covering missing
  block, full override, partial override (only specified fields change),
  and identity return when no override exists.

## Files modified

- `src/config/app.py` — `AppConfig.subagents: SubAgentsConfig` field
  added, defaults to empty.
- `src/config/loader.py` — parses optional top-level `subagents:` block,
  builds `SubAgentOverride` per key, attaches to `AppConfig`.
- `src/runtime/subagents/runner.py`:
  - `SubAgentRunner.run()` calls `_merge_overrides(spec)` before
    dispatch. The merge is a pure function: returns the original spec
    when no override exists, or a `dataclasses.replace`'d copy with only
    the overridden fields swapped (frozen dataclass-safe).
  - Logs `subagent <name>: applying config overrides [provider, model]`
    when an override fires, so the user can see what's been swapped.
- `src/main.py:dispatch()` — `arc subagent` routed to `cli.subagent.cmd_subagent`.
- `config.yml` — top-level `subagents: {}` block added with commented
  example showing `ghidra_analyst` pinned to Opus + extended timeout.

## CLI surface

```
$ arc subagent list

Registered sub-agents (1):

  ghidra_analyst
    description: Specialised reverse-engineering sub-agent. …
    provider:    (inherit)
    model:       (inherit)
    toolsets:    reversing, file_io, shell
    skills:      (none)
    response:    json
    timeout:     900s
    max iters:   25
```

When an override is active, the corresponding field shows `(overridden)`
beside it. `arc subagent info <name>` prints the full spec including the
system prompt and JSON response schema.

## Override semantics

- Missing key in `subagents:` block → spec defaults apply (no warning).
- Unknown key → silently ignored (we don't know what the user meant; a
  future enhancement could log a warning at startup if the user has
  configured an override for a name that doesn't exist).
- Partial override (e.g., only `model`) → other fields keep spec
  defaults. Useful when you only want to swap the model but keep the
  rest of the spec intact.
- Override applies at dispatch time. Spec defaults survive in the
  registry so the override can be removed without restarting.

## Verification

- Compile-check: clean.
- 5 new unit tests pass.
- Full pytest: 180 passed (+5), same 9 pre-existing failures, no new
  regressions.
- Smoke test:
  - `arc subagent list` shows ghidra_analyst with `(inherit)` values ✓.
  - `SubAgentRunner._merge_overrides` with no override → returns same
    spec object (identity check) ✓.
  - With provider+model override → returns new spec with overridden
    fields ✓.
  - With timeout/max_iter override → only those fields change, rest
    keep defaults ✓.

## What changes user-visible behavior

- `arc subagent list` / `arc subagent info <name>` commands available.
- Users can pin sub-agents to specific providers/models via config.yml
  without code changes.
- When an override fires during a run, a log line surfaces it:
  `  subagent 'ghidra_analyst': applying config overrides ['provider', 'model']`.

## Open issues / known limitations

- **Unknown sub-agent names in config.yml are silently ignored.** A
  startup-time validation pass would catch typos. Punted to a small
  follow-up if it becomes a support burden.
- **No per-spec cost telemetry rollup yet** (the plan called for it
  under 0090e). The `subagent.completed` event already carries
  `tokens_in`, `tokens_out`, `cost_usd` per child (0090c shipped this),
  so analysts can group by `name` in pandas. A built-in `arc subagent
  costs` summary CLI would be nice; flagged for follow-up.
- **System prompt override happens at the spec level, not config level.**
  You can pin provider/model from config but you can't override the
  system prompt without editing code. Intentional: system prompts encode
  the sub-agent's methodology and shouldn't be casual config knobs. If
  this becomes a pain point, an explicit `system_prompt_path` field on
  the override could load from a file.
