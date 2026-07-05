# Code quality — smells, long files, coupling, dead code

*Non-security findings. Ranked by leverage (how much fixing it improves
maintainability / decoupling). The codebase is generally clean and well-layered;
these are the sharp edges.*

---

## The one that matters most: `cli.py` (1818 lines)

`v2/src/arc/cli.py` is the single worst file in the tree and the biggest
onboarding tax.

- **What it is:** a ~285-line argparse builder + ~24 `_cmd_*` handlers + helper
  functions, all in one module.
- **The real smell — 5× duplicated session-wiring.** `_cmd_run`, `_cmd_replay`,
  `_cmd_resume`, `_cmd_rerun`, and `_cmd_interactive` each independently repeat
  the same ~30-line sequence: `resolve_home → bootstrap → paths_for → load →
  build_provider → build_tools → HookRegistry → EventBus → build_plugins →
  register loop → AgentSession(...)`. (Near-identical blocks around lines
  728-759, 864-897, 1138-1164, 1270-1293, 1360-1392.)
- **Fix (highest-leverage refactor in the tree):** extract
  `build_session(cfg, paths, *, gate, initial_messages=None) -> (sess, bus,
  plugins)` and split into a `cli/` package: `parser.py`, `_wiring.py`, and
  `commands/{session,replay,resume_rerun,inspect,mcp,subagents,llm,setup,wipe}.py`.
  `main()` becomes a thin dispatcher. Pure mechanical; large readability payoff.

---

## Long files (against the repo's ~600-line convention)

| File | Lines | Verdict | How to split |
|---|---|---|---|
| `v2/src/arc/cli.py` | 1818 | **Split now** | see above |
| `v2/src/arc/runtime/loop.py` | 780 | Split the method, not the file | `_run_turn_inner` is a ~260-line god method — extract `_invoke_provider`, `_dispatch_tool_uses`, `_check_caps`. Fix stale "~300 lines" docstring. |
| `v2/src/arc/runtime/subagents/runner.py` | 676 | Split | `_dispatch_once` is ~230 lines; peel off `_ChildMetricsObserver` → `runner_metrics.py`, `_bridge_progress` → `runner_progress.py`, child config/provider/registry assembly → `child_session.py`. |
| `v2/src/arc/tui/app.py` | 672 | Optional | move slash-command handling → `tui/commands.py`, toolbar/pricing → `tui/toolbar.py`. |
| `v2/src/arc/setup/writer.py` | 573 | Optional | 7 near-parallel ruamel round-trip mutators; extract a `_rt_edit(path, mutate_fn)` context manager to kill the load/dump boilerplate. |
| `v2/src/arc/plugins/log_writer/formatter.py` | 546 | Fine for now | one `_fmt_*` per event; if it grows, split subagent+mcp formatters (already a self-contained block) into a submodule sharing a registry. |
| `v2/src/arc/setup/hub.py` | 469 | Fine | cohesive. |
| `cos/src/cos/core/backend.py` | 496 | Watch | grew with the image/GC work; still under 600. `_combined_logs`/`_logs` duplicate. |

No external plugin or sub-agent file exceeds 600 lines (largest: gcs
`file_ops.py` 465, ghidra `BridgeServer.java` 451).

---

## Duplication (extract-a-helper opportunities)

1. **Provider retry loop, hand-rolled 5×** — `gemini.py`, `anthropic.py`,
   `vertex_gemini.py`, `openai_compat.py`, `llama_cpp/provider.py` each reimplement
   exponential backoff against `cfg.retry`. Extract `providers/_retry.py:
   call_with_retry(fn, retry_cfg, *, label, non_retryable=...)`; vertex's 403/429
   short-circuit becomes a `non_retryable` predicate.
2. **Universal tool-message shape decoded by hand 6×** — the
   `[{"function_response": {"name","response":{"result"}}}]` shape is built in
   `loop.py` and re-parsed independently in each provider + `resume/reconstruct.py`,
   each a slightly different `isinstance` walk (drift-prone). One
   `extract_tool_result_text(msg) -> str` helper used everywhere.
3. **CLI session-wiring 5×** — see `cli.py` above.
4. **cos** `_combined_logs(c)` == `_logs(c, stdout=True, stderr=True)`.

---

## Decoupling / layering observations

The "runtime mediates, model drives, plugins extend" principle is **largely
upheld** — providers never emit events, plugins import only `arc.plugin_api`, and
policy lives in plugins. Sharp edges:

- **The 12th hook `assess_step` is a dead contract** (`hooks.py` + `bus.py` +
  `ALL_HOOK_NAMES`): defined and mapped everywhere but the runtime **never fires
  it**. A plugin implementing it silently never runs. *Decision needed: wire a
  step boundary in the loop, or delete the Protocol + types + registry entries.*
  (Not auto-removed here — it is a contract-surface decision, not obvious junk.)
- **Sub-agent child sessions run with `plugins.enabled=[]`** — see the security
  audit H1. Beyond the security angle, this is a *layering* statement worth
  making explicit: a sub-agent is currently "runtime + tools, no plugins." If
  that is intended, document it; if not, it is a coupling gap.
- **Hardcoded tunables bypassing `config`/`defaults.py`** (principle #3, "no
  hardcoded user-tunables"): `ollama.py` `_TOOL_CAPABLE_FAMILIES` (a model
  allowlist that ages as models ship — belongs in catalog/config),
  `process.py` `term_timeout=10s`, `health.py` `poll=1s`, `tui/pricing.py` cache
  age (1w) + fetch timeout (10s), `llm/commands.py` default `127.0.0.1:8080`.
  All low-impact; fold into config keys when surfaced.
- **`guard` imports `arc.runtime.subagents.tripwire`** — a Layer-2 policy plugin
  reaching into the sub-agent subsystem's internal contextvar. Acceptable in-tree
  but couples two otherwise-independent subsystems; a neutral home for
  `inside_subagent()` (e.g. `runtime.scope`) would decouple them.
- **Defensive exception-swallowing** (principle #5 says the runtime quarantines,
  so plugins shouldn't catch): `sliding_window_context/plugin.py:_emit_packed`
  wraps its own `bus.emit` in `try/except: pass`, hiding emit regressions
  (inconsistent with `safety_gate._emit`, which correctly doesn't catch).
- **External plugins' `try/except ImportError → Any` import block** (ghidra, gcs)
  degrades *every* name in the block to `Any` if one symbol is missing — a single
  absent symbol masks real compat breakage. Split so one missing import can't
  hide the rest.

---

## Correctness smells (non-security)

- **`loop.py` tool-call cap** (also in security M2) — the dangling-tool_use bug
  is a correctness defect first, a robustness issue second. ✅ **MITIGATED,
  `_mitigation/03`.**
- **`.raw` byte-fidelity deviations** — `openai_compat.py` `{"_repr": …}`
  fallback and `llama_cpp` grammar-mode synthetic `.raw` keys + fresh tool-call
  ids break deterministic replay for local/compat providers. Given replay is
  arc's signature feature, these deserve attention.
- **angr** `engine.py:219-222` — the `elif source == "file"` branch is
  byte-identical to its `else`; redundant differentiation (likely an
  unimplemented file-specific path).

---

## Dead code

### Removed in this pass (100%-safe — unused imports only; see `06-…`)
- `runtime/subagents/runner.py` — unused imports `dc_replace`, `datetime`,
  `timezone`, and `Message`.
- `plugins/safety_gate/plugin.py` — unused import `DEFAULT_PATTERNS`.
- `arc-plugin-websearch/backends/{brave,google_pse,searxng,ddg_html}.py` —
  unused `SearchBackend` import (backends satisfy the Protocol structurally).

### Flagged, NOT removed (needs an owner decision — could be intended future use)
- **`assess_step` hook + `Step`/`StepAssessment`/`AssessStep`** (`hooks.py`,
  `bus.py`) — a dead *contract*. Wire it or delete it deliberately.
- **`arc-plugin-gcs/formatters.py`** — the `DISPATCH` table + all 16 `fmt_*`
  functions are unwired (zero references); the module docstring says "consumed
  manually; future PRs may auto-register." Author intent signals future use —
  left in place.
- **`arc-plugin-gcs/client.py:72-75`** `GCSClient.bucket(name)` — no caller;
  plausibly a convenience API. Left in place.
- **`subagents/errors.py` `SubAgentTimeoutError`** — defined + exported in the
  frozen `subagent_api`, never raised (timeout surfaces as
  `SubAgentResult(status="timeout")`). Part of the public API → keep.
- **`replay/provider.py` `ReplayProvider.remaining`** — no `src/` callers;
  possibly used by tests. Verify before any removal.
- **`mcp/manager.py` `_flatten_result` `"structured"` key** — produced, never
  consumed by the adapter. Inert.
- **cos** `backend.py` `_combined_logs` (dup of `_logs`), `Handle.status`
  (always the literal `"running"`).

---

## Summary judgment

The codebase is **well above average for a single-maintainer project**: clean
layering, a real plugin contract, event-sourced observability, safe
deserialization, and disciplined plugin coupling. The debt is concentrated in
three places — `cli.py` (size/duplication), a handful of near-600-line god
methods (`_run_turn_inner`, `_dispatch_once`), and provider-layer duplication
(retry, tool-message parsing). None of it is structural rot; all of it is
mechanical to pay down. Fix `cli.py` and extract the provider `_retry`/tool-shape
helpers and the tree gets materially easier to work in.
