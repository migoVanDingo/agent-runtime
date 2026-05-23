# agent-runtime — multi-repo workspace

You're at the top of a directory containing several related repos. Each
subdir has its own `CLAUDE.md` that's authoritative for work inside it.

| Directory | Purpose |
|---|---|
| `v1/`                    | Legacy agent runtime (SQLModel, Textual TUI, Ghidra/Briefbot). Bug-fix mode only. |
| `v2/`                    | Active rewrite. Plugin runtime, replay, two providers. Read `v2/CLAUDE.md`. |
| `arc-plugin-template/`   | Template repo for building **external** arc plugins. Forked when a new plugin starts. |
| `arc-plugin-briefbot/`   | External plugin: read-only tools over a local Briefbot SQLite corpus. |
| `arc-plugin-websearch/`  | External plugin: `web_search` / `read_url` / `http_request` / `extract_html`. |

`v1/` and `v2/` are separate Python projects with independent CLAUDE.md
files. The plugin repos are independent pip-installable packages.

## The plugin contract (TL;DR)

External plugins target `arc.plugin_api` (v0.1), a re-export shim arc v2
maintains specifically as a frozen surface for plugin authors:

```python
from arc.plugin_api import (
    Tool, ToolError, ToolInputSchema,
    PluginBuildContext, RuntimeEvent, SessionContext,
)
```

A plugin registers via the `arc.plugins` entry-point group in its
`pyproject.toml`:

```toml
[project.entry-points."arc.plugins"]
<name> = "arc_plugin_<name>.plugin:build"
```

arc discovers it at startup (`arc/plugins/discovery.py`), prompts the user
once on first run, and persists the answer to `~/.arc/config.yml`. From
then on the plugin loads automatically each session. Toggle with
`arc plugins`.

Two plugin **shapes**:
- **Session-scoped** (briefbot): owns a DB handle / model / cache via
  `on_session_start` + `on_session_end`. Tools constructed in
  `on_session_start` and contributed via `provides_tools()`.
- **Stateless tool pack** (websearch): no lifecycle. `build()` instantiates
  the tools; `provides_tools()` returns them.

Full design + breakage policy in `arc-plugin-template/docs/PLUGIN_API.md`.

## When the user references something cross-repo

The user often jumps between repos. If they say "the briefbot tool" or
"the websearch plugin", they mean those subdirs. If they say "the runtime"
or "the agent" they usually mean `v2/`. When in doubt, ask which repo —
the wrong assumption wastes time.

## User preferences (carried across sessions)

- Senior engineer. Terse. Skip "as you can see" framing. Pragmatic over pure.
- "Knock it out and test" over long design conversations. The user reviews
  after; document defensible judgment calls in design docs.
- Reverse-engineering is the primary use case. Long shell outputs,
  Ghidra integrations, persistent state matter.

## Conventions across all repos

- Use Edit/Write, not bash heredocs.
- Don't write multi-paragraph docstrings or obvious comments. WHY-only,
  when non-obvious. Let names carry the WHAT.
- No emojis in code, commit messages, or PR bodies unless explicitly asked.
- Run the tests after non-trivial changes. Each repo has its own suite;
  `pytest` from the repo root works in all of them.
- Don't edit `_BUILDERS` in `v2/src/arc/plugins/__init__.py` directly —
  it's now derived from `_BUILTIN_BUILDERS` + entry-point discovery via
  `_refresh_builders()`.
