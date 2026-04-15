import json
from providers.base import BaseProvider, TextBlock
from runtime.prompts import CLASSIFIER_SYSTEM_PROMPT, CLASSIFIER_USER_TEMPLATE
from app_config import config
from logger import get_logger

logger = get_logger(__name__)


class IntentClassifier:

    def __init__(self, provider: BaseProvider):
        self._provider = provider
        self._context_window = config.runtime.intent_classifier.context_window

    def classify(self, message: str, history: list[dict]) -> str:
        """Return 'plan' or 'direct'."""
        if not config.runtime.intent_classifier.enabled:
            return "direct"

        context = self._build_context(history)
        user_turn = CLASSIFIER_USER_TEMPLATE.format(context=context, message=message)

        from messenger import Messenger
        messenger = Messenger()
        messenger.add_user_message(user_turn)

        response = self._provider.chat(
            messages=messenger.get_messages(),
            tools=[],
            system=CLASSIFIER_SYSTEM_PROMPT,
        )

        raw = next(
            (b.text for b in response.content if isinstance(b, TextBlock)), ""
        )

        mode, reason = self._parse(raw)
        logger.info(f"  mode: {mode}  reason: {reason}")
        return mode

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

    def _parse(self, raw: str) -> tuple[str, str]:
        """Parse classifier response. Returns (mode, reason)."""
        text = raw.strip()

        if text.startswith("```"):
            lines = text.splitlines()
            inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
            text = "\n".join(inner).strip()

        try:
            data = json.loads(text)
            mode = data.get("mode", "direct")
            reason = data.get("reason", "")
            if mode not in ("plan", "direct"):
                logger.info(f"  classifier returned invalid mode '{mode}' — defaulting to direct")
                mode = "direct"
            return mode, reason
        except (json.JSONDecodeError, AttributeError):
            logger.info(f"  classifier parse failed — defaulting to direct")
            return "direct", "parse error"
