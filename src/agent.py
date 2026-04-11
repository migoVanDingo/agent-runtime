import anthropic
from messenger import Messenger
from tools.registry import ToolRegistry
from tools.implementations.read_file import ReadFileTool
from tools.implementations.list_files import ListFilesTool
from tools.implementations.bash_exec import BashExecTool
from settings import get_settings
from logger import get_logger

logger = get_logger(__name__)

SYSTEM_PROMPT = """You are a helpful assistant with access to tools that let you read files,
list directories, and run bash commands. Use these tools when needed to answer the user's questions."""


class Agent:

    def __init__(self):
        settings = get_settings()
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self.model = settings.anthropic_model
        self.messenger = Messenger()
        self.registry = ToolRegistry()

        self.registry.register(ReadFileTool())
        self.registry.register(ListFilesTool())
        self.registry.register(BashExecTool())

    def call(self, user_message: str) -> str:
        self.messenger.add_user_message(user_message)

        while True:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=self.registry.to_api_schema(),
                messages=self.messenger.get_messages(),
            )

            logger.info(f"Stop reason: {response.stop_reason}")

            if response.stop_reason == "end_turn":
                self.messenger.add_assistant_message(response.content)
                text = next(
                    (
                        block.text
                        for block in response.content
                        if hasattr(block, "text")
                    ),
                    "",
                )
                return text

            if response.stop_reason == "tool_use":
                self.messenger.add_assistant_message(response.content)
                tool_results = []

                for block in response.content:
                    if block.type == "tool_use":
                        logger.info(f"Tool call: {block.name}({block.input})")
                        tool = self.registry.get(block.name)
                        result = tool.execute(block.input)
                        logger.info(f"Tool result: {result[:200]}")
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": result,
                            }
                        )

                self.messenger.add_tool_results(tool_results)
