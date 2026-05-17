"""LLM-assisted importance classification for context management.

After each plan step completes, the importance scorer classifies the step
result's importance (CRITICAL, HIGH, MEDIUM, LOW) using a lightweight LLM call.

Optional: if runtime.importance_council.enabled is true, a 2-councillor council
vote replaces the single-model call when the result is scored MEDIUM (the
ambiguous middle tier where a second opinion has the most value).
"""

from runtime.json_extract import extract_json
from runtime.schema import Importance
from providers.base import BaseProvider, TextBlock
from runtime.scope import RUNTIME, scoped
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
        When importance_council is enabled, escalates MEDIUM results to a
        multi-model vote.
        """
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
            with scoped(RUNTIME):
                response = self._provider.chat(
                    messages=messenger.get_messages(),
                    tools=[],
                    system=_IMPORTANCE_PROMPT,
                    label="ImportanceScorer",
                )
            raw = next((b.text for b in response.content if isinstance(b, TextBlock)), "")
            importance = self._parse(raw)
        except Exception as e:
            logger.info(f"  importance scorer error: {e} — defaulting to MEDIUM")
            importance = Importance.MEDIUM

        # ── Optional council escalation for MEDIUM tier ────────────────────
        from app_config import config
        ic = config.runtime.importance_council
        if ic.enabled and (not ic.only_on_medium or importance == Importance.MEDIUM):
            importance = self._council_score(
                original_query, step_description, result, ic.n_councillors
            )

        self._cache[cache_key] = importance
        logger.info(f"  importance: {importance.value} — {step_description[:50]}")
        return importance

    def _council_score(self, original_query: str, step_description: str,
                       result: str, n_councillors: int) -> Importance:
        """Run a council vote to resolve an ambiguous importance classification."""
        import dataclasses
        from runtime.council import Council
        from runtime.adapters import ImportanceAdapter
        from app_config import config

        council_input = {
            "original_query": original_query[:200],
            "step_description": step_description,
            "result": result[:500],
        }

        base_cfg = config.runtime.council
        active = base_cfg.councillors[:n_councillors]
        effective_cfg = dataclasses.replace(base_cfg, councillors=active, mode="independent")

        adapter = ImportanceAdapter()
        council = Council(adapter=adapter, config=effective_cfg)
        council_result = council.deliberate(
            council_input=council_input, context="importance", query=original_query
        )
        importance = council_result.final.importance
        logger.info(f"  importance council: {importance.value} — {step_description[:50]}")
        return importance

    def _parse(self, raw: str) -> Importance:
        data = extract_json(raw)
        if not isinstance(data, dict):
            return Importance.MEDIUM
        try:
            return Importance(data.get("importance", "medium").lower())
        except ValueError:
            return Importance.MEDIUM
