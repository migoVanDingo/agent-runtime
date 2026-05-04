# 0065 — Phase A: Cleanup sprint + test substrate

## Goal

Remove obsolete scaffolding, kill dead code, stand up a real test suite. This
is pure drag-removal — every later phase benefits from starting with a clean
repo and a green test suite.

## Scope

### Deletions
- `_projects/` — entire tree (curriculum scaffolding, superseded).
- `runtime/classifier.py::IntentClassifier` — marked UNUSED in its own docstring
  since the inline routing refactor. `WorkflowSelector` in the same file stays.

### README rewrite
- Describes what the system is today: a multi-stage agent runtime with
  structured events, sandboxed shell, artifact memory, council-reviewed
  planning. Drops curriculum framing.

### Test infrastructure
- Migrate `tests/test_runtime_phase0.py` from `unittest` to `pytest`.
- `pyproject.toml` gains pytest config (testpaths, addopts for `-q --tb=short`).
- `scripts/test.sh` — single entry point that runs `pytest tests/ -q`.

### Unit tests (30+ cases)
New test files in `tests/unit/`:
- `test_critic_synthesis.py` — PlanCriticAdapter.synthesize, all branches.
- `test_context_manager.py` — _pack_chronological pair atomicity, fidelity
  downgrades, plan-window flooring.
- `test_validator.py` — PlanValidator.validate, all validation rules.
- `test_entity_critic.py` — _is_suspicious_candidate, clean vs suspicious.
- `test_workflow_matchers.py` — regex patterns for each workflow implementation.
- `test_path_policy.py` — consolidated path policy tests (moved from phase0).

Existing `tests/test_runtime_phase0.py` → `tests/unit/test_phase0.py`, pytest style.

## Files touched
- `_projects/` (deleted)
- `src/runtime/classifier.py` (IntentClassifier removed)
- `README.md` (rewritten)
- `pyproject.toml` (pytest config added)
- `scripts/test.sh` (new)
- `tests/unit/test_phase0.py` (migrated)
- `tests/unit/test_critic_synthesis.py` (new)
- `tests/unit/test_context_manager.py` (new)
- `tests/unit/test_validator.py` (new)
- `tests/unit/test_entity_critic.py` (new)
- `tests/unit/test_workflow_matchers.py` (new)

## Exit criteria
- `_projects/` does not exist.
- `grep -rn "IntentClassifier" src/` returns zero hits.
- `pytest tests/ -q` passes ≥ 40 cases.
- `make test` runs the suite.
