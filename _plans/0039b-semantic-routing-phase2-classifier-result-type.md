# Phase 2: ClassifierResult Type

## What Was Built

Replaced the `(mode, risk)` tuple return from `classify()` with a `ClassifierResult` dataclass. No behavior change — `workflow_hint` is always `None` at this point. Sets up the type infrastructure for Phase 3.

## Changes

### `runtime/schema.py`
- Added `ClassifierResult(mode, risk, workflow_hint)` dataclass
- `workflow_hint: str | None = None` — populated in Phase 3

### `runtime/classifier.py`
- Imported `ClassifierResult`
- `classify()` return type changed from `tuple[str, str]` to `ClassifierResult`
- Returns `ClassifierResult(mode=mode, risk=risk)` — `workflow_hint` defaults to `None`

### `agent.py`
- Updated call site: `classification = self.classifier.classify(...)` then `mode, risk = classification.mode, classification.risk`

## No Behavior Change
`workflow_hint` is always `None`. Routing is identical to before. Phase 3 populates the hint.
