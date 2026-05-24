"""Gemini provider.

Uses `google-genai` SDK (verified byte-faithful — see
_design/0002-foundation-phase1-gemini-sdk-byte-fidelity.md).

Translates between our provider-agnostic types (Message, ToolSpec, ContentBlock,
LLMResponse) and Gemini's native types. Captures raw response dict for replay.

Retry policy is in this layer (not the runtime layer) because retries are
provider-specific (e.g., rate-limit responses, transient 5xx). Config knobs
come from `config.provider.retry`.
"""
from __future__ import annotations

import os
import time
from typing import Any

from arc.config import ProviderConfig
from arc.providers._gemini_translation import (
    messages_to_contents,
    response_to_llm_response,
    tools_to_gemini,
)
from arc.runtime.hooks import LLMRequest, LLMResponse


class GeminiProvider:
    """Gemini implementation of LLMProvider."""

    name = "gemini"

    def __init__(self, cfg: ProviderConfig) -> None:
        from google import genai

        api_key = os.environ.get(cfg.api_key_env)
        if not api_key:
            raise RuntimeError(
                f"Gemini provider: env var {cfg.api_key_env!r} is not set\n"
                f"  set it in your .env or environment before running arc"
            )

        self._cfg = cfg
        self._client = genai.Client(api_key=api_key)

    # ── Public entry point ─────────────────────────────────────────────────

    def chat(self, req: LLMRequest) -> LLMResponse:
        """Send a request, retry per policy, return a translated response."""
        from google.genai import types

        contents = messages_to_contents(req.messages)
        gemini_tools = tools_to_gemini(req.tools) if req.tools else None

        # Build the generation config from our params + Gemini-specific fields
        gen_config = types.GenerateContentConfig(
            system_instruction=req.system or None,
            temperature=req.params.get("temperature"),
            max_output_tokens=req.params.get("max_tokens"),
            top_p=req.params.get("top_p"),
            tools=gemini_tools,
        )

        resp = self._call_with_retry(req.model, contents, gen_config)

        return response_to_llm_response(resp)

    # ── Retry loop ─────────────────────────────────────────────────────────

    def _call_with_retry(self, model: str, contents: Any, config: Any) -> Any:
        """Exponential backoff, capped by config.provider.retry.

        Retries on any exception other than auth/quota-permanent errors.
        We deliberately keep the classification simple: retry everything up
        to the limit. Permanent errors will fail after `max_attempts` with
        a clear message.
        """
        cfg = self._cfg.retry
        backoff = cfg.backoff_base_seconds
        last_exc: Exception | None = None

        for attempt in range(1, cfg.max_attempts + 1):
            try:
                return self._client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=config,
                )
            except Exception as exc:
                last_exc = exc
                if attempt >= cfg.max_attempts:
                    break
                time.sleep(min(backoff, cfg.backoff_max_seconds))
                backoff *= 2

        raise RuntimeError(
            f"Gemini call failed after {cfg.max_attempts} attempts: {last_exc}"
        ) from last_exc

    # Translation helpers live in arc.providers._gemini_translation so the
    # vertex_gemini provider can reuse them. messages_to_contents,
    # tools_to_gemini, response_to_llm_response, translate_stop_reason.
