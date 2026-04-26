from messenger import Messenger
from providers.base import BaseProvider, TextBlock
from planning.schema import Plan
from planning.prompts import SYNTHESIS_SYSTEM_PROMPT, SYNTHESIS_USER_TURN
from logger import get_logger

logger = get_logger(__name__)


class Synthesizer:

    def __init__(self, provider: BaseProvider):
        self._provider = provider

    def synthesize(self, plan: Plan) -> str:
        messenger = Messenger()

        user_turn = SYNTHESIS_USER_TURN.format(
            original_query=plan.original_query,
            summary=plan.summary(),
        )
        messenger.add_user_message(user_turn)

        response = self._provider.chat(
            messages=messenger.get_messages(),
            tools=[],
            system=SYNTHESIS_SYSTEM_PROMPT,
            label="Synthesizer",
        )

        text = next(
            (b.text for b in response.content if isinstance(b, TextBlock)), ""
        )
        logger.info("Synthesizer: response generated")
        return text
