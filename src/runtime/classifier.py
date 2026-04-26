import json
from providers.base import BaseProvider, TextBlock
from runtime.prompts import (
    CLASSIFIER_SYSTEM_PROMPT,
    CLASSIFIER_USER_TEMPLATE,
    WORKFLOW_SELECTOR_SYSTEM_PROMPT,
    WORKFLOW_SELECTOR_USER_TEMPLATE,
)
from runtime.schema import ClassifierResult
from app_config import config
from logger import get_logger

logger = get_logger(__name__)


# UNUSED: replaced by inline routing header in agent.py (_build_routing_system / _parse_routing_response).
# Kept for reference and potential A/B testing. Safe to delete if no longer needed.
class IntentClassifier:

    def __init__(self, provider: BaseProvider):
        self._provider = provider
        self._context_window = config.runtime.intent_classifier.context_window

    def classify(
        self,
        message: str,
        history: list[dict],
        workflow_descriptions: list[tuple[str, str]] | None = None,
    ) -> ClassifierResult:
        """Classify intent. Returns ClassifierResult(mode, risk, workflow_hint).

        workflow_descriptions: list of (name, intent) pairs from WorkflowMatcher.get_descriptions().
        When provided, the classifier also attempts to identify a matching workflow.
        """
        if not config.runtime.intent_classifier.enabled:
            return ClassifierResult(mode="direct", risk="low")

        context = self._build_context(history)
        user_turn = CLASSIFIER_USER_TEMPLATE.format(context=context, message=message)

        # Build workflow description block for the system prompt
        if workflow_descriptions:
            wf_lines = "\n".join(
                f'  "{name}": {intent}' for name, intent in workflow_descriptions
            )
        else:
            wf_lines = "  (none)"

        system = CLASSIFIER_SYSTEM_PROMPT.format(workflow_descriptions=wf_lines)

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

        mode, risk, reason, workflow_hint = self._parse(raw, workflow_descriptions)
        logger.info(f"  mode: {mode}  risk: {risk}  reason: {reason}")
        if workflow_hint:
            logger.info(f"  workflow hint: {workflow_hint}")
        return ClassifierResult(mode=mode, risk=risk, workflow_hint=workflow_hint)

    def _build_context(self, history: list[dict]) -> str:
        """Format the last N messages as context for the classifier."""
        if not history:
            return ""

        recent = history[-self._context_window:]
        lines = []
        for msg in recent:
            role = msg["role"]
            content = msg["content"]
            if isinstance(content, str):
                preview = content[:120]
                lines.append(f"[{role}]: {preview}")
            elif isinstance(content, list):
                if role == "assistant":
                    texts = [b.get("text", "") for b in content if b.get("type") == "text"]
                    tools = [b.get("name", "") for b in content if b.get("type") == "tool_use"]
                    parts = []
                    if texts:
                        parts.append(texts[0][:80])
                    if tools:
                        parts.append(f"[used tools: {', '.join(tools)}]")
                    lines.append(f"[assistant]: {' '.join(parts)}")
                elif role == "user":
                    # tool results
                    tool_ids = [b.get("tool_use_id", "?") for b in content if b.get("type") == "tool_result"]
                    lines.append(f"[tool results: {len(tool_ids)} result(s)]")

        if not lines:
            return ""

        return "Recent conversation:\n" + "\n".join(lines) + "\n\n"

    def _parse(
        self,
        raw: str,
        workflow_descriptions: list[tuple[str, str]] | None = None,
    ) -> tuple[str, str, str, str | None]:
        """Parse classifier response. Returns (mode, risk, reason, workflow_hint)."""
        text = raw.strip()

        if text.startswith("```"):
            lines = text.splitlines()
            inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
            text = "\n".join(inner).strip()

        valid_workflows = {name for name, _ in workflow_descriptions} if workflow_descriptions else set()

        try:
            data = json.loads(text)
            mode = data.get("mode", "direct")
            risk = data.get("risk", "low")
            reason = data.get("reason", "")
            workflow_hint = data.get("workflow") or None

            if mode not in ("plan", "direct"):
                logger.info(f"  classifier returned invalid mode '{mode}' — defaulting to direct")
                mode = "direct"
            if risk not in ("low", "moderate", "high"):
                logger.info(f"  classifier returned invalid risk '{risk}' — defaulting to low")
                risk = "low"
            if workflow_hint and workflow_hint not in valid_workflows:
                logger.info(f"  classifier returned unknown workflow '{workflow_hint}' — ignoring")
                workflow_hint = None

            return mode, risk, reason, workflow_hint
        except (json.JSONDecodeError, AttributeError):
            logger.info(f"  classifier parse failed — defaulting to direct")
            return "direct", "low", "parse error", None


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
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
            text = "\n".join(inner).strip()

        try:
            data = json.loads(text)
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
        except (json.JSONDecodeError, AttributeError):
            logger.info("  workflow selector: parse failed — no match")
            return None
