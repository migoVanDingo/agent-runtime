# 0012 — Dynamic Tool Loading: Phase 3 — Static Router (Revised v2)

## Goal

Implement a rule-based static router where routing rules are owned by toolsets,
not by the router. The router collects rules from the registry at init time.
Adding a new toolset requires zero changes to the router.

---

## Design

### Problem with previous approach
Rules were hardcoded in `StaticRouter.__init__`. Every new toolset required
opening the router and adding entries — same scaling problem as the original if-chain,
just in a different form.

### Solution: rules live on the Toolset
Each `Toolset` carries its own `RoutingRule` list. When a toolset is registered
with the `ToolRegistry`, its rules become available to the router. The router
is completely decoupled from toolset definitions.

---

## Files

### New: `src/types.py`
Shared types with no dependencies. Owns `RoutingRule`:
```python
@dataclass
class RoutingRule:
    toolset: str
    condition: Callable[[str, list[dict]], bool]
```
Lives here (not in routing or tools) so neither package depends on the other.

### New: `src/routing/conditions.py`
Condition builder functions — reusable factories that return
`Callable[[str, list[dict]], bool]`. Imported by `toolsets.py` to define rules.

| Builder | What it checks |
|---|---|
| `has_extension(*exts)` | Any word in message ends with a given file extension |
| `has_file_path()` | Message contains a detectable file path pattern |
| `any_keyword(*keywords)` | Message tokens intersect the keyword set |
| `last_tools_were(*names)` | Most recent assistant tool calls match the named set |
| `all_of(*conditions)` | AND combinator — all conditions must match |

### Updated: `src/tools/toolset.py`
Add `rules: list[RoutingRule]` field (default empty list).
Toolset is now fully self-contained: tools + description + routing rules.

### Updated: `src/tools/registry.py`
Add `get_all_rules() -> list[RoutingRule]` — returns all rules from all
registered toolsets. Called by `StaticRouter` at init time.

### Rewritten: `src/routing/static_router.py`
`StaticRouter.__init__` takes a `ToolRegistry` reference.
Collects rules via `registry.get_all_rules()` — no hardcoded rules.
Embedding model and toolset embeddings still initialized here.

`select()` flow unchanged:
1. Evaluate heuristic rules from registry
2. Encode message once → compare against toolset embeddings
3. Union results, fallback if empty

---

## Dependency flow

```
src/types.py                    ← RoutingRule (no deps)
src/routing/conditions.py       ← condition builders (imports RoutingRule from types)
src/tools/toolset.py            ← Toolset with rules field (imports RoutingRule from types)
src/tools/registry.py           ← get_all_rules() aggregates from toolsets
src/routing/static_router.py    ← reads rules from registry (no hardcoded toolset knowledge)
src/tools/toolsets.py           ← (Phase 4) defines toolsets with rules using condition builders
```

No circular imports. `tools` and `routing` both depend on `types`, not on each other.

---

## Notes

- `StaticRouter` is initialized after the registry has toolsets registered
- Embedding descriptions still live in `static_router.py` as a dict keyed by toolset name —
  this is the one remaining coupling, acceptable since it's purely for semantic matching
- Condition builders are intentionally stateless pure functions
