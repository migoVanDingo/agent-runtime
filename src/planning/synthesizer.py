from __future__ import annotations
from typing import Callable, Iterator
from messenger import Messenger
from providers.base import BaseProvider, TextBlock
from planning.schema import Plan
from planning.prompts import SYNTHESIS_SYSTEM_PROMPT, SYNTHESIS_USER_TURN
from logger import get_logger

logger = get_logger(__name__)


class Synthesizer:

    def __init__(self, provider: BaseProvider):
        self._provider = provider

    def _build_messages(self, plan: Plan) -> tuple[list[dict], str]:
        messenger = Messenger()
        user_turn = SYNTHESIS_USER_TURN.format(
            original_query=plan.original_query,
            summary=plan.summary(),
        )
        messenger.add_user_message(user_turn)
        return messenger.get_messages(), SYNTHESIS_SYSTEM_PROMPT

    def synthesize(self, plan: Plan) -> str:
        messages, system = self._build_messages(plan)
        response = self._provider.chat(
            messages=messages,
            tools=[],
            system=system,
            label="Synthesizer",
        )
        text = next(
            (b.text for b in response.content if isinstance(b, TextBlock)), ""
        )
        logger.info("Synthesizer: response generated")
        return text

    def stream_synthesize(self, plan: Plan, on_token: Callable[[str], None]) -> str:
        """Stream the synthesis response. Calls on_token for each chunk, returns full text."""
        stream_fn = getattr(self._provider, "stream_completion", None)
        if stream_fn is None:
            # Provider doesn't support streaming — fall back to non-streaming
            text = self.synthesize(plan)
            on_token(text)
            return text

        messages, system = self._build_messages(plan)
        chunks: list[str] = []
        for chunk in stream_fn(messages=messages, system=system, label="Synthesizer"):
            on_token(chunk)
            chunks.append(chunk)
        text = "".join(chunks)
        logger.info("Synthesizer: response streamed")
        return text
