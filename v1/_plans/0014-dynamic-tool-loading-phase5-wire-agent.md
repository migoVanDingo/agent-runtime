# 0014 — Dynamic Tool Loading: Phase 5 — Wire Router into Agent

## Goal

Replace the static full-schema call with per-turn dynamic routing. On each
iteration of the agent loop the router inspects the current message and
conversation history, selects the relevant toolsets, and only those schemas
are sent to the model.

---

## Changes

### Updated: `src/agent.py`

**Init:** instantiate `StaticRouter` after the registry is fully populated.

```python
self.router = StaticRouter(self.registry)
```

Must come after `register_toolset()` calls — the router encodes toolset
description embeddings at init time using the descriptions from `config.yml`.

**Loop:** replace `registry.to_api_schema()` with a router call each iteration.

```python
selected = self.router.select(user_message, self.messenger.get_messages())
tools = self.registry.get_toolset_schema(selected)
```

The original `user_message` string is passed every iteration (not reconstructed
from history) so keyword and embedding signals remain stable as the conversation
grows. History is passed so `last_tools_were` continuation rules fire correctly.

---

## Routing per iteration

```
loop iteration N:
  1. router.select(user_message, history) → [toolset_names]
  2. registry.get_toolset_schema(toolset_names) → [tool_schemas]
  3. provider.chat(messages, tools=tool_schemas, system=system_prompt)
  4. if end_turn  → return text
  5. if tool_use  → execute, add results, go to N+1
```

On iteration 1: keyword/embedding rules fire on the user message.
On iteration N+1: same keyword/embedding rules still fire, plus `last_tools_were`
rules can now fire based on what the model called on iteration N.

---

## What does not change

- `StaticRouter.select()` interface — unchanged
- `ToolRegistry.get()` for tool dispatch — unchanged
- `Messenger` — unchanged
- `main.py` — unchanged
- All tool implementations — unchanged

---

## Notes

- If the router returns an empty list (no signals), `get_toolset_schema([])` returns
  `[]` — the model gets no tools and responds in plain text (pure conversation turn)
- The router is a read-only observer of the registry; it does not modify any state
