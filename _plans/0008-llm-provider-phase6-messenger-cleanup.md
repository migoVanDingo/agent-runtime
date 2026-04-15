# 0008 — LLM Provider: Phase 6 — Messenger Cleanup

## Goal

Remove the Anthropic SDK-specific `.model_dump()` call from `messenger.py`.
The messenger now receives our normalized dataclasses from the provider,
so boundary conversion is no longer needed here.

---

## Files

### Updated: `src/messenger.py`

**`add_assistant_message(content: list)`**

Remove:
```python
serialized = [block.model_dump() for block in content]
```

Replace with a conversion from our normalized dataclasses to plain dicts
that the API (via the provider) can consume on the next turn.

---

## Conversion

| Normalized type | Plain dict |
|---|---|
| `TextBlock` | `{"type": "text", "text": block.text}` |
| `ToolUseBlock` | `{"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}` |

---

## Notes

- `dataclasses.asdict()` handles this cleanly — no manual field mapping needed
- The messenger still stores plain dicts internally, not dataclasses
- After this phase the entire llm-provider feature is complete and testable
