"""SlidingWindowStrategy — keep last N messages verbatim, summarise the rest.

When the conversation exceeds the window size, older messages are folded
into a single ``user`` summary message inserted at position 0. The summary
is generated via the configured summarizer (typically the runtime LLM) and
cached by digest so repeated calls within a turn don't re-summarise.

The window edge is expanded backward to keep tool_use/tool_result pairs
intact — Anthropic's API rejects a tool_result that doesn't have its
matching tool_use preserved.

Config (``runtime.context.params.sliding``):
    budget_tokens: int        (default 30_000) — soft cap; controls when to summarise
    keep_last_n: int          (default 20)     — minimum tail length
    summarize_older: bool     (default True)   — when False, drop older messages without summary
"""
from __future__ import annotations

import hashlib
import time
from typing import Any

from logger import get_logger
from runtime.context.packing import detect_tool_pairs
from runtime.context.scoring import estimate_tokens, message_text

logger = get_logger(__name__)


class SlidingWindowStrategy:
    name = "sliding"

    def __init__(self, params: dict | None = None) -> None:
        params = params or {}
        self._budget = int(params.get("budget_tokens", 30_000))
        self._keep_last_n = max(1, int(params.get("keep_last_n", 20)))
        self._summarize_older = bool(params.get("summarize_older", True))
        self._summary_max_chars = int(params.get("summary_max_chars", 1500))
        self._summarizer = None
        self._summary_cache: dict[str, str] = {}

    # ── Strategy protocol ─────────────────────────────────────────────

    def set_summarizer(self, provider) -> None:
        self._summarizer = provider

    def set_importance(self, message_index: int, importance) -> None:
        del message_index, importance

    def get_importance(self, message_index: int):
        del message_index
        return None

    # ── Packing ───────────────────────────────────────────────────────

    def pack(
        self,
        messages: list[dict],
        current_query: str,
        plan_start_index: int | None = None,
        *,
        system_prompt_size: int = 0,
    ) -> list[dict]:
        del current_query, plan_start_index, system_prompt_size
        if not messages:
            return messages
        if len(messages) <= self._keep_last_n:
            return messages

        total = _sum_tokens(messages)
        bus, identity = _bus_and_identity()
        t0 = time.monotonic()
        if bus is not None:
            bus.emit(_pack_event("context.pack.started", identity, payload={
                "strategy": self.name,
                "n_messages_in": len(messages),
                "input_token_estimate": total,
                "budget": self._budget,
                "keep_last_n": self._keep_last_n,
            }))

        split = len(messages) - self._keep_last_n
        # Expand split backward so we never start the window in the middle of a tool pair.
        pairs = detect_tool_pairs(messages)
        while split > 0 and split in pairs and pairs[split] == split - 1:
            split -= 1

        older = messages[:split]
        recent = messages[split:]

        result: list[dict] = []
        summary_chars = 0
        if older and self._summarize_older:
            summary = self._summarize(older)
            if summary:
                summary_chars = len(summary)
                result.append({
                    "role": "user",
                    "content": (
                        "[Earlier conversation summary]\n" + summary
                    ),
                })

        result.extend(recent)
        packed_tokens = _sum_tokens(result)
        logger.info(
            f"  sliding: kept last {len(recent)} of {len(messages)} messages "
            f"(summary: {summary_chars} chars, total: {packed_tokens} tokens)"
        )

        if bus is not None:
            bus.emit(_pack_event("context.pack.completed", identity, payload={
                "strategy": self.name,
                "n_messages_out": len(result),
                "output_token_estimate": packed_tokens,
                "n_dropped": len(messages) - len(recent),
                "summary_chars": summary_chars,
                "packed": True,
            }, duration_ms=int((time.monotonic() - t0) * 1000)))
        return result

    # ── Summarisation ─────────────────────────────────────────────────

    def _summarize(self, messages: list[dict]) -> str:
        key = _digest(messages)
        if key in self._summary_cache:
            return self._summary_cache[key]

        # Mechanical fallback: concatenate role+text snippets.
        mechanical = self._mechanical_summary(messages)

        if self._summarizer is None:
            self._summary_cache[key] = mechanical
            return mechanical

        prompt = (
            "Summarise the following multi-turn conversation in 1500 characters or less. "
            "Preserve: the user's original request, key facts uncovered, file paths or "
            "identifiers mentioned, and any decisions made. Drop redundant tool I/O.\n\n"
            f"{mechanical}"
        )
        try:
            from messenger import Messenger
            from providers.base import TextBlock
            scratch = Messenger()
            scratch.add_user_message(prompt)
            response = self._summarizer.chat(
                messages=scratch.get_messages(),
                tools=[],
                system="You produce concise, faithful summaries of agent conversations.",
                label="SlidingWindowSummary",
            )
            text = next(
                (b.text for b in response.content if isinstance(b, TextBlock)),
                "",
            ).strip()
            if not text:
                text = mechanical
        except Exception as exc:
            logger.warning(f"  sliding: summariser call failed ({exc!r}) — using mechanical fallback")
            text = mechanical

        if len(text) > self._summary_max_chars:
            text = text[: self._summary_max_chars] + " […truncated]"
        self._summary_cache[key] = text
        return text

    def _mechanical_summary(self, messages: list[dict]) -> str:
        lines: list[str] = []
        for m in messages:
            role = m.get("role", "?")
            text = message_text(m).strip().replace("\n", " ")
            if not text:
                continue
            lines.append(f"[{role}] {text[:240]}")
        joined = "\n".join(lines)
        if len(joined) > self._summary_max_chars:
            joined = joined[: self._summary_max_chars] + " […truncated]"
        return joined


# ── Helpers ──────────────────────────────────────────────────────────────────


def _digest(messages: list[dict]) -> str:
    h = hashlib.sha1()
    for m in messages:
        h.update(m.get("role", "?").encode())
        h.update(message_text(m).encode(errors="replace"))
        h.update(b"\x00")
    return h.hexdigest()


def _sum_tokens(messages: list[dict]) -> int:
    return sum(estimate_tokens(message_text(m)) for m in messages)


def _bus_and_identity():
    try:
        from runtime.events import get_event_bus, get_runtime_identity
        return get_event_bus(), get_runtime_identity()
    except Exception:
        return None, None


def _pack_event(event_type: str, identity, *, payload: dict[str, Any], duration_ms: int | None = None):
    from runtime.events import RuntimeEvent
    return RuntimeEvent(
        event_type,
        identity,
        payload=payload,
        stage="ContextStrategy",
        duration_ms=duration_ms,
    )
