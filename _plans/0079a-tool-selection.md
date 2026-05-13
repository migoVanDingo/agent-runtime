# 0079a — Phase A: Tool Selection Unification

> **Read first:** `_plans/0079-runtime-as-god.md` §0 and §4.
> This phase is independent of all others. Land it first; it's a
> low-risk warm-up that proves the pipeline still works end-to-end.

## Goal

Replace the three-tier tool-selection logic in `ExecutionStage` with a
**single consistent policy** owned by infrastructure. Move hardcoded
utility-tool relationships (`write_file` ↔ `make_directory`,
`bash_exec` ↔ `read_file`) out of code and into config so they are
data-driven and inspectable.

This addresses **DRIFT-4** from the brief.

## What's broken today

In `src/runtime/stages/execution.py:215-228` the tool selection cascade
has three branches with three different policies:

```python
# src/runtime/stages/execution.py:215-228
if step.action_type == ActionType.CONVERSATION:
    tools = []
elif step.tool:                                  # PLAN-TIME — pre-selected single tool
    tools = self._registry.get_tool_schema(step.tool)
    utility_tools = _step_utility_tools(step)    # HEURISTIC — hardcoded extras
    for ut in utility_tools:
        tools.extend(self._registry.get_tool_schema(ut))
    logger.info(f"  tool: {step.tool}" + (f" (+{utility_tools})" if utility_tools else ""))
else:                                            # RUNTIME — router selects toolsets
    selected = self._router.select(step.description, self._messenger.get_messages())
    if step.action_type.value not in selected:
        selected = list(set(selected + [step.action_type.value]))
    tools = self._registry.get_toolset_schema(selected)
    logger.info(f"  toolsets (fallback): {selected}")
```

And `src/runtime/stages/execution.py:74-81`:

```python
def _step_utility_tools(step: Step) -> list[str]:
    utilities = []
    if step.tool == "write_file":
        utilities.append("make_directory")
    if step.tool == "bash_exec":
        utilities.append("read_file")
    return utilities
```

Two problems:
1. **Three branches, three mental models.** Plan-time pre-selection,
   hardcoded heuristics, runtime router. A reader can't predict what
   the LLM will see for any given step without running the code.
2. **Hardcoded utility map.** Adding "`grep_files` should also expose
   `read_file`" requires editing a function. Worse, it's invisible from
   config — debugging what tools a step had requires reading code.

## Target design

One function: `_resolve_step_tools(step) -> list[ToolSchema]`. It
applies these rules in order, **in code**, with no plan-time short-circuits:

1. If `step.action_type == CONVERSATION` → **no tools.**
2. Compute the **base set** of tool names:
   - If `step.tool` is set → start with `[step.tool]`.
   - Else → use the router's selected toolsets, expanded to tool names,
     ensuring `step.action_type.value` is in the selection.
3. **Augment** the base set with utility tools from
   `config.runtime.tool_policy.utility_tools` — a `dict[str, list[str]]`
   mapping a base tool name to its utility companions.
4. Resolve the final name list to schemas via the registry.
5. Log: `tool selection: base={...}, utilities={...}`.

The `step.tool` branch and the router branch now share post-processing
(utility augmentation, schema resolution, logging). The "three tiers"
collapse to "one tier with two sources of the base set."

## Files to change

| File | Why |
|------|-----|
| `src/config.py` | Add `ToolPolicyConfig` dataclass and wire into `RuntimeConfig`. |
| `config.yml` | Add `runtime.tool_policy.utility_tools` map. |
| `src/runtime/stages/execution.py` | Replace `_step_utility_tools` and lines 215-228 tool-selection block. |

## Detailed changes

### Change 1 — Add `ToolPolicyConfig` dataclass

**File:** `src/config.py`

After the `SandboxConfig` dataclass (currently ends around line 224),
insert:

```python
@dataclass
class ToolPolicyConfig:
    """Infrastructure policy for tool exposure to step execution.

    utility_tools: when a step's base tool is the key, the value's tools
    are also exposed to the step. Centralized so the relationships are
    data, not code.
    """
    utility_tools: dict[str, list[str]] = field(default_factory=dict)
```

In `RuntimeConfig` (currently `src/config.py:254-267`), add a field:

```python
@dataclass
class RuntimeConfig:
    events: EventsConfig
    sandbox: SandboxConfig
    pipeline: PipelineConfig
    plan_validator: PlanValidatorConfig
    plan_critic: PlanCriticConfig
    execution_monitor: ExecutionMonitorConfig
    context_manager: ContextManagerConfig
    council: CouncilConfig = field(default_factory=CouncilConfig)
    monitor_council: MonitorCouncilConfig = field(default_factory=MonitorCouncilConfig)
    synthesis_quality: SynthesisQualityConfig = field(default_factory=SynthesisQualityConfig)
    importance_council: ImportanceCouncilConfig = field(default_factory=ImportanceCouncilConfig)
    tool_policy: ToolPolicyConfig = field(default_factory=ToolPolicyConfig)   # ← NEW
```

In the loader function (the one that reads `raw["runtime"]` around
`src/config.py:370-456`), add:

```python
tool_policy = ToolPolicyConfig(
    utility_tools=rt.get("tool_policy", {}).get("utility_tools", {}),
)
# ... pass into RuntimeConfig(...) constructor:
runtime = RuntimeConfig(
    ...
    tool_policy=tool_policy,
)
```

Read the loader carefully and follow its existing style for optional
sections.

### Change 2 — Add config.yml entries

**File:** `config.yml`

Locate the `runtime:` block and add (sibling to `pipeline:`,
`execution_monitor:`, etc.):

```yaml
runtime:
  # ... existing entries ...

  tool_policy:
    utility_tools:
      write_file:
        - make_directory
      bash_exec:
        - read_file
```

These are exactly the relationships currently hardcoded in
`_step_utility_tools` — preserving behavior.

### Change 3 — Replace `_step_utility_tools` and the tool block

**File:** `src/runtime/stages/execution.py`

Delete the function `_step_utility_tools` at lines 74-81 entirely.

Add a new method on `ExecutionStage` (place it after `__init__` and
before `run`):

```python
def _resolve_step_tools(self, step: Step) -> list[dict]:
    """Single point of tool resolution for a step.

    1. CONVERSATION → []
    2. base set: step.tool (if set) else router-selected toolsets
    3. augment with config.runtime.tool_policy.utility_tools
    4. resolve names → schemas via registry
    """
    if step.action_type == ActionType.CONVERSATION:
        logger.info("  tools: none (CONVERSATION step)")
        return []

    # ── 1. Base tool name set ─────────────────────────────────────────
    if step.tool:
        base_names: list[str] = [step.tool]
        base_source = "step.tool"
    else:
        selected_sets = self._router.select(
            step.description, self._messenger.get_messages()
        )
        if step.action_type.value not in selected_sets:
            selected_sets = list(set(selected_sets + [step.action_type.value]))
        # Expand toolsets → names so utility augmentation is uniform.
        base_names = []
        for ts in selected_sets:
            base_names.extend(self._registry.toolset_tool_names(ts))
        base_source = f"router(toolsets={selected_sets})"

    # ── 2. Augment with utility tools ─────────────────────────────────
    utility_map = config.runtime.tool_policy.utility_tools
    utilities: list[str] = []
    for name in list(base_names):
        for u in utility_map.get(name, []):
            if u not in base_names and u not in utilities:
                utilities.append(u)

    final_names = base_names + utilities

    logger.info(
        f"  tool selection: base={base_names} ({base_source})"
        + (f" utilities={utilities}" if utilities else "")
    )

    # ── 3. Resolve to schemas ─────────────────────────────────────────
    schemas: list[dict] = []
    for name in final_names:
        schemas.extend(self._registry.get_tool_schema(name))
    return schemas
```

Replace the block at lines 215-228 with:

```python
tools = self._resolve_step_tools(step)
```

That's the entire substitution at the call site.

### Change 4 — Add `toolset_tool_names` on registry if missing

`_resolve_step_tools` calls `self._registry.toolset_tool_names(ts)`.
Check `src/tools/registry.py` for an existing method that returns the
tool names belonging to a toolset name. If one exists under a different
name (e.g., `tool_names_for_toolset`), use that name instead and update
the call in `_resolve_step_tools`. If none exists, add it:

```python
# src/tools/registry.py
def toolset_tool_names(self, toolset_name: str) -> list[str]:
    """Return the names of tools registered under a toolset."""
    toolset = self._toolsets.get(toolset_name)
    if toolset is None:
        return []
    return [t.name for t in toolset.tools]
```

(Adapt to the actual structure of `ToolRegistry`/`Toolset` — the
implementer should read `src/tools/registry.py` and `src/tools/toolsets.py`
to confirm naming.)

## Verification

```bash
# 1. Unit/integration suite still passes
pytest -x -q

# 2. App boots and answers a trivial question
python -m src.main <<< "what is 2+2"     # or however the CLI is invoked

# 3. A plan that uses step.tool='write_file' still gets make_directory
#    Run any flow that hits write_file and grep the log for:
#      "tool selection: base=['write_file'] (step.tool) utilities=['make_directory']"

# 4. A router-selected step still selects the right action_type
#    Run a query that triggers the planner (no workflow match) and
#    confirm the log shows:  "tool selection: base=[...] (router(toolsets=[...]))"
```

## Done when

- [ ] `src/runtime/stages/execution.py` has no function called `_step_utility_tools`.
- [ ] The three-branch `if/elif/else` at lines 215-228 is replaced by a single call.
- [ ] `config.runtime.tool_policy.utility_tools` is the single source of truth for utility augmentation.
- [ ] Logs clearly show base set source (`step.tool` vs `router(...)`) and utilities.
- [ ] `pytest` green.
- [ ] App boots; both planner-driven and workflow-driven flows still execute.

## Out of scope for this phase

- Changing `step.tool` semantics (still pre-selects a single tool when set).
  Phase **0079f** rethinks plan-time pre-selection if needed.
- Changing the router. The router is preserved as-is.
- Adding new utility relationships. Only port existing ones.
