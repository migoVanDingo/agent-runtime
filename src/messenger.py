from logger import get_logger

logger = get_logger(__name__)


class Messenger:

    def __init__(self):
        self._messages: list[dict] = []

    def add_user_message(self, content: str) -> None:
        self._messages.append({"role": "user", "content": content})
        logger.info("Added user message")

    def add_assistant_message(self, content: list) -> None:
        # Boundary conversion: SDK objects → plain dicts
        serialized = [block.model_dump() for block in content]
        self._messages.append({"role": "assistant", "content": serialized})
        logger.info("Added assistant message")

    def add_tool_results(self, tool_results: list[dict]) -> None:
        self._messages.append({"role": "user", "content": tool_results})
        logger.info(f"Added {len(tool_results)} tool result(s)")

    def get_messages(self) -> list[dict]:
        return self._messages
