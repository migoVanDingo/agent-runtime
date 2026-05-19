"""Replay tool stubs + the ReplayingToolRegistry.

The runtime's loop calls `self.tools.get(name).execute(input)`. For replay
we want that call to return a recorded output instead of running the real
tool. So we replace the registry with one that vends stubs.

Two strategies, picked at construction:
  - 'in_order' (mode 2): FIFO per tool name. Pops on each call. Strict.
  - 'by_call'  (mode 3): lookup by (name, canonical_input). Allows live LLM
                          to make tool calls in any order, as long as it
                          stays within the recorded (name, input) set.
"""
from __future__ import annotations

import json
from collections import deque
from typing import Any

from arc.replay.errors import ReplayDivergenceError
from arc.replay.loader import ReplayData
from arc.tools.base import Tool, ToolInputSchema, ToolRegistry


class ReplayingTool:
    """Stub tool. Returns recorded outputs, never runs anything for real.

    Mirrors the original tool's description + input_schema so the runtime's
    llm.call.started events look identical to the recording — otherwise
    the replayed event log spuriously diverges (the runtime always emits
    the tool list it has, not the one originally sent).
    """

    def __init__(
        self,
        name: str,
        *,
        description: str = "",
        input_schema_dict: dict | None = None,
        in_order_queue: deque[str] | None = None,
        by_call_table: dict[tuple[str, str], deque[str]] | None = None,
        mode: str = "in_order",
    ) -> None:
        self.name = name
        self.description = description or f"(replay stub for {name})"
        self._schema_dict = input_schema_dict or {
            "type": "object", "properties": {}, "required": [],
        }
        self._queue = in_order_queue
        self._table = by_call_table
        self._mode = mode

    @property
    def input_schema(self) -> ToolInputSchema:
        # Mirror the recorded schema exactly. The ToolInputSchema dataclass
        # only carries properties + required, but to_json_schema() reads them
        # back out — we need both fields to match what the recording had.
        return ToolInputSchema(
            properties=self._schema_dict.get("properties", {}),
            required=self._schema_dict.get("required", []),
        )

    def execute(self, input: dict[str, Any]) -> str:
        if self._mode == "in_order":
            return self._execute_in_order()
        if self._mode == "by_call":
            return self._execute_by_call(input)
        raise ValueError(f"unknown replay mode: {self._mode}")

    # ── Mode 2 ─────────────────────────────────────────────────────────

    def _execute_in_order(self) -> str:
        if self._queue is None or not self._queue:
            raise ReplayDivergenceError(
                f"replay diverged on tool {self.name!r}: the runtime called "
                f"it more times than the recording had outputs for. The "
                f"replayed agent took a different path than the recorded one."
            )
        return self._queue.popleft()

    # ── Mode 3 ─────────────────────────────────────────────────────────

    def _execute_by_call(self, input: dict[str, Any]) -> str:
        if self._table is None:
            raise ReplayDivergenceError(
                f"replay diverged on tool {self.name!r}: no recorded outputs"
            )
        canonical = json.dumps(input, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False)
        key = (self.name, canonical)
        queue = self._table.get(key)
        if not queue:
            # Show the input the LLM gave so the user can see why
            preview = canonical[:200] + ("…" if len(canonical) > 200 else "")
            recorded_inputs = [k[1] for k in self._table if k[0] == self.name]
            raise ReplayDivergenceError(
                f"replay diverged: tool {self.name!r} was called with inputs "
                f"the recording doesn't cover.\n"
                f"  requested input: {preview}\n"
                f"  recorded inputs for this tool ({len(recorded_inputs)}): "
                f"{[i[:80] for i in recorded_inputs]}\n"
                f"  (this typically means the live LLM made a different "
                f"choice than the recorded one)"
            )
        return queue.popleft()


class ReplayingToolRegistry(ToolRegistry):
    """ToolRegistry that vends ReplayingTool stubs for every name the
    recording knows about. Anything else → KeyError (same as the base).
    """

    def __init__(self, data: ReplayData, *, mode: str = "in_order") -> None:
        super().__init__()
        if mode not in ("in_order", "by_call"):
            raise ValueError(f"unknown replay mode: {mode!r}")
        self._mode = mode
        self._data = data

        # Tools, in the order they were first OFFERED to the LLM.
        # Ordering matters: the runtime emits llm.call.started with the
        # current tool registry in its insertion order. If our replay
        # registry has the same tools in a different order, the event
        # diverges character-for-character even though the set is identical.
        # We preserve order via a dict (insertion-ordered in 3.7+).
        names: dict[str, None] = {n: None for n in data.tool_specs}
        # Defensive — include any called tools missing from tool_specs
        # (shouldn't happen, but harmless)
        for name in data.tool_outputs_in_order:
            names.setdefault(name, None)
        for (name, _input) in data.tool_outputs_by_call:
            names.setdefault(name, None)

        for name in names:
            queue = data.tool_outputs_in_order.get(name)
            spec = data.tool_specs.get(name, {})
            self.register(ReplayingTool(
                name=name,
                description=spec.get("description", ""),
                input_schema_dict=spec.get("input_schema"),
                in_order_queue=queue,
                by_call_table=data.tool_outputs_by_call,
                mode=mode,
            ))
