# 0072 — Phase H: Container / Dependency Injection

## Goal

29 files import `from app_config import config` at module top. Tests can't
construct an `Agent` with different configs. Two Agents can't run side by side.
This phase introduces a `Container` object built once in `main.py` and passed
to `Agent`. Stages continue to receive what they need via constructor but no
longer pull module-level globals at import time.

## Scope

- New `runtime/container.py`: `Container` dataclass with all shared
  service instances.
- `Agent.__init__` accepts an optional `Container`; builds its own if absent
  (for backward compat during transition).
- Stop calling `from app_config import config` at function/method top level
  inside the most-called paths (stages, tool_loop, providers). Module top-level
  reads at startup are acceptable; per-call reads are not.
- Verify: two `Agent` instances with different `AppConfig` objects can coexist
  in the same process without interfering.

## Files touched

`runtime/container.py` (new), `agent.py`, `main.py`.

## Exit criteria

- Integration test: two Agents run side-by-side producing separate event files.
- `grep -rn "^from app_config import" src/` returns ≤5 hits (only startup code).
