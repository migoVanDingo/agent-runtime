"""ReplayProvider — an LLMProvider that serves recorded responses.

Used by mode 2 (full deterministic replay). The runtime's loop calls
`chat()` per LLM call; this implementation pops the next recorded
LLMResponse from the queue instead of contacting any real provider.

If the queue runs out, it means the replayed agent made more LLM calls
than the recording had — that's a divergence, raise ReplayDivergenceError
so the diff layer reports it cleanly.
"""
from __future__ import annotations

from collections import deque

from arc.replay.errors import ReplayDivergenceError
from arc.runtime.hooks import LLMRequest, LLMResponse


class ReplayProvider:
    """LLMProvider that serves recorded responses in FIFO order.

    Mode 2 only. For mode 3, the real provider is used and this isn't built.
    """

    name = "replay"

    def __init__(self, responses: deque[LLMResponse]) -> None:
        # Copy so we don't mutate ReplayData
        self._queue: deque[LLMResponse] = deque(responses)
        self._call_count = 0

    def chat(self, req: LLMRequest) -> LLMResponse:
        self._call_count += 1
        if not self._queue:
            raise ReplayDivergenceError(
                f"replay diverged at LLM call #{self._call_count}: "
                f"the runtime asked for another LLM response but the recording "
                f"only had {self._call_count - 1}. The replayed agent took a "
                f"different path than the recorded one."
            )
        return self._queue.popleft()

    @property
    def remaining(self) -> int:
        """Responses still in the queue. After a clean replay, should be 0."""
        return len(self._queue)
