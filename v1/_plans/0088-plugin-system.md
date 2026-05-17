# 0088 — Plugin system for user-installable tools and skills

> **Audience:** Implementer with full codebase access, no prior context.
> Read this document end-to-end. Phase docs (`0088a` … `0088e`) will be
> written separately when each phase is scheduled.
>
> **Reading order:** `0079-runtime-as-god.md` (you must understand that
> plugins are passive participants) → this document → the phase doc.

---

## 0. Goal

Let third parties extend arc with new tools and skills **without forking
the codebase**. A plugin must be installable, auto-discoverable, and
declare its own native dependencies so missing-dep plugins degrade gracefully
rather than crashing.

Constraints:

- Plugins remain **passive** per the runtime-as-god tenet. A plugin tool
  cannot decide retry / escalate / replan. It only returns strings (results
  or structured errors) like every built-in tool.
- Plugins must work without a full arc rebuild. `pip install arc-plugin-foo`
  → restart arc → plugin available.
- A plugin with missing optional deps must **disable itself** with a clear
  warning rather than break agent startup.

---

## 1. Current state

### 1.1 Tools

`src/tools/toolsets.py` (561 lines):

- Hardcoded `from tools.implementations.<group>.<file> import <Tool>` at the
  top of the file.
- 14 `Toolset(name=..., tools=[...], rules=[...])` literal constructions.
- `ALL_TOOLSETS = [FILE_IO, SHELL, ANALYSIS, ...]` exported.

Wiring in `src/agent.py:134`:

```python
self.registry = ToolRegistry()
for toolset in ALL_TOOLSETS:
    self.registry.register_toolset(toolset)
```

`ToolRegistry` (`src/tools/registry.py`) holds:

- `_tools: dict[str, BaseTool]` — name → tool instance
- `_toolsets: dict[str, Toolset]` — name → toolset
- Methods to introspect schemas and rules.

### 1.2 Skills

`src/skills/implementations/__init__.py`:

```python
from skills.implementations.solve_crackme import SolveCrackme
# ... 9 more imports ...

ALL_SKILLS = [SolveCrackme(), AuditBinary(), TestReconstruction(), ...]
```

`SkillRegistry` (`src/skills/registry.py`) constructs `_by_name = {s.name: s
for s in ALL_SKILLS}`.

### 1.3 Tool base class

`src/tools/base.py`:

```python
class BaseTool(ABC):
    name: str
    description: str
    weight: ToolWeight = ToolWeight.MODERATE

    @property
    @abstractmethod
    def input_schema(self) -> InputSchema: ...

    @abstractmethod
    def execute(self, tool_input: dict) -> str: ...
```

### 1.4 Skill base class

`src/skills/base.py`:

```python
class Skill(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def intent(self) -> str: ...

    @property
    def pattern(self) -> re.Pattern | None: ...

    @abstractmethod
    def expand(self, ctx: SkillContext) -> list[Step]: ...

    @property
    def completion_criteria(self) -> CompletionCriteria | None: ...

    def continuation_steps(self, ctx, prior_results) -> list[Step] | None: ...
```

### 1.5 What's missing

- No discovery mechanism for external code.
- No declared optional dependencies (Ghidra tools just call
  `if not ghidra_home(): return error`, which is fine at runtime but means
  the tool *is* registered even when unusable).
- No version/compatibility checking.
- No conflict resolution if two plugins register the same name.

---

## 2. Design decisions

### 2.1 Discovery: prefer entry points, also support a runtime-loadable dir

**Primary**: Python entry points via `pyproject.toml`:

```toml
# in someone-elses-plugin-package/pyproject.toml
[project.entry-points."arc.tools"]
my_pdf_table_extractor = "arc_pdf_plugin:PdfTableExtractorTool"

[project.entry-points."arc.skills"]
extract_invoice = "arc_pdf_plugin:ExtractInvoiceSkill"

[project.entry-points."arc.toolsets"]
pdf_extras = "arc_pdf_plugin:PDF_EXTRAS"
```

**Secondary**: filesystem scan of `~/.arc/plugins/`:

```
~/.arc/plugins/
├── manifest.toml             ← optional: declare a search order, disable a plugin
├── tools/
│   ├── my_local_tool.py      ← single-file plugin
│   └── complex_plugin/
│       ├── plugin.toml       ← plugin manifest (required for dir plugins)
│       ├── tool.py
│       └── helpers.py
└── skills/
    └── my_skill.py
```

Filesystem plugins are **second-priority** — they fill the "I want to write
a quick local tool without making a package" case. Entry points are the
canonical distribution path.

**Why both**:

- **Entry points** are the Python ecosystem standard (`importlib.metadata`).
  Distributable via PyPI / `pip install`. Versioning via package metadata.
- **Filesystem** is the no-friction path for solo experimentation. Users
  edit a file in `~/.arc/plugins/` and reload.

**Rejected alternatives**:

- Decorator-only registration (`@arc.tool`) — requires the plugin to import
  arc at module top-level, which is hostile to plugins that work standalone.
- Config-file-driven (`config.yml: plugins: [...]`) — fragile when paths
  move; harder to share.

### 2.2 Registration API

Plugins export **classes** (not instances). Discovery instantiates them.

For tools, the existing `BaseTool` interface is sufficient.

For skills, the existing `Skill` interface is sufficient.

For toolsets, plugins export a `Toolset` instance:

```python
PDF_EXTRAS = Toolset(
    name="pdf_extras",
    description="Advanced PDF extraction (tables, forms, signatures)",
    tools=[PdfTableExtractorTool(), PdfFormFieldsTool()],
    rules=[RoutingRule(toolset="pdf_extras", condition=...)],
)
```

For single tools (no toolset), the discovery wraps them in a synthetic
`<plugin_name>` toolset, or they may declare which existing toolset they
extend via a class attribute:

```python
class PdfTableExtractorTool(BaseTool):
    name = "pdf_table_extract"
    extends_toolset = "document"  # NEW field — optional
    ...
```

When set, the tool joins the named built-in toolset. When unset (and the
plugin doesn't ship a `Toolset`), the loader creates a `plugin:<name>`
toolset.

### 2.3 Manifest schema (TOML)

Use **TOML** to match `pyproject.toml`. Lightweight, native to Python 3.11+.

For directory plugins (`~/.arc/plugins/tools/complex_plugin/plugin.toml`):

```toml
[plugin]
name = "arc-pdf-extras"
version = "0.1.0"
description = "Advanced PDF extraction tools"
author = "Acme Corp"
arc_min_version = "0.3.0"     # required arc version

[plugin.entry]
tools = ["tool:PdfTableExtractorTool", "tool:PdfFormFieldsTool"]
skills = ["skill:ExtractInvoice"]
toolsets = ["PDF_EXTRAS"]

[plugin.requires]
python = ["camelot-py>=0.11", "pdfplumber>=0.10"]
system = ["poppler-utils"]   # optional — informational only

[plugin.permissions]
network = false              # if true, requires explicit user approval
filesystem_write = false
```

For single-file plugins, embed the manifest as a module-level dict:

```python
# ~/.arc/plugins/tools/my_local_tool.py
ARC_PLUGIN = {
    "name": "my-local-tool",
    "version": "0.0.1",
    "requires": {"python": ["beautifulsoup4>=4.12"]},
}

class MyLocalTool(BaseTool):
    name = "my_tool"
    ...
```

### 2.4 Optional dependencies

The loader runs in three steps per plugin:

1. **Parse manifest** — gather `requires.python` list.
2. **Probe imports** — for each required dist, try `importlib.metadata
   .version(dist)` (or a `--strict` flag does a real `importlib.import_module`
   check on a sentinel module).
3. **Skip with warning** if any required dep is missing.

Logs at INFO:

```
plugin: arc-pdf-extras 0.1.0 enabled (2 tools, 1 skill)
plugin: arc-mass-spec disabled — missing: rdkit>=2023.09
       install: pip install arc-mass-spec[full]   (hint from manifest)
plugin: my-local-tool disabled — manifest parse error: ...
```

Emit a runtime event `plugin.loaded` / `plugin.disabled` for each (joins
0087 telemetry).

### 2.5 Conflict handling

If a plugin tool's `name` collides with a built-in:

- Built-ins always win.
- Log warning: `plugin: skipping tool 'read_file' from arc-bad-plugin — name conflicts with built-in`.
- The plugin tool is dropped (its toolset, if shipped, is otherwise loaded).

If two plugins collide:

- First registered wins (i.e., entry-point order or filesystem alphabetical).
- Log warning.

Same rules for skills.

Future enhancement (not v1): explicit namespacing via `plugin_id:tool_name`
when invoking. Out of scope here.

### 2.6 Sandboxing

Plugin tools share the same `ActionGuard` rules as built-ins. The guard
treats them by name; the policy is read from the same `ActionGuard` config
(`tool_policies` in `runtime/guard.py`).

For a new plugin with a never-before-seen name, the default policy applies
(ALLOW for non-destructive default actions). The plugin manifest's
`permissions` block (§2.3) can request stricter policy:

- `network: true` → guard tags as ESCALATE on first invocation per session.
- `filesystem_write: true` → guard tags as ESCALATE if writes outside
  workspace.

This is enforced by the `ActionGuard` consulting `registry.get_plugin_manifest
(tool_name)`. New API on the registry.

### 2.7 Hot reload — punt to v2

Hot reload (`arc plugin reload`) requires either:

- Tearing down and rebuilding `ToolRegistry`/`SkillRegistry` mid-run, *and*
  ensuring no in-flight pipeline holds stale references; or
- Process restart.

For v1: **process restart only**. The `arc plugin reload` CLI is a thin
wrapper that exits with a known status code and a message ("restart arc to
apply"). This is honest and avoids subtle bugs.

### 2.8 CLI commands

Sub-commands under `arc plugin`:

| Command | Behavior |
|---|---|
| `arc plugin list` | Shows enabled + disabled plugins with reason for each disabled |
| `arc plugin info <name>` | Verbose: manifest, tool/skill names, deps status |
| `arc plugin install <path-or-pkg>` | If path → copy/symlink into `~/.arc/plugins/`. If `pkg` → `pip install` it (uv preferred, fallback pip). |
| `arc plugin remove <name>` | Remove from `~/.arc/plugins/` or uninstall the package. Confirms before package removal. |
| `arc plugin reload` | Prints "restart arc to apply" (placeholder for v2 hot reload) |
| `arc plugin doctor` | Diagnostics: which discovery paths were scanned, which were rejected, missing deps |

---

## 3. Files to add / change

### 3.1 New files

```
src/plugins/
├── __init__.py
├── loader.py             ~180 lines — main discovery + registration entry point
├── manifest.py           ~120 lines — TOML/dict parsing, schema validation
├── deps.py               ~80 lines  — dep probing
├── cli.py                ~150 lines — `arc plugin` subcommands
└── builtins_index.py     ~50 lines  — exposes built-in names for conflict detection
```

### 3.2 Modified files

- `src/agent.py` — replace direct `ALL_TOOLSETS` iteration with
  `plugins.load_into(self.registry, self.skill_registry)`.
- `src/main.py` — add `arc plugin` subcommand to `dispatch()`.
- `src/ui/app.py` — add `/plugin list` slash command (optional, defer to
  later if v1 is tight).
- `src/tools/base.py` — add optional `extends_toolset: str | None = None`
  class attribute.
- `src/tools/registry.py` — add `plugin_manifests: dict[str, PluginManifest]`
  and `get_plugin_manifest(tool_name)`.
- `src/runtime/guard.py` — when classifying a tool not in built-in policies,
  consult the plugin manifest for `permissions` block.
- `src/runtime/events/schema.py` — add event types `plugin.loaded`,
  `plugin.disabled`, `plugin.dep_missing`.
- `pyproject.toml` — declare arc's own toolsets via `[project.entry-points.
  "arc.toolsets"]` to dogfood the mechanism (optional in v1).

---

## 4. Layout for distributable plugins

This is what a third-party plugin package looks like:

```
arc-pdf-extras/
├── pyproject.toml
├── README.md
├── src/
│   └── arc_pdf_extras/
│       ├── __init__.py
│       ├── tool_table.py     ← class PdfTableExtractorTool(BaseTool)
│       ├── tool_forms.py     ← class PdfFormFieldsTool(BaseTool)
│       ├── skill_invoice.py  ← class ExtractInvoiceSkill(Skill)
│       └── toolset.py        ← PDF_EXTRAS = Toolset(...)
└── tests/
```

```toml
# pyproject.toml
[project]
name = "arc-pdf-extras"
version = "0.1.0"
dependencies = ["arc-agent>=0.3", "camelot-py>=0.11", "pdfplumber>=0.10"]

[project.entry-points."arc.tools"]
pdf_table = "arc_pdf_extras.tool_table:PdfTableExtractorTool"
pdf_forms = "arc_pdf_extras.tool_forms:PdfFormFieldsTool"

[project.entry-points."arc.skills"]
extract_invoice = "arc_pdf_extras.skill_invoice:ExtractInvoiceSkill"

[project.entry-points."arc.toolsets"]
pdf_extras = "arc_pdf_extras.toolset:PDF_EXTRAS"
```

User install: `pip install arc-pdf-extras` → restart arc → plugin loaded.

---

## 5. Layout for filesystem plugins

### 5.1 Single-file

`~/.arc/plugins/tools/word_count.py`:

```python
from tools.base import BaseTool, InputSchema, ToolProperty

ARC_PLUGIN = {
    "name": "word-count",
    "version": "0.0.1",
    "extends_toolset": "data",
}

class WordCountTool(BaseTool):
    name = "word_count"
    description = "Count words in a string"
    @property
    def input_schema(self) -> InputSchema:
        return InputSchema(properties={"text": ToolProperty(type="string", description="...")}, required=["text"])
    def execute(self, tool_input: dict) -> str:
        return str(len(tool_input["text"].split()))
```

Loader scans `~/.arc/plugins/tools/*.py`, imports each, finds the
`ARC_PLUGIN` dict, registers any `BaseTool` subclass defined in the module.

### 5.2 Directory

`~/.arc/plugins/skills/my_pipeline/`:

```
plugin.toml
__init__.py        ← exports class MyPipelineSkill(Skill)
helpers.py
```

`plugin.toml` is the manifest (§2.3 format). Loader reads it, adds the
directory to `sys.path` (with cleanup on shutdown), imports the named
entries.

---

## 6. Discovery + registration flow

Detailed in `loader.py`:

```python
def discover_plugins() -> list[DiscoveredPlugin]:
    """Two-pass discovery: entry points first, filesystem second."""
    plugins: list[DiscoveredPlugin] = []
    plugins.extend(_discover_entry_points())
    plugins.extend(_discover_filesystem())
    return plugins

def _discover_entry_points() -> list[DiscoveredPlugin]:
    """Iterate importlib.metadata.entry_points for arc.tools/skills/toolsets groups."""
    eps = entry_points()
    for ep in eps.select(group="arc.tools"):
        yield _from_entry_point(ep, kind="tool")
    for ep in eps.select(group="arc.skills"):
        yield _from_entry_point(ep, kind="skill")
    for ep in eps.select(group="arc.toolsets"):
        yield _from_entry_point(ep, kind="toolset")

def _discover_filesystem() -> list[DiscoveredPlugin]:
    """Scan ~/.arc/plugins/{tools,skills}/."""
    root = arc_home() / "plugins"
    if not root.exists():
        return []
    # ... walk tools/, skills/, build DiscoveredPlugin records ...

def load_into(registry: ToolRegistry, skill_registry: SkillRegistry) -> LoadReport:
    """Apply discovery to the live registries.
    Skips plugins whose deps are missing. Returns a report for telemetry."""
    report = LoadReport()
    for plugin in discover_plugins():
        manifest = plugin.manifest
        # Conflict check
        if manifest.name in registry.builtin_tool_names() and plugin.kind == "tool":
            report.skipped[plugin.name] = "name conflict with built-in"
            continue
        # Dep probe
        missing = probe_dependencies(manifest.requires_python)
        if missing:
            report.disabled[plugin.name] = f"missing deps: {missing}"
            _emit_plugin_event("plugin.dep_missing", plugin, missing)
            continue
        # Register
        try:
            plugin.instantiate_and_register(registry, skill_registry)
            report.enabled.append(plugin.name)
            _emit_plugin_event("plugin.loaded", plugin)
        except Exception as e:
            report.failed[plugin.name] = repr(e)
    return report
```

`Agent.__init__` becomes:

```python
self.registry = ToolRegistry()
for toolset in ALL_TOOLSETS:
    self.registry.register_toolset(toolset)
self.skill_registry = SkillRegistry()
from plugins.loader import load_into
load_into(self.registry, self.skill_registry)
```

---

## 7. CLI design

`src/plugins/cli.py`:

```python
def cmd_plugin(argv: list[str]) -> None:
    """arc plugin <subcommand>"""
    p = argparse.ArgumentParser(prog="arc plugin")
    sub = p.add_subparsers(dest="action", required=True)

    sub.add_parser("list", help="Show installed plugins")
    sub.add_parser("doctor", help="Diagnose plugin discovery")

    p_info = sub.add_parser("info"); p_info.add_argument("name")
    p_install = sub.add_parser("install"); p_install.add_argument("target")
    p_remove = sub.add_parser("remove"); p_remove.add_argument("name")
    sub.add_parser("reload")

    args = p.parse_args(argv)
    if args.action == "list":
        _cmd_list()
    elif args.action == "info":
        _cmd_info(args.name)
    # ... etc.
```

Wire into `main.py:dispatch()`:

```python
if argv and argv[0] == "plugin":
    from plugins.cli import cmd_plugin
    cmd_plugin(argv[1:])
    return
```

### 7.1 `arc plugin list` output

```
Enabled plugins (3):
  arc-pdf-extras 0.1.0       2 tools, 1 skill        [entry-point]
  my-local-tool 0.0.1        1 tool                  [~/.arc/plugins/tools/word_count.py]
  arc-news-prefs 0.4.2       1 skill                 [entry-point]

Disabled plugins (1):
  arc-mass-spec 0.2.0        missing: rdkit>=2023.09
                             install: pip install arc-mass-spec[full]

Conflicts (0):
  (none)
```

### 7.2 `arc plugin info <name>`

```
arc-pdf-extras 0.1.0
  Source: entry-point  (installed at /opt/venv/lib/.../arc_pdf_extras/)
  Author: Acme Corp
  Description: Advanced PDF extraction tools

  Tools (2):
    pdf_table             extends_toolset=document    [enabled]
    pdf_forms             extends_toolset=document    [enabled]

  Skills (1):
    extract_invoice                                    [enabled]

  Requires:
    Python: camelot-py>=0.11 ✓     pdfplumber>=0.10 ✓
    System: poppler-utils  ✓ (informational only)

  Permissions:
    network: false                  filesystem_write: false
```

---

## 8. Example plugin (shape only — not code to copy)

Hypothetical `arc-markdown-tools` plugin:

```
arc-markdown-tools/
├── pyproject.toml
├── README.md
└── src/
    └── arc_md_tools/
        ├── __init__.py            ← (re-exports for convenience)
        ├── tool_md_to_html.py     ← class MdToHtmlTool(BaseTool)
        ├── tool_md_outline.py     ← class MdOutlineTool(BaseTool)
        ├── skill_doc_summary.py   ← class DocSummarySkill(Skill)
        └── toolset.py             ← MARKDOWN = Toolset(
                                          name="markdown",
                                          tools=[MdToHtmlTool(), MdOutlineTool()],
                                          rules=[...],
                                       )
```

`pyproject.toml`:

```toml
[project]
name = "arc-markdown-tools"
version = "0.1.0"
dependencies = ["arc-agent>=0.3", "markdown>=3.5", "beautifulsoup4>=4.12"]

[project.entry-points."arc.toolsets"]
markdown = "arc_md_tools.toolset:MARKDOWN"

[project.entry-points."arc.skills"]
doc_summary = "arc_md_tools.skill_doc_summary:DocSummarySkill"
```

Notes:

- The plugin doesn't import any arc internals beyond `tools.base.BaseTool`,
  `tools.toolset.Toolset`, `skills.base.Skill`, `planning.schema.Step`,
  `shared_types.RoutingRule`, `routing.conditions.{any_keyword, has_extension}`.
- These imports form the **plugin API surface**. Document them in
  `_plans/0088-plugin-api.md` (future doc, post-implementation).

---

## 9. Phase breakdown

| Phase | Title | Scope |
|---|---|---|
| **0088a** | Plugin schema + manifest parser | `plugins/manifest.py`, `plugins/deps.py`, telemetry hooks |
| **0088b** | Entry-point discovery + ToolRegistry/SkillRegistry integration | `plugins/loader.py`, `agent.py` wiring |
| **0088c** | Filesystem discovery + `extends_toolset` + manifest from module dict | `plugins/loader.py` (extend), `tools/base.py` (`extends_toolset` attr) |
| **0088d** | Conflict handling + sandbox permission integration | `plugins/loader.py` (conflict logic), `runtime/guard.py` (permission consult) |
| **0088e** | `arc plugin` CLI + doctor + telemetry events | `plugins/cli.py`, `main.py:dispatch()` |

### 0088a — Schema + manifest

- `Manifest` dataclass with required fields (`name`, `version`) and optional
  ones (`description`, `arc_min_version`, `requires_python`, `permissions`).
- `parse_toml_manifest(path) -> Manifest`.
- `parse_dict_manifest(raw: dict) -> Manifest`.
- Schema validation: known fields, value ranges, sensible defaults.
- `probe_dependencies(reqs: list[str]) -> list[str]` — returns missing dist names.
- Plugin events: define the three new `RuntimeEvent` types
  (`plugin.loaded`/`plugin.disabled`/`plugin.dep_missing`).
- Tests: round-trip a valid manifest; reject ones missing `name`; probe with
  a known-installed and known-missing dep.

**Verification**: a unit test that loads a fake manifest and reports correct
status.

### 0088b — Entry points

- `_discover_entry_points()` per §6.
- `DiscoveredPlugin` dataclass with `kind: str`, `obj: type | Toolset`,
  `manifest: Manifest`.
- `instantiate_and_register(registry, skill_registry)`:
  - For tools: `registry.register(plugin.obj())`.
  - For toolsets: `registry.register_toolset(plugin.obj)`.
  - For skills: `skill_registry._by_name[plugin.obj().name] = plugin.obj()`
    (extend `SkillRegistry` with a `register(skill: Skill)` method first —
    cleaner API).
- `agent.py`: replace direct `ALL_TOOLSETS` loop with `load_into(...)` after
  the built-in loop.

**Verification**: build a sample plugin package (in `tests/fixtures/`), install
it into a venv, run `arc plugin list` → it appears.

### 0088c — Filesystem discovery

- Walk `~/.arc/plugins/tools/*.py` and `~/.arc/plugins/skills/*.py` (single
  file).
- Walk `~/.arc/plugins/tools/*/plugin.toml` and `~/.arc/plugins/skills/*/plugin.toml`
  (dir form).
- Use `importlib.util.spec_from_file_location` (single file) or temporary
  `sys.path` add (dir form).
- Honor `ARC_PLUGIN` module dict for single files.
- Honor `extends_toolset` attribute on tool classes.

**Verification**: drop a single-file tool into `~/.arc/plugins/tools/`,
restart, verify it appears in `arc plugin list`.

### 0088d — Conflicts + permissions

- In `loader.py`: collect built-in names first, then check plugins.
  Built-ins win. Track conflicts in the load report.
- In `guard.py`: when classifying a tool name not in built-in policies,
  consult `registry.get_plugin_manifest(tool_name).permissions`. If
  `network: true` or `filesystem_write: true`, escalate on first use of
  the session.
- `ToolRegistry.get_plugin_manifest(tool_name) -> Manifest | None`.

**Verification**: plugin with `permissions.network=true` triggers ESCALATE
on first call; the user approval flow runs.

### 0088e — CLI

- `plugins/cli.py` with `cmd_plugin(argv)`.
- `arc plugin list` / `info` / `doctor` read from a `PluginCatalog` built
  by `loader.discover_plugins()` (note: this is the discovery step
  *without* registration — does not require a live Agent).
- `arc plugin install <path-or-pkg>`:
  - If path → `shutil.copy` / symlink into `~/.arc/plugins/tools/` or
    `skills/` based on the plugin's declared kind in the manifest. Reject
    if no manifest.
  - If a name with no `/` → `uv pip install <name>` (fall back to `pip`
    if uv missing). Caveat: this modifies the current venv — log and
    confirm.
- `arc plugin remove <name>`:
  - Filesystem plugin → `shutil.rmtree`.
  - Package plugin → `uv pip uninstall <name>` after confirmation.
- `arc plugin reload`:
  - Prints "restart arc to apply" — v1 stops here.

**Verification**: `arc plugin install ./tests/fixtures/arc-md-tools` —
plugin appears. `arc plugin remove arc-md-tools` — plugin gone.

---

## 10. Backwards compatibility

- The built-in toolsets and skills continue to live exactly where they are.
  No file moves required by this plan.
- An arc install with `~/.arc/plugins/` empty and no entry-point plugins
  behaves identically to today.
- `ALL_TOOLSETS` and `ALL_SKILLS` remain exported for any external code that
  may import them directly (tests, scripts). The loader registers built-ins
  via these exports and then layers plugins on top.

---

## 11. Risks

| Risk | Mitigation |
|---|---|
| Malicious plugin reads disk / hits network | Permissions block in manifest; guard escalates first-use; never auto-install from URL |
| Plugin imports break agent startup | All discovery wrapped in try/except; failure logs and disables that plugin only |
| Plugin name conflicts silently shadow built-ins | Built-ins always win; conflicts surfaced in `arc plugin list` |
| Filesystem-plugin path traversal | Path resolution via `Path.resolve()` and explicit allowlist (`~/.arc/plugins/`) — reject any path outside it |
| Dep probe is slow on many plugins | `importlib.metadata.version` is O(1); probe is cheap. Limit total plugins to ~100 (warning at 50) |
| Plugin uses an `arc_min_version` newer than installed | Manifest parser checks; plugin disabled with clear message |
| Versioning of the plugin API itself | Document the public surface in `_plans/0088-plugin-api.md` (post-impl); follow semver |

---

## 12. Open questions

**Q1**. Should plugins be allowed to register *new toolsets* freely, or only
extend existing ones via `extends_toolset`? Recommend allow both — solo
extensions go to existing toolsets; cohesive families ship as their own.

**Q2**. How do plugins declare router-rules for their tools? Two options:

- **Implicit**: a single tool with `extends_toolset = "document"` inherits
  the document toolset's rules.
- **Explicit**: plugin manifest can include `routing` rules, or the plugin's
  `Toolset(...)` carries `rules=[...]`.

Recommend **explicit via toolset**. If you want fine-grained routing for your
tool, ship a toolset.

**Q3**. Should there be a plugin marketplace UI inside arc? No — defer.
The package ecosystem (PyPI) is the marketplace.

**Q4**. Where do plugins put data they generate (logs, caches)? Recommend
`~/.arc/plugin_data/<plugin_name>/` resolved via a new `session_paths`
helper, mirror to per-session if relevant.

**Q5**. Should hot reload be in v1? **No.** v2.

**Q6**. Type-stub package (`arc-types`) for plugin authors to import without
pulling in all of arc? Defer. v1 plugin authors install arc as a dev
dependency.

---

## 13. Verification — end-to-end

After all phases land:

1. `pip install ./tests/fixtures/arc-md-tools` into the venv.
2. `arc plugin list` shows it enabled.
3. Run an agent turn that triggers a `markdown` tool — succeeds.
4. `arc plugin remove arc-md-tools` — clean.
5. Drop a single-file `~/.arc/plugins/tools/echo_tool.py`. Restart.
6. `arc plugin list` shows it.
7. Modify the file. `arc plugin reload` → message "restart arc to apply".
8. Manifest declares `requires: ["does-not-exist>=99"]` → plugin disabled,
   `arc plugin info` shows the missing dep.
9. Plugin tool with `permissions.network=true` → first call escalates.

---

## 14. Reading order for the implementer

1. `_plans/0079-runtime-as-god.md` — make sure your plugin design doesn't
   leak control flow into tools/skills.
2. This document.
3. The relevant phase doc.

For the user-facing API surface of plugins, write `_plans/0088-plugin-api.md`
after 0088e lands, capturing the imports a plugin author may rely on.
