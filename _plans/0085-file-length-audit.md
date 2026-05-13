# 0085 — File length audit and refactoring candidates

> **Audience:** Implementer with full codebase access but no prior context.
> Read this document end-to-end. Each section can be executed as an independent
> refactor; ordering only matters within a single file's section.
>
> **Reading order:** this file → the section for the file being refactored.
> No phase docs (`0085a` … ) needed — each file is its own atomic refactor.

---

## 0. Goal

Identify every `.py` file at or above 500 lines in `src/`, decide whether it
should be kept as-is or split, and (for splits) specify the new file boundaries
to a degree of detail that a Sonnet-class implementer can execute mechanically.

The 600-line ceiling is the architectural rule. Files inherently structured as
flat registries (long `from ... import ...` blocks plus a couple of data tables)
are allowed to exceed it; everything else must be decomposed.

## 1. Scope

Verified via `find src -name "*.py" | xargs wc -l | sort -rn`:

| Lines | File | Action |
|---|---|---|
| 880 | `src/ui/app.py` | **SPLIT** (§2) |
| 607 | `src/main.py` | **SPLIT** (§3) |
| 561 | `src/tools/toolsets.py` | **KEEP** with carve-out (§4) |
| 537 | `src/runtime/stages/execution.py` | **SPLIT** (§5) |
| 522 | `src/runtime/context_manager.py` | **SPLIT** (§6) — also feeds 0089 |
| 510 | `src/tools/implementations/container/tools.py` | **SPLIT** (§7) |
| 508 | `src/config.py` | **SPLIT** (§8) |
| 479 | `src/service/inprocess.py` | **WATCH** (§9) — near threshold; recommend pre-emptive split |

Also surveyed but under 500 lines (no action):

- `src/runtime/council.py` (378), `src/runtime/tool_loop.py` (377)
- `src/planning/prompts.py` (336), `src/runtime/guard.py` (334)
- `src/planning/planner.py` (311), `src/runtime/artifact_store/crud.py` (301)
- `src/runtime/stages/continuation.py` (294), `src/runtime/entity_critic.py` (285)

These are healthy. Re-audit only if a feature push grows one above 500.

## 2. `src/ui/app.py` (880 lines) — **SPLIT**

### Current structure

Single file containing:

1. Module-level imports + the `_STAGE_LABELS` map (lines 1–63)
2. `_SuppressStderr` context manager (66–87)
3. `_build_app()` — assembles the `prompt_toolkit.Application`, including all
   key bindings, visual line-navigation helpers
   (`_pos_to_visual`/`_visual_to_pos`), the `_input_line_prefix`, layout
   construction, and style (90–352)
4. `_execute_command()` — slash command dispatcher (357–431)
5. `_handle_input()` — input router (escalation / ASK_USER / queue / send)
   (436–501)
6. `_consume_events()` — agent-event → model translator (506–583)
7. `_spinner_tick()` (588–596)
8. `_escalation_watcher()` (601–641)
9. `_interactive()` — top-level interactive loop (646–701)
10. `_handle_resume()` / `_handle_resume_selection()` — session picker
    (711–824)
11. `_headless()` — `--print` mode (829–845)
12. `_run_async()` / `run()` — entry point (850–880)

### Natural seams

- **Visual-line key handling** (~180 lines, 159–259 in the current file).
  This is a self-contained subsystem (the `_pos_to_visual`/`_visual_to_pos`
  helpers plus the `up`/`down`/`pageup`/`pagedown` bindings) that doesn't
  reference any UI state beyond `input_model.escalation_gate` and
  `input_model.input_gate` for prefix width.
- **Layout construction** (~70 lines) is cleanly bracketed: the
  `conv_control`/`conv_window`/`spinner_window`/`separator`/`input_window`/
  `footer` block at 261–332 plus the `Layout(...)` and `Style(...)`
  construction. No outside references.
- **Background asyncio tasks** (`_consume_events`, `_spinner_tick`,
  `_escalation_watcher`) form a coherent module — they're the
  service-event-loop adapter.
- **Slash command dispatch** (`_execute_command`) is a separable handler table.
- **Session resume** (`_handle_resume` + `_handle_resume_selection`) is a
  ~120-line concern that imports rich at call-site and is only used when the
  user types `/resume`.

### Proposed split

```
src/ui/
├── app.py                  ~170 lines — public run(), arg parsing, _interactive, _headless, _SuppressStderr
├── app_layout.py           ~140 lines — _build_app() body minus key bindings (layout, widgets, style)
├── app_keybindings.py      ~180 lines — KeyBindings factory: enter/newline/exit/pause + visual line nav
├── app_commands.py         ~100 lines — _execute_command (slash command table)
├── app_input_router.py     ~80 lines  — _handle_input (picker/slash/escalation/ASK_USER/queue/send)
├── app_event_bridge.py     ~150 lines — _consume_events, _spinner_tick, _escalation_watcher
└── app_resume.py           ~150 lines — _handle_resume, _handle_resume_selection
```

Total ≈ 970 lines (slightly higher than current 880 due to module docstrings
and the imports each new file carries). No file exceeds 200 lines.

#### File-by-file responsibilities

**`src/ui/app.py`** (entry point — kept thin):

- `run(argv)` and `_run_async(args)` (unchanged behavior)
- `_interactive(service, info, args)` — constructs the four UI models, calls
  `build_app()` from `app_layout`, starts the three asyncio tasks (each
  imported from `app_event_bridge`), and runs the app
- `_headless(service, message)`
- `_SuppressStderr`
- `_STAGE_LABELS` dict (used only inside `_consume_events`, but exporting it
  from here keeps it discoverable)

**`src/ui/app_layout.py`**:

```python
def build_app(
    conv: ConversationModel,
    spinner: SpinnerModel,
    input_model: InputModel,
    service: AgentService,
    app_state: dict,
) -> tuple[Application, Buffer]:
    """Construct layout, widgets, style. Key bindings imported from app_keybindings."""
```

**`src/ui/app_keybindings.py`**:

```python
def build_key_bindings(
    input_model: InputModel,
    conv: ConversationModel,
    service: AgentService,
    app_state: dict,
) -> KeyBindings:
    """Visual-line nav + submit/newline + ESC pause + Ctrl+D exit."""

# Module-private helpers:
def _prefix_width(input_model) -> int: ...
def _effective_width(event, input_model) -> int: ...
def _pos_to_visual(text, pos, width) -> tuple[int, int]: ...
def _visual_to_pos(text, target_row, target_col, width) -> int: ...
```

**`src/ui/app_commands.py`**:

```python
async def execute_command(
    name: str,
    args: str,
    conv: ConversationModel,
    spinner: SpinnerModel,
    input_model: InputModel,
    service: AgentService,
    app_state: dict,
) -> None: ...
```

**`src/ui/app_input_router.py`**:

```python
async def handle_input(
    text: str,
    conv: ConversationModel,
    spinner: SpinnerModel,
    input_model: InputModel,
    service: AgentService,
    app_state: dict,
) -> None: ...
```

**`src/ui/app_event_bridge.py`**:

```python
async def consume_events(service, conv, spinner, input_model, app_state) -> None: ...
async def spinner_tick(spinner, app_state) -> None: ...
async def escalation_watcher(input_model, conv, app_state) -> None: ...
```

**`src/ui/app_resume.py`**:

```python
async def handle_resume(service, conv, input_model, app_state) -> None: ...
async def handle_resume_selection(text, service, conv, input_model) -> None: ...
```

### Cross-cutting concerns to extract globally

- The pattern `app = app_state.get("app"); if app: app.invalidate()` appears
  ~14 times. Introduce `ui/app_state.py` with `class AppState(TypedDict)` and
  a small helper:

  ```python
  def invalidate(app_state: AppState) -> None:
      app = app_state.get("app")
      if app:
          app.invalidate()
  ```

  This isn't required by the 600-line rule but it removes ~30 lines of
  duplication across the split files.

### Acceptance for §2

- `wc -l src/ui/app.py` < 250
- No file in `src/ui/app_*.py` > 220 lines
- `arc-tui` starts; ESC pauses; `/help` renders; resume picker works
- `pytest tests/integration/test_service.py` (if present) still passes

---

## 3. `src/main.py` (607 lines) — **SPLIT**

### Current structure

1. Imports + module setup (1–25)
2. `print_session_banner` / `print_session_end` / `_fmt_ts` (30–47)
3. `_pick_resume_session` (50–77)
4. `_resolve_session_id` (80–114)
5. `_apply_decay_if_enabled` (117–126)
6. `_maybe_prompt_workflow_candidates` (129–162)
7. `_build_session_summary` (165–180)
8. `_shutdown_jvm_if_running` (183–190)
9. `_finalize_session` (193–223)
10. **`_cmd_wipe`** (226–335) — large subcommand, ~110 lines
11. **`_cmd_bootstrap`** (338–415) — large subcommand, ~80 lines
12. `main()` — interactive legacy CLI driver (418–574)
13. `dispatch()` — top-level argv router (577–603)

### Proposed split

```
src/
├── main.py                  ~280 lines — main(), dispatch(), banners, finalize
├── cli/
│   ├── __init__.py
│   ├── wipe.py              ~120 lines — _cmd_wipe + measurement helper
│   ├── bootstrap.py         ~90 lines — _cmd_bootstrap + migration map
│   ├── resume_picker.py     ~50 lines  — _pick_resume_session, _fmt_ts
│   └── session.py           ~120 lines — _resolve_session_id, _finalize_session, _build_session_summary
```

Rationale: `main.py` should host only the legacy interactive turn loop and the
top-level argv routing. Subcommands are independent and grow over time.

#### File-by-file

**`src/main.py`** (kept):

- Imports + `_RESUME_PICK` constant
- `print_session_banner`, `print_session_end`
- `_apply_decay_if_enabled`, `_maybe_prompt_workflow_candidates`,
  `_shutdown_jvm_if_running`
- `main()` — uses helpers from `cli.session` and `cli.resume_picker`
- `dispatch()` — calls `cli.wipe._cmd_wipe`, `cli.bootstrap._cmd_bootstrap`

**`src/cli/wipe.py`**:

```python
def cmd_wipe(argv: list[str]) -> None: ...
def _measure(path: Path) -> tuple[int, float]: ...
```

**`src/cli/bootstrap.py`**:

```python
def cmd_bootstrap(argv: list[str]) -> None: ...

_LEGACY_DIRS = {"_sessions": "sessions", "_rag": "rag", ...}
```

**`src/cli/resume_picker.py`**:

```python
def fmt_ts(ts: float) -> str: ...
def pick_resume_session(options: list[ResumableSession]) -> str | None: ...
```

**`src/cli/session.py`**:

```python
def resolve_session_id(resume_arg: str | None, store_enabled: bool) -> tuple[str, bool]: ...
def build_session_summary(agent: Agent) -> str: ...
def finalize_session(session_id: str, agent: Agent | None, store_enabled: bool) -> None: ...
```

### Acceptance for §3

- `wc -l src/main.py` ≤ 300
- `arc wipe -a -y` and `arc bootstrap` still work
- Legacy CLI (`arc --cli`) accepts an input, runs a turn, exits on `exit`/`quit`

### Risks

- `dispatch()` currently does `from ui.app import run as tui_run`. Keep that
  import in `main.py`; don't move it.
- The wipe/bootstrap helpers rely on `from session_paths import arc_home`
  resolving at import time of the function bodies, not module load. Preserve
  that by importing inside the functions.

---

## 4. `src/tools/toolsets.py` (561 lines) — **KEEP** (with one carve-out)

### Why keep

Lines 1–84 are the import block (one line per tool — ~80 tools). The remaining
~480 lines are 14 `Toolset(...)` literal constructions, each a flat data
structure (name, description, planning_note, tools list, rules list).

This file is essentially a manifest. Splitting one large data file into 14
small data files doesn't improve clarity — it scatters the global view, which
is the value of having it in one place. The team needs to see "all toolsets
at a glance" frequently (router debugging, planner system-prompt generation,
critic).

### One carve-out: routing rules

The four big regex-pattern routing rules (in `ANALYSIS` at 162–171, `SEARCH`
at 296–306, `GIT` at 338–345, `BRIEFBOT` at 408–418, `REVERSING` at 468–478,
`SYMBOLIC` at 508–518, `CONTAINER` at 548–557) total ~70 lines of inline
regex compilation. They are noisy and don't belong in a toolset data manifest.

**Extract to**: `src/routing/toolset_patterns.py`:

```python
import re

ANALYSIS_PATTERN = re.compile(
    r"\bwhat\s+(?:kind|type|sort)\s+of\s+(?:file|binary|program)\b"
    r"|\bwhat\s+is\s+this\s+(?:file|binary|program)\b"
    r"|\bfile\s+type\b"
    r"|\bwhat(?:'s|'s| is)\s+(?:the\s+)?(?:file\s+)?(?:type|format|architecture)\b",
    re.IGNORECASE,
)
# ... one constant per toolset that has a regex rule
```

Then in `toolsets.py` the rule shrinks to:

```python
from routing.toolset_patterns import ANALYSIS_PATTERN

RoutingRule(
    toolset="analysis",
    condition=lambda msg, _: bool(ANALYSIS_PATTERN.search(msg)),
),
```

`toolsets.py` drops to ~490 lines.

### Acceptance for §4

- `wc -l src/tools/toolsets.py` ≤ 500
- All existing routing tests pass
- Planner system prompt (via `build_tool_list(ALL_TOOLSETS)`) is byte-identical

---

## 5. `src/runtime/stages/execution.py` (537 lines) — **SPLIT**

### Current structure

1. Imports (1–32)
2. `_step_system()` — builds the per-step system prompt (35–77)
3. `class ExecutionStage:` (79–537)
   - `__init__` (91–119)
   - `_resolve_step_tools` (120–166)
   - `run` (168–185)
   - `_execute_plan` (187–459) — **272 lines**
   - `_run_step` (461–537) — **76 lines**

`_execute_plan` is the bulk. It contains:

- Plan persistence (record_plan call)
- Step-iteration with retry/replan/defer/skip/goal-achieved/escalate logic
- Pre-step guard
- Importance scoring
- Per-step persistence (record_step)
- Monitor assessment
- Decision-handling for 7 monitor verdicts (CONTINUE / RETRY / REPLAN /
  DEFER / SKIP / GOAL_ACHIEVED / ESCALATE)

### Natural seams

The 7 monitor-decision branches inside `_execute_plan` are the strongest seam.
Each is a coherent ~10–30 line block. They share `idx`/`queue`/`plan`/`step`
mutation, so extraction must be careful — but a `_StepLoopState` dataclass
makes this clean.

Tool resolution (`_resolve_step_tools`) is independent and could move out, but
it's only used here; the savings aren't worth the import cost.

### Proposed split

```
src/runtime/stages/
├── execution.py              ~250 lines — Stage class + run() + _execute_plan loop skeleton
└── execution/
    ├── __init__.py
    ├── step_loop.py          ~150 lines — _StepLoopState + apply_decision()
    ├── step_prompt.py        ~80 lines  — _step_system() builder
    └── step_runner.py        ~110 lines — _run_step() (tool loop construction + hooks)
```

#### `execution.py` (post-split)

```python
class ExecutionStage(Stage):
    name = "ExecutionStage"

    def __init__(self, provider, registry, ...): ...

    def _resolve_step_tools(self, step: Step) -> list[dict]: ...  # ~40 lines

    def run(self, context: PipelineContext) -> StageResult: ...   # ~18 lines

    def _execute_plan(self, plan, *, db_session_id=None) -> str:
        state = _StepLoopState.from_plan(plan, self._messenger)
        # Init: emit plan.created, record_plan persistence
        ...
        while state.has_more():
            step = state.current()
            self._prepare_step(step, state)
            tools = self._resolve_step_tools(step)
            result = self._execute_with_guard(step, tools, plan, state)
            self._post_step(step, result, plan, state, db_plan_id, db_session_id)
            apply_decision(state, plan, step, self._monitor.assess(...),
                           self._planner, self._skill_expansion, ...)
        return state.final_result()
```

Total ≈ 230–250 lines.

#### `execution/step_loop.py`

```python
@dataclass
class _StepLoopState:
    queue: list[Step]
    idx: int
    replan_count: int
    plan_start_index: int

    @classmethod
    def from_plan(cls, plan, messenger) -> "_StepLoopState": ...
    def has_more(self) -> bool: ...
    def current(self) -> Step: ...
    def step_display(self) -> int: ...

def apply_decision(
    state: _StepLoopState,
    plan: Plan,
    step: Step,
    assessment: MonitorAssessment,
    planner: Planner,
    skill_expansion,
    db_session_id: str | None,
    db_plan_id: int | None,
    identity,
) -> None:
    """Mutate state/plan/step in place according to assessment.decision.
    Handles CONTINUE / RETRY / REPLAN / DEFER / SKIP / GOAL_ACHIEVED / ESCALATE.
    Emits step.completed / replan.triggered / goal.achieved events.
    """
```

This is the only meaningful extraction. The 7 decision branches become 7
short functions called from one dispatch (`apply_decision`).

#### `execution/step_prompt.py`

```python
def step_system(
    plan: Plan,
    current_step: Step,
    agent_system: str,
    rag_context: str = "",
    step_display: int = 0,
) -> str: ...
```

#### `execution/step_runner.py`

```python
def run_step(
    *,
    step: Step,
    n_total: int,
    tools: list[dict],
    system: str,
    provider,
    messenger,
    context_mgr,
    tool_executor,
    user_gate,
    query: str,
    plan_start_index: int | None,
    step_display: int,
    checkpoint,
    parent_identity,
) -> str:
    """Build ToolLoopConfig + hooks, invoke ToolLoop.run, propagate tool_errors."""
```

Pure function — `ExecutionStage._run_step` becomes a one-liner that calls
this with `self._*` attributes.

### Acceptance for §5

- `wc -l src/runtime/stages/execution.py` ≤ 280
- No file in `execution/` > 200 lines
- All existing pipeline tests pass
- `plan.created` / `step.started` / `step.completed` / `replan.triggered` /
  `goal.achieved` events still fire with the same payloads
- Persistence: `PersistenceWriter.record_plan` / `record_step` call counts and
  arguments unchanged

### Risks

- `_StepLoopState.apply_decision` is a god-function in the making. Resist
  inlining all 7 branches into one method body — use per-decision sub-functions
  (e.g. `_handle_retry`, `_handle_replan`, …) within `step_loop.py`.
- The implicit invariant "if `step.flags.deferred or step.flags.retry_count > 0`
  then mark complete on DEFER" (line 403) is subtle; preserve the comparison
  exactly.

---

## 6. `src/runtime/context_manager.py` (522 lines) — **SPLIT**

This file is also load-bearing for 0089 (pluggable context manager). The split
proposed here is the **prerequisite refactor** to make the pluggable strategy
plan tractable.

### Current structure

1. Imports + token estimate + message text extraction (1–50)
2. `class ContextManager:` (53–522)
   - `__init__`, `set_summarizer`, `set_importance`, `get_importance` (55–86)
   - `pack()` — public entry (88–120) — **the strategy boundary**
   - `_score_messages()` (122–184) — embeddings + importance + recency
   - `_classify_importance()` (186–223) — rule-based
   - `_assign_fidelity()` (225–259) — score → FidelityLevel
   - `_pack_chronological()` (261–357) — budget pack with tool-pair atomicity
   - `_try_fit()` (359–387) — fit-with-fallback
   - `_compress_message()` (389–437)
   - `_compress_tool_result()` (439–474) — LLM-summarized
   - `_placeholder_message()` (476–522)

### Natural seams

Three distinct concerns:

1. **Scoring** (`_score_messages` + `_classify_importance`) — pure functions
   over messages → ScoredMessage. Depends on embedding model + Importance enum.
2. **Fidelity assignment** (`_assign_fidelity`) — scores + plan boundary → 
   fidelity per message. Pure.
3. **Packing** (`_pack_chronological` + `_try_fit`) — budget walk preserving
   tool-pair atomicity.
4. **Compression** (`_compress_*` + `_placeholder_*`) — converts a message to
   a smaller variant. The only stateful concern (LLM summarizer + cache).

### Proposed split

```
src/runtime/context/
├── __init__.py               re-exports ContextManager for back-compat
├── manager.py                ~140 lines — ContextManager facade (pack + state)
├── scoring.py                ~140 lines — _score_messages, _classify_importance
├── fidelity.py               ~50 lines  — _assign_fidelity
├── packing.py                ~140 lines — _pack_chronological, _try_fit, pair detection
└── compression.py            ~120 lines — _compress_message, _compress_tool_result, _placeholder_message
```

(Existing `src/runtime/context_manager.py` becomes a thin import shim
`from runtime.context.manager import ContextManager  # noqa: F401` so callers
don't need updating. Delete after 0089 lands and callers migrate.)

#### `manager.py`

```python
class ContextManager:
    def __init__(self, embedding_model=None) -> None: ...
    def set_summarizer(self, provider) -> None: ...
    def set_importance(self, message_index, importance) -> None: ...
    def get_importance(self, message_index): ...

    def pack(self, messages, current_query, plan_start_index=None) -> list[dict]:
        if not config.runtime.context_manager.enabled or not messages:
            return messages
        total = sum(_estimate_tokens(_message_text(m)) for m in messages)
        if total <= self._budget:
            return messages

        scored = score_messages(
            messages, current_query, plan_start_index,
            embedding_model=self._embedding_model(),
            importance_overrides=self._importance_overrides,
            half_life=self._half_life,
        )
        assign_fidelity(scored, plan_start_index=plan_start_index,
                        threshold_high=self._threshold_high,
                        threshold_mid=self._threshold_mid)
        packed = pack_chronological(scored, budget=self._budget,
                                    compressor=self._compressor())
        return [s.message for s in packed]
```

#### `scoring.py`

```python
def score_messages(
    messages: list[dict],
    current_query: str,
    plan_start_index: int | None,
    *,
    embedding_model,
    importance_overrides: dict[int, Importance],
    half_life: int,
) -> list[ScoredMessage]: ...

def classify_importance(
    msg: dict, index: int, total: int,
    overrides: dict[int, Importance],
) -> Importance: ...
```

#### `fidelity.py`

```python
def assign_fidelity(
    scored: list[ScoredMessage],
    *,
    plan_start_index: int | None,
    threshold_high: float,
    threshold_mid: float,
) -> None:
    """Mutates scored[*].fidelity in place."""
```

#### `packing.py`

```python
def pack_chronological(
    scored: list[ScoredMessage],
    *,
    budget: int,
    compressor: "Compressor",
) -> list[ScoredMessage]: ...

def _try_fit(
    s: ScoredMessage, budget: int, compressor: "Compressor",
) -> tuple[dict | None, int, FidelityLevel]: ...

def _detect_tool_pairs(scored: list[ScoredMessage]) -> dict[int, int]: ...
```

#### `compression.py`

```python
class Compressor:
    """Produces FULL/COMPRESSED/PLACEHOLDER variants of a message.
    Holds the LLM summarizer + summary cache.
    """
    def __init__(self, summarizer=None, max_chars=400): ...
    def compress(self, msg, index) -> dict: ...
    def placeholder(self, msg, index) -> dict: ...
    def compress_tool_result(self, content: str) -> str: ...
```

### Acceptance for §6

- All files in `runtime/context/` ≤ 200 lines
- `runtime/context_manager.py` is a 1-line import shim or removed entirely
- All callers of `from runtime.context_manager import ContextManager` continue
  to work (4 call sites: `RoutingStage`, `ExecutionStage`,
  `DirectExecutionStage`, `ToolLoop`)
- Behavior unchanged: packing produces the same message list for the same
  input (verify with a recorded test fixture)

### Risks

- `_compress_tool_result` uses the summarizer with a `Messenger` instance —
  this creates a dependency cycle if not careful. Keep that import lazy.
- `_pack_chronological` mutates `scored[*].message` / `.token_estimate` /
  `.fidelity` in place during the loop. The split preserves this; do not
  refactor it to a pure function in this pass (out of scope here; can be
  pursued in 0089 if desired).

---

## 7. `src/tools/implementations/container/tools.py` (510 lines) — **SPLIT**

### Current structure

1. Helpers: `_parse_test_cases`, `_mismatch_summary`, `_build_container_script`,
   `_run_in_container` (1–~200)
2. `RunTargetTool` (~205–283)
3. `DiffBehaviorTool` (~287–~440)
4. `FuzzTargetTool` (~445–510)

Three tools sharing a moderately large script-builder and a result-comparison
helper. Each tool is small once the helpers move out.

### Proposed split

```
src/tools/implementations/container/
├── tools.py                  KEEP — re-exports the three tool classes (~30 lines)
├── _helpers.py               ~180 lines — _parse_test_cases, _mismatch_summary,
│                                          _build_container_script, _run_in_container
├── run_target.py             ~90 lines  — RunTargetTool
├── diff_behavior.py          ~160 lines — DiffBehaviorTool
└── fuzz_target.py            ~80 lines  — FuzzTargetTool
```

The existing import in `toolsets.py`:
```python
from tools.implementations.container.tools import RunTargetTool, DiffBehaviorTool, FuzzTargetTool
```
keeps working because `tools.py` becomes:

```python
from tools.implementations.container.run_target import RunTargetTool
from tools.implementations.container.diff_behavior import DiffBehaviorTool
from tools.implementations.container.fuzz_target import FuzzTargetTool

__all__ = ["RunTargetTool", "DiffBehaviorTool", "FuzzTargetTool"]
```

### Acceptance for §7

- No file in `container/` (excluding `adapters.py`/`runtime.py`) > 200 lines
- All existing container tool tests pass
- `diff_behavior` end-to-end smoke (the canonical workflow) still produces a
  `DiffReport` with `all_match` field

### Risks

- `_run_in_container` reaches into `adapters.py` and `runtime.py`. Verify
  imports after extraction.
- Make sure the `_helpers.py` module stays private (`_helpers` prefix). It is
  internal to the container tool family.

---

## 8. `src/config.py` (508 lines) — **SPLIT**

### Current structure

- ~26 `@dataclass` definitions covering: LLM, timeouts, tools (radare2/ghidra/
  angr), routing, agent, artifact store (decay/workflow_discovery/sqlite_vec/
  project), storage, RAG, planning, plan validator, execution monitor, context
  manager, pipeline, plan critic, monitor council, synthesis quality,
  importance council, events, tool policy, continuation, sandbox, council
  (councillor/debate/master), container (limits/images), and top-level
  `AppConfig`.
- One `load_config()` function (~150 lines) that parses YAML into these
  dataclasses.

### Natural seams

The dataclasses split cleanly along subsystem:

- **Provider/LLM** — `LLMConfig`
- **Tool-related** — `ToolsConfig`, `Radare2Config`, `GhidraConfig`,
  `AngrConfig`, `ToolPolicyConfig`
- **Routing** — `RoutingConfig`
- **Agent** — `AgentConfig`
- **Artifact store** — 5 dataclasses
- **RAG** — `RagConfig`
- **Planning / runtime** — `PlanningConfig`, `PipelineConfig`,
  `PlanValidatorConfig`, `PlanCriticConfig`, `ExecutionMonitorConfig`,
  `ContextManagerConfig`, `EventsConfig`, `ContinuationConfig`,
  `SandboxConfig`, `MonitorCouncilConfig`, `SynthesisQualityConfig`,
  `ImportanceCouncilConfig`
- **Council** — `CouncillorConfig`, `DebateConfig`, `CouncilConfig`
- **Container** — `ContainerLimitsConfig`, `ContainerImagesConfig`,
  `ContainerConfig`
- **Top-level** — `AppConfig`, `load_config`

### Proposed split

```
src/config/
├── __init__.py               re-exports AppConfig, load_config (~10 lines)
├── llm.py                    ~25 lines  — LLMConfig
├── tools.py                  ~80 lines  — ToolsConfig + radare2/ghidra/angr/tool_policy
├── routing.py                ~15 lines  — RoutingConfig
├── agent.py                  ~10 lines  — AgentConfig
├── artifact_store.py         ~70 lines  — 5 artifact-store dataclasses
├── rag.py                    ~25 lines  — RagConfig
├── runtime.py                ~180 lines — Planning/Pipeline/Monitor/Continuation/etc.
├── council.py                ~35 lines  — Councillor/Debate/CouncilConfig
├── container.py              ~35 lines  — Container dataclasses
├── app.py                    ~30 lines  — AppConfig + TimeoutsConfig
└── loader.py                 ~180 lines — load_config() yaml parser
```

Total ≈ 700 lines (overhead from per-file imports + module docstrings), no
single file > 200 lines, and each module corresponds to one subsystem.

### Compat shim

Either:
- Keep `src/config.py` as a thin re-export:
  ```python
  from config.app import AppConfig
  from config.loader import load_config
  from config.llm import LLMConfig
  # ... re-export every dataclass currently importable from config
  ```
- Or rename `src/config.py` → `src/config/app.py` and update every import.
  This affects ~40 call sites (`from config import ...` or
  `from app_config import config`). The re-export approach is safer.

Note: `src/app_config.py` (separate file) likely just does
`config = load_config()`. Verify and update its import line.

### Acceptance for §8

- No file in `src/config/` > 200 lines
- `from config import AppConfig` still works (via re-export shim)
- `from app_config import config` still works
- YAML round-trip: load + re-serialize produces identical fields

### Risks

- `dataclass` field order matters for `__init__` positional calls. Most call
  sites use keyword args, but spot-check.
- `__post_init__` on `ToolsConfig` mutates `self.radare2`/`ghidra`/`angr` —
  preserve it exactly.

---

## 9. `src/service/inprocess.py` (479 lines) — **WATCH** (pre-emptive split recommended)

At 479 lines this file is under the 500-line trigger but is the most actively
edited file in the service layer (0083/0084 series). It will exceed 500 soon.

### Current structure

1. Imports + `NoopSpinner` (1–55)
2. `TUIUserGate` (59–86)
3. `TUIInputGate` (90–116)
4. `_TurnHandleImpl` (120–172)
5. `InProcessAgentService` (177–479):
   - `__init__` (~50 lines)
   - `send` / `_run_turn` (~80 lines)
   - `checkpoint` / `pause` / `resume` / `cancel_current_turn` (~30 lines)
   - `conversation_history` / `list_resumable_sessions` /
     `load_conversation` / `close` (~50 lines)
   - Internal: `_add/remove_subscriber_queue`, `_publish`,
     `_publish_threadsafe`, `_on_runtime_event` (~35 lines)

### Recommendation

Pre-emptive split (low priority — schedule when next material change lands):

```
src/service/
├── inprocess.py              ~280 lines — InProcessAgentService
├── gates.py                  ~80 lines  — NoopSpinner, TUIUserGate, TUIInputGate
└── turn_handle.py            ~70 lines  — _TurnHandleImpl
```

### Acceptance for §9

- After split, no file > 300 lines
- `arc-tui` boots, sends, paused, cancels — no behavioral change

### Note

Do not block on this until another feature pushes the file over 500. Filed as
a known future split.

---

## 10. Cross-cutting concerns observed during audit

These are not refactors per se but observations the implementer should be
aware of while working on the splits above.

### 10.1 Logging idiom inconsistency

The codebase uses two patterns for stage banners and progress lines:

- `logger.info(banner("Step 1/3"))` — used in `execution.py`
- `logger.info(f"  step {i}/{n}: …")` — used in many other stages

Don't change in this work. Flag for a separate "logging style guide" plan if
the team wants consistency.

### 10.2 Inline event emission

`bus.emit(RuntimeEvent(...))` blocks are 4–10 lines each and appear 26 times.
Plan 0087 should introduce typed helpers (e.g. `events.step_started(...)`) to
shrink these to 1–2 lines and centralize payload shape. Out of scope here.

### 10.3 Repeated `getattr(self, "_identity", None)` pattern

In `execution.py` only. The pattern exists because `run()` sets `_identity`
as an instance attribute and helper methods read it back. Tighten by storing
on a per-call `_StepLoopState` (covered in §5). No global cross-cutting fix
needed.

---

## 11. Implementation order

These refactors are independent. Recommended schedule based on risk and
unlocking value:

1. **§4** (`toolsets.py` regex carve-out) — trivial, ~30-min change
2. **§7** (container tools) — three small extractions, low risk
3. **§8** (`config.py`) — mechanical splits with re-export shim
4. **§3** (`main.py`) — clean module boundaries
5. **§2** (`ui/app.py`) — touches the most-changed file; do after 0084 lands
6. **§5** (`execution.py`) — most subtle (state mutation in monitor decisions)
7. **§6** (`context_manager.py`) — prerequisite for 0089

§9 is optional/deferred.

---

## 12. Verification

After each section's refactor:

1. `pytest -x -q` (if there is a test suite — `find tests -name "*.py" | head`
   to confirm before starting)
2. `python -m src.main` (legacy CLI) — ask a trivial question, get an answer,
   exit cleanly
3. `arc-tui --print "hello"` — headless mode produces a response
4. `find src -name "*.py" | xargs wc -l | awk '$1 >= 600'` — should be empty
5. Check `git diff` for unintended behavioral changes (no logic should change
   in a pure refactor)

## 13. Risks & open questions

**Risk: import cycles.** Several proposed extractions could introduce cycles
(e.g., `runtime/context/manager.py` importing from `runtime/context/scoring.py`
which lazy-imports `embeddings`). Each refactor doc above keeps the lazy
imports lazy. Verify with `python -c "import src.main"` after each split.

**Risk: implicit invariants.** `_pack_chronological`'s pair-atomicity
invariant in §6 is subtle. Keep the existing implementation in the new
`packing.py` byte-for-byte; do not "clean up" during the split.

**Open question:** Should the `src/cli/` directory in §3 instead live at
`src/main/`? `cli/` is more conventional. Recommend `src/cli/`.

**Open question:** Should the `src/config/` package replace `src/app_config.py`
(which currently just does `config = load_config()`)? Recommend leaving
`app_config.py` alone — moving it is out of scope.
