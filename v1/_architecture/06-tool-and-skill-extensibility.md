# 06 — Tool and skill extensibility

How third parties (or your own future self) add new tools, skills, and
sub-agents to arc without forking the codebase.

## Three extension surfaces

| Surface | What it is | Discovery |
|---|---|---|
| **Tools** | One-shot operations returning a string. Subclass `BaseTool`. | Built-ins via `tools.toolsets.ALL_TOOLSETS`; plugins via entry points or `~/.arc/plugins/` |
| **Skills** | Deterministic multi-step expansions of a domain task. Subclass `Skill`. | Built-ins via `skills.implementations`; plugins via the same channels as tools |
| **Sub-agents** | Specs that describe a scoped child agent (toolset + provider + system prompt + response schema). Build a `SubAgentSpec`. | Currently built-in; plugin entry-point group `arc.subagents` is queued as 0091 |

## Plugin loading (0088)

Two discovery paths, both processed at agent startup:

1. **Python entry points** (canonical for PyPI plugins):
   ```toml
   [project.entry-points."arc.tools"]
   my_tool = "my_pkg:MyTool"

   [project.entry-points."arc.skills"]
   my_skill = "my_pkg:MySkill"

   [project.entry-points."arc.toolsets"]
   my_toolset = "my_pkg:MY_TOOLSET"
   ```

2. **Filesystem** (`~/.arc/plugins/`):
   - `~/.arc/plugins/tools/<name>.py` — single-file plugin with an
     `ARC_PLUGIN` dict manifest.
   - `~/.arc/plugins/skills/<name>/plugin.toml` + package — directory
     plugins for larger codebases.

Both feed into `plugins.loader.load_into(registry, skill_registry)`,
called from `Agent.__init__`. Conflict rules: **built-ins always win**
on name collision; plugin tools/skills with conflicting names are
dropped with a logged warning.

## Plugin permissions

Plugins declare `permissions` in their manifest:

```toml
[plugin.permissions]
network = true
filesystem_write = true
```

`ActionGuard` consults the plugin manifest at tool-call time: if a
plugin tool requests sensitive permissions, it escalates on first use
of the session (user can approve once via the gate).

## Sub-agent specs (0090)

Specs register at module-import time via
`runtime.subagents.register_spec`. The `tools.toolsets._build_subagent_toolset()`
function builds a `SubAgentTool` per registered spec and bundles them
into the `subagent` toolset. So sub-agents are exposed to the agent as
ordinary tools.

Adding a new spec today: create a module under
`src/tools/implementations/subagents/` that calls `register_spec` at
import time, and ensure the module is imported on startup (the package
`__init__.py` triggers this).

Adding a sub-agent spec via a plugin (queued 0091): same shape as
tool/skill plugins, with a new entry-point group `arc.subagents`.

## Sandboxing

| Type | Sandbox |
|---|---|
| Built-in tools that hit the network or filesystem aggressively | `ActionGuard` escalates per the policy table in `runtime/guard.py` |
| `bash_exec` | `SandboxManager` runs in macOS `sandbox-exec` (default), Docker, or host (explicit opt-in) |
| Plugin tools with manifest permissions | `ActionGuard` consults manifest, escalates if `network: true` or `filesystem_write: true` |
| Ghidra subprocess | Spawned via `subprocess.Popen` with its own JVM, killable on timeout |

## Where to extend

| Task | Read first |
|---|---|
| Add a built-in tool | `tools/base.py`, `tools/toolsets.py`. Subclass `BaseTool`, register in a toolset. |
| Add a built-in skill | `skills/base.py`, `skills/implementations/__init__.py`. Subclass `Skill`, add to `ALL_SKILLS`. |
| Add a sub-agent | `runtime/subagents/spec.py`, `tools/implementations/subagents/ghidra_analyst.py` (template). |
| Add a plugin tool | `_plans/0088-plugin-system.md`. Either pip-installable package with entry points, or single-file in `~/.arc/plugins/tools/`. |
| Add a context strategy | `runtime/context/strategy.py` Protocol, `runtime/context/factory.py:register_strategy`. |
| Add a context-strategy plugin | Not yet — punt to a follow-up plan once the use case is concrete. |

## Related plans

- `_plans/0088-plugin-system.md` — full plugin design (entry points,
  filesystem, manifest, permissions, CLI).
- `_plans/0089-pluggable-context-manager.md` — context-strategy
  plug-in mechanism.
- `_plans/0090-context-discipline-and-subagents.md` — sub-agent
  primitive that's extensible the same way.
