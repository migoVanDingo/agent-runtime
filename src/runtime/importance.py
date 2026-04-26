"""LLM-assisted importance classification for context management.

After each plan step completes, the importance scorer classifies the step
result's importance (CRITICAL, HIGH, MEDIUM, LOW) using a lightweight LLM call.
This supplements the rule-based classification in the context manager.

Based on AFM paper finding: importance classification is the dominant factor
(83.3% pass rate with it, 0% without it).
"""

import json
from runtime.schema import Importance
from providers.base import BaseProvider, TextBlock
from logger import get_logger

logger = get_logger(__name__)

_IMPORTANCE_PROMPT = """\
You classify the importance of a tool result for an AI assistant's working memory.

Given the user's original request and a tool result, classify how important this \
result is for completing the task.

Return ONLY a JSON object:
  {"importance": "critical"|"high"|"medium"|"low", "reason": "..."}

Guidelines:
- "critical": the result contains the primary answer or a key fact the user asked for
- "high": the result contains useful information that will influence the final output
- "medium": the result is helpful context but not essential
- "low": the result is boilerplate, a confirmation message, or redundant with other results\
"""


class ImportanceScorer:
    """LLM-based importance scorer for tool results."""

    def __init__(self, provider: BaseProvider):
        self._provider = provider
        self._cache: dict[str, Importance] = {}

    def score(self, original_query: str, step_description: str, result: str) -> Importance:
        """Classify the importance of a step result.

        Returns an Importance enum value. Falls back to MEDIUM on parse failure.
        """
        # Cache key: hash of inputs (avoid re-scoring identical results)
        cache_key = f"{step_description[:50]}:{result[:100]}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        user_turn = (
            f"Original request: {original_query[:200]}\n\n"
            f"Step: {step_description}\n\n"
            f"Tool result (first 500 chars):\n{result[:500]}"
        )

        from messenger import Messenger
        messenger = Messenger()
        messenger.add_user_message(user_turn)

        try:
            response = self._provider.chat(
                messages=messenger.get_messages(),
                tools=[],
                system=_IMPORTANCE_PROMPT,
                label="ImportanceScorer",
            )

            raw = next(
                (b.text for b in response.content if isinstance(b, TextBlock)), ""
            )
            importance = self._parse(raw)
        except Exception as e:
            logger.info(f"  importance scorer error: {e} — defaulting to MEDIUM")
            importance = Importance.MEDIUM

        self._cache[cache_key] = importance
        logger.info(f"  importance: {importance.value} — {step_description[:50]}")
        return importance

    def _parse(self, raw: str) -> Importance:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
            text = "\n".join(inner).strip()

        try:
            data = json.loads(text)
            level = data.get("importance", "medium").lower()
            return Importance(level)
        except (json.JSONDecodeError, ValueError):
            return Importance.MEDIUM
