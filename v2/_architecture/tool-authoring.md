# Tool authoring guide

A tool in arc is a small class with three things: a `name`, a `description`,
an `input_schema`, and an `execute(input: dict) -> str` method. Everything
else — policy, retries, escalation, sandboxing — lives in plugins.

This guide walks the contract. Reference implementations:
[`ls.py`](../src/arc/tools/ls.py) and
[`bash_exec.py`](../src/arc/tools/bash_exec.py).

---

## 1. The Protocol

[`src/arc/tools/base.py`](../src/arc/tools/base.py):

```python
class Tool(Protocol):
    name: ClassVar[str]
    description: ClassVar[str]

    @property
    def input_schema(self) -> ToolInputSchema: ...

    def execute(self, input: dict[str, Any]) -> str: ...
```

That's it. arc uses structural typing — if it walks like a tool, it is one.

### Why the design is this small

Tools are *just* "given args, do work, return a string". Anything else —
"only allow this tool for these args" (guard), "ask the user before running"
(safety_gate), "page the output back" (future paging plugin), "run inside
sandbox-exec" (future sandbox plugin) — is a hook the runtime fires around
the tool. The tool itself stays oblivious.

---

## 2. Class structure

Tools live at `src/arc/tools/<name>.py`:

```python
# src/arc/tools/your_tool.py
from typing import Any
from arc.tools.base import Tool, ToolInputSchema, ToolError


class YourTool:
    """Tool that does <thing>. One-line summary the model could read."""

    name = "your_tool"  # must match the config key under tools.config.<name>

    description = (
        "Do <thing>. Args: <arg> (required, type, what it means). "
        "Returns: <what the model sees>."
    )

    def __init__(self, *, your_setting: int = 5):
        self._your_setting = your_setting

    @property
    def input_schema(self) -> ToolInputSchema:
        return ToolInputSchema(
            properties={
                "thing": {
                    "type": "string",
                    "description": "What to do <thing> to",
                },
                "count": {
                    "type": "integer",
                    "description": "How many times (optional, default 1)",
                    "minimum": 1,
                    "maximum": 100,
                },
            },
            required=["thing"],
        )

    @classmethod
    def from_config(cls, cfg: dict) -> "YourTool":
        """Build from the dict in tools.config.your_tool."""
        return cls(your_setting=int(cfg.get("your_setting", 5)))

    def execute(self, input: dict[str, Any]) -> str:
        thing = input["thing"]
        count = int(input.get("count", 1))

        if count > self._your_setting:
            raise ToolError(f"count {count} exceeds limit {self._your_setting}")

        return f"did {thing} {count} time(s)"
```

A few things worth noting:

- **`description` is for the LLM.** Make it action-oriented and explicit about
  args and return shape. The model uses this verbatim to decide when to call
  your tool and with what args.
- **`input_schema` is JSON Schema.** Providers convert it to their native shape.
  Use standard JSON Schema types and constraints; both Gemini and Anthropic
  honor `minimum`, `maximum`, `enum`, `description`, `pattern`, etc.
- **`from_config` is optional.** Tools that take no config can skip it and the
  registry uses the default constructor.

---

## 3. Returning vs raising

The runtime treats your tool's behavior as:

- **Returned string** → `ToolResult(ok=True, output=<string>)`. The model sees
  the string. Treat this as success even if the underlying operation was
  semantically a "no-op" or "found nothing".
- **Raised `ToolError`** → `ToolResult(ok=False, output=<error message>)`. The
  model still sees the message, but `ok=False` is logged and any
  `after_tool_call` hook can react.
- **Raised any other exception** → `ToolResult(ok=False, output="internal
  error: ...", error_code="internal")`. The runtime logs the exception and
  the agent gets a generic error.

**Always raise `ToolError`** for known-bad inputs or operation failures.
Don't return a success string that says "Error: ..." — the model often
won't notice, and your post-call hooks can't tell success from failure.

---

## 4. Output formatting

The model sees the literal string you return. A few conventions that pay off:

- **Empty output → return a non-empty placeholder.** Returning `""` looks
  identical to "command produced no output", which the model might interpret
  as the call having failed. `bash_exec` returns `"(command produced no
  output; exit code 0)"` for this exact reason.
- **Truncate at the source.** If your tool can produce huge output (file
  contents, `nm`, decompiled functions), enforce a `max_chars` and append
  `… [+N chars]` when you truncate. The runtime + log_writer also truncate
  for display, but providers will happily pay for full output if you let
  them.
- **Lead with the structured part, trail with detail.** If the model needs
  one fact (exit code, file count), put it first. Pages of detail after.
- **Use Markdown sparingly.** The model interprets it; the human reading
  the log doesn't need it. Plain text wins for both.

---

## 5. Registration

Two places:

### a. Builder dict in `arc/tools/__init__.py`

```python
from arc.tools.your_tool import YourTool

_BUILDERS = {
    "ls": LSTool.from_config,
    "bash_exec": BashExecTool.from_config,
    "your_tool": YourTool.from_config,
}
```

### b. Default config block in `defaults.py`

```yaml
tools:
  enabled:
    - ls
    - bash_exec
    - your_tool        # ← add to enable by default
  config:
    your_tool:
      your_setting: 5
```

Users can disable by removing from `tools.enabled`. The registry skips
builders for tools that aren't enabled, so disabled tools incur zero cost.

---

## 6. Tools that need the workspace

Tools that touch the filesystem should respect `config.runtime.workspace`.
The runtime sets the cwd before invoking the tool, so relative paths
resolve correctly. But:

- **Resolve relative paths against cwd, not `os.getcwd()` directly.** Use
  `pathlib.Path(cwd).resolve() / user_path` if you take a `cwd` config knob.
- **Reject `..` traversal at the workspace boundary** if your tool should
  not let the model escape. (Most tools don't enforce this — `bash_exec`
  certainly doesn't — but file-write tools should.)
- **Use `subprocess.run` with `cwd=` explicitly** rather than relying on
  the runtime's cwd. Safer and more obvious in logs.

---

## 7. Tools that produce side effects

For tools that *change* things (write files, mutate state, send messages):

- **Make them idempotent where possible.** If the agent calls twice, the
  second call should be a no-op rather than a duplicate.
- **Return what changed.** "Wrote 124 bytes to ./foo.md" beats "Done".
  The model uses this to decide whether to follow up.
- **Don't validate at runtime what the schema validates.** If your schema
  says `count: integer`, providers reject non-integer calls. You don't need
  `if not isinstance(input["count"], int): raise`.

For *destructive* side effects (delete, overwrite, force-push), the
`safety_gate` plugin pattern-matches and prompts the user. You don't need
to build user-prompt logic into the tool itself — that's the plugin's
job. See [`_design/0012-destructive-action-gate.md`](../_design/0012-destructive-action-gate.md).

---

## 8. Testing tools

Unit test pattern (`tests/unit/test_<tool>.py`):

```python
from arc.tools.your_tool import YourTool
from arc.tools.base import ToolError
import pytest


def test_basic():
    tool = YourTool(your_setting=5)
    assert tool.execute({"thing": "x"}) == "did x 1 time(s)"


def test_count_clamps():
    tool = YourTool(your_setting=5)
    with pytest.raises(ToolError, match="exceeds limit"):
        tool.execute({"thing": "x", "count": 99})


def test_from_config():
    tool = YourTool.from_config({"your_setting": 10})
    assert tool._your_setting == 10
```

For tools that touch the filesystem, use `tmp_path` and assert on filesystem
state after `execute`. For tools that shell out, use `subprocess`-friendly
tmpdirs rather than mocking — real subprocess calls are fast and catch real
bugs.

---

## 9. Worked example: `bash_exec`

[`src/arc/tools/bash_exec.py`](../src/arc/tools/bash_exec.py) — the most
complex shipped tool. Worth reading in full. Key points:

- Takes `timeout_seconds`, `max_output_chars`, `blocked_pattern_categories`
  via `from_config`.
- Pattern-matches against configured blocklist *before* invoking subprocess.
  This is a tool-level safety net; the `guard` plugin is the real policy
  layer.
- Captures stdout + stderr separately, then composes into one output. Adds
  exit code and `STDERR:` prefix when stderr is non-empty.
- Truncates to `max_output_chars` and appends ellipsis.
- Returns the canonical "no output" placeholder rather than empty string.

That's the full pattern. Define a small class with a tight schema, raise
`ToolError` for known failures, truncate generously, register it, ship it.
