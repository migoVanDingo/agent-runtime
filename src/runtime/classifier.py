"""Workflow selector — targeted fallback workflow router.

Called when the classifier returned no workflow hint AND regex matching
produced no match AND mode=plan. Makes one focused LLM call to ask which
registered workflow, if any, matches the user's request.
"""
from __future__ import annotations

import json

from providers.base import BaseProvider, TextBlock
from runtime.json_extract import extract_json
from runtime.prompts import (
    WORKFLOW_SELECTOR_SYSTEM_PROMPT,
    WORKFLOW_SELECTOR_USER_TEMPLATE,
)
from logger import get_logger

logger = get_logger(__name__)


class WorkflowSelector:
    """Targeted fallback workflow router.

    Called only when the classifier returned no hint AND regex matching produced
    no match AND mode=plan. Makes one focused LLM call asking solely "which
    workflow does this request match, if any?"
    """

    def __init__(self, provider: BaseProvider):
        self._provider = provider

    def select(
        self,
        message: str,
        workflow_descriptions: list[tuple[str, str]],
    ) -> str | None:
        """Return a workflow name or None. One LLM call, no tools."""
        if not workflow_descriptions:
            return None

        wf_lines = "\n".join(
            f'  "{name}": {intent}' for name, intent in workflow_descriptions
        )
        system = WORKFLOW_SELECTOR_SYSTEM_PROMPT.format(workflow_descriptions=wf_lines)
        user_turn = WORKFLOW_SELECTOR_USER_TEMPLATE.format(message=message)

        from messenger import Messenger
        messenger = Messenger()
        messenger.add_user_message(user_turn)

        response = self._provider.chat(
            messages=messenger.get_messages(),
            tools=[],
            system=system,
            label="WorkflowSelector",
        )

        raw = next(
            (b.text for b in response.content if isinstance(b, TextBlock)), ""
        )

        return self._parse(raw, {name for name, _ in workflow_descriptions})

    def _parse(self, raw: str, valid_names: set[str]) -> str | None:
        data = extract_json(raw)
        if not isinstance(data, dict):
            logger.info("  workflow selector: parse failed — no match")
            return None

        name = data.get("workflow") or None
        reason = data.get("reason", "")
        if name and name not in valid_names:
            logger.info(f"  workflow selector: unknown name '{name}' — ignoring")
            return None
        if name:
            logger.info(f"  workflow selector: matched '{name}' — {reason}")
        else:
            logger.info(f"  workflow selector: no match — {reason}")
        return name
