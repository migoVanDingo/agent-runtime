"""Provider Protocol — what every LLM backend must implement.

The runtime never sees provider-specific types. Providers translate to/from
our provider-agnostic types (Message, ToolSpec, LLMResponse, ContentBlock —
all in runtime/hooks.py) so the rest of the codebase stays vendor-neutral.

For replay: every LLMResponse carries `.raw` (the provider's full response
as a JSON-faithful dict). Recorders persist this; replayers reconstruct from it.
See _design/0002-foundation-phase1-gemini-sdk-byte-fidelity.md.
"""
from __future__ import annotations

from typing import Protocol

from arc.runtime.hooks import LLMRequest, LLMResponse


class LLMProvider(Protocol):
    """Every provider exposes a single sync `chat()` method.

    Async is intentionally not in the v2.0 interface — sync is simpler and
    sufficient for a CLI agent. When we need streaming, we'll add a `stream()`
    method as a separate v2 protocol rather than retrofitting async here.
    """

    name: str  # "gemini", "anthropic", "openai" — matches config.provider.name

    def chat(self, req: LLMRequest) -> LLMResponse:
        """Synchronous LLM call.

        Implementations should:
          - Translate our types to provider-native types
          - Handle retries per config.provider.retry
          - Capture the raw provider response (for replay byte-fidelity)
          - Translate back to our types and return

        Should NOT:
          - Emit events directly (the runtime does that, wrapping the call)
          - Mutate the request
          - Re-raise transient errors without using configured retry policy
        """
        ...
