import openai
from providers.openai_compat import OpenAICompatibleProvider


class DeepSeekProvider(OpenAICompatibleProvider):
    """DeepSeek — OpenAI-compatible API at api.deepseek.com."""

    def __init__(self, api_key: str, model: str):
        self.model = model
        self.client = openai.OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com/v1",
        )
